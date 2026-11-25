"""Resolve the public recovery link from the platform's selected channel."""
import asyncio
import os
import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.qr_legacy import configured_tenant


async def administrative_channel():
    from app.qr_channels import _canonical, _bridge_control, _control_result

    tenant = configured_tenant()
    async with support_session_factory()() as db:
        platform = (await db.execute(text("""
            SELECT t.whatsapp_channel_id FROM tenants t
            WHERE t.id=:tenant AND (public.orion_plan_snapshot(t.id)->>'platform_workspace')='true'
        """), {"tenant": tenant})).scalar_one_or_none()
        if platform is None:
            return None
        channel, state = await _canonical(db, tenant)
        if state != "ready" or not channel or channel["id"] != platform or not channel["enabled"]:
            return None
    live = _control_result(channel, await _bridge_control(channel, "status", "GET"), "status")
    phone = str(live.get("phone") or "").removeprefix("+")
    if live.get("phone_verified") is True and re.fullmatch(r"55[1-9]\d{9,10}", phone):
        return channel, phone
    return None


async def support_url():
    message = "Olá! Preciso de ajuda com o plano da minha empresa no Orion."
    try:
        async with asyncio.timeout(2):
            selected = await administrative_channel()
        if selected:
            return "https://wa.me/" + selected[1] + "?text=" + quote(message, safe="")
    except (HTTPException, httpx.HTTPError, OSError, TimeoutError, ValueError):
        pass
    contact = os.getenv("ORION_SUPPORT_FALLBACK_EMAIL", "").strip()
    if re.fullmatch(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+", contact):
        return "mailto:" + quote(contact, safe="@") + "?body=" + quote(message, safe="")
    return None
