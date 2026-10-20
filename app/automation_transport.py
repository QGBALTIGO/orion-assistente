"""Server-only managed QR transport. Never retries an uncertain operation."""
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def bridge_origin():
    value = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
    parsed = urlsplit(value)
    try:
        valid = parsed.scheme == "http" and re.fullmatch(r"[a-z0-9-]+\.railway\.internal", parsed.hostname or "") and parsed.port == 3000
    except ValueError:
        valid = False
    if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("bridge_unavailable")
    return value


async def _request(tenant, channel_id, operation_id, *, message=None):
    from app.qr_channels import outbound_channel_credentials
    operation = str(UUID(str(operation_id)))
    sending = message is not None
    credentials = await outbound_channel_credentials(tenant, channel_id, read_only=not sending)
    path = credentials["path"] if sending else f"/internal/channels/{channel_id}/operations/{operation}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=4), trust_env=False, follow_redirects=False) as client:
        response = await client.request("POST" if sending else "GET", bridge_origin() + path,
          headers={"X-Orion-Channel-Key": credentials["key"]}, json=message)
    if response.status_code != 200:
        raise ValueError("delivery_unverified")
    data = response.json()
    if not isinstance(data, dict) or data.get("operation_id") != operation:
        raise ValueError("delivery_unverified")
    return data


async def send_automation(tenant, channel_id, phone, body, operation_id):
    from app.automation_runtime import execution_enabled
    if not execution_enabled():
        raise ValueError("execution_paused")
    from app.automation_policy import text_units
    if not isinstance(body, str) or not body.strip() or not 1 <= text_units(body) <= 1200:
        raise ValueError("invalid_body")
    if not isinstance(phone, str) or re.fullmatch(r"\+55[1-9][0-9]{9,10}", phone) is None:
        raise ValueError("invalid_recipient")
    data = await _request(tenant, channel_id, operation_id, message={"to": phone.lstrip("+"), "text": body, "operation_id": str(UUID(str(operation_id)))})
    if data.get("accepted") is not True or data.get("state") != "accepted":
        raise ValueError("delivery_unverified")
    return data["operation_id"]  # Accepted by transport; not evidence of phone readability.


async def inspect_automation(tenant, channel_id, operation_id):
    return await _request(tenant, channel_id, operation_id)
