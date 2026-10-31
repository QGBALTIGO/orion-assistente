"""Private one-time recipient readiness test. Never sends a WhatsApp message.

Reads one tenant-owned active professional phone under RLS, queries the
existing fixed-origin QR bridge's read-only /internal/recipient endpoint, and
returns only safe booleans/counts. No contact, JID, message body, or secrets
leave the trusted workers or appear in logs.
"""
import os
import re
from uuid import UUID

import httpx
from sqlalchemy import text

from app.automation_transport import bridge_origin
from app.database import get_session_factory, tenant_transaction

TENANT = UUID("26694b4b-be15-4850-bcb9-69b184897e79")
PROFESSIONAL = UUID("fb09446d-5173-47f4-aaf5-0fc0205d25ee")
SAFE_IDENTITIES = frozenset({"pn_confirmed", "phone_normalized", "different_identifier",
                             "unverified", "unknown", "lookup_failed"})
SAFE_LOOKUP_KINDS = frozenset({"exact", "provider_normalized", "no_result",
    "ambiguous_lookup", "different_identifier", "not_registered", "unavailable",
    "invalid_lookup"})
COUNTS = ("count", "pending", "serverAccepted", "delivered", "read",
          "failed", "retryRequests", "messagesWithRetry")
RECEIPTS = frozenset({"pending", "server_accepted", "delivered", "read",
                      "played", "failed", "unknown", "none"})


def legacy_brazil_candidate(phone: str) -> str | None:
    """Read-only comparison, never a routing or authorization decision.

    A Brazilian WhatsApp registration may still use the legacy 8-digit local
    PN. Even a positive result NEVER proves that it is the same person.
    """
    if not isinstance(phone, str) or not re.fullmatch(
        r"\+55[1-9][0-9]9[0-9]{8}", phone
    ):
        return None
    return phone[:5] + phone[6:]


def safe_report(payload):
    """Never log an arbitrary provider field, contact identifier or error."""
    if not isinstance(payload, dict):
        return {"registered": None, "identity": "unknown", "delivery": {}}
    registered = payload.get("registered")
    identity = payload.get("identity")
    delivery = payload.get("delivery")
    summary = {
        "registered": registered if type(registered) is bool else None,
        "identity": identity if identity in SAFE_IDENTITIES else "unknown",
        "has_lid": payload.get("hasLid") if type(payload.get("hasLid")) is bool else None,
        "delivery": {},
    }
    # Optional diagnostic; never include the returned PN, JID, LID or contact.
    if payload.get("lookupKind") in SAFE_LOOKUP_KINDS:
        summary["lookup_kind"] = payload["lookupKind"]
    if isinstance(delivery, dict):
        for name in COUNTS:
            value = delivery.get(name)
            if type(value) is int and 0 <= value <= 10000:
                summary["delivery"][name] = value
        last = delivery.get("lastReceipt")
        summary["delivery"]["last_receipt"] = last if last in RECEIPTS else "unknown"
    return summary


async def inspect():
    key = os.getenv("QR_BRIDGE_TOKEN", "")
    if len(key) < 32:
        return {"state": "bridge_credential_missing"}
    async with get_session_factory()() as db:
        async with tenant_transaction(db, TENANT):
            phone = (await db.execute(text("""
                SELECT phone_e164 FROM professionals
                WHERE tenant_id=:tenant AND id=:professional AND active
            """), {"tenant": TENANT, "professional": PROFESSIONAL})).scalar_one_or_none()
    if not isinstance(phone, str) or not phone.startswith("+"):
        return {"state": "professional_phone_missing"}
    control_phone = os.getenv("ORION_PEIXOTO_CONTROL_PHONE", "")
    # Test owner-authorized WhatsApp number is provided only in private
    # deployment environment, never source, parameters or provider logs.
    if control_phone and re.fullmatch(r"\+(?:55[1-9][0-9]{9,10}|5959[0-9]{8})",control_phone) is None:
        return {"state": "invalid_control_scope"}
    try:
        origin = bridge_origin()
        headers = {"X-Orion-Bridge-Key": key}
        # Only the Railway private QR origin is accepted; never a caller URL.
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4),
                follow_redirects=False, trust_env=False) as client:
            response = await client.get(
                origin + "/internal/recipient",
                params={"phone": phone.lstrip("+")},
                headers=headers,
            )
            if response.status_code != 200:
                return {"state": "private_lookup_unavailable"}
            result = {"state": "inspected", **safe_report(response.json())}
            if os.getenv("ORION_PEIXOTO_COMPARE_LEGACY_PN") == "1":
                alternate = legacy_brazil_candidate(phone)
                if alternate is None:
                    result["legacy_pn_comparison"] = {"state": "not_applicable"}
                else:
                    try:
                        check = await client.get(
                            origin + "/internal/recipient",
                            params={"phone": alternate.lstrip("+")}, headers=headers)
                        result["legacy_pn_comparison"] = (
                            {"state": "inspected",
                             "registered": safe_report(check.json()).get("registered"),
                             "identity": safe_report(check.json()).get("identity")}
                            if check.status_code == 200 else {"state": "unavailable"}
                        )
                    except Exception:
                        result["legacy_pn_comparison"] = {"state": "failed_closed"}
            if control_phone and control_phone != phone:
                try:
                    control = await client.get(
                        origin + "/internal/recipient",
                        params={"phone": control_phone.lstrip("+")},
                        headers=headers)
                    if control.status_code == 200:
                        safe = safe_report(control.json())
                        result["control_lookup"] = {
                            "state": "inspected",
                            "registered": safe.get("registered"),
                            "identity": safe.get("identity"),
                            "delivery": safe.get("delivery", {}),
                        }
                    else:
                        result["control_lookup"] = {"state": "unavailable"}
                except Exception:
                    result["control_lookup"] = {"state": "failed_closed"}
        return result
    except Exception:
        return {"state": "private_lookup_failed_closed"}
