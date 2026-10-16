"""Reconciliador de eventos Asaas SOMENTE em sandbox, nunca cria cobranças."""
import os
from sqlalchemy import text
from app.billing_api import reconcile_event
from app.catalog_api import support_session_factory


async def reconcile_asaas_job(ctx:dict,external_event_id:str):
    if not os.getenv("ASAAS_SANDBOX_API_KEY"):
        return "sandbox_not_configured"
    try:
        return await reconcile_event(external_event_id)
    except Exception:
        async with support_session_factory()() as session:
            async with session.begin():
                await session.execute(text("""
                    UPDATE billing_events SET attempts=attempts+1,
                      next_attempt_at=now()+make_interval(
                          secs=>LEAST(3600,15*power(2,LEAST(attempts,8))::int)),
                      status=CASE WHEN attempts>=7 THEN 'failed' ELSE 'pending_verification' END
                    WHERE provider_event_id=:id AND status='pending_verification'
                """),{"id":external_event_id})
        return "retry"


async def scan_pending_billing(ctx:dict):
    if not os.getenv("ASAAS_SANDBOX_API_KEY"):
        return 0
    async with support_session_factory()() as session:
        ids=(await session.execute(text("""
            SELECT provider_event_id FROM billing_events
            WHERE status='pending_verification' AND next_attempt_at<=now()
            LIMIT 100
        """))).scalars().all()
    for item in ids:
        await ctx["redis"].enqueue_job("reconcile_asaas_job",item)
    return len(ids)
