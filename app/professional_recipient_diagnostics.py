"""Owner-only, read-only recipient verification through Orion's private QR bridge.

A phone is retrieved under tenant RLS by the calling API, never from a browser
parameter. No WhatsApp message is sent, no JID or phone is returned or logged.
Provider lookup can be inconclusive; it is not proof of delivery.
"""
import os
import re

import httpx

from app.automation_transport import bridge_origin

PHONE = re.compile(r"^\+(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$")
# A changed identifier is not proof of a safe destination. The bridge
# recognizes Brazilian legacy PN normalization only when the SAME USync query
# returns one verified canonical phone JID.
IDENTITIES = frozenset({"pn_confirmed", "phone_normalized"})


async def check_professional_recipient(phone: str | None) -> str:
    if not phone:
        return "missing_phone"
    if not isinstance(phone, str) or PHONE.fullmatch(phone) is None:
        return "invalid_phone"
    token = os.getenv("QR_BRIDGE_TOKEN", "")
    if len(token) < 32:
        return "unavailable"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12, connect=4),
            follow_redirects=False, trust_env=False,
        ) as client:
            response = await client.get(
                bridge_origin() + "/internal/recipient",
                params={"phone": phone.lstrip("+")},
                headers={"X-Orion-Bridge-Key": token},
            )
        if response.status_code != 200:
            return "unavailable"
        data = response.json()
        if not isinstance(data, dict):
            return "unavailable"
        identity = data.get("identity")
        recognized = data.get("registered")
        if recognized is True and identity in IDENTITIES:
            return "registered"
        if recognized is False and identity == "unverified":
            return "not_recognized"
        return "unverified"
    except Exception:
        # Never expose a provider error, destination or bridge hostname.
        return "unavailable"
