"""Bounded, QR-bound transactional notices. Unknown outcomes are reconciled, never blindly resent."""
import asyncio
import os
import re
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import text
from app.appointment_notices import enabled, notice_text, phone
from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction

OPEN = ('pending','blocked','sending','sent','delivered','uncertain')

async def bridge(path,method='GET',payload=None):
    from app.qr_api import _bridge
    async with asyncio.timeout(10):
        return await _bridge('/internal/appointment-notices/'+path,method,payload)


def binding():
    try:return UUID(os.environ['QR_AI_TENANT_ID'])
    except (KeyError,ValueError):return None


def result_state(result,row):
    if not isinstance(result,dict) or result.get('id')!=str(row['id']):return 'uncertain',None
    if result.get('status')=='unknown':return 'uncertain',None
    if result.get('tenant_id')!=str(row['tenant_id']) or result.get('account_phone')!=row['account_phone']:
        return 'uncertain',None
    state=result.get('status');message=result.get('message_id')
    if state in {'sent','delivered','read'} and isinstance(message,str) and re.fullmatch(r'[A-Za-z0-9:_-]{1,100}',message):
        return state,message
    if state=='not_sent':return 'blocked',None
    return 'uncertain',None


async def update_result(db,tenant,item,state,message=None,error=None):
    # Polling and a late HTTP response must not downgrade a more conclusive receipt.
    allowed={'blocked':('sending','uncertain'),'uncertain':('sending','uncertain'),
             'sent':('sending','uncertain','sent'),'delivered':('sending','uncertain','sent','delivered'),
             'read':('sending','uncertain','sent','delivered','read')}[state]
    await db.execute(text('''
        UPDATE appointment_notices SET status=:state,message_id=COALESCE(:message,message_id),
          last_error=:error,updated_at=clock_timestamp(),next_attempt_at=now()+interval '60 seconds'
        WHERE tenant_id=:tenant AND id=:id AND status=ANY(CAST(:allowed AS text[]))
    '''),{'tenant':tenant,'id':item,'state':state,'message':message,'error':error,'allowed':list(allowed)})


async def postpone(tenant,item,error):
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await db.execute(text('''
                UPDATE appointment_notices SET status=CASE WHEN expires_at<=now() THEN 'expired' ELSE 'blocked' END,
                  attempts=attempts+1,last_error=:error,updated_at=clock_timestamp(),
                  next_attempt_at=now()+make_interval(secs=>LEAST(900,30*power(2,LEAST(attempts,5))::int))
                WHERE tenant_id=:tenant AND id=:id AND status IN ('pending','blocked')
            '''),{'tenant':tenant,'id':item,'error':error})


async def reconcile(row):
    tenant,item=row['tenant_id'],row['id']
    try:
        result=await bridge('receipt','POST',{'id':str(item),'tenant_id':str(tenant),'account_phone':row['account_phone']})
        state,message=result_state(result,row)
    except (HTTPException,TimeoutError,ValueError):state,message='uncertain',None
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await update_result(db,tenant,item,state,message,'send_uncertain' if state=='uncertain' else 'not_sent' if state=='blocked' else None)
            if row['expires_at']<=datetime.now(timezone.utc):
                # Preserve sent != delivered. Stop polling old unknowns without replaying them.
                await db.execute(text('''
                    UPDATE appointment_notices SET next_attempt_at=now()+interval '10 years',
                      last_error=CASE WHEN status='sent' THEN 'delivery_unconfirmed' WHEN status IN ('sending','uncertain') THEN 'send_uncertain' ELSE last_error END
                    WHERE tenant_id=:tenant AND id=:id
                '''),{'tenant':tenant,'id':item})
    return state


async def professional_lock(db,tenant,professional):
    return (await db.execute(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:scope,0))'),
                            {'scope':str(tenant)+':'+str(professional)})).scalar_one()


