"""Exact-body authority and tenant connection routing; no external messages."""
import base64
import hashlib
import json
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import affiliate_whatsapp as api


TENANT = UUID("10000000-0000-4000-8000-000000000001")
SCOPE = {"device_id": "20000000-0000-4000-8000-000000000001",
         "generation": "30000000-0000-4000-8000-000000000001", "settings_revision": 4}
PATH = "/api/v1/afiliados/bridge/whatsapp/"


@pytest.fixture
def context(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    monkeypatch.setattr(api.channels, "enabled", lambda: None)
    ctx = {"signer": Fernet(key), "snapshots": [], "calls": [], "scope": dict(SCOPE)}

    async def snapshot(tenant):
        ctx["snapshots"].append(tenant)
        assert tenant == TENANT
        return {"id": UUID("40000000-0000-4000-8000-000000000001")}, ctx["scope"]

    async def bridge(transport, action, payload):
        ctx["calls"].append((action, payload))
        if action == "groups":
            return {**SCOPE, "groups": [{"address": "120363400001234567@g.us", "title": "Ofertas"}]}
        if action == "verify":
            return {**SCOPE, "address": payload["address"], "title": "Ofertas", "role": "ADMIN"}
        return {**SCOPE, "state": "sent", "accepted": True, "provider_id": "synthetic-receipt"}

    monkeypatch.setattr(api, "snapshot", snapshot)
    monkeypatch.setattr(api, "bridge_control", bridge)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    ctx["app"] = app
    return ctx


async def call(ctx, action, payload, *, edit=None, signature=True, raw_override=None):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    claim = {"purpose": api.PURPOSE, "tenant": TENANT.hex, "method": "POST", "path": PATH+action,
             "action": action, "body_sha256": hashlib.sha256(raw).hexdigest()}
    claim.update(edit or {})
    headers = {"X-Orion-Affiliate-Operation": ctx["signer"].encrypt(json.dumps(claim).encode()).decode()} if signature else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx["app"]), base_url="https://orion.test") as client:
        return await client.post(PATH+action, content=raw_override if raw_override is not None else raw, headers=headers)


def payload():
    return {"job_id": "a"*32, "destination": {"mode": "whatsapp_group", "address": "120363400001234567@g.us",
             "connection_version": api.version(SCOPE)}, "text": "Café\nhttps://s.shopee.com.br/test",
            "link": "https://s.shopee.com.br/test", "media": None}


@pytest.mark.parametrize("edit", [{"purpose": "workspace-read"}, {"action": "groups"},
    {"path": PATH+"groups"}, {"tenant": "invalid"}, {"method": "GET"}, {"body_sha256": "0"*64}])
async def test_invalid_authority_never_reaches_the_tenant_or_transport(context, edit):
    response = await call(context, "send", payload(), edit=edit)
    assert response.status_code == 401
    assert context["snapshots"] == context["calls"] == []


async def test_missing_signature_and_modified_body_are_rejected(context):
    assert (await call(context, "send", payload(), signature=False)).status_code == 401
    assert (await call(context, "send", payload(), raw_override=b"{}")).status_code == 401
    assert context["snapshots"] == context["calls"] == []


async def test_send_uses_only_signed_tenant_current_device_and_exact_job(context):
    response = await call(context, "send", payload())
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert context["snapshots"] == [TENANT]
    action, body = context["calls"][0]
    assert action == "send"
    assert body["operation_id"] == str(UUID(hex="a"*32))
    assert all(body[key] == value for key, value in SCOPE.items())
    assert body["text"] == payload()["text"]
    assert "connection_version" not in body and "tenant" not in body
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("change", ["missing_version", "stale_version", "arbitrary_jid", "missing_link", "unknown_field"])
async def test_send_rejects_changed_destinations_or_unreviewable_body(context, change):
    body = payload()
    if change == "missing_version": body["destination"].pop("connection_version")
    elif change == "stale_version": body["destination"]["connection_version"] = "orion:previous-device"
    elif change == "arbitrary_jid": body["destination"]["address"] = "5511999999999@s.whatsapp.net"
    elif change == "missing_link": body["text"] = "Oferta sem link"
    else: body["actor"] = "admin"
    assert (await call(context, "send", body)).status_code in {409, 422}
    assert context["calls"] == []


async def test_verification_allows_only_current_group_or_channel_link(context):
    body = {"destination": {"mode": "whatsapp_group", "address": "120363400001234567@g.us"}}
    response = await call(context, "verify", body)
    assert response.status_code == 200
    assert response.json()["version"] == api.version(SCOPE)
    assert response.json()["transport"] == "orion"
    assert len(context["calls"]) == 1


async def test_stale_transport_reply_does_not_claim_sent(context, monkeypatch):
    async def bridge(*args):
        return {**SCOPE, "generation": "different", "accepted": True, "state": "sent"}
    monkeypatch.setattr(api, "bridge_control", bridge)
    assert (await call(context, "send", payload())).status_code == 502


@pytest.mark.parametrize("image", ["not-base64", base64.b64encode(b"<svg>unsafe</svg>"*5).decode(), "x"*1400001])
def test_only_bounded_raster_cards_are_accepted(image):
    body = {**payload(), "media": image}
    with pytest.raises(HTTPException) as result:
        api.send_fields(body)
    assert result.value.status_code == 422
