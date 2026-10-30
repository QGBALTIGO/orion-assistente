"""Read-only readiness proof for one explicitly authorized Orion tenant.

Operator opt-in via one-time worker flag. Outputs only booleans and bounded
counts: never a customer, professional phone, token, message, or QR credential.
"""
from uuid import UUID

from sqlalchemy import text

from app.appointment_notifications import central_enabled, central_channel, notification_settings
from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import has_feature

TENANT = UUID("26694b4b-be15-4850-bcb9-69b184897e79")
PROFESSIONAL = UUID("fb09446d-5173-47f4-aaf5-0fc0205d25ee")
E164_PATTERN = r"^\+(55[1-9][0-9]{9,10}|5959[0-9]{8})$"


async def inspect():
    """Does not write rows, enqueue jobs, or send network messages to WhatsApp."""
    result = {
        "central_feature_enabled": bool(central_enabled()),
        "active_essential_entitlement": False,
        "professional_active": False,
        "professional_phone_valid": False,
        "notifications_configured": False,
        "professional_notice_enabled": False,
        "sender_identity_available": False,
        "sender_channel_ready": False,
        "sender_connected_and_verified": False,
        "last_24h_notifications": {},
    }
    admin, channel = central_channel()
    if admin is not None and admin != TENANT and channel is not None:
        result["sender_identity_available"] = True

    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, TENANT):
            result["active_essential_entitlement"] = bool(
                await has_feature(db, TENANT, "notifications.basic"))
            professional = (await db.execute(text("""
                SELECT active, phone_e164 ~ :pattern AS phone_valid
                FROM professionals
                WHERE tenant_id=:tenant AND id=:professional
            """), {"tenant": TENANT, "professional": PROFESSIONAL,
                   "pattern": E164_PATTERN})).mappings().first()
            if professional is not None:
                result["professional_active"] = professional["active"] is True
                result["professional_phone_valid"] = professional["phone_valid"] is True
            preference = (await db.execute(text("""
                SELECT * FROM appointment_communication_settings
                WHERE tenant_id=:tenant
            """), {"tenant": TENANT})).mappings().first()
            chosen = notification_settings(preference, True, channel)
            result["notifications_configured"] = chosen.get("enabled") is True
            result["professional_notice_enabled"] = chosen.get("professional_notice") is True
            counts = (await db.execute(text("""
                SELECT event,status,count(*) AS total
                FROM appointment_notifications
                WHERE tenant_id=:tenant
                  AND created_at >= clock_timestamp()-interval '24 hours'
                GROUP BY event,status
                ORDER BY event,status LIMIT 30
            """), {"tenant": TENANT})).mappings().all()
            result["last_24h_notifications"] = {
                str(item["event"]) + ":" + str(item["status"]): int(item["total"])
                for item in counts
            }

    if result["sender_identity_available"]:
        try:
            from app.qr_channels import _canonical, _bridge_control, _control_result
            async with support_session_factory()() as registry:
                row, state = await _canonical(registry, admin)
            result["sender_channel_ready"] = bool(
                row is not None and state == "ready"
                and row["id"] == channel and row["enabled"])
            if result["sender_channel_ready"]:
                observed = _control_result(
                    row, await _bridge_control(row, "status", "GET"), "status")
                result["sender_connected_and_verified"] = bool(
                    observed.get("status") == "connected"
                    and observed.get("phone_verified"))
        except Exception:
            # No provider exception or credential details in application logs.
            pass
    return result
