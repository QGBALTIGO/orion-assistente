"""Real local plan authority and channel exclusion; all provider calls are fake."""
import asyncio
import hashlib
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import plan_management as plans
from app.catalog_api import Actor
from test_manual_plan_permissions_postgres import manual_db  # noqa: F401
from test_manual_checkout_priority_postgres import checkout_db, intent  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def billing_db(checkout_db):
    c = checkout_db
    try:
        yield c
    finally:
        async with c.engine.begin() as db:
            for table in ("orion_prepaid_pix_orders", "orion_prepaid_pix_access"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"),
                                 {"a": c.tenant, "b": c.other})


async def paid_pix(c, *, tenant=None, code="profissional"):
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_prepaid_pix_access
            (tenant_id,plan_code,paid_until,last_verified_payment_at,last_order_id)
            VALUES(:tenant,:code,now()+interval '20 days',now()-interval '10 days',:order)
        """), {"tenant": tenant or c.tenant, "code": code, "order": "synthetic-" + str(uuid4())})


async def pending_pix(db, c, state="reserved"):
    identity = uuid4()
    await db.execute(text("""INSERT INTO orion_prepaid_pix_orders
        (id,tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,state,expires_at)
        VALUES(:id,:tenant,:account,'essencial','synthetic-pix-offer',2990,:hash,:state,now()-interval '1 day')
    """), {"id": identity, "tenant": c.tenant, "account": c.account, "state": state,
            "hash": hashlib.sha256(str(identity).encode()).hexdigest()})


async def first_payment(c, *, paid_at, created_at, expires_at):
    identity = await intent(c)
    token = plans._replayable_checkout_token(c.tenant, identity, "essencial")
    order_id, subscription_id = "synthetic-order-"+str(uuid4()), "synthetic-sub-"+str(uuid4())
    async with c.engine.begin() as db:
        await db.execute(text("""UPDATE orion_cakto_checkout_intents SET created_at=:created,
            expires_at=:expires WHERE id=:id"""),
            {"id":identity,"created":created_at,"expires":expires_at})
    event = {"event_type":"purchase_approved","external_order_id":order_id,
        "external_offer_id":"synthetic-offer","external_subscription_id":subscription_id,
        "callback_hash":hashlib.sha256(token.encode()).hexdigest(),"amount_cents":2990,"currency":"BRL"}
    order = {"id":order_id,"type":"subscription","offer_type":"main","status":"paid",
        "amount":"29.90","currency":"BRL","subscription":subscription_id,"sck":token,
        "checkoutUrl":plans.CODES["essencial"]["checkout_url"]}
    if paid_at is not None:
        order["paidAt"] = paid_at.isoformat()
    remote = {"id":subscription_id,"offer":"synthetic-offer","amount":"29.90",
        "recurrence_period":30,"status":"active","parent_order":order_id,"paymentMethod":"threeDs"}
    assert plans.first_cakto_subscription_confirmed(event,order,remote) is True
    return identity,event,order,remote


@pytest.mark.asyncio
async def test_delayed_verified_first_payment_grants_only_the_remaining_bank_paid_period(billing_db):
    c=billing_db
    now=datetime.now(timezone.utc)
    paid=now-timedelta(days=8)
    identity,event,order,remote=await first_payment(c,paid_at=paid,
        created_at=now-timedelta(days=9),expires_at=now-timedelta(days=2))
    assert await plans.apply_verified_cakto_payment(event,order,subscription=remote)=="activated"
    assert await plans.apply_verified_cakto_payment(event,order,subscription=remote)=="already_verified"
    state=(await c.client.get(c.path)).json()["effective_plan"]
    assert state["active"] is True and state["source"]=="cakto"
    assert datetime.fromisoformat(state["valid_until"])==paid+timedelta(days=30)
    assert timedelta(days=21,hours=23)<datetime.fromisoformat(state["valid_until"])-datetime.now(timezone.utc)<=timedelta(days=22)
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT last_verified_payment_at FROM subscriptions WHERE tenant_id=:t"),
                                 {"t":c.tenant})).scalar_one()==paid
        assert (await db.execute(text("SELECT state FROM orion_cakto_checkout_intents WHERE id=:id"),
                                 {"id":identity})).scalar_one()=="verified"
        assert (await db.execute(text("SELECT count(*) FROM orion_cakto_bindings WHERE tenant_id=:t"),
                                 {"t":c.tenant})).scalar_one()==1
    assert c.provider_posts==c.offer_reads==0


@pytest.mark.asyncio
@pytest.mark.parametrize("case",["missing","before_intent","after_expiry","future"])
async def test_first_payment_without_bank_time_inside_the_frozen_intent_cannot_bind(billing_db,case):
    c=billing_db
    now=datetime.now(timezone.utc)
    created,expires=now-timedelta(days=9),now-timedelta(days=2)
    paid={"missing":None,"before_intent":created-timedelta(minutes=5,seconds=1),
          "after_expiry":expires+timedelta(seconds=1),"future":now+timedelta(minutes=6)}[case]
    identity,event,order,remote=await first_payment(c,paid_at=paid,created_at=created,expires_at=expires)
    assert await plans.apply_verified_cakto_payment(event,order,subscription=remote)=="manual_review"
    async with c.engine.connect() as db:
        for table in ("subscriptions","orion_cakto_bindings"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),{"t":c.tenant})).scalar_one()==0
        assert (await db.execute(text("SELECT state FROM orion_cakto_checkout_intents WHERE id=:id"),
                                 {"id":identity})).scalar_one()=="pending"
    assert c.provider_posts==c.offer_reads==0


@pytest.mark.asyncio
async def test_verified_pix_with_tolerated_future_bank_time_cannot_open_a_recurring_bill(billing_db):
    c=billing_db
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_prepaid_pix_access
            (tenant_id,plan_code,paid_until,last_verified_payment_at,last_order_id)
            VALUES(:tenant,'essencial',now()+interval '30 days',
                   now()+interval '1 minute',:order)
        """),{"tenant":c.tenant,"order":"synthetic-future-bank-"+str(uuid4())})
    state=(await c.client.get(c.path)).json()["effective_plan"]
    assert state["active"] is False and state["source"]=="none"
    with pytest.raises(HTTPException) as caught:
        async with plans.support_session_factory()() as db:
            async with db.begin():
                await plans.require_checkout_available(db,c.tenant,c.account,
                                                      channel="recurring",lock="exclusive")
    assert caught.value.status_code==409 and caught.value.detail["code"]=="CHECKOUT_CHANNEL_CONFLICT"
    assert c.provider_posts==c.offer_reads==0


