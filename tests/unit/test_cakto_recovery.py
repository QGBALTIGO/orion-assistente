"""Authenticated read recovery and OAuth guards; no external network or money."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from types import MappingProxyType
from unittest.mock import AsyncMock

import httpx
import pytest

from app import cakto_api, cakto_recovery as recovery
from app.cakto_subscription_lifecycle import canceled_paid_until
from tests.unit.test_cakto_lifecycle_boundaries import case


def direct_case():
    data = case("purchase_approved")
    tracking = "synthetic-checkout-binding"
    data.order.update(paymentMethod="pix_auto",sck=tracking)
    row = {"external_order_id":data.order["id"],"method":"pix",
           "offer_id":data.event["external_offer_id"],"expected_cents":2990,
           "callback_hash":hashlib.sha256(tracking.encode()).hexdigest()}
    return data, MappingProxyType(row)


def test_cached_client_is_reused_but_rotated_credentials_cannot_reuse_token(monkeypatch):
    cakto_api.cached_cakto_client.cache_clear()
    try:
        monkeypatch.setenv("CAKTO_CLIENT_ID","synthetic-client")
        monkeypatch.setenv("CAKTO_CLIENT_SECRET","synthetic-secret-a")
        first = cakto_api.cached_cakto_client()
        first._token = "synthetic-previous-token"
        assert cakto_api.cached_cakto_client() is first
        monkeypatch.setenv("CAKTO_CLIENT_SECRET","synthetic-secret-b")
        second = cakto_api.cached_cakto_client()
        assert second is not first and second._token is None
        assert second.client_secret == "synthetic-secret-b"
        assert cakto_api.cached_cakto_client.cache_info().currsize == 1
    finally:
        cakto_api.cached_cakto_client.cache_clear()


@pytest.mark.asyncio
async def test_parallel_oauth_refresh_issues_one_token(monkeypatch):
    posts = []
    class HTTP:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            posts.append(url)
            await asyncio.sleep(0)
            return httpx.Response(200,json={"access_token":"synthetic-token","expires_in":36000},
                                  request=httpx.Request("POST","https://synthetic.invalid/token"))
    monkeypatch.setenv("CAKTO_CLIENT_ID","synthetic-client")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET","synthetic-secret")
    monkeypatch.setattr(cakto_api.httpx,"AsyncClient",HTTP)
    client = cakto_api.CaktoClient()
    assert await asyncio.gather(*(client.token() for _ in range(12))) == ["synthetic-token"]*12
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_provider_gets_never_exceed_three_parallel_requests(monkeypatch):
    active = peak = 0
    class HTTP:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,**kwargs):
            nonlocal active,peak
            active += 1
            peak = max(peak,active)
            try:
                await asyncio.sleep(.002)
                return httpx.Response(200,json={},request=httpx.Request("GET","https://synthetic.invalid/order"))
            finally:
                active -= 1
    monkeypatch.setattr(cakto_api.httpx,"AsyncClient",HTTP)
    client = cakto_api.CaktoClient()
    client._token,client._expires = "synthetic-token",float("inf")
    await asyncio.gather(*(client._get("/orders/synthetic/") for _ in range(12)))
    assert peak == 3 and active == 0


@pytest.mark.asyncio
async def test_retry_after_prevents_immediate_get_retry(monkeypatch):
    clock = [100.0]
    calls = []
    class HTTP:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,**kwargs):
            calls.append(url)
            return httpx.Response(429 if len(calls)==1 else 200,headers={"Retry-After":"42"},
                                  json={},request=httpx.Request("GET","https://synthetic.invalid/order"))
    class Clock:
        @staticmethod
        def monotonic(): return clock[0]
    monkeypatch.setattr(cakto_api,"time",Clock)
    monkeypatch.setattr(cakto_api.httpx,"AsyncClient",HTTP)
    client = cakto_api.CaktoClient()
    client._token,client._expires = "synthetic-token",1000
    with pytest.raises(httpx.HTTPStatusError):
        await client._get("/orders/synthetic/")
    with pytest.raises(RuntimeError,match="cooldown"):
        await client._get("/orders/synthetic/")
    assert len(calls) == 1
    clock[0] += 43
    assert await client._get("/orders/synthetic/") == {}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_known_post_order_supports_real_get_shape_without_offer_or_pix():
    data,row = direct_case()
    data.order.pop("offer")
    data.order.update(customer={"docNumber":"never-retain"},card={"lastDigits":"0000"})
    result = await recovery.direct_receipts(type("Client",(),{"get_order":AsyncMock(return_value=data.order)})(),row)
    assert len(result) == 1 and result[0]["offer_id"] == row["offer_id"]
    assert result[0]["callback_hash"] == row["callback_hash"]
    assert not any(word in repr(result) for word in ("never-retain","customer","card","secret","pix"))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("id","foreign-order"),("offer",{"id":"foreign-offer"}),("offer","foreign"),
    ("subscription",None),("amount","29.91"),("currency","USD"),
    ("paymentMethod","pix"),("type","unique"),("offer_type","orderbump"),
    ("sck","foreign-callback"),("paidAt",None),
    ("status",{}),("status",[]),
])
async def test_direct_read_never_recovers_inconsistent_contract(field,value):
    data,row = direct_case()
    data.order[field] = value
    client = type("Client",(),{"get_order":AsyncMock(return_value=data.order)})()
    result = await recovery.direct_receipts(client,row)
    if field == "subscription":
        # Initial activation's ordinary verifier requires the authenticated
        # subscription GET. A missing contract is never promoted by this helper.
        assert result == []
    else:
        assert result == []


@pytest.mark.asyncio
async def test_completed_receipt_is_emitted_before_next_get_and_cancel_does_not_claim_rest(monkeypatch):
    data,row = direct_case()
    claimed = []
    waiting = asyncio.Event()
    saved = []
    async def claim(limit):
        assert limit == 1
        claimed.append(len(claimed))
        return [row]
    async def get_order(order):
        if len(claimed)>1:
            waiting.set()
            await asyncio.Event().wait()
        return data.order
    async def on_receipt(event): saved.append(event)
    monkeypatch.setattr(recovery,"claim_direct",claim)
    task = asyncio.create_task(recovery.recover_recurring_provider_receipts(
        type("Client",(),{"get_order":staticmethod(get_order)})(),limit=10,on_receipt=on_receipt))
    await asyncio.wait_for(waiting.wait(),1)
    assert len(saved) == 1 and len(claimed) == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert len(saved) == 1 and len(claimed) == 2


@pytest.mark.asyncio
async def test_collection_timeout_keeps_each_already_durable_receipt(monkeypatch):
    from app import prepaid_pix_recovery
    data,_ = direct_case()
    event = recovery.receipt("purchase_approved",data.order,offer_id=data.event["external_offer_id"])
    saved = []
    stopped = []
    async def leaf(client,*,limit,on_receipt):
        await on_receipt(event)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    async def persist(events,*,origin):
        assert origin == "provider_read"
        saved.extend(events)
    monkeypatch.setattr(prepaid_pix_recovery,"recover_prepaid_provider_receipts",leaf)
    monkeypatch.setattr(cakto_api,"persist_events",persist)
    monkeypatch.setattr(cakto_api,"RECOVERY_TIMEOUT_SECONDS",.01)
    monkeypatch.setenv("ORION_CAKTO_LIFECYCLE_ENABLED","0")
    assert await cakto_api.recover_provider_receipts(None) == 1
    assert saved == [event] and stopped == [True]


@pytest.mark.asyncio
async def test_old_schema_never_starts_provider_recovery(monkeypatch):
    client = type("Client",(),{"configured":True})()
    recover = AsyncMock()
    claim = AsyncMock()
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:client)
    monkeypatch.setattr(cakto_api,"_cakto_schema_ready",AsyncMock(return_value=False))
    monkeypatch.setattr(cakto_api,"recover_provider_receipts",recover)
    monkeypatch.setattr(cakto_api,"_claim_pending_cakto",claim)
    assert await cakto_api.scan_pending_cakto({}) == 0
    recover.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_receipt_batch_has_three_workers_and_awaits_all(monkeypatch):
    active = peak = completed = 0
    async def verify(client,row):
        nonlocal active,peak,completed
        active += 1
        peak = max(peak,active)
        try:
            await asyncio.sleep(.001)
            completed += 1
        finally:
            active -= 1
    monkeypatch.setattr(cakto_api,"_verify_cakto_receipt",verify)
    await cakto_api._verify_cakto_batch(None,list(range(30)))
    assert peak == 3 and active == 0 and completed == 30


@pytest.mark.asyncio
async def test_unexpected_child_failure_cancels_and_awaits_background_recovery(monkeypatch):
    stopped = []
    async def recover(client):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    async def fail(client,row):
        await asyncio.sleep(0)
        raise RuntimeError("synthetic verification failure")
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda:type("Client",(),{"configured":True})())
    monkeypatch.setattr(cakto_api,"_cakto_schema_ready",AsyncMock(return_value=True))
    monkeypatch.setattr(cakto_api,"_claim_pending_cakto",AsyncMock(return_value=[{}]))
    monkeypatch.setattr(cakto_api,"recover_provider_receipts",recover)
    monkeypatch.setattr(cakto_api,"_verify_cakto_receipt",fail)
    with pytest.raises(ExceptionGroup):
        await cakto_api.scan_pending_cakto({})
    assert stopped == [True]


def paid_cycle():
    data = case("subscription_canceled")
    now = datetime(2026,11,6,12,tzinfo=timezone.utc)
    paid = now-timedelta(days=5)
    data.binding.update(last_order_id=data.order["id"],last_verified_payment_at=paid,
                        local_status="active",grace_until=None)
    data.order["paidAt"] = paid.isoformat()
    return data,now,paid+timedelta(days=30)


def test_cancellation_keeps_only_the_original_paid_cycle_not_thirty_more_days():
    data,now,deadline = paid_cycle()
    assert canceled_paid_until(data.order,data.binding,expected_offer=data.event["external_offer_id"],now=now) == deadline
    data.binding["last_verified_payment_at"] = now  # Old initial activation used verification time.
    assert canceled_paid_until(data.order,data.binding,expected_offer=data.event["external_offer_id"],now=now) == deadline


@pytest.mark.parametrize("change", ["expired","refunded","foreign_order","future_paid","unverified","foreign_offer"])
def test_unverified_or_reversed_cycle_has_no_cancelled_paid_access(change):
    data,now,_ = paid_cycle()
    if change == "expired": data.order["paidAt"] = (now-timedelta(days=31)).isoformat()
    elif change == "refunded": data.order["status"] = "refunded"
    elif change == "foreign_order": data.order["id"] = "foreign-order"
    elif change == "future_paid": data.order["paidAt"] = (now+timedelta(days=1)).isoformat()
    elif change == "unverified": data.binding["last_verified_payment_at"] = None
    else: data.order["offer"] = {"id":"foreign-offer"}
    assert canceled_paid_until(data.order,data.binding,expected_offer=data.event["external_offer_id"],now=now) is None


def test_duplicate_cancellation_never_restores_paid_access_cleared_by_refund():
    data,now,deadline = paid_cycle()
    data.binding.update(local_status="canceled",grace_until=deadline-timedelta(days=1))
    assert canceled_paid_until(data.order,data.binding,expected_offer=data.event["external_offer_id"],now=now) == deadline-timedelta(days=1)
    data.binding["grace_until"] = None
    assert canceled_paid_until(data.order,data.binding,expected_offer=data.event["external_offer_id"],now=now) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,event", [("refunded","refund"),("MED","chargeback")])
async def test_cancelled_paid_cycle_recovery_prioritizes_bank_reversal(monkeypatch,status,event):
    data = case()
    data.binding.update(local_status="canceled",last_order_id=data.order["id"])
    data.subscription.update(status="canceled",orders=[data.order["id"]])
    data.order["status"] = status
    async def get(path,**params):
        if path.startswith("/subscriptions/"): return data.subscription
        assert params["id"] == data.order["id"]
        return {"results":[data.order],"next":None}
    monkeypatch.setattr(recovery,"original_intent",AsyncMock(return_value={
        "offer_id":data.event["external_offer_id"],"expected_cents":2990}))
    result = await recovery.binding_receipts(type("Client",(),{"_get":staticmethod(get)})(),data.binding)
    assert len(result) == 1 and result[0]["event"] == event
