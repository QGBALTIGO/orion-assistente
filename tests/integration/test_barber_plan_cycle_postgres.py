"""Isolated PostgreSQL acceptance cases for Barbearia's subscription lifecycle."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import confirmed_billing, orion_welcome, plan_management, plan_trial_benefits
from app.cakto_subscription_lifecycle import reconcile_existing_cakto_subscription
from app.catalog_api import support_session_factory
from app.manual_plan_terms import calendar_end
from app.plan_permissions import derive_plan
from tests.integration.test_manual_plan_permissions_postgres import manual_db  # noqa: F401

pytestmark = pytest.mark.integration


def dt(value):
    return datetime.fromisoformat(value)


async def start_term(case, code, months):
    selection = {"plan_code": code, "duration_months": months}
    quote = await case.client.post(case.path + "/quote", json=selection)
    assert quote.status_code == 200, quote.text
    current = (await case.client.get(case.path)).json()["effective_plan"]
    term = quote.json()
    assert term["expected_revision"] == current["revision"]
    payload = {**selection, "transition": term["kind"], "confirmation": "ALTERAR PLANO",
               "operation_id": str(uuid4()), "expected_version": current["version"],
               "expected_revision": current["revision"]}
    return term, payload


@pytest.mark.asyncio
async def test_manual_calendar_term_renewal_and_scheduled_reduction(manual_db):
    c = manual_db
    async with c.engine.begin() as db:
        await db.execute(text("UPDATE tenants SET timezone='America/Cuiaba' WHERE id=:tenant"),
                         {"tenant": c.tenant})
    term, payload = await start_term(c, "elite", 3)
    assert term["kind"] == "activation" and term["timezone"] == "America/Cuiaba"
    rejected = await c.client.put(c.path, json={**payload, "access_type":"indefinite"})
    assert rejected.status_code == 422
    assert (await c.client.post(c.path + "/quote", json={"plan_code":"elite","duration_months":True})).status_code == 422
    responses = await asyncio.gather(c.client.put(c.path, json=payload), c.client.put(c.path, json=payload))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    first, second = [response.json() for response in responses]
    assert {first["replayed"], second["replayed"]} == {True, False}
    original = first["effective_plan"]["manual_assignment"]
    assert first["effective_plan"]["limits"]["max_professionals"] == 10
    assert dt(original["expires_at"]) == calendar_end(dt(original["started_at"]), 3, "America/Cuiaba")
    assert first["history"][0]["duration_months"] == 3 and first["history"][0]["actor_id"] == str(c.operator)

    term, payload = await start_term(c, "elite", 6)
    assert term["kind"] == "renewal" and dt(term["added_period_start"]) == dt(original["expires_at"])
    response = await c.client.put(c.path, json=payload)
    assert response.status_code == 200, response.text
    next_end = response.json()["effective_plan"]["valid_until"]
    assert dt(next_end) == calendar_end(dt(original["expires_at"]), 6, "America/Cuiaba")

    term, payload = await start_term(c, "essencial", 12)
    assert term["kind"] == "downgrade" and dt(term["starts_at"]) == dt(next_end)
    response = await c.client.put(c.path, json=payload)
    assert response.status_code == 200, response.text
    view = response.json()["effective_plan"]
    assert view["code"] == "elite" and view["scheduled_manual_assignment"]["plan_code"] == "essencial"
    async with c.engine.begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscriptions WHERE tenant_id=:t"),
                                 {"t": c.tenant})).scalar_one() == 0
        await db.execute(text("""
            UPDATE tenant_plan_controls SET manual_started_at=now()-interval '10 days',
                manual_expires_at=now()-interval '2 days', manual_next_starts_at=now()-interval '2 days',
                manual_next_ends_at=now()+interval '2 days'
            WHERE tenant_id=:tenant
        """), {"tenant": c.tenant})
    view = (await c.client.get(c.path)).json()["effective_plan"]
    assert view["code"] == "essencial" and view["limits"]["max_professionals"] == 2
    async with c.engine.begin() as db:
        await db.execute(text("""
            UPDATE tenant_plan_controls SET manual_next_ends_at=now()-interval '1 second'
            WHERE tenant_id=:tenant
        """), {"tenant": c.tenant})
    view = (await c.client.get(c.path)).json()["effective_plan"]
    assert not view["active"] and view["status"] == "expired"
    assert not view["permissions"]["agenda.manage"] and view["permissions"]["plan.manage"]


@pytest.mark.asyncio
async def test_automatic_trial_claim_and_welcome_consent_are_durable(manual_db, monkeypatch):
    c = manual_db
    monkeypatch.setenv("ORION_BARBER_PLAN_CYCLE", "1")
    monkeypatch.setenv("ORION_BARBER_SELF_SERVICE_TRIAL", "1")
    monkeypatch.setenv("ORION_TRIAL_IDENTITY_KEY", "synthetic-identity-key-for-role-test-10001")
    monkeypatch.setenv("ORION_WELCOME_WHATSAPP", "1")
    values = {"tenant": c.tenant, "account": c.account, "admin": c.other}
    base = f"/api/v1/tenants/{c.tenant}/management"
    try:
        async with c.engine.begin() as db:
            await db.execute(text("""
                INSERT INTO business_onboarding (tenant_id,created_by,configuration)
                VALUES (:tenant,:account,'{"niche":"barbearia","contact_phone":"+5567999990101"}')
            """), values)
        overview = await c.client.get(base+"/plan-overview")
        assert overview.status_code == 200, overview.text
        assert overview.json()["barbershop"]["trial_eligible"] and overview.json()["trial"] is None
        declined = await c.client.post(base+"/plan-trial/activate", json={"confirmed":False})
        assert declined.status_code == 422
        trials = await asyncio.gather(*[c.client.post(base+"/plan-trial/activate", json={"confirmed":True}) for _ in range(4)])
        assert all(trial.status_code == 200 for trial in trials), [trial.text for trial in trials]
        trial = trials[0].json()
        assert all(response.json() == trial for response in trials)
        assert dt(trial["ends_at"])-dt(trial["started_at"]) == timedelta(days=7)
        checked = (await c.client.get(base+"/plan-overview")).json()
        assert checked["effective_plan"]["source"] == "trial"
        assert checked["barbershop"]["trial_days"] == 7
        assert 0 < checked["barbershop"]["trial_remaining_seconds"] <= timedelta(days=7).total_seconds()
        assert not checked["barbershop"]["trial_eligible"]
        async with support_session_factory()() as db:
            async with db.begin():
                await plan_trial_benefits.finish_for_payment(db, c.tenant)
                for _ in range(2):
                    await orion_welcome.enqueue(db, c.tenant, c.account, "+5567999990101")
        response = await c.client.get(base+"/plan-overview")
        assert response.json()["trial"]["status"] == "superseded"
        assert response.json()["barbershop"]["welcome"] == {"phone_changed":False,"revoked":False,"status":"queued"}
        async with c.engine.begin() as db:
            await db.execute(text("""
                UPDATE business_onboarding SET configuration=jsonb_set(configuration,'{contact_phone}','"+5567999990102"')
                WHERE tenant_id=:tenant
            """), values)
        authorized = await c.client.put(base+"/plan-welcome", json={"authorized":True})
        assert authorized.status_code == 200, authorized.text
        async with support_session_factory()() as db:
            async with db.begin():
                job = await orion_welcome._claim(db, {"id": uuid4(), "tenant_id": c.other})
                assert job and job[1] == "+5567999990102"
                assert "equipe Orion" in job[2] and "Conta" in job[2]
        await orion_welcome._record(job[0], "uncertain")
        async with support_session_factory()() as db:
            async with db.begin():
                assert await orion_welcome._claim(db, {"id": uuid4(), "tenant_id": c.other}) is None
        async with c.engine.connect() as db:
            for table in ("orion_trial_benefits", "orion_welcome_outbox", "orion_confirmed_billing_state"):
                assert (await db.execute(text("SELECT has_table_privilege('app_runtime',:table,'SELECT')"),
                                         {"table": table})).scalar_one() is False
    finally:
        async with c.engine.begin() as db:
            await db.execute(text("DELETE FROM orion_welcome_outbox WHERE tenant_id=:tenant"), values)
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:tenant"), values)


def test_billing_hold_overrides_manual_and_test_and_keeps_recovery_routes():
    now = datetime.now(timezone.utc)
    snapshot = {"tenant_status":"active","niche":"barbearia",
                "manual":{"plan_code":"elite","started_at":now-timedelta(days=1),"expires_at":now+timedelta(days=1)},
                "trial":{"status":"active","started_at":now-timedelta(days=1),"ends_at":now+timedelta(days=1)},
                "confirmed_billing":{"channel":"cakto","hold":"refund_requested"}}
    state = derive_plan(snapshot, now)
    assert not state["active"] and state["status"] == "suspended"
    assert state["permissions"]["account.manage"] and not state["permissions"]["agenda.manage"]


def test_calendar_terms_at_month_end_leap_year_and_existing_local_hour():
    anchor = datetime(2028,1,31,16,30,tzinfo=timezone.utc)
    assert calendar_end(anchor, 1, "America/Cuiaba") == datetime(2028,2,29,16,30,tzinfo=timezone.utc)
    assert calendar_end(datetime(2028,2,29,16,30,tzinfo=timezone.utc), 12, "America/Cuiaba") == datetime(2029,2,28,16,30,tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_cakto_keeps_paid_days_and_restores_withdrawn_refund_of_current_order(manual_db, monkeypatch):
    c = manual_db
    monkeypatch.setenv("ORION_BARBER_PLAN_CYCLE", "1")
    first_paid = datetime.now(timezone.utc) - timedelta(days=20)
    second_paid = first_paid + timedelta(days=19)
    subscription_id, first_order, second_order, offer = [str(uuid4()) for _ in range(4)]
    keys = {"tenant": c.tenant, "account": c.account, "first": first_order, "offer": offer,
            "subscription": subscription_id, "paid": first_paid, "digest": c.tenant.hex * 2}
    try:
        async with c.engine.begin() as db:
            await db.execute(text("""
                INSERT INTO business_onboarding(tenant_id,created_by,configuration)
                VALUES(:tenant,:account,'{"niche":"barbearia"}')
            """), keys)
            record_id = (await db.execute(text("""
                INSERT INTO subscriptions(tenant_id,plan_id,external_subscription_id,status,last_verified_payment_at)
                SELECT :tenant,id,:subscription,'active',:paid FROM plans WHERE code='orion_essencial' RETURNING id
            """), keys)).scalar_one()
            await db.execute(text("""
                INSERT INTO orion_cakto_bindings(tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
                VALUES(:tenant,:id,:subscription,'essencial',:first)
            """), {**keys, "id": record_id})
            await db.execute(text("""
                INSERT INTO orion_cakto_checkout_intents
                    (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,
                     state,verified_at,expires_at,external_order_id)
                VALUES(:tenant,:account,'essencial',:offer,2990,:digest,'verified',:paid,now(),:first)
            """), keys)
        order = {"id":second_order,"subscription":subscription_id,"type":"subscription","offer_type":"main",
                 "status":"paid","currency":"BRL","amount":"29.90","paidAt":second_paid.isoformat()}
        event = {"event_type":"subscription_renewed","external_order_id":second_order,
                 "external_subscription_id":subscription_id,"external_offer_id":offer,"currency":"BRL","amount_cents":2990}
        provider_subscription = {"id":subscription_id,"offer":offer,"recurrence_period":30,"paymentMethod":"credit_card",
                                 "status":"active","amount":"29.90","parent_order":first_order,
                                 "next_payment_date":(first_paid+timedelta(days=60)).isoformat()}
        class ReadOnlyCakto:
            async def _get(self, path):
                assert path == "/subscriptions/" + subscription_id + "/"
                return provider_subscription
            async def get_order(self, identity):
                assert identity == second_order
                return order
        provider = ReadOnlyCakto()
        assert await reconcile_existing_cakto_subscription(event, order, provider) == "reconciled"
        assert await reconcile_existing_cakto_subscription(event, order, provider) == "already_verified"
        base = f"/api/v1/tenants/{c.tenant}/management/plan-overview"
        data = (await c.client.get(base)).json()
        assert dt(data["effective_plan"]["valid_until"]) == first_paid + timedelta(days=60)
        assert dt(data["barbershop"]["next_payment_at"]) == first_paid + timedelta(days=60)
        hold = {**event, "event_type":"refund_requested"}
        older = {**hold,"external_order_id":first_order}
        assert await reconcile_existing_cakto_subscription(
            older,{**order,"id":first_order,"status":"refund_requested"},provider
        ) == "manual_review"
        assert await reconcile_existing_cakto_subscription(hold,{**order,"status":"refund_requested"},provider) == "reconciled"
        data = (await c.client.get(base)).json()
        assert data["effective_plan"]["billing"]["hold"] == "refund_requested"
        assert data["subscription"]["status"] == "active" and data["effective_plan"]["status"] == "suspended"
        async with c.engine.begin() as db:
            await db.execute(text("""
                UPDATE orion_confirmed_billing_state SET checked_at=now()-interval '10 minutes'
                WHERE tenant_id=:tenant
            """), keys)
        await confirmed_billing.recover_withdrawn_refunds(provider)
        data = (await c.client.get(base)).json()
        assert data["effective_plan"]["active"] is True
        assert dt(data["effective_plan"]["valid_until"]) == first_paid + timedelta(days=60)
    finally:
        async with c.engine.begin() as db:
            await db.execute(text("DELETE FROM orion_confirmed_billing_state WHERE tenant_id=:tenant"), keys)
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:tenant"), keys)


@pytest.mark.asyncio
async def test_checkout_after_canceled_paid_period_preserves_orders_and_ignores_late_old_events(manual_db, monkeypatch):
    c = manual_db
    monkeypatch.setenv("ORION_BARBER_PLAN_CYCLE", "1")
    monkeypatch.setenv("ORION_BARBER_RECONTRACT_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", "isolated-checkout-hmac-key-" + "a" * 32)
    monkeypatch.setattr(plan_management, "embedded_checkout_enabled", lambda: True)
    checkout_url = plan_management.CODES["profissional"]["checkout_url"]
    old_sub, old_order, old_offer, new_sub, new_order, new_offer = [str(uuid4()) for _ in range(6)]
    now = datetime.now(timezone.utc)
    old_paid = now - timedelta(days=40)
    old_end = old_paid + timedelta(days=30)
    keys = {"tenant": c.tenant, "account": c.account, "subscription": old_sub,
            "order": old_order, "offer": old_offer, "paid": old_paid, "digest": c.tenant.hex * 2}
    provider = {"id": old_sub, "parent_order": old_order, "offer": old_offer,
                "status": "active", "amount": "29.90", "recurrence_period": 30}
    provider_reads = []

    class ReadOnlyCakto:
        async def list_offers(self):
            return [{"id": new_offer, "name": "Orion Profissional", "type": "subscription",
                     "price": "89.90", "status": "active", "recurrence_period": 30}]

        async def _get(self, path):
            assert path == f"/subscriptions/{old_sub}/"
            provider_reads.append(path)
            return dict(provider)

    cakto = ReadOnlyCakto()
    monkeypatch.setattr(plan_management, "cached_cakto_client", lambda: cakto)

    async def verify_embedded_checkout(client, offers):
        assert client is cakto and offers[0]["id"] == new_offer

    monkeypatch.setattr(plan_management, "verify_embedded_checkout", verify_embedded_checkout)
    base = f"/api/v1/tenants/{c.tenant}/management/plan"
    payload = {"embedded": True, "plan_code": "profissional", "expected_cents": 8990}
    try:
        async with c.engine.begin() as db:
            await db.execute(text("""
                INSERT INTO business_onboarding(tenant_id,created_by,configuration)
                VALUES(:tenant,:account,'{"niche":"barbearia"}')
            """), keys)
            row_id = (await db.execute(text("""
                INSERT INTO subscriptions(tenant_id,plan_id,external_subscription_id,status,last_verified_payment_at)
                SELECT :tenant,id,:subscription,'active',:paid FROM plans WHERE code='orion_essencial' RETURNING id
            """), keys)).scalar_one()
            await db.execute(text("""
                INSERT INTO orion_cakto_bindings(tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
                VALUES(:tenant,:id,:subscription,'essencial',:order)
            """), {**keys, "id": row_id})
            await db.execute(text("""
                INSERT INTO orion_cakto_checkout_intents
                  (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,
                   state,verified_at,expires_at,external_order_id)
                VALUES(:tenant,:account,'essencial',:offer,2990,:digest,'verified',:paid,now(),:order)
            """), keys)
            await confirmed_billing.record(db, c.tenant, old_order, "cakto", old_paid, 2990,
                                           start=old_paid, end=old_end)

        assert not (await c.client.get(base + "-overview")).json()["barbershop"]["can_recontract"]
        assert (await c.client.post(base + "-checkout", json=payload)).status_code == 409
        async with c.engine.begin() as db:
            await db.execute(text("""
                UPDATE subscriptions SET status='canceled',grace_until=:end WHERE tenant_id=:tenant
            """), {**keys, "end": now + timedelta(days=2)})
        assert not (await c.client.get(base + "-overview")).json()["barbershop"]["can_recontract"]
        assert (await c.client.post(base + "-checkout", json=payload)).status_code == 409
        assert provider_reads == []
        async with c.engine.begin() as db:
            await db.execute(text("""
                UPDATE subscriptions SET grace_until=:end WHERE tenant_id=:tenant
            """), {**keys, "end": old_end})
        ended = (await c.client.get(base + "-overview")).json()["barbershop"]
        assert ended["can_recontract"] and dt(ended["previous_period_ends_at"]) == old_end
        assert (await c.client.post(base + "-checkout", json=payload)).status_code == 409
        provider.update(status="canceled", parent_order=new_order)
        assert (await c.client.post(base + "-checkout", json=payload)).status_code == 409
        provider["parent_order"] = old_order
        link = await c.client.post(base + "-checkout", json=payload)
        assert link.status_code == 200, link.text
        assert (await c.client.post(base + "-checkout", json=payload)).json() == link.json()

        paid = datetime.now(timezone.utc)
        callback = parse_qs(urlsplit(link.json()["checkout_url"]).query)["callback"][0]
        event = {"event_type": "purchase_approved", "external_order_id": new_order,
                 "external_subscription_id": new_sub, "external_offer_id": new_offer, "currency": "BRL",
                 "amount_cents": 8990, "callback_hash": hashlib.sha256(callback.encode()).hexdigest()}
        order = {"id": new_order, "subscription": new_sub, "offer": {"id": new_offer}, "status": "paid",
                 "type": "subscription", "offer_type": "main", "checkoutUrl": checkout_url,
                 "currency": "BRL", "amount": "89.90", "sck": callback, "paidAt": paid.isoformat()}
        subscription = {"id": new_sub, "offer": new_offer, "recurrence_period": 30, "paymentMethod": "credit_card",
                        "status": "active", "amount": "89.90", "parent_order": new_order}
        provider["status"] = "active"
        assert await plan_management.apply_verified_cakto_payment(event, order, subscription) == "manual_review"
        provider["status"] = "canceled"
        assert await plan_management.apply_verified_cakto_payment(event, order, subscription) == "activated"
        assert await plan_management.apply_verified_cakto_payment(event, order, subscription) == "already_verified"
        late_old = {"event_type": "subscription_canceled", "external_subscription_id": old_sub,
                    "external_order_id": old_order, "external_offer_id": old_offer}
        assert await reconcile_existing_cakto_subscription(late_old, {"subscription": old_sub}, cakto) == "unlinked"
        result = (await c.client.get(base + "-overview")).json()
        assert result["effective_plan"]["code"] == "profissional"
        assert dt(result["effective_plan"]["valid_until"]) == paid + timedelta(days=30)
        async with c.engine.connect() as db:
            assert (await db.execute(text("""
                SELECT external_subscription_id FROM subscriptions WHERE tenant_id=:tenant
            """), keys)).scalar_one() == new_sub
            preserved = (await db.execute(text("""
                SELECT external_order_id FROM orion_cakto_checkout_intents
                WHERE tenant_id=:tenant AND state='verified'
            """), keys)).scalars().all()
            assert set(preserved) == {old_order, new_order}
    finally:
        async with c.engine.begin() as db:
            await db.execute(text("DELETE FROM orion_confirmed_billing_state WHERE tenant_id=:tenant"), keys)
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:tenant"), keys)