async def process_appointment_notice(ctx,tenant_id,notice_id):
    if not enabled():return 'disabled'
    tenant,item=UUID(tenant_id),UUID(notice_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            raw=(await db.execute(text('''SELECT * FROM appointment_notices WHERE tenant_id=:tenant AND id=:id
                AND status IN ('pending','blocked','sending','sent','delivered','uncertain') AND next_attempt_at<=now()'''),
                {'tenant':tenant,'id':item})).mappings().first()
            row=dict(raw) if raw else None
    if not row:return 'not_pending'
    if row['status'] in {'sending','sent','delivered','uncertain'}:
        return await reconcile(row)
    if row['expires_at']<=datetime.now(timezone.utc):
        await postpone(tenant,item,'expired');return 'expired'
    if tenant!=binding():
        await postpone(tenant,item,'channel_not_configured');return 'blocked'
    try:
        ready=await bridge('readiness')
        account=phone(ready.get('account_phone'))
        if ready.get('tenant_id')!=str(tenant) or not ready.get('ready') or not account:
            error=ready.get('error') if ready.get('error') in {'storage_unavailable','not_connected'} else 'channel_not_configured'
            await postpone(tenant,item,error);return 'blocked'
        if row['account_phone'] and row['account_phone']!=account:
            await postpone(tenant,item,'account_changed');return 'blocked'
    except (HTTPException,TimeoutError,ValueError,AttributeError):
        await postpone(tenant,item,'bridge_unavailable');return 'blocked'
    # Commit a send-intent first. A crash from here is recoverable by receipt lookup.
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            claimed=(await db.execute(text('''UPDATE appointment_notices SET status='sending',
                account_phone=:account,attempts=attempts+1,updated_at=clock_timestamp(),
                next_attempt_at=now()+interval '60 seconds'
                WHERE tenant_id=:tenant AND id=:id AND status IN ('pending','blocked') AND next_attempt_at<=now()
                RETURNING *'''),{'tenant':tenant,'id':item,'account':account})).mappings().first()
            if not claimed:return 'not_pending'
            row=dict(claimed)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            if not await professional_lock(db,tenant,row['snapshot']['professional_id']):
                await update_result(db,tenant,item,'blocked',error='not_sent');return 'blocked'
            current=(await db.execute(text('''SELECT a.updated_at,a.status,c.whatsapp_e164,c.deleted_at
                FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND a.id=:id'''),{'tenant':tenant,'id':row['appointment_id']})).mappings().one()
            if current['updated_at']!=row['appointment_version'] or current['status']!=row['snapshot']['status']:
                terminal='superseded'
            elif row['expires_at']<=datetime.now(timezone.utc):terminal='expired'
            elif current['deleted_at'] or phone(current['whatsapp_e164'])!=row['snapshot']['phone']:terminal='failed'
            else:terminal=None
            if terminal:
                await db.execute(text("UPDATE appointment_notices SET status=:state,last_error=:error,updated_at=clock_timestamp() WHERE tenant_id=:tenant AND id=:id AND status='sending'"),
                    {'tenant':tenant,'id':item,'state':terminal,'error':'recipient_changed' if terminal=='failed' else 'expired' if terminal=='expired' else None})
                return terminal
            # Keep the same short professional lock used by the booking engine until this
            # bounded transport attempt ends, so a later cancellation cannot overtake it.
            try:
                payload={'id':str(item),'tenant_id':str(tenant),'account_phone':account,
                         'phone':row['snapshot']['phone'],'text':notice_text(row['event'],row['snapshot']),
                         'expires_at':int(row['expires_at'].timestamp()*1000)}
                result=await bridge('send','POST',payload)
                state,message=result_state(result,row)
            except (HTTPException,TimeoutError,ValueError):state,message='uncertain',None
            await update_result(db,tenant,item,state,message,'send_uncertain' if state=='uncertain' else 'not_sent' if state=='blocked' else None)
    return state


async def scan_appointment_notices(ctx):
    if not enabled():return 0
    async with support_session_factory()() as db:
        rows=(await db.execute(text('''SELECT id,tenant_id,next_attempt_at FROM appointment_notices
            WHERE status IN ('pending','blocked','sending','sent','delivered','uncertain') AND next_attempt_at<=now()
            ORDER BY next_attempt_at,id LIMIT 30'''))).mappings().all()
    queued=0
    for row in rows:
        job=await ctx['redis'].enqueue_job('process_appointment_notice',str(row['tenant_id']),str(row['id']),
            _job_id=f"appointment-notice:{row['id']}:{row['next_attempt_at'].isoformat()}")
        queued+=job is not None
    return queued
