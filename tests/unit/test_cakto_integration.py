"""No real Cakto calls, accounts, purchases or customer identifiers in tests."""
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app import cakto_api
from app.main import app


def signed(payload, secret):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = "v1=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    return raw, {"X-Cakto-Timestamp": timestamp, "X-Cakto-Signature": signature}


def test_hmac_raw_timestamp_and_expiry(monkeypatch):
    secret = "synthetic-cakto-webhook-secret-for-unit-tests"
    raw, headers = signed({"secret": secret, "event": "purchase_approved", "data": {}}, secret)
    assert cakto_api.verify_cakto_signature(
        raw, headers["X-Cakto-Timestamp"], headers["X-Cakto-Signature"], secret
    )
    assert not cakto_api.verify_cakto_signature(
        raw+b" ", headers["X-Cakto-Timestamp"], headers["X-Cakto-Signature"], secret
    )
    assert not cakto_api.verify_cakto_signature(
        raw, str(int(time.time())-301), headers["X-Cakto-Signature"], secret
    )
    assert not cakto_api.verify_cakto_signature(raw, None, None, secret)
    assert not cakto_api.verify_cakto_signature(raw, headers["X-Cakto-Timestamp"],
                                               "v1=é" + "a"*63, secret)


def test_v2_order_bump_and_customer_data_are_not_persisted():
    order = {"id": "fake-main-uuid", "status": "paid", "offer_type": "main",
             "offer": {"id": "offer-test"}, "customer": {"email": "never-store@example.test",
             "docNumber": "00000000000"}, "card": {"lastDigits": "1234"}}
    data = {"event": "purchase_approved", "data": [
        order, {"id": "bump", "status": "paid", "offer_type": "orderbump"},
    ]}
    a = cakto_api.normalized_events(data)
    b = cakto_api.normalized_events(data)
    assert len(a) == 1 and a == b
    assert a[0]["order_id"] == "fake-main-uuid"
    assert "never-store" not in repr(a) and "docNumber" not in repr(a)
    assert "card" not in repr(a) and "bump" not in repr(a)


@pytest.mark.asyncio
async def test_webhook_requires_signature_and_does_not_activate(monkeypatch):
    secret = "synthetic-cakto-webhook-secret-for-unit-tests"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    seen = []
    async def fake_persist(events):
        seen.extend(events)
    monkeypatch.setattr(cakto_api, "persist_events", fake_persist)
    raw, headers = signed({
        "secret": secret, "event": "purchase_approved",
        "data": {"id": "fake-uuid", "status": "paid", "offer_type": "main",
                 "offer": {"id": "fake-offer"}}
    }, secret)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/webhooks/cakto", content=raw)).status_code == 401
        assert (await client.post("/webhooks/cakto", content=raw,
                                  headers=headers)).status_code == 200
        assert (await client.post("/webhooks/cakto", content=raw,
                                  headers=headers)).status_code == 200
        wrong = json.dumps({"secret": "wrong", "event": "purchase_approved",
                            "data": {"id": "fake", "status": "paid",
                                     "offer_type": "main"}}).encode()
        ts = str(int(time.time()))
        sig = "v1=" + hmac.new(secret.encode(), ts.encode()+b"."+wrong,
                                hashlib.sha256).hexdigest()
        assert (await client.post("/webhooks/cakto", content=wrong,
            headers={"X-Cakto-Timestamp": ts, "X-Cakto-Signature": sig})).status_code == 401
    assert len(seen) == 2
    assert seen[0]["digest"] == seen[1]["digest"]


def test_verification_requires_order_and_matching_state():
    row = {"external_order_id": "test-order", "external_offer_id": "offer",
           "event_type": "purchase_approved"}
    assert cakto_api.verify_order_event(row, {
        "id": "test-order", "status": "paid", "offer_type": "main",
        "offer": {"id": "offer"}}) == "verified"
    assert cakto_api.verify_order_event(row, {
        "id": "different", "status": "paid"}) == "mismatch"
    assert cakto_api.verify_order_event(row, {
        "id": "test-order", "status": "paid", "offer": {"id": "wrong"}}) == "mismatch"
    assert cakto_api.verify_order_event(row, {
        "id": "test-order", "status": "waiting_payment"}) == "mismatch"
    row["event_type"] = "subscription_canceled"
    assert cakto_api.verify_order_event(row, {
        "id": "test-order", "status": "paid"}) == "manual_review"


def test_catalog_prices_links_are_only_read_only():
    assert [(x["code"], x["monthly_cents"]) for x in cakto_api.PLANS] == [
        ("essencial", 2990), ("profissional", 8990), ("elite", 13990)]
    assert cakto_api.match_plans([
        {"id": "verified-id", "name": "Orion Pro", "price": "89.90",
         "status": "active", "type": "subscription", "recurrence_period": 30},
        {"id": "fake-offer", "name": "Orion Elite", "price": "120.00",
         "status": "active"},
    ]) == [
        {"code": "essencial", "found": False, "offer_id": None, "ambiguous": False},
        {"code": "profissional", "found": True, "offer_id": "verified-id", "ambiguous": False},
        {"code": "elite", "found": False, "offer_id": None, "ambiguous": False},
    ]


@pytest.mark.asyncio
async def test_api_oauth2_reads_only_offers_and_orders(monkeypatch):
    monkeypatch.setenv("CAKTO_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "test-client-secret")
    paths = []
    class FakeResponse:
        def __init__(self, data):
            self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    class FakeHttp:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            paths.append(("POST", url))
            assert "client_secret" in kwargs["data"]
            return FakeResponse({"access_token": "fake-token", "expires_in": 36000})
        async def get(self, url, **kwargs):
            paths.append(("GET", url))
            assert kwargs["headers"]["Authorization"] == "Bearer fake-token"
            if url.endswith("offers/"):
                return FakeResponse({"results": [{"name": "Orion Pro", "price": 89.9,
                      "status": "active", "id": "offer"}], "next": None})
            return FakeResponse({"id": "test-order", "status": "paid"})
    monkeypatch.setattr(cakto_api.httpx, "AsyncClient", FakeHttp)
    cli = cakto_api.CaktoClient()
    assert (await cli.get_order("test-order"))["status"] == "paid"
    assert (await cli.list_offers())[0]["id"] == "offer"
    assert sum(1 for method, _ in paths if method == "POST") == 1
    assert not any("/payments/" in url for _, url in paths)


@pytest.mark.asyncio
async def test_checkout_links_are_not_public_before_plan_entitlements():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/public/cakto/plans")
    assert response.status_code == 200
    payload = response.json()
    assert payload["subscription_activation_enabled"] is False
    assert [x["monthly_cents"] for x in payload["plans"]] == [2990, 8990, 13990]
    assert all("checkout_url" not in item for item in payload["plans"])
