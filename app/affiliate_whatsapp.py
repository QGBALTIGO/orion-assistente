"""Tenant-bound WhatsApp adapter for explicitly confirmed affiliate offers.

The affiliate service signs the exact body and action after validating its
stored destination and manual job. Channel credentials stay inside Orion.
The QR transport rechecks the live SQL lease before touching its socket.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
import httpx

from app.catalog_api import support_session_factory
from app import qr_channels as channels
from app import subscription_devices as devices
from app.workspace_policy import AFFILIATE_NICHE, AFFILIATE_WORKSPACE


router = APIRouter()
PURPOSE = "orion.affiliate.manual-whatsapp.v1"
MAX_BODY = 1500000
HEX_ID = re.compile(r"^[a-f0-9]{32}$")
GROUP = re.compile(r"^[1-9]\d{4,20}(?:-\d{1,20})?@g\.us$")
CHANNEL = re.compile(r"^https://(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9]{20,32})/?(?:\?[^\s#]*)?$")


def fail(message="Confira a conexão do WhatsApp e verifique o destino novamente.", status=409):
    return HTTPException(status, message)


async def signed_input(request, action):
    if action not in {"status", "groups", "verify", "send"}:
        raise HTTPException(404)
    signed = request.headers.get("X-Orion-Affiliate-Operation", "")
    if not signed or len(signed) > 2048:
        raise HTTPException(401)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_BODY:
            raise HTTPException(413)
    try:
        signer = Fernet(os.environ["ORION_AFFILIATE_WORKSPACE_KEY"].encode("ascii"))
        claim = json.loads(signer.decrypt(signed.encode("ascii"), ttl=45))
        if (claim.get("purpose") != PURPOSE or claim.get("action") != action
                or claim.get("path") != request.url.path or claim.get("method") != "POST"
                or not isinstance(claim.get("tenant"), str) or not HEX_ID.fullmatch(claim["tenant"])
                or not hmac.compare_digest(str(claim.get("body_sha256", "")), hashlib.sha256(raw).hexdigest())):
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        return UUID(hex=claim["tenant"]), payload
    except (KeyError, ValueError, TypeError, UnicodeError, InvalidToken):
        raise HTTPException(401) from None


async def snapshot(tenant):
    """Resolve the canonical connection of this active affiliate owner only."""
    async with support_session_factory()() as db:
        async with db.begin():
            state = await devices._workspace(db, tenant)
            if state.get("workspace_profile") != AFFILIATE_WORKSPACE:
                raise HTTPException(403)
            rows = (await db.execute(text("""
                SELECT d.id FROM subscription_devices d
                JOIN tenants t ON t.id=d.tenant_id
                JOIN business_onboarding o ON o.tenant_id=t.id
                JOIN tenant_memberships m ON m.tenant_id=t.id AND m.account_id=d.owner_account_id
                JOIN panel_accounts a ON a.id=m.account_id
                WHERE d.tenant_id=:tenant AND d.removed_at IS NULL
                  AND d.status NOT IN ('disabled','removing','removed')
                  AND t.status='active' AND o.completed_at IS NOT NULL
                  AND o.configuration->>'niche'=:niche
                  AND m.active AND m.role='owner' AND a.verified_at IS NOT NULL
            """), {"tenant": tenant, "niche": AFFILIATE_NICHE})).scalars().all()
            if len(rows) != 1:
                raise fail("Conecte seu WhatsApp em Conexões e destinos.")
            device, transport = await devices._device(db, tenant, rows[0])
            if (not transport["enabled"] or transport["assistant_enabled"] or transport["allowed_senders"]
                    or device["restart_required"] or device["revision"] != device["applied_revision"]):
                raise fail()
            scope = {"device_id": str(device["id"]), "generation": str(device["generation"]),
                     "settings_revision": device["revision"]}
            return transport, scope


def version(scope):
    return f"orion:{scope['device_id']}:{scope['generation']}:{scope['settings_revision']}"


def destination_input(payload):
    destination = payload.get("destination")
    if not isinstance(destination, dict) or set(destination) - {"mode", "address", "connection_version"}:
        raise fail(status=422)
    mode, address = destination.get("mode"), destination.get("address")
    if not isinstance(address, str):
        raise fail(status=422)
    if mode == "whatsapp_group" and GROUP.fullmatch(address):
        return {"mode": mode, "address": address}
    if mode == "whatsapp_channel" and (match := CHANNEL.fullmatch(address)):
        return {"mode": mode, "invite": match[1]}
    raise fail("Escolha um grupo da conta conectada ou informe o link completo do canal.", 422)


def send_fields(payload):
    fields = {"job_id", "text", "link", "media", "thumbnail", "destination"}
    if set(payload) - fields or not HEX_ID.fullmatch(str(payload.get("job_id", ""))):
        raise fail(status=422)
    caption, link = payload.get("text"), payload.get("link")
    if (not isinstance(caption, str) or not caption.strip() or len(caption.encode("utf-16-le")) // 2 > 4096
            or not isinstance(link, str) or len(link) > 2048 or not link.startswith("https://")
            or any(c.isspace() for c in link) or link not in caption):
        raise fail("Confira o texto e o link da oferta.", 422)
    media = payload.get("media")
    if media is not None:
        try:
            if not isinstance(media, str) or len(media) > 1400000:
                raise ValueError
            data = base64.b64decode(media, validate=True)
            if not 32 <= len(data) <= 1000000 or not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
                raise ValueError
            if len(caption.encode("utf-16-le")) // 2 > 1024:
                raise ValueError
        except (ValueError, TypeError):
            raise fail("O card ou a legenda ultrapassou o limite permitido.", 422) from None
    # The operation UUID is derived from the persisted per-destination job.
    # Retrying an HTTP request must never create a new transport operation.
    return {"operation_id": str(UUID(hex=payload["job_id"])), "text": caption,
            "link": link, "media": media}


async def bridge_control(transport, action, payload):
    """Fixed private transport with its own bounded media timeout."""
    if action not in {"groups", "verify", "send"}:
        raise HTTPException(404)
    try:
        root = os.environ["QR_BRIDGE_URL"].rstrip("/")
        url = urlsplit(root)
        if (url.scheme != "http" or not re.fullmatch(r"[a-z0-9-]+\.railway\.internal", url.hostname or "")
                or url.port != 3000 or url.username or url.password or url.path or url.query or url.fragment):
            raise ValueError
        key = channels._cipher().decrypt(transport["credential_encrypted"].encode()).decode()
        if not hmac.compare_digest(channels.credential_hash(key), transport["credential_hash"]):
            raise ValueError
    except (KeyError, ValueError, UnicodeError, InvalidToken):
        raise fail("A conexão do WhatsApp está indisponível neste momento.", 503) from None
    try:
        async with httpx.AsyncClient(timeout=44 if action == "send" else 30,
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{root}/internal/channels/{transport['id']}/affiliate/{action}",
                headers={"X-Orion-Channel-Key": key}, json=payload)
        if len(response.content) > 256000:
            raise ValueError
        value = response.json()
        if response.status_code != 200 or not isinstance(value, dict):
            # A bridge failure can happen after its transport boundary (for
            # example when persisting a receipt). Never label it not sent.
            raise fail("O WhatsApp não confirmou acesso ao destino. Confira a conexão e a administração do grupo ou canal.",
                       502 if action == "send" else 409)
        return value
    except (httpx.HTTPError, ValueError):
        raise fail("O WhatsApp demorou para responder. Confira o histórico antes de tentar novamente.", 502) from None


@router.post("/afiliados/bridge/whatsapp/{action}", include_in_schema=False)
async def affiliate_whatsapp(action: str, request: Request):
    channels.enabled()
    tenant, payload = await signed_input(request, action)
    transport, scope = await snapshot(tenant)
    if action == "status":
        if payload:
            raise fail(status=422)
        status = await channels._bridge_control(transport, "device/status", "GET")
        connected = status.get("status") == "connected"
        return JSONResponse({"status": "connected" if connected else "disconnected",
                             "version": version(scope), "available": connected}, headers={"Cache-Control": "no-store"})
    if action == "groups":
        if payload:
            raise fail(status=422)
        result = await bridge_control(transport, "groups", scope)
        return JSONResponse({"groups": result.get("groups", []), "version": version(scope)},
                            headers={"Cache-Control": "no-store"})
    target = destination_input(payload)
    if action == "verify" and set(payload) != {"destination"}:
        raise fail(status=422)
    expected = payload["destination"].get("connection_version")
    if expected and expected != version(scope):
        raise fail("A conexão mudou. Verifique o destino novamente antes de enviar.")
    if action == "send" and not expected:
        raise fail("Verifique o destino antes de enviar.")
    body = {**scope, **target}
    if action == "send":
        body.update(send_fields(payload))
    result = await bridge_control(transport, action, body)
    if any(result.get(key) != value for key, value in scope.items()):
        raise fail(status=502 if action == "send" else 409)
    if action == "verify":
        return JSONResponse({"version": version(scope), "address": result["address"],
                             "title": result["title"], "role": result["role"], "transport": "orion"},
                            headers={"Cache-Control": "no-store"})
    return JSONResponse({"job_id": payload["job_id"], "state": result.get("state", "unknown"),
                         "accepted": result.get("accepted") is True,
                         "provider_id": result.get("provider_id"), "transport": "orion"},
                        headers={"Cache-Control": "no-store"})