@pytest.mark.asyncio
async def test_paid_pix_admin_list_detail_and_customer_permissions_use_the_same_authority(billing_db):
    c = billing_db
    await paid_pix(c)
    query = {"source": "pix_prepaid", "q": str(c.account)}
    # Search uses the account's actual synthetic email; no other fixture rows
    # can affect this assertion even when another suite is running in parallel.
    async with c.engine.connect() as db:
        query["q"] = (await db.execute(text("SELECT email FROM panel_accounts WHERE id=:id"),
                                      {"id": c.account})).scalar_one()
    listed = await c.client.get("/api/v1/admin/accounts", params=query)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    customer = await c.client.get(f"/api/v1/tenants/{c.tenant}/management/permissions")
    detail = await c.client.get(f"/api/v1/admin/accounts/{c.account}")
    company = await c.client.get(c.path)
    assert customer.status_code == detail.status_code == company.status_code == 200
    states = [listed.json()["items"][0]["effective_plan"], detail.json()["account"]["effective_plan"],
              company.json()["effective_plan"], customer.json()["effective_plan"]]
    for state in states:
        assert state["source"] == "pix_prepaid" and state["active"] is True and state["code"] == "profissional"
        assert state["limits"]["max_professionals"] == 6
    assert len({state["revision"] for state in states}) == 1
    assert c.provider_posts == c.offer_reads == 0


@pytest.mark.asyncio
async def test_paid_pix_cannot_request_or_consume_a_trial_and_same_plan_pix_can_renew(billing_db):
    c = billing_db
    trial = uuid4()
    async with c.engine.begin() as db:
        await db.execute(text("""INSERT INTO orion_plan_trial_requests
            (id,tenant_id,account_id,full_name,company_name,whatsapp_e164)
            VALUES(:id,:tenant,:account,'Pessoa Sintética','Empresa Sintética','+5567999999901')
        """), {"id": trial, "tenant": c.tenant, "account": c.account})
    await paid_pix(c)
    actor = Actor("member", c.tenant, c.account, "owner")
    request = plans.TrialRequest(full_name="Pessoa Sintética", company_name="Empresa Sintética",
                                 whatsapp="(67) 99999-1111", privacy_ack=True)
    for action in (lambda: plans.request_plan_trial(c.tenant, request, actor),
                   lambda: plans.admin_decide_trial(trial, plans.TrialDecision(action="activate", expected_version=1), admin=c.operator)):
        with pytest.raises(HTTPException) as caught:
            await action()
        assert caught.value.status_code == 409
    async with plans.support_session_factory()() as db:
        async with db.begin():
            state = await plans.require_checkout_available(db, c.tenant, c.account, channel="pix", lock="exclusive")
            assert state["source"] == "pix_prepaid"
    async with c.engine.connect() as db:
        row = (await db.execute(text("SELECT status,started_at,ends_at,version FROM orion_plan_trial_requests WHERE id=:id"),
                               {"id": trial})).one()
        assert tuple(row) == ("pending", None, None, 1)
    assert c.provider_posts == c.offer_reads == 0


