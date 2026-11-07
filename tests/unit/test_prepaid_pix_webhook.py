"""Both Cakto webhook secrets are authenticated; no Pix grants without proof."""
import hashlib
import hmac
import json
import time

import httpx
import pytest
from app import cakto_api
from app.main import app


def signed(body,secret):
    raw=json.dumps(body,separators=(",",":")).encode()
    stamp=str(int(time.time()))
    mac=hmac.new(secret.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    return raw,{"X-Cakto-Signature":"v1="+mac,"X-Cakto-Timestamp":stamp}


@pytest.mark.asyncio
async def test_pix_and_existing_subscription_secrets_do_not_cross_authenticate(monkeypatch):
    original="synthetic-original-provider-signature-key-12345"
    prepaid="synthetic-new-pix-provider-signature-key-67890"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",original)
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET",prepaid)
    saved=[]
    async def capture(events):saved.extend(events)
    monkeypatch.setattr(cakto_api,"persist_events",capture)
    for secret in (original,prepaid):
        body={"secret":secret,"event":"purchase_approved",
              "data":{"id":"test-pix-order","status":"paid","offer_type":"main",
                      "offer":{"id":"test-pix-offer"},"amount":"29.90","currency":"BRL"}}
        raw,headers=signed(body,secret)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="http://test") as client:
            response=await client.post("/webhooks/cakto",content=raw,headers=headers)
        assert response.status_code==200
    assert len(saved)==2
    forged={"secret":original,"event":"purchase_approved",
             "data":{"id":"synthetic-other","status":"paid",
                     "offer_type":"main","offer":{"id":"test-offer"}}}
    raw,headers=signed(forged,prepaid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test") as client:
        response=await client.post("/webhooks/cakto",content=raw,headers=headers)
    assert response.status_code==401


@pytest.mark.asyncio
async def test_pix_provider_does_not_accept_webhook_without_a_long_valid_secret(monkeypatch):
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET","short")
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET","short")
    raw,headers=signed({"secret":"short","event":"purchase_approved","data":{}},
                       "short")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test") as client:
        response=await client.post("/webhooks/cakto",content=raw,headers=headers)
    assert response.status_code==503
