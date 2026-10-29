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


def test_signed_cakto_callback_is_reconciled_even_without_sck():
    from app import cakto_api
    import hashlib
    payload = {"event": "purchase_approved", "data": {
        "id": "synthetic-first-order", "status": "paid", "offer_type": "main",
        "offer": {"id": "synthetic-offer-id"}, "callback": "tenant-callback-0123456789",
        "amount": 29.9, "currency": "BRL"}}
    normalized = cakto_api.normalized_events(payload)
    assert len(normalized) == 1
    assert normalized[0]["callback_hash"] == hashlib.sha256(b"tenant-callback-0123456789").hexdigest()
    assert normalized[0]["amount_cents"] == 2990


@pytest.mark.asyncio
async def test_public_checkout_availability_tracks_real_server_flag(monkeypatch):
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED", "1")
    result=await cakto_api.public_cakto_plans()
    assert result["subscription_activation_enabled"] is True
    assert [x["monthly_cents"] for x in result["plans"]] == [2990,8990,13990]
    assert all("checkout_url" not in x for x in result["plans"])
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED", "0")
    assert (await cakto_api.public_cakto_plans())["subscription_activation_enabled"] is False


def test_webhook_audit_requires_exact_domain_product_events_and_matching_secret():
    from scripts.probe_orion_webhooks_read_only import coverage, REQUIRED_EVENTS
    synthetic_secret = "synthetic-webhook-secret-for-ci-32-char-minimum"
    plans = [
        {"id":f"offer-{i}", "name":name, "type":"subscription",
         "price":price, "recurrence_period":30, "status":"active",
         "product":f"product-{i}"}
        for i,(name,price) in enumerate([
            ("Orion Essencial",29.9), ("Orion Pro",89.9), ("Orion Elite",139.9)])
    ]
    hooks = [{
        "status":"active","url":"https://orionagenda.pro/webhooks/cakto",
        "products":[{"id":f"product-{i}"}],
        "events":[{"custom_id":event} for event in REQUIRED_EVENTS],
        "fields":{"secret":synthetic_secret}
    } for i in range(3)]
    assert coverage(plans,hooks,synthetic_secret)["webhook_ready"]==3
    assert coverage(plans,hooks,"incorrect-secret-at-least-32-characters")["webhook_ready"]==0
    assert coverage(plans,[{**hooks[0],"url":"https://orionagenda.pro.evil.test/webhooks/cakto"},*hooks[1:]],synthetic_secret)["webhook_ready"]==2
    assert coverage(plans,[{**hooks[0],"events":[]},*hooks[1:]],synthetic_secret)["webhook_ready"]==2
    assert coverage(plans,[{**hooks[0],"status":"disabled"},*hooks[1:]],synthetic_secret)["webhook_ready"]==2
    assert coverage(plans,[{**hooks[0],"products":[{"id":"other-product"}]},*hooks[1:]],synthetic_secret)["webhook_ready"]==2


@pytest.mark.asyncio
async def test_webhook_read_probe_only_gets_provider_data_and_exposes_counts(monkeypatch):
    from scripts.probe_orion_webhooks_read_only import probe, REQUIRED_EVENTS
    offers = [
        {"id":f"offer-{i}", "name":name, "type":"subscription","price":price,
         "recurrence_period":30,"status":"active","product":f"product-{i}"}
        for i,(name,price) in enumerate([
            ("Orion Essencial",29.9),("Orion Pro",89.9),("Orion Elite",139.9)])
    ]
    secret = "synthetic-webhook-secret-for-ci-32-char-minimum"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",secret)
    class ReadOnly:
        def __init__(self):
            self.calls=[]
        async def _get(self,path,**params):
            self.calls.append((path,params))
            assert path=="/webhook/"
            assert params["page"]==1
            return {"results":[{"status":"active",
                       "url":"https://orionagenda.pro/webhooks/cakto",
                       "products":[{"id":f"product-{i}"}],
                       "fields":{"secret":secret},
                       "events":[{"custom_id":event} for event in REQUIRED_EVENTS]}
                      for i in range(3)],"next":None}
    client=ReadOnly()
    result=await probe(client,offers)
    assert result=={"offer_matches":3,"webhook_ready":3,"expected":3}
    assert client.calls==[("/webhook/",{"page":1,"limit":100})]
    assert secret not in str(result)



