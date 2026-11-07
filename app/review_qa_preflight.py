"""Read-only check before a user-authorized review WhatsApp smoke test.

No endpoints or customer details are logged. All queries are tenant-scoped.
Never create an appointment or silently modify operational/marketing consent.
"""
from collections import Counter
from uuid import UUID

from sqlalchemy import text
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import has_feature
from app.appointment_notifications import central_enabled, central_channel, notification_settings

TENANT = UUID("82552b25-d0c8-5338-8c42-49f11de59e45")
PHONE = "+5521997557364"


async def inspect():
    result = {
        "central_enabled": central_enabled(), "admin_sender_available": False,
        "plan_allows_notifications": False, "review_enabled": False,
        "review_delay_minutes": None, "return_invitation": False,
        "matching_bookings": 0, "states": {},
        "eligible_unreviewed_team_completed": 0,
        "completed_with_prior_review": 0,
        "eligible_with_prior_review_notice": 0,
        "consent_operational": False, "consent_marketing": False,
    }
    admin, channel = central_channel()
    result["admin_sender_available"] = admin == TENANT and channel is not None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, TENANT):
            result["plan_allows_notifications"] = bool(
                await has_feature(db, TENANT, "notifications.basic"))
            settings = (await db.execute(text("""
                SELECT * FROM appointment_communication_settings WHERE tenant_id=:t
            """), {"t": TENANT})).mappings().first()
            preference = notification_settings(settings, result["central_enabled"], channel)
            if preference is not None:
                result["review_enabled"] = bool(preference.get("enabled") and preference.get("review"))
                result["review_delay_minutes"] = preference.get("review_delay_min")
                result["return_invitation"] = bool(preference.get("return_invitation"))
            rows = (await db.execute(text("""
                SELECT a.status,
                  EXISTS(SELECT 1 FROM appointment_history h
                         WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
                           AND h.event='completed'
                           AND h.details->>'source'='panel'
                           AND h.details->'actor'->>'role'
                               IN ('owner','editor','admin','support')) AS completed_by_team,
                  EXISTS(SELECT 1 FROM appointment_reviews r
                         WHERE r.tenant_id=a.tenant_id AND r.appointment_id=a.id) AS reviewed,
                  EXISTS(SELECT 1 FROM appointment_notifications n
                         WHERE n.tenant_id=a.tenant_id AND n.appointment_id=a.id
                           AND n.event='review') AS review_noticed,
                  a.customer_access_blocked
                FROM appointments a JOIN customers c
                  ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:t AND c.whatsapp_e164=:phone
                  AND c.active AND c.deleted_at IS NULL
                ORDER BY a.created_at DESC LIMIT 100
            """), {"t": TENANT, "phone": PHONE})).mappings().all()
            consent = (await db.execute(text("""
                SELECT operational_opt_in, marketing_opt_in FROM contact_consents
                WHERE tenant_id=:t AND contact_e164=:phone
            """), {"t": TENANT, "phone": PHONE})).mappings().first()
            if consent:
                result["consent_operational"] = consent["operational_opt_in"] is True
                result["consent_marketing"] = consent["marketing_opt_in"] is True
            result["matching_bookings"] = len(rows)
            result["states"] = dict(Counter(str(row["status"]) for row in rows))
            eligible = [
                row for row in rows
                if row["status"] == "completed"
                and row["completed_by_team"] is True
                and row["reviewed"] is False
                and row["customer_access_blocked"] is False
            ]
            result["eligible_unreviewed_team_completed"] = len(eligible)
            result["eligible_with_prior_review_notice"] = sum(bool(x["review_noticed"]) for x in eligible)
            result["completed_with_prior_review"] = sum(
                bool(row["reviewed"]) for row in rows if row["status"] == "completed")
    return result
