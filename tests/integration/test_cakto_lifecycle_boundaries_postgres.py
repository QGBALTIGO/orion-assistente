"""Real disposable PostgreSQL/support RLS; provider GETs are synthetic only."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.cakto_subscription_lifecycle import reconcile_existing_cakto_subscription
from app.catalog_api import support_session_factory
from tests.integration.test_manual_plan_permissions_postgres import manual_db

pytestmark = pytest.mark.integration


async def contract(c):
    first, previous, current = (prefix + c.tenant.hex for prefix in ("initial-", "previous-", "renewal-"))
    subscription = "subscription-" + c.tenant.hex
    offer = "offer-" + c.tenant.hex
    async with c.engine.begin() as db:
        identity = (await db.execute(text("""
            INSERT INTO subscriptions(tenant_id,plan_id,external_subscription_id,status,last_verified_payment_at)
            SELECT :tenant,id,:external,'active',now()-interval '30 days'
            FROM plans WHERE code='orion_essencial' RETURNING id
        """), {"tenant":c.tenant,"external":subscription})).scalar_one()
        await db.execute(text("""
            INSERT INTO orion_cakto_bindings
              (tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
            VALUES (:tenant,:id,:external,'essencial',:previous)
        """), {"tenant":c.tenant,"id":identity,"external":subscription,"previous":previous})
        await db.execute(text("""
            INSERT INTO orion_cakto_checkout_intents
              (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,
               state,expires_at,verified_at,external_order_id)
            VALUES (:tenant,:account,'essencial',:offer,2990,:hash,
                    'verified',now()-interval '29 days',now()-interval '30 days',:first)
        """), {"tenant":c.tenant,"account":c.account,"offer":offer,
                 "hash":c.tenant.hex+c.tenant.hex,"first":first})
    event = {"event_type":"subscription_renewed","external_order_id":current,
             "external_subscription_id":subscription,"external_offer_id":offer,
             "amount_cents":2990,"currency":"BRL"}
    order = {"id":current,"subscription":subscription,"offer":{"id":offer},
             "type":"subscription","offer_type":"main","status":"paid",
             "amount":"29.90","currency":"BRL","paidAt":datetime.now(timezone.utc).isoformat(),
             "createdAt":datetime.now(timezone.utc).isoformat()}
    remote = {"id":subscription,"parent_order":first,"offer":offer,"status":"active",
              "amount":"29.90","recurrence_period":30,"paymentMethod":"pix_auto"}
    return SimpleNamespace(event=event,order=order,remote=remote,first=first,previous=previous)


def provider(remote, order=None):
    class ReadOnlyProvider:
        async def _get(self, path):
            assert path == "/subscriptions/" + remote["id"] + "/"
            return dict(remote)

        async def list_offers(self):
            raise AssertionError("An established contract must not depend on today's sale catalog")

        async def get_order(self, order_id):
            if order is not None:
                assert order_id == order["id"]
                return dict(order)
            # Baseline cases cancel an old, expired cycle. Paid-cycle cases
            # below supply the exact immutable bank order explicitly.
            return {"id":order_id,"subscription":remote["id"],"status":"paid",
                    "type":"subscription","offer_type":"main","currency":"BRL",
                    "amount":remote["amount"],"paidAt":(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()}

    return ReadOnlyProvider()


async def state(c):
    async with c.engine.connect() as db:
        return (await db.execute(text("""
            SELECT s.status,s.last_verified_payment_at,b.last_order_id,s.grace_until
            FROM subscriptions s JOIN orion_cakto_bindings b ON b.subscription_id=s.id
            WHERE s.tenant_id=:tenant
        """), {"tenant":c.tenant})).one()


@pytest.mark.asyncio
async def test_parallel_same_renewal_is_applied_once_under_support_rls(manual_db):
    c = manual_db
    data = await contract(c)
    async with support_session_factory()() as db:
        assert (await db.execute(text("SELECT current_user"))).scalar_one() == "app_support"
    results = await asyncio.gather(*(reconcile_existing_cakto_subscription(
        data.event,data.order,provider(data.remote),
    ) for _ in range(8)))
    assert results.count("reconciled") == 1
    assert results.count("already_verified") == 7
    result = await state(c)
    assert result.status == "active" and result.last_order_id == data.order["id"]
    assert result.last_verified_payment_at == datetime.fromisoformat(data.order["paidAt"])


@pytest.mark.asyncio
@pytest.mark.parametrize("event,status,remote_status,expected", [
    ("subscription_canceled","paid","canceled","canceled"),
    ("refund","refunded","active","suspended"),
    ("chargeback","MED","active","suspended"),
    ("purchase_refused","refused","inactive","past_due"),
])
async def test_verified_reversal_uses_frozen_contract_without_catalog(
    manual_db,event,status,remote_status,expected,
):
    c = manual_db
    data = await contract(c)
    data.event["event_type"] = event
    data.order["status"] = status
    data.remote["status"] = remote_status
    if event in {"refund","chargeback"}:
        async with c.engine.begin() as db:
            await db.execute(text("UPDATE orion_cakto_bindings SET last_order_id=:order WHERE tenant_id=:tenant"),
                             {"order":data.order["id"],"tenant":c.tenant})
    before = await state(c)
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "reconciled"
    after = await state(c)
    assert after.status == expected
    assert after.last_verified_payment_at == before.last_verified_payment_at


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant_id",None),("plan_code","elite"),("expected_cents",1),
    ("state","pending"),("offer_id","foreign-offer"),
])
async def test_foreign_or_inconsistent_historical_intent_cannot_renew(manual_db,field,value):
    c = manual_db
    data = await contract(c)
    if field == "tenant_id":
        value = c.other
    async with c.engine.begin() as db:
        await db.execute(text(f"UPDATE orion_cakto_checkout_intents SET {field}=:value WHERE tenant_id=:tenant"),
                         {"value":value,"tenant":c.tenant})
    before = await state(c)
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "manual_review"
    assert await state(c) == before


@pytest.mark.asyncio
async def test_old_renewal_and_refund_do_not_override_newer_payment(manual_db):
    c = manual_db
    data = await contract(c)
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "reconciled"
    before = await state(c)
    old = {**data.order,"id":data.previous,"paidAt":(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
    event = {**data.event,"external_order_id":data.previous}
    assert await reconcile_existing_cakto_subscription(event,old,provider(data.remote)) == "manual_review"
    assert await reconcile_existing_cakto_subscription(
        {**event,"event_type":"refund"},{**old,"status":"refunded"},provider(data.remote),
    ) == "manual_review"
    assert await state(c) == before


@pytest.mark.asyncio
async def test_original_payer_no_longer_owner_does_not_block_confirmed_cancellation(manual_db):
    c = manual_db
    data = await contract(c)
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=:tenant AND account_id=:account"),
                         {"tenant":c.tenant,"account":c.account})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:account,'owner',true)"),
                         {"tenant":c.tenant,"account":c.second})
    data.remote["status"] = "canceled"
    assert await reconcile_existing_cakto_subscription(
        {**data.event,"event_type":"subscription_canceled"},data.order,provider(data.remote),
    ) == "reconciled"
    assert (await state(c)).status == "canceled"


@pytest.mark.asyncio
async def test_cancel_then_refund_then_stale_active_snapshot_never_reopens_contract(manual_db):
    c = manual_db
    data = await contract(c)
    active_snapshot = provider(dict(data.remote))
    assert await reconcile_existing_cakto_subscription(
        {**data.event,"event_type":"subscription_canceled"},data.order,
        provider({**data.remote,"status":"canceled"}),
    ) == "reconciled"
    assert (await state(c)).status == "canceled"
    # Both provider snapshots predate the SQL cancellation.
    refund = {**data.event,"event_type":"refund","external_order_id":data.previous}
    assert await reconcile_existing_cakto_subscription(
        refund,{**data.order,"id":data.previous,"status":"refunded"},active_snapshot,
    ) == "reconciled"
    assert (await state(c)).status == "canceled"
    assert await reconcile_existing_cakto_subscription(
        data.event,data.order,active_snapshot,
    ) == "manual_review"
    assert (await state(c)).status == "canceled"


@pytest.mark.asyncio
async def test_cancellation_preserves_bank_paid_cycle_and_refund_clears_it_permanently(manual_db):
    c = manual_db
    data = await contract(c)
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "reconciled"
    paid = datetime.fromisoformat(data.order["paidAt"])
    cancel = {**data.event,"event_type":"subscription_canceled"}
    bank = {k:v for k,v in data.order.items() if k!="offer"}  # Real GET omits it.
    canceled = provider({**data.remote,"status":"canceled"},bank)
    assert await reconcile_existing_cakto_subscription(cancel,data.order,canceled) == "reconciled"
    first = await state(c)
    assert first.status == "canceled" and first.grace_until == paid+timedelta(days=30)
    assert await reconcile_existing_cakto_subscription(cancel,data.order,canceled) == "reconciled"
    assert (await state(c)).grace_until == first.grace_until
    assert await reconcile_existing_cakto_subscription(
        {**data.event,"event_type":"refund"},{**data.order,"status":"refunded"},provider(data.remote),
    ) == "reconciled"
    assert (await state(c)).grace_until is None
    # Even a bank response fetched before the reversal cannot recreate the grant.
    assert await reconcile_existing_cakto_subscription(cancel,data.order,canceled) == "reconciled"
    assert (await state(c)).status == "canceled" and (await state(c)).grace_until is None


@pytest.mark.asyncio
async def test_new_payment_during_cancel_bank_lookup_requires_fresh_retry(manual_db):
    c = manual_db
    data = await contract(c)
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "reconciled"
    entered,release = asyncio.Event(),asyncio.Event()
    class Paused:
        async def _get(self,path): return {**data.remote,"status":"canceled"}
        async def get_order(self,order_id):
            entered.set()
            await release.wait()
            return dict(data.order)
    task = asyncio.create_task(reconcile_existing_cakto_subscription(
        {**data.event,"event_type":"subscription_canceled"},data.order,Paused()))
    try:
        await asyncio.wait_for(entered.wait(),5)
        newer = {**data.order,"id":"newer-"+c.tenant.hex,"paidAt":datetime.now(timezone.utc).isoformat()}
        assert await reconcile_existing_cakto_subscription(
            {**data.event,"external_order_id":newer["id"]},newer,provider(data.remote),
        ) == "reconciled"
        release.set()
        assert await task == "retry_later"
        assert (await state(c)).last_order_id == newer["id"]
        assert (await state(c)).status == "active"
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_old_refusal_with_stale_inactive_snapshot_never_revokes_new_payment(manual_db):
    c = manual_db
    data = await contract(c)
    inactive_snapshot = provider({**data.remote,"status":"inactive"})
    assert await reconcile_existing_cakto_subscription(data.event,data.order,provider(data.remote)) == "reconciled"
    before = await state(c)
    old = {**data.order,"id":data.previous,"status":"refused","paidAt":None,
           "createdAt":(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
    assert await reconcile_existing_cakto_subscription(
        {**data.event,"event_type":"purchase_refused","external_order_id":data.previous},old,inactive_snapshot,
    ) == "manual_review"
    assert await state(c) == before