def test_one_product_webhook_covers_three_distinct_orion_subscription_offers():
    from scripts.probe_orion_webhooks_read_only import (
        coverage, detailed_coverage, REQUIRED_EVENTS,
    )
    secret = "synthetic-webhook-secret-for-ci-32-char-minimum"
    product_id = "synthetic-orion-master-product"
    offers = [
        {"id":f"orion-offer-{i}","name":name,"price":cents/100,
         "type":"subscription","recurrence_period":30,"status":"active",
         "product":product_id}
        for i,(name,cents) in enumerate((
            ("Orion Essencial",2990),("Orion Pro",8990),("Orion Elite",13990)
        ))
    ]
    hook = {
        "status":"active",
        "url":"https://orionagenda.pro/webhooks/cakto",
        "products":[{"id":product_id,"name":"ORION RECEPCIONISTA DIGITAL"}],
        "events":[{"custom_id":event} for event in REQUIRED_EVENTS],
        "fields":{"secret":secret},
    }
    assert coverage(offers,[hook],secret) == {
        "offer_matches":3,"webhook_ready":3,"expected":3}
    complete = detailed_coverage(offers,[hook],secret)
    assert complete["distinct_products"] == 1
    assert complete["active_target_hooks"] == 1
    assert complete["webhooks_listed"] == 1
    assert complete["active_hooks"] == 1
    assert complete["matching_target_url"] == 1
    assert complete["product_coverage_any_url"] == 3
    assert complete["product_coverage"] == 3
    assert complete["secret_coverage"] == 3
    assert complete["event_coverage"] == 3

    mismatched_secret = detailed_coverage(
        offers,[hook],"a-different-synthetic-secret-value-32-chars")
    assert mismatched_secret["product_coverage"] == 3
    assert mismatched_secret["event_coverage"] == 3
    assert mismatched_secret["secret_coverage"] == 0
    assert mismatched_secret["webhook_ready"] == 0

    missing_event = detailed_coverage(
        offers,[{**hook,"events":[{"custom_id":"purchase_approved"}]}],secret)
    assert missing_event["secret_coverage"] == 3
    assert missing_event["event_coverage"] == 0
    assert missing_event["webhook_ready"] == 0

    wrong_product = detailed_coverage(
        offers,[{**hook,"products":[{"id":"another-product"}]}],secret)
    assert wrong_product["active_target_hooks"] == 1
    assert wrong_product["product_coverage"] == 0
    assert wrong_product["webhook_ready"] == 0
    assert secret not in str(complete)
    assert product_id not in str(complete)


@pytest.mark.asyncio
async def test_diagnostic_webhook_probe_uses_only_get_and_returns_safe_counts(monkeypatch):
    from scripts.probe_orion_webhooks_read_only import probe, REQUIRED_EVENTS
    secret = "synthetic-webhook-secret-for-ci-32-char-minimum"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",secret)
    product_id = "synthetic-single-product"
    offers = [
        {"id":f"offer-{i}","name":name,"price":price,"type":"subscription",
         "recurrence_period":30,"status":"active","product":product_id}
        for i,(name,price) in enumerate((
            ("Orion Essencial",29.90),("Orion Pro",89.90),("Orion Elite",139.90)
        ))
    ]
    class ReadOnly:
        def __init__(self): self.calls = []
        async def _get(self,path,**kwargs):
            self.calls.append((path,kwargs))
            return {"results":[{
                "status":"active",
                "url":"https://orionagenda.pro/webhooks/cakto",
                "fields":{"secret":secret},
                "products":[{"id":product_id}],
                "events":[{"custom_id":e} for e in REQUIRED_EVENTS]}],
                "next":None}
    client = ReadOnly()
    result = await probe(client,offers,detailed=True)
    assert result["webhook_ready"] == 3
    assert result["distinct_products"] == 1
    assert client.calls == [("/webhook/",{"page":1,"limit":100})]
    assert secret not in str(result)
    assert product_id not in str(result)


def test_webhook_diagnostics_distinguish_no_registration_disabled_and_wrong_url():
    from scripts.probe_orion_webhooks_read_only import detailed_coverage, REQUIRED_EVENTS
    key="synthetic-webhook-secret-for-ci-32-char-minimum"
    product="shared-product"
    offers=[
        {"id":f"off-{i}","name":name,"price":amount,
         "type":"subscription","recurrence_period":30,"status":"active",
         "product":product}
        for i,(name,amount) in enumerate((
            ("Orion Essencial",29.9),("Orion Pro",89.9),("Orion Elite",139.9)
        ))
    ]
    template={
        "status":"active","url":"https://orionagenda.pro/webhooks/cakto",
        "products":[{"id":product}],
        "events":[{"custom_id":event} for event in REQUIRED_EVENTS],
        "fields":{"secret":key},
    }
    no_hooks=detailed_coverage(offers,[],key)
    assert no_hooks["webhooks_listed"]==0
    assert no_hooks["active_target_hooks"]==0

    disabled=detailed_coverage(offers,[{**template,"status":"disabled"}],key)
    assert disabled["webhooks_listed"]==1
    assert disabled["active_hooks"]==0
    assert disabled["matching_target_url"]==1
    assert disabled["product_coverage_any_url"]==3
    assert disabled["webhook_ready"]==0

    wrong_url=detailed_coverage(offers,[{**template,"url":"https://example.test/webhooks/cakto"}],key)
    assert wrong_url["webhooks_listed"]==1
    assert wrong_url["active_hooks"]==1
    assert wrong_url["matching_target_url"]==0
    assert wrong_url["product_coverage_any_url"]==3
    assert wrong_url["webhook_ready"]==0
    assert key not in str(wrong_url)
    assert product not in str(wrong_url)


