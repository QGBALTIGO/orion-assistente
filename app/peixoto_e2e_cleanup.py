"""Clean up ONE synthetically labeled public booking after central delivery QA.

Disabled unless operator explicitly opts in. No public endpoint, no arbitrary
tenant or professional, no deletes, no account modification. Uses normal
customer-actor cancellation and ordinary scheduling/audit invariants.
The expected protocol and authorized test phone are passed as private Railway
variables, never printed or written to GitHub.
"""
from datetime import datetime, timedelta, timezone
import os
import re
from uuid import UUID

from sqlalchemy import text

from app.booking_service import cancel_appointment
from app.database import get_session_factory, tenant_transaction
from app.appointment_lifecycle import customer_actor

TENANT = UUID("26694b4b-be15-4850-bcb9-69b184897e79")
PROFESSIONAL = UUID("fb09446d-5173-47f4-aaf5-0fc0205d25ee")
PROTOCOL = re.compile(r"^AG-[A-HJ-NP-Z2-9]{6}$")
CONTACT = re.compile(r"^\+(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$")
MARKER = "TESTE ORION"


def candidate_error(row, phone, now):
    """A changed booking, different customer, or expired test cannot be touched."""
    if row is None:
        return "test_booking_not_found"
    if row["status"] == "cancelled":
        return "already_cancelled"
    if row["status"] != "confirmed":
        return "test_booking_not_confirmed"
    if row["professional_id"] != PROFESSIONAL:
        return "professional_changed"
    if row["customer_phone"] != phone:
        return "contact_changed"
    if not isinstance(row["customer_name"], str) or not row["customer_name"].startswith(MARKER):
        return "not_a_labeled_test"
    if row["start_at"] <= now + timedelta(hours=1):
        return "booking_too_close_to_cancel"
    if row["created_at"] < now - timedelta(hours=2):
        return "test_window_expired"
    if row["public_slug"] != "barbearia-peixoto":
        return "site_changed"
    return None


async def cancel_one():
    if os.getenv("ORION_E2E_PEIXOTO_CLEANUP") != "1":
        return "disabled"
    protocol = os.getenv("ORION_E2E_PEIXOTO_PROTOCOL", "")
    phone = os.getenv("ORION_E2E_PEIXOTO_PHONE", "")
    if not PROTOCOL.fullmatch(protocol) or not CONTACT.fullmatch(phone):
        return "invalid_operator_scope"

    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, TENANT):
            records = (await db.execute(text("""
                SELECT a.id,a.status,a.updated_at,a.customer_id,a.professional_id,
                       a.start_at,a.created_at,c.name AS customer_name,
                       c.whatsapp_e164 AS customer_phone,p.public_slug
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                JOIN public_booking_receipts r ON r.tenant_id=a.tenant_id
                     AND r.appointment_id=a.id
                JOIN management_site_preferences p ON p.tenant_id=a.tenant_id
                WHERE a.tenant_id=:t AND a.protocol=:protocol
                ORDER BY a.id LIMIT 2
            """), {"t":TENANT,"protocol":protocol})).mappings().all()
            if len(records) != 1:
                return "test_booking_not_unique"
            row = records[0]
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            issue = candidate_error(row, phone, now)
            if issue:
                return issue
            statuses = (await db.execute(text("""
                SELECT status FROM appointment_notifications
                WHERE tenant_id=:t AND appointment_id=:a
                  AND event='professional_new'
                  AND recipient_kind='professional' AND recipient_id=:p
                ORDER BY created_at DESC LIMIT 1
            """), {"t":TENANT,"a":row["id"],"p":PROFESSIONAL})
            ).scalar_one_or_none()
            if statuses not in {"accepted", "delivered", "read"} and os.getenv(
                    "ORION_E2E_PEIXOTO_FORCE_CLEANUP") != "1":
                return "professional_delivery_not_confirmed"

    # The domain service will re-lock the professional, check the row version,
    # verify the customer's actual WhatsApp contact and apply the 1h deadline.
    async with factory() as db:
        response = await cancel_appointment(
            db, TENANT, row["id"], customer_phone=phone,
            expected_updated_at=row["updated_at"], future_only=True,
            idempotency_key="orion-peixoto-e2e:" + protocol,
            context=customer_actor(row["customer_id"], "public_site"),
            reason="Cancelamento do agendamento de homologacao TESTE ORION",
            source="public_site",
        )
    return "cancelled" if response["status"] == "cancelled" else "unexpected_status"
