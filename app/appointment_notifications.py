"""Appointment transactional outbox, using existing managed QR transport receipts.

No Redis timer owns the business operation. A committed 'sending' attempt is
never reset to pending: transport ambiguity requires receipt reconciliation.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import appointment_access as access
from app.booking_service import read_appointment
from app.database import get_session_factory, tenant_transaction
from app.appointment_messages import compose


def cipher():
    return Fernet(os.environ['INTEGRATION_FERNET_KEY'].encode())


def central_enabled():
    return (access.enabled()
            and os.getenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED') == '1'
            and os.getenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED') == '1')


def central_channel():
    from app.qr_legacy import channel_id, configured_tenant
    try:
        admin = configured_tenant()
        return admin, channel_id(admin)
    except HTTPException:
        return None, None


def notification_settings(row, central, channel=None):
    if not central:
        return dict(row) if row else None
    # Every company gets safe defaults, without requiring its own QR session.
    # An explicit company opt-out is preserved; its channel cannot select
    # the platform WhatsApp number.
    defaults = {'enabled': True, 'version': 0, 'confirmation': True,
        'professional_notice': True, 'cancellation': True, 'rescheduling': True,
        'reschedule_acceptance': False, 'reminder': True,
        'review': True, 'review_delay_min': 15,
        'return_invitation': False, 'signature': 'Orion Agenda'}
    return {**defaults, **(dict(row) if row else {}), 'channel_id': channel}


async def enqueue_event(db, tenant, appointment, event, details):
    central = central_enabled()
    if not central and not access.tenant_enabled(tenant):return
    from app.plan_permissions import has_feature
    if not await has_feature(db, tenant, "notifications.basic"):return
    # Every committed mutation invalidates old pending facts in this SAME tx.
    if event in {'confirmed','cancelled','rescheduled','completed','no_show','hold_expired'}:
        await db.execute(text("""UPDATE appointment_notifications SET status='cancelled',
        last_error='appointment_changed',updated_at=clock_timestamp()
        WHERE tenant_id=:t AND appointment_id=:id AND status='pending'"""), {'t':tenant,'id':appointment})
    if event=='cancelled' and (details.get('previous') or {}).get('status')!='confirmed':return
    saved=(await db.execute(text('SELECT * FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant})).mappings().first()
    admin,channel=central_channel() if central else (None,None)
    if central and (admin is None or channel is None):return
    settings=notification_settings(saved,central,channel)
    if not settings or not settings['enabled'] or not settings['channel_id']:return
    row=(await db.execute(text('SELECT * FROM appointments WHERE tenant_id=:t AND id=:id'),{'t':tenant,'id':appointment})).mappings().one()
    now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
    customer=details.get('requested_by')=='customer'
    events=[]
    def add(kind,recipient,due=None,expiry=None,recipient_id=None):
        events.append((kind,recipient,recipient_id or row['professional_id' if recipient=='professional' else 'customer_id'],due or now,expiry or now+timedelta(hours=24)))
    if event=='confirmed' and settings['professional_notice']:add('professional_new','professional')
    if event=='cancelled' and settings['cancellation']:
        add('professional_cancelled' if customer else 'customer_cancelled','professional' if customer else 'customer')
    if event=='reschedule_proposed' and settings['rescheduling']:add('customer_proposal','customer')
    if event=='reschedule_declined' and settings['rescheduling']:add('professional_proposal_declined','professional')
    if event=='rescheduled' and settings['rescheduling']:
        add('professional_rescheduled' if customer else 'customer_rescheduled','professional' if customer else 'customer')
    if row['status']=='confirmed' and event in {'confirmed','rescheduled'} and settings['reminder']:
        due=row['start_at']-timedelta(hours=1)
        # Never invent 'one hour' for an appointment created after that instant.
        if due>=now:add('reminder','customer',due,due+timedelta(minutes=2))
    if event=='completed' and settings['review'] and details.get('source')!='system':
        due=now+timedelta(minutes=settings['review_delay_min'])
        add('review','customer',due,due+timedelta(hours=24))
    for kind,recipient,recipient_id,due,expiry in events:
        key=event+':'+str(appointment)+':'+row['updated_at'].isoformat()+':'+kind+':'+str((details.get('previous') or {}).get('proposal_id',''))
        center_column=",central_channel_id" if central else ""
        center_value=",:central_channel" if central else ""
        await db.execute(text('''INSERT INTO appointment_notifications
          (tenant_id,appointment_id,appointment_version,event,recipient_kind,recipient_id,channel_id''' +
          center_column + ''',settings_version,event_key,previous,due_at,expires_at)
          VALUES(:t,:id,:version,:event,:kind,:recipient,:channel''' +
          center_value + ''',:settings,:key,CAST(:previous AS jsonb),:due,:expiry)
          ON CONFLICT DO NOTHING'''),{'t':tenant,'id':appointment,'version':row['updated_at'],'event':kind,'kind':recipient,
          'recipient':recipient_id,'channel':None if central else settings['channel_id'],
            'central_channel':channel if central else None,
            'settings':settings['version'],'key':key,
          'previous':json.dumps(details.get('previous') or {},default=str),'due':due,'expiry':expiry})


async def eligibility(db, tenant, job):
    central = job.get('central_channel_id') is not None
    if central_enabled() and not central:
        # Prevent any pre-rollout tenant channel notice from bypassing the
        # platform-only sender rule when the central lane becomes active.
        return 'execution_paused',None,None,None
    if central:
        admin, channel = central_channel()
        if not central_enabled() or admin is None or channel != job['central_channel_id']:
            return 'execution_paused',None,None,None
    from app.plan_permissions import has_feature
    if not await has_feature(db, tenant, "notifications.basic"):
        return 'plan_feature_unavailable',None,None,None
    saved=(await db.execute(text('SELECT * FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant})).mappings().first()
    settings=notification_settings(saved,central,job.get('central_channel_id'))
    if (not central and not access.tenant_enabled(tenant)) or not settings or not settings['enabled']:
        return 'execution_paused',None,None,None
    expected_channel = job.get('central_channel_id') if central else job['channel_id']
    if settings['version']!=job['settings_version'] or settings['channel_id']!=expected_channel:
        return 'settings_changed',None,None,None
    row=await read_appointment(db,tenant,job['appointment_id'])
    if row['updated_at']!=job['appointment_version']:return 'appointment_changed',None,None,None
    channel=(await db.execute(text('SELECT whatsapp_channel_id FROM tenants WHERE id=:t'),{'t':tenant})).scalar_one_or_none()
    if not central and channel is not None and channel!=job['channel_id']:return 'channel_changed',None,None,None
    if job['event']=='customer_proposal':
        proposal=(await db.execute(text("""SELECT 1 FROM appointment_change_proposals WHERE tenant_id=:t
            AND appointment_id=:id AND id=CAST(:proposal AS uuid) AND status='pending' AND expires_at>clock_timestamp()"""),
            {'t':tenant,'id':job['appointment_id'],'proposal':job['previous'].get('proposal_id')})).first()
        if proposal is None:return 'proposal_changed',None,None,None
    if job['event'] in {'review','customer_rescheduled','customer_proposal'}:
        blocked=(await db.execute(text('SELECT customer_access_blocked FROM appointments WHERE tenant_id=:t AND id=:id'),
            {'t':tenant,'id':job['appointment_id']})).scalar_one()
        if blocked:return 'customer_link_revoked',None,None,None
    expected='completed' if job['event']=='review' else 'cancelled' if 'cancelled' in job['event'] else 'confirmed'
    if row['status']!=expected:return 'appointment_changed',None,None,None
    now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
    if now<job['due_at']:return 'not_due',None,None,None
    if now>=job['expires_at']:return 'expired',None,None,None
    if job['recipient_kind']=='professional':
        if row['professional_id']!=job['recipient_id']:return 'professional_changed',None,None,None
        phone=(await db.execute(text('SELECT phone_e164 FROM professionals WHERE tenant_id=:t AND id=:id AND active'),
            {'t':tenant,'id':job['recipient_id']})).scalar_one_or_none()
        if not phone:return 'professional_contact_unavailable',None,None,None
    else:
        if row['customer_id']!=job['recipient_id']:return 'customer_changed',None,None,None
        contact=(await db.execute(text('''SELECT c.whatsapp_e164,s.operational_opt_in,s.marketing_opt_in
          FROM customers c LEFT JOIN contact_consents s ON s.tenant_id=c.tenant_id AND s.contact_e164=c.whatsapp_e164
          WHERE c.tenant_id=:t AND c.id=:id AND c.active AND c.deleted_at IS NULL'''),{'t':tenant,'id':job['recipient_id']})).mappings().first()
        if not contact or contact['operational_opt_in'] is not True:return 'communication_not_authorized',None,None,None
        if job['event']=='review' and contact['marketing_opt_in'] is not True:return 'review_not_authorized',None,None,None
        phone=contact['whatsapp_e164']
    if central:
        if not isinstance(phone,str) or not re.fullmatch(r'(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})',phone):
            return 'invalid_recipient',None,None,None
    elif not access.recipient_allowed(phone):
        return 'pilot_recipient_not_authorized',None,None,None
    digest=hashlib.sha256(phone.encode()).hexdigest()
    if job.get('recipient_hash') and job['recipient_hash']!=digest:return 'contact_changed',None,None,None
    return None,row,phone,settings


async def prepare_body(db,tenant,job,row,settings):
    shop=(await db.execute(text('''SELECT t.name,p.public_slug,p.public_enabled,p.address,p.city,p.state
        FROM tenants t LEFT JOIN management_site_preferences p ON p.tenant_id=t.id WHERE t.id=:t'''),{'t':tenant})).mappings().one()
    customer_link=None
    if job['recipient_kind']=='customer' and job['event'] in {'customer_rescheduled','customer_proposal','review'}:
        customer_link=await access.issue(db,tenant,job['appointment_id'])
    from app.public_origins import canonical_public_origin
    booking_link=canonical_public_origin()+'/s/'+shop['public_slug'] if shop.get('public_slug') and shop['public_enabled'] else None
    future=(await db.execute(text("SELECT EXISTS(SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:customer AND id<>:id AND status='confirmed' AND start_at>now())"),
        {'t':tenant,'customer':row['customer_id'],'id':row['id']})).scalar_one()
    return compose(job['event'],row,company=shop['name'],signature=settings['signature'],customer_link=customer_link,
        booking_link=booking_link,previous=job['previous'],include_return=settings['return_invitation'] and not future,
        address=', '.join(str(shop[k]) for k in ('address','city','state') if shop.get(k)))


async def process_notification(ctx,tenant_id,notification_id):
    if not access.enabled():return 'disabled'
    tenant,identity=UUID(tenant_id),UUID(notification_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            job=(await db.execute(text('SELECT * FROM appointment_notifications WHERE tenant_id=:t AND id=:id FOR UPDATE SKIP LOCKED'),{'t':tenant,'id':identity})).mappings().first()
            if not job:return 'already_claimed'
            if job['status']=='sending':
                if job['attempted_at']<datetime.now(timezone.utc)-timedelta(minutes=2):
                    await db.execute(text("UPDATE appointment_notifications SET status='unknown',last_error='worker_interrupted' WHERE tenant_id=:t AND id=:id"),{'t':tenant,'id':identity})
                return 'unknown'
            if job['status']!='pending':return job['status']
            reason,row,phone,settings=await eligibility(db,tenant,job)
            if reason:
                if reason!='not_due':
                    await db.execute(text("UPDATE appointment_notifications SET status=:status,last_error=:error,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"),
                        {'t':tenant,'id':identity,'status':'cancelled' if reason in {'expired','appointment_changed','settings_changed','execution_paused'} else 'failed','error':reason})
                return reason
            # Only the verified admin sender is used for central appointment
            # notices. Other senders keep the original explicit allowlist.
            from app.catalog_api import support_session_factory
            central = job.get('central_channel_id') is not None
            send_tenant, send_channel = (central_channel() if central else (tenant,job['channel_id']))
            if not central:
                async with support_session_factory()() as registry:
                    senders=(await registry.execute(text('SELECT allowed_senders FROM qr_channels WHERE tenant_id=:t AND id=:c AND enabled'),
                        {'t':tenant,'c':job['channel_id']})).scalar_one_or_none()
                if not senders or phone.lstrip('+') not in senders:
                    await db.execute(text("UPDATE appointment_notifications SET status='failed',last_error='transport_recipient_not_authorized' WHERE tenant_id=:t AND id=:id"),{'t':tenant,'id':identity})
                    return 'transport_recipient_not_authorized'
            if ctx.get('appointment_sender') is None:
                # An observed offline channel is a definite no-send. Only this
                # preflight can be retried; ambiguous HTTP sends never are.
                try:
                    from app.qr_channels import _canonical, _bridge_control, _control_result
                    async with support_session_factory()() as registry:
                        channel,state=await _canonical(registry,send_tenant)
                    if channel is None or state!='ready' or channel['id']!=send_channel:
                        raise ValueError('channel_changed')
                    observed=_control_result(channel,await _bridge_control(channel,'status','GET'),'status')
                    if observed['status']!='connected' or not observed.get('phone_verified'):
                        raise ValueError('channel_offline')
                except Exception:
                    if central:
                        # No transport attempt occurred. Keep the durable
                        # notification queued during a temporary central outage;
                        # expiration is still bounded by the existing 24h TTL.
                        query="""UPDATE appointment_notifications SET
                            status=CASE WHEN clock_timestamp()+interval '2 minutes'>=expires_at THEN 'failed' ELSE 'pending' END,
                            due_at=LEAST(clock_timestamp()+interval '2 minutes',expires_at-interval '1 microsecond'),
                            last_error='channel_unavailable',updated_at=clock_timestamp()
                            WHERE tenant_id=:t AND id=:id"""
                    else:
                        query="""UPDATE appointment_notifications SET
                            status=CASE WHEN attempts>=4 OR clock_timestamp()+interval '30 seconds'>=expires_at THEN 'failed' ELSE 'pending' END,
                            attempts=attempts+1,due_at=LEAST(clock_timestamp()+interval '30 seconds',expires_at-interval '1 microsecond'),
                            last_error='channel_unavailable',updated_at=clock_timestamp()
                            WHERE tenant_id=:t AND id=:id"""
                    await db.execute(text(query),{'t':tenant,'id':identity})
                    return 'channel_unavailable'
            try:body=await prepare_body(db,tenant,job,row,settings)
            except (ValueError,KeyError):
                await db.execute(text("UPDATE appointment_notifications SET status='failed',last_error='message_facts_invalid' WHERE tenant_id=:t AND id=:id"),{'t':tenant,'id':identity})
                return 'message_facts_invalid'
            encrypted=cipher().encrypt(body.encode()).decode()
            # The support-only broker compares the exact prepared body hash,
            # not a caller-supplied text or arbitrary phone. Legacy pilot rows
            # retain the unchanged old update until the feature is enabled.
            digest_sql=",body_sha256=:body_digest" if central else ""
            values={'t':tenant,'id':identity,'body':encrypted,
                    'hash':hashlib.sha256(phone.encode()).hexdigest()}
            if central:values['body_digest']=hashlib.sha256(body.encode()).hexdigest()
            await db.execute(text("""UPDATE appointment_notifications SET status='sending',attempts=attempts+1,
                body_encrypted=:body,recipient_hash=:hash""" + digest_sql +
                """,attempted_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id"""),values)
            job={**dict(job),'recipient_hash':hashlib.sha256(phone.encode()).hexdigest()}
    result,error,provider='unknown','transport_result_unknown',None
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                reason,_,_,_=await eligibility(db,tenant,job)
        if reason:
            result,error='cancelled',reason
        else:
            sender=ctx.get('appointment_sender')
            if sender is None:
                from app.automation_transport import _request
                data=await _request(send_tenant,send_channel,identity,message={
                    'to':phone.lstrip('+'),'text':body,'operation_id':str(identity),
                    **({'source':'appointment'} if central else {})})
            else:data=await sender(send_tenant,send_channel,phone,body,str(identity))
            if data.get('accepted') is True and data.get('state')=='accepted':
                result,error,provider='accepted',None,data.get('provider_id')
            elif data.get('state')=='refused':result,error='failed','transport_refused'
    except Exception:
        pass  # Never persist provider exception strings or secrets.
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await db.execute(text('''UPDATE appointment_notifications SET status=:status,last_error=:error,
                provider_id=:provider,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND status='sending' '''),
                {'t':tenant,'id':identity,'status':result,'error':error,'provider':provider})
    return result


async def scan_notifications(ctx):
    if not access.enabled():return 'disabled'
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        jobs=(await db.execute(text('SELECT * FROM public.orion_due_appointment_notifications(100)'))).mappings().all()
    for job in jobs:
        await reconcile_notification(ctx,str(job['tenant_id']),str(job['id']))
        await ctx['redis'].enqueue_job('process_appointment_notification',str(job['tenant_id']),str(job['id']),
            _job_id='appointment-notice:'+str(job['id'])+':'+str(int(datetime.now(timezone.utc).timestamp()//30)))
    return len(jobs)


async def cleanup_notification_payloads(ctx):
    if not access.enabled():return 0
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        async with db.begin():
            return (await db.execute(text('SELECT public.orion_clean_appointment_payloads(1000)'))).scalar_one()


async def reconcile_notification(ctx,tenant_id,notification_id):
    """Only inspect the original operation receipt, never resend it."""
    if not access.enabled():return 'disabled'
    tenant,identity=UUID(tenant_id),UUID(notification_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            columns='channel_id,central_channel_id,status' if central_enabled() else 'channel_id,status'
            row=(await db.execute(text("SELECT "+columns+" FROM appointment_notifications WHERE tenant_id=:t AND id=:id AND status IN ('accepted','unknown','delivered')"),
                {'t':tenant,'id':identity})).mappings().first()
    if not row:return 'unavailable'
    try:
        reader=ctx.get('appointment_inspector')
        if reader is None:
            from app.automation_transport import inspect_automation
            reader=inspect_automation
        channel=row.get('central_channel_id') or row['channel_id']
        receipt_tenant=central_channel()[0] if row.get('central_channel_id') else tenant
        if channel is None or receipt_tenant is None:return 'unknown'
        receipt=await reader(receipt_tenant,channel,str(identity))
        if receipt.get('operation_id')!=str(identity) or receipt.get('accepted') is not True:
            # An unconfirmed inspection is not evidence of delivery. Advance
            # its inspection time so other unknown receipts get a fair turn.
            async with get_session_factory()() as db:
                async with tenant_transaction(db,tenant):
                    await db.execute(text("UPDATE appointment_notifications SET updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND status IN ('accepted','unknown','delivered')"),{'t':tenant,'id':identity})
            return 'unknown'
        status=receipt['delivery'] if receipt.get('delivery') in {'read','delivered'} else 'accepted'
        if row['status']=='delivered' and status=='accepted':status='delivered'
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await db.execute(text("UPDATE appointment_notifications SET status=:status,last_error=NULL,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND status IN ('accepted','unknown','delivered')"),
                    {'t':tenant,'id':identity,'status':status})
        return status
    except Exception:
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await db.execute(text("UPDATE appointment_notifications SET updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND status IN ('accepted','unknown','delivered')"),{'t':tenant,'id':identity})
        return 'unknown'