def test_panel_v2_webhook_history_proves_one_product_three_offers():
    from datetime import datetime, timezone
    from scripts.probe_orion_webhook_history_read_only import history_coverage
    from scripts.probe_orion_webhooks_read_only import REQUIRED_EVENTS
    secret="synthetic-webhook-secret-for-ci-32-char-minimum"
    now=datetime(2026,10,29,15,0,tzinfo=timezone.utc)
    product="product-shared-orion-test"
    offers=[{
        "id":f"offer-{i}","name":name,"price":value,
        "type":"subscription","recurrence_period":30,"status":"active",
        "product":product,
    } for i,(name,value) in enumerate((
        ("Orion Essencial",29.9),("Orion Pro",89.9),("Orion Elite",139.9)
    ))]
    records=[]
    for event in REQUIRED_EVENTS:
        records.append({
            "app":{"status":"active",
                   "url":"https://orionagenda.pro/webhooks/cakto",
                   "products":[product],"fields":{"secret":secret}},
            "url":"https://orionagenda.pro/webhooks/cakto",
            "payload":{"event":event,"secret":secret,
                       "data":[{"id":"synthetic-order"}]},
            "event_id":event, "event_status":200,
            "sentAt":"2026-10-29T14:45:00+00:00",
        })
    complete=history_coverage(offers,records,secret,now=now)
    assert complete["offer_matches"]==3
    assert complete["targeted_deliveries"]==6
    assert complete["http_2xx_deliveries"]==6
    assert complete["secret_matched_deliveries"]==6
    assert complete["recent_verified_deliveries"]==6
    assert complete["product_coverage"]==3
    assert complete["required_event_coverage"]==3
    assert complete["webhook_ready"]==3
    assert secret not in str(complete)
    assert product not in str(complete)

    incomplete=history_coverage(offers,records[:-1],secret,now=now)
    assert incomplete["product_coverage"]==3
    assert incomplete["required_event_coverage"]==0
    assert history_coverage(offers,records,"another-secret-with-32-characters-minimum",now=now)["webhook_ready"]==0
    assert history_coverage(offers,[{**r,"event_status":401} for r in records],secret,now=now)["webhook_ready"]==0
    assert history_coverage(offers,[{**r,"sentAt":"2026-10-27T14:00:00+00:00"} for r in records],secret,now=now)["webhook_ready"]==0
    assert history_coverage(offers,[{**r,"url":"https://orionagenda.pro.attacker.test/webhooks/cakto"} for r in records],secret,now=now)["webhook_ready"]==0
    assert history_coverage(offers,[{**r,"app":{**r["app"],"products":["another-product"]}} for r in records],secret,now=now)["webhook_ready"]==0


@pytest.mark.asyncio
async def test_panel_v2_history_probe_is_read_only_and_bounded(monkeypatch):
    from datetime import datetime,timezone
    from scripts.probe_orion_webhook_history_read_only import probe
    from scripts.probe_orion_webhooks_read_only import REQUIRED_EVENTS
    secret="synthetic-webhook-secret-for-ci-32-char-minimum"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",secret)
    now=datetime(2026,10,29,15,0,tzinfo=timezone.utc)
    product="shared-orion-product"
    offers=[{
        "id":f"offer-{i}","name":name,"price":value,
        "type":"subscription","recurrence_period":30,"status":"active",
        "product":product,
    } for i,(name,value) in enumerate((
        ("Orion Essencial",29.9),("Orion Pro",89.9),("Orion Elite",139.9)
    ))]
    records=[{"app":{"status":"active",
                     "url":"https://orionagenda.pro/webhooks/cakto",
                     "products":[{"id":product}],"fields":{"secret":secret}},
              "url":"https://orionagenda.pro/webhooks/cakto",
              "event_id":event,
              "payload":{"event":event,"secret":secret,"data":[]},
              "event_status":200,
              "sentAt":"2026-10-29T14:59:00+00:00"} for event in REQUIRED_EVENTS]
    class Client:
        def __init__(self):self.calls=[]
        async def _get(self,path,**params):
            self.calls.append((path,params))
            return {"results":records,"next":None}
    client=Client()
    result=await probe(client,offers,now=now)
    assert client.calls==[("/webhook/event_history/",{"page":1,"limit":100,"search":"orionagenda.pro"})]
    assert result["webhook_ready"]==3
    assert "customer" not in str(result)
    assert secret not in str(result)
