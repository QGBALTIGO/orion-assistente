"""Durable archive work and terminal bookkeeping; never retries a WhatsApp/HTTP effect."""
import logging
import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory


log = logging.getLogger(__name__)


async def process_subscription_chatbot_restore(ctx, tenant_id: str, job_id: str):
    from app.subscription_chatbot_archive import process_restore
    return await process_restore(UUID(tenant_id), UUID(job_id))


async def scan_subscription_chatbot(ctx):
    """Read only queue identities before each operation takes its tenant guard."""
    async with support_session_factory()() as db:
        rows = (await db.execute(text("""SELECT tenant_id,id FROM subscription_chatbot_jobs
            WHERE kind='restore' AND (status='queued' OR status='running'
              AND started_at<clock_timestamp()-interval '10 minutes')
            ORDER BY created_at,id LIMIT 25"""))).mappings().all()
        stale = (await db.execute(text("""SELECT DISTINCT tenant_id FROM subscription_chatbot_jobs
            WHERE (kind='url_tags' AND status='running' AND started_at<clock_timestamp()-interval '2 minutes')
               OR (kind='test_response' AND status IN ('queued','running')
                   AND created_at<clock_timestamp()-interval '15 minutes')
            LIMIT 100"""))).scalars().all()
    # A worker restart does not repeat an HTTP probe/POST or an uncertain send.
    # These jobs receive a terminal answer instead of leaving the UI polling.
    for tenant in stale:
        async with support_session_factory()() as db:
            async with db.begin():
                await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": tenant})
                await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',
                    error_code='CHATBOT_DELIVERY_UNCONFIRMED',finished_at=clock_timestamp()
                    WHERE tenant_id=:t AND (
                      kind='url_tags' AND status='running' AND started_at<clock_timestamp()-interval '2 minutes'
                      OR kind='test_response' AND status IN ('queued','running')
                         AND created_at<clock_timestamp()-interval '15 minutes')"""), {"t": tenant})
    for row in rows:
        # The SQL processor owns the durable claim, including safe recovery.
        # ARQ uniqueness merely reduces duplicate delivery to that processor.
        await ctx["redis"].enqueue_job("process_subscription_chatbot_restore", str(row["tenant_id"]), str(row["id"]),
                                      _job_id=f"subscription-chatbot-restore:{row['id']}:{int(time.time()) // 60}")
    return len(rows)


async def backup_subscription_chatbots(ctx):
    from app.subscription_chatbot_archive import scheduled_backup
    async with support_session_factory()() as db:
        tenants = (await db.execute(text("""SELECT t.id FROM tenants t
            JOIN business_onboarding b ON b.tenant_id=t.id
            WHERE b.configuration->>'niche'='servico_assinaturas'
              AND NOT EXISTS(SELECT 1 FROM subscription_chatbot_backups s WHERE s.tenant_id=t.id
                AND s.source='scheduled' AND s.backup_day=(clock_timestamp() AT TIME ZONE t.timezone)::date)
            ORDER BY t.id LIMIT 100"""))).scalars().all()
    completed = 0
    for tenant in tenants:
        try:
            completed += bool(await scheduled_backup(tenant))
        except HTTPException:
            # A revoked owner/niche or a bounded graph is not an instruction to
            # widen access. Another tenant's backup can still complete.
            continue
        except Exception as error:
            log.warning("subscription_chatbot_backup_failed type=%s", type(error).__name__)
    return completed
