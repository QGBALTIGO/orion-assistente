"""One-use, fail-closed recovery of the specific pre-rollout Peixoto booking.

This is NOT a public endpoint, message composer, batch resend, or generic
admin override. It accepts no user-supplied phone, appointment, or text.
The normal appointment outbox, admin QR broker, tenant RLS and receipt ledger
remain the only transport. A missing, changed or ambiguous booking sends none.
"""
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from sqlalchemy import text

from app.appointment_notifications import central_channel, central_enabled, notification_settings
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import has_feature

TENANT = UUID("26694b4b-be15-4850-bcb9-69b184897e79")
SITE = "barbearia-peixoto"
START = datetime.fromisoformat("2026-10-30T21:01:30+00:00")
END = datetime.fromisoformat("2026-10-30T21:02:20+00:00")
PHONE = re.compile(r"^\+(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$")
TEST_KEY = "orion_test_recovery"


def eligible_booking(item, now):
    """No expired/cancelled booking and no phone guessed from an owner account."""
    return (item is not None and item["status"] == "confirmed"
            and item["end_at"] > now and item["tenant_status"] == "active"
            and item["public_slug"] == SITE and item["public_enabled"] is True
            and item["professional_active"] is True
            and isinstance(item["phone_e164"], str)
            and PHONE.fullmatch(item["phone_e164"]) is not None)


async def recover_once():
    """Returns (state, job id or None); no network send is performed here."""
    if not central_enabled():
        return "central_disabled", None
    admin, channel = central_channel()
    if admin is None or admin == TENANT or channel is None:
        return "central_identity_unavailable", None

    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, TENANT):
            # Concurrent worker starts cannot select, check, and enqueue twice.
            await db.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "orion:single-peixoto-20261030"})
            if not await has_feature(db, TENANT, "notifications.basic"):
                return "plan_not_eligible", None
            candidates = (await db.execute(text("""
                SELECT a.id,a.updated_at,a.professional_id,a.status,a.end_at,
                    p.active AS professional_active,p.phone_e164,
                    t.status AS tenant_status,site.public_slug,site.public_enabled
                FROM public_booking_receipts r
                JOIN appointments a ON a.tenant_id=r.tenant_id
                    AND a.id=r.appointment_id
                JOIN professionals p ON p.tenant_id=a.tenant_id
                    AND p.id=a.professional_id
                JOIN tenants t ON t.id=a.tenant_id
                JOIN management_site_preferences site ON site.tenant_id=t.id
                WHERE r.tenant_id=:tenant
                  AND r.created_at>=:first AND r.created_at<:last
                ORDER BY r.created_at,a.id LIMIT 2
            """), {"tenant": TENANT, "first": START, "last": END}
            )).mappings().all()
            if len(candidates) != 1:
                return "booking_not_unique_or_missing", None
            row = candidates[0]
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if not eligible_booking(row, now):
                return "booking_changed_or_contact_unavailable", None

            prior = (await db.execute(text("""
                SELECT id FROM appointment_notifications
                WHERE tenant_id=:tenant AND appointment_id=:appointment
                    AND event='professional_new'
                LIMIT 1
            """), {"tenant": TENANT, "appointment": row["id"]}
            )).scalar_one_or_none()
            if prior is not None:
                return "notice_already_exists", None

            settings = (await db.execute(text(
                "SELECT * FROM appointment_communication_settings WHERE tenant_id=:t"),
                {"t": TENANT})).mappings().first()
            config = notification_settings(settings, True, channel)
            if not config["enabled"] or not config["professional_notice"]:
                return "company_notice_disabled", None

            key = "test-orion:peixoto:20261030:" + str(row["id"])
            created = (await db.execute(text("""
                INSERT INTO appointment_notifications
                  (tenant_id,appointment_id,appointment_version,event,
                   recipient_kind,recipient_id,channel_id,central_channel_id,
                   settings_version,event_key,previous,due_at,expires_at)
                VALUES (:tenant,:appointment,:version,'professional_new',
                  'professional',:pro,NULL,:channel,:settings,:key,
                  CAST(:details AS jsonb),clock_timestamp(),
                  clock_timestamp()+interval '30 minutes')
                ON CONFLICT DO NOTHING
                RETURNING id
            """), {"tenant": TENANT, "appointment": row["id"],
                   "version": row["updated_at"], "pro": row["professional_id"],
                   "channel": channel, "settings": config["version"],
                   "key": key, "details": json.dumps({TEST_KEY: True})}
            )).scalar_one_or_none()
            return ("enqueued", created) if created else ("already_enqueued", None)