@pytest.mark.asyncio
async def test_unassigned_company_listing_keeps_paid_pix_and_never_selects_another_owner(billing_db):
    c = billing_db
    await paid_pix(c, tenant=c.other)
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"),
                         {"tenant": c.other, "account": c.second})
    response = await c.client.get("/api/v1/admin/accounts/companies/unassigned")
    assert response.status_code == 200, response.text
    company = next(row for row in response.json()["companies"] if row["id"] == str(c.other))
    assert company["owner_status"] == "unassigned" and company["effective_plan"]["source"] == "pix_prepaid"
    assert company["effective_plan"]["active"] is True and company["effective_plan"]["code"] == "profissional"
    assert all(row["id"] != str(c.tenant) for row in response.json()["companies"])


@pytest.mark.asyncio
async def test_old_recurring_intent_cannot_bind_over_a_verified_pix_period(billing_db):
    c = billing_db
    identity = await intent(c)  # Intent persisted before the channel-exclusion correction.
    token = plans._replayable_checkout_token(c.tenant, identity, "essencial")
    await paid_pix(c)
    order_id, subscription_id = "synthetic-order-" + str(uuid4()), "synthetic-sub-" + str(uuid4())
    event = {"event_type": "purchase_approved", "external_order_id": order_id, "external_offer_id": "synthetic-offer",
             "external_subscription_id": subscription_id, "callback_hash": hashlib.sha256(token.encode()).hexdigest(),
             "amount_cents": 2990, "currency": "BRL"}
    order = {"id": order_id, "type": "subscription", "offer_type": "main", "status": "paid", "amount": "29.90",
             "currency": "BRL", "subscription": subscription_id, "sck": token,"paidAt":datetime.now(timezone.utc).isoformat()}
    remote = {"id": subscription_id, "offer": "synthetic-offer", "amount": "29.90", "recurrence_period": 30,
              "status": "active", "parent_order": order_id, "paymentMethod": "threeDs"}
    assert plans.first_cakto_subscription_confirmed(event, order, remote) is True
    assert await plans.apply_verified_cakto_payment(event, order, subscription=remote) == "manual_review"
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscriptions WHERE tenant_id=:tenant"), {"tenant": c.tenant})).scalar_one() == 0
        assert (await db.execute(text("SELECT state FROM orion_cakto_checkout_intents WHERE id=:id"), {"id": identity})).scalar_one() == "pending"
        assert (await db.execute(text("SELECT plan_code FROM orion_prepaid_pix_access WHERE tenant_id=:tenant"), {"tenant": c.tenant})).scalar_one() == "profissional"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["paid", "reserved", "created", "needs_review"])
async def test_pix_paid_days_or_uncertain_attempts_block_recurring_before_provider_reads(billing_db, state):
    c = billing_db
    if state == "paid":
        await paid_pix(c)
    else:
        async with c.engine.begin() as db:
            await pending_pix(db, c, state)
    with pytest.raises(HTTPException) as caught:
        await plans.start_plan_checkout(c.tenant, plans.CheckoutChoice(plan_code="essencial"),
                                        Actor("member", c.tenant, c.account, "owner"))
    assert caught.value.detail["code"] == "CHECKOUT_CHANNEL_CONFLICT"
    assert c.provider_posts == c.offer_reads == 0
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM orion_cakto_checkout_intents WHERE tenant_id=:tenant"),
                                 {"tenant": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_recurring_writer_waits_for_pix_reservation_then_rechecks_without_dual_intents(billing_db):
    c = billing_db
    offers_entered, release_offers, pix_reserved, release_pix = (asyncio.Event() for _ in range(4))

    async def pause_offers():
        offers_entered.set()
        await asyncio.wait_for(release_offers.wait(), 10)
    c.on_offers = pause_offers
    recurring = asyncio.create_task(plans.start_plan_checkout(c.tenant, plans.CheckoutChoice(plan_code="essencial"),
                                                             Actor("member", c.tenant, c.account, "owner")))
    await asyncio.wait_for(offers_entered.wait(), 10)

    async def reserve_pix():
        async with plans.support_session_factory()() as db:
            async with db.begin():
                await plans.require_checkout_available(db, c.tenant, c.account, channel="pix", lock="exclusive")
                await pending_pix(db, c)
                pix_reserved.set()
                await asyncio.wait_for(release_pix.wait(), 10)
    pix = asyncio.create_task(reserve_pix())
    try:
        await asyncio.wait_for(pix_reserved.wait(), 10)
        release_offers.set()
        await asyncio.sleep(0.1)
        assert not recurring.done(), "The exclusive company lock covers intent admission, not just its initial preflight"
    finally:
        release_pix.set()
        release_offers.set()
    await asyncio.wait_for(pix, 10)
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(recurring, 10)
    assert caught.value.detail["code"] == "CHECKOUT_CHANNEL_CONFLICT"
    async with c.engine.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM orion_prepaid_pix_orders WHERE tenant_id=:tenant"),
                                 {"tenant": c.tenant})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM orion_cakto_checkout_intents WHERE tenant_id=:tenant"),
                                 {"tenant": c.tenant})).scalar_one() == 0
    assert c.provider_posts == 0 and c.offer_reads == 1
