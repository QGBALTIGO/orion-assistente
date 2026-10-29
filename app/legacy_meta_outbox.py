"""One durable attempt for pre-central Meta replies; uncertain never means retry.

The legacy schema has only pending/sent/dead. Its terminal `dead` state reserves
an attempt before I/O, with `delivery_unverified` explicitly requiring review.
A signed Meta receipt can reconcile that attempt without sending anything.
"""
from datetime import datetime,timedelta,timezone
import os
from uuid import UUID
from cryptography.fernet import Fernet,InvalidToken
from sqlalchemy import text
from app.database import get_session_factory,tenant_transaction
from app.messaging import CloudApiProvider
from app.qr_presentation import message_units

UNVERIFIED=('delivery_unverified','prior_attempt_unverified')


async def reason(db,tenant,item):
    from app.plan_permissions import has_feature
    if not await has_feature(db, tenant, "reception.ai"):
        return 'plan_feature_unavailable'
    if not isinstance(item['body'],str) or not item['body'].strip() or message_units(item['body'])>1200:
        return 'payload_not_approved'
    if (await db.execute(text('SELECT public.inbox_meta_guard(:t,:c,true)'),
        {'t':tenant,'c':item['channel_id']})).scalar_one() is not True or item['channel_status']!='connected':
        return 'channel_not_authorized'
    stamp=(item['envelope'] or {}).get('provider_at')
    now=datetime.now(timezone.utc)
    if type(stamp) is not int or not 0<=stamp<=9999999999:return 'service_window_unverified'
    if not now-timedelta(hours=24)<datetime.fromtimestamp(stamp,timezone.utc)<=now:return 'outside_service_window'
    if item['received_at']<now-timedelta(minutes=10):return 'reply_expired'
    values={'t':tenant,'c':item['channel_id'],'phone':item['to_e164'],'order':item['ingress_order']}
    stopped=(await db.execute(text('''SELECT
      EXISTS(SELECT 1 FROM human_handoffs WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone
        AND status='active' AND expires_at>clock_timestamp()) OR
      EXISTS(SELECT 1 FROM inbox_controls WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone AND mode<>'ai') OR
      EXISTS(SELECT 1 FROM automation_contact_state WHERE tenant_id=:t AND contact_e164=:phone AND human_paused)
      '''),values)).scalar_one()
    if stopped:return 'customer_or_human_stop'
    newer=(await db.execute(text('''SELECT EXISTS(SELECT 1 FROM inbound_events WHERE tenant_id=:t AND channel_id=:c
      AND sender_e164=:phone AND ingress_order>:order AND coalesce(envelope->>'kind','text') NOT IN ('reaction','sticker'))'''),values)).scalar_one()
    if newer:return 'newer_customer_event'
    return None


async def process(ctx,tenant_id,outbox_id):
    from app.inbox_control import enabled
    if enabled():return 'central_outbox_required'
    if os.getenv('META_LIVE_SEND')!='1':return 'not_enabled'
    if not os.getenv('INTEGRATION_FERNET_KEY'):return 'missing_encryption_key'
    tenant,identity=UUID(tenant_id),UUID(outbox_id);factory=get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            item=(await db.execute(text('''SELECT o.*,c.phone_number_id,c.token_enc,c.status AS channel_status,
              e.received_at,e.envelope,e.ingress_order FROM message_outbox o JOIN whatsapp_channels c
              ON c.tenant_id=o.tenant_id AND c.id=o.channel_id JOIN inbound_events e
              ON e.tenant_id=o.tenant_id AND e.id=o.event_id
              WHERE o.tenant_id=:t AND o.id=:id AND o.status='pending' AND o.next_attempt_at<=clock_timestamp()
              FOR UPDATE OF o SKIP LOCKED'''),{'t':tenant,'id':identity})).mappings().first()
            if not item:return 'unavailable'
            item=dict(item)
            await db.execute(text('SELECT pg_advisory_xact_lock_shared(hashtextextended(:scope,0))'),
                {'scope':f'meta-input:{tenant}:{item["channel_id"]}:{item["to_e164"]}'})
            blocked=await reason(db,tenant,item)
            if item['attempts']>0:blocked='prior_attempt_unverified'
            try:token=Fernet(os.environ['INTEGRATION_FERNET_KEY'].encode()).decrypt(item['token_enc']).decode()
            except (ValueError,TypeError,InvalidToken):blocked=blocked or 'invalid_credentials';token=None
            await db.execute(text("UPDATE message_outbox SET status='dead',last_error=:error,attempts=attempts+:attempt WHERE tenant_id=:t AND id=:id"),
                {'t':tenant,'id':identity,'error':blocked or 'delivery_unverified','attempt':0 if blocked else 1})
    if blocked:return 'verification_required' if blocked in UNVERIFIED else 'suppressed'
    from app.plan_permissions import tenant_has_feature
    if not await tenant_has_feature(tenant, "reception.ai"):
        async with factory() as db:
            async with tenant_transaction(db, tenant):
                await db.execute(text("UPDATE message_outbox SET last_error='plan_feature_unavailable' WHERE tenant_id=:t AND id=:id AND status='dead'"),
                                 {'t':tenant,'id':identity})
        return 'suppressed'
    # Reservation has committed. A crash before/during/after I/O cannot replay it.
    try:
        external=await (ctx.get('legacy_meta_sender') or CloudApiProvider().send_text)(
            {'phone_number_id':item['phone_number_id'],'access_token':token,'operation_id':str(identity)},item['to_e164'],item['body'])
        if not isinstance(external,str) or not 1<=len(external)<=150:raise ValueError('unverified_delivery')
    except Exception:return 'uncertain'
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            await db.execute(text("""UPDATE message_outbox SET status='sent',external_delivery_id=:external,last_error=NULL
              WHERE tenant_id=:t AND id=:id AND status='dead' AND last_error='delivery_unverified'"""),
              {'t':tenant,'id':identity,'external':external})
    return 'sent'


async def receipt(db,tenant,channel,phone,external,status,operation=None):
    """Only the authenticated webhook's resolved tenant/channel invokes this."""
    if not operation:return
    try:operation=UUID(str(operation))
    except (ValueError,TypeError,AttributeError):return
    if status not in {'accepted','delivered','read','failed'}:return
    if not isinstance(external,str) or not 1<=len(external)<=150:return
    await db.execute(text('''UPDATE message_outbox SET status=:status,external_delivery_id=:external,last_error=:error
      WHERE tenant_id=:t AND channel_id=:c AND id=:id AND to_e164=:phone AND attempts>0
        AND (external_delivery_id IS NULL OR external_delivery_id=:external)
        AND status='dead' AND last_error='delivery_unverified' '''),
      {'t':tenant,'c':channel,'id':operation,'phone':phone,'external':external,
       'status':'dead' if status=='failed' else 'sent','error':'provider_reported_failure' if status=='failed' else None})
