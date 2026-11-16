"""Bounded, recoverable processing of durable service-receipt deadlines."""
from uuid import UUID
from sqlalchemy import text
from fastapi import HTTPException
from app.catalog_api import support_session_factory
from app.database import get_session_factory,tenant_transaction
from app.service_receipts import ReceiptAction,act


async def scan_service_receipts(ctx):
    async with support_session_factory()() as db:
        candidates=(await db.execute(text('SELECT * FROM public.orion_due_service_receipts(100)'))).mappings().all()
    count=0
    for row in candidates:
        queued=await ctx['redis'].enqueue_job('confirm_service_receipt',str(row['tenant_id']),str(row['entry_id']),
            _job_id='receipt:'+str(row['entry_id']),_expires=120)
        count+=queued is not None
    return count


async def confirm_service_receipt(ctx,tenant_id,entry_id):
    tenant,entry=UUID(tenant_id),UUID(entry_id)
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                row=(await db.execute(text("""SELECT e.version,e.auto_receipt_policy_version
                    FROM financial_entries e JOIN tenants t ON t.id=e.tenant_id
                    WHERE e.tenant_id=:t AND e.id=:id AND e.status='pending' AND t.status='active'"""),
                    {'t':tenant,'id':entry})).mappings().first()
                if not row:return 'changed'
                payload=ReceiptAction(action='confirm',expected_version=row['version'],
                    idempotency_key='receipt-auto:'+str(entry)+':'+str(row['auto_receipt_policy_version']))
                await act(db,tenant,entry,payload,automatic=True)
                return 'confirmed'
    except HTTPException as exc:
        if exc.status_code in (404,409,422):return 'changed'
        raise
