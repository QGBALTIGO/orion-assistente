"""Management persistence, tenant isolation and concurrent mutations on PostgreSQL."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.catalog_api import Actor, authenticate, router as catalog_router, support_session_factory
from app.database import get_engine
from app.management_api import router
from app.management_preferences import router as preferences_router

pytestmark = pytest.mark.integration


def async_url(url):
    return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture
async def management_db(monkeypatch):
    names = ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")
    if not all(os.getenv(name) for name in names):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Management tests require real PostgreSQL runtime/migrator/support URLs")
        pytest.skip("Requires PostgreSQL with the management migration")
    app = FastAPI()
    app.include_router(router)
    app.include_router(preferences_router)
    app.include_router(catalog_router)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "management-test-support-token-of-at-least-32-characters")
    monkeypatch.setenv("VIEWER_API_TOKEN", "management-test-viewer-token-of-at-least-32-characters")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    a, b, pro, other_pro, service, account = [uuid4() for _ in range(6)]
    monkeypatch.setenv("VIEWER_TENANT_ID", str(a))
    get_engine.cache_clear()
    support_session_factory.cache_clear()
    migrator = create_async_engine(async_url(os.environ["MIGRATION_DATABASE_URL"]))
    runtime = create_async_engine(async_url(os.environ["APP_DATABASE_URL"]))
    support = create_async_engine(async_url(os.environ["SUPPORT_DATABASE_URL"]))
    now = datetime.now(timezone.utc)
    async with migrator.begin() as db:
        for tenant in (a, b):
            await db.execute(text("INSERT INTO tenants (id,name,slug,timezone) VALUES (:id,'Empresa sintética',:slug,'America/Sao_Paulo')"),
                             {"id": tenant, "slug": "mgmt-" + str(tenant)})
        for tenant, identity in ((a, pro), (b, other_pro)):
            await db.execute(text("INSERT INTO professionals (id,tenant_id,name,commission_percent) VALUES (:id,:tenant,'Profissional de teste',25)"),
                             {"id": identity, "tenant": tenant})
        await db.execute(text("INSERT INTO services (id,tenant_id,name,duration_min,price_cents) VALUES (:id,:tenant,'Corte sintético',30,10000)"),
                         {"id": service, "tenant": a})
        await db.execute(text("INSERT INTO panel_accounts (id,name,email,password_hash) VALUES (:id,'Conta sintética',:email,'test-not-a-real-password-hash')"),
                         {"id": account, "email": str(account) + "@example.invalid"})
        await db.execute(text("INSERT INTO tenant_memberships (tenant_id,account_id,role) VALUES (:tenant,:account,'owner')"),
                         {"tenant": a, "account": account})
    headers = {"Authorization": "Bearer " + os.environ["SUPPORT_API_TOKEN"]}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers) as client:
            yield {"a": a, "b": b, "pro": pro, "other_pro": other_pro, "service": service, "account": account,
                   "runtime": runtime, "migrator": migrator, "support": support, "app": app, "client": client,
                   "base": f"/api/v1/tenants/{a}/management", "other_base": f"/api/v1/tenants/{b}/management",
                   "now": now, "period": {"start_date": str((now - timedelta(days=120)).date()),
                                            "end_date": str((now + timedelta(days=10)).date())}}
    finally:
        async with migrator.begin() as db:
            for table in ("commission_advance_allocations", "commission_settlements", "loyalty_transactions", "loyalty_coupons", "financial_entries", "loyalty_rules", "professional_goals", "management_requests",
                          "management_site_preferences", "management_preferences", "campaigns", "appointment_history", "appointment_items", "calendar_outbox",
                          "appointments", "customers", "working_breaks", "working_hours", "professional_services", "services", "professionals", "tenant_audit", "tenant_memberships"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": a, "b": b})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": a, "b": b})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:id"), {"id": account})
        await migrator.dispose()
        await runtime.dispose()
        await support.dispose()
        await get_engine().dispose()
        get_engine.cache_clear()
        support_session_factory.cache_clear()


async def customer(d, *, name="Cliente sintético", other=False, **fields):
    response = await d["client"].post((d["other_base"] if other else d["base"]) + "/customers",
        json={"name": name, "idempotency_key": str(uuid4()), **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def appointment(d, customer_id, *, status="confirmed", start_at=None, amount=10000, professional_id=None, service_id=None):
    identity = uuid4()
    starts = start_at or d["now"] - timedelta(hours=3)
    ends = starts + timedelta(minutes=30)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO appointments (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
                start_at,end_at,occupied_from,occupied_until,status,hold_expires_at,total_cents)
            VALUES (:id,:tenant,:customer,:pro,:protocol,:key,:fingerprint,:starts,:ends,:starts,:ends,:status,:expiry,:amount)
        """), {"id": identity, "tenant": d["a"], "customer": UUID(customer_id), "pro": professional_id or d["pro"],
               "protocol": "AG-" + identity.hex[:6].upper(), "key": str(identity), "fingerprint": "0" * 64,
               "starts": starts, "ends": ends, "status": status, "expiry": d["now"] + timedelta(minutes=5) if status == "hold" else None, "amount": amount})
        await db.execute(text("""
            INSERT INTO appointment_items (tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,price_snapshot_cents,
                prep_snapshot_min,cleanup_snapshot_min,item_order) VALUES (:tenant,:id,:service,'Corte sintético',30,:amount,0,0,0)
        """), {"tenant": d["a"], "id": identity, "service": service_id or d["service"], "amount": amount})
    return str(identity)


async def entry(d, **fields):
    payload = {"kind": "income", "description": "Lançamento sintético", "amount_cents": 1000,
               "occurred_at": d["now"].isoformat(), "idempotency_key": str(uuid4()), **fields}
    response = await d["client"].post(d["base"] + "/finance", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_onboarding_uses_lifetime_scoped_facts_and_current_configuration(management_db):
    d = management_db
    today = str(d["now"].astimezone(ZoneInfo("America/Sao_Paulo")).date())

    async def snapshot(day=today):
        response = await d["client"].get(d["base"] + "/dashboard", params={"start_date": day, "end_date": day})
        assert response.status_code == 200, response.text
        return response.json()

    other_customer = await customer(d, other=True, phone="11912345678")
    other_service = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO services (id,tenant_id,name,duration_min,price_cents) VALUES (:id,:tenant,'Outro serviço',30,1000)"),
                         {"id": other_service, "tenant": d["b"]})
        await db.execute(text("INSERT INTO professional_services (tenant_id,professional_id,service_id) VALUES (:tenant,:pro,:service)"),
                         {"tenant": d["b"], "pro": d["other_pro"], "service": other_service})
        await db.execute(text("INSERT INTO working_hours (tenant_id,professional_id,weekday,start_time,end_time) VALUES (:tenant,:pro,0,'09:00','18:00')"),
                         {"tenant": d["b"], "pro": d["other_pro"]})
    await appointment({**d, "a": d["b"], "pro": d["other_pro"], "service": other_service}, other_customer["id"], status="completed")
    assert (await snapshot())["onboarding"] == {"has_appointment": False, "scheduling_ready": False, "customers_with_phone": 0}

    person = await customer(d)  # A name alone must not count as a contactable client.
    await customer(d, phone="11912345671", active=False)
    deleted = await customer(d, phone="11912345672")
    response = await d["client"].request("DELETE", d["base"] + "/customers/" + deleted["id"], json={"expected_version": 1})
    assert response.status_code == 200
    await customer(d, phone="11912345673")
    visit = await appointment(d, person["id"], status="hold", start_at=d["now"] - timedelta(days=30))
    assert (await snapshot())["onboarding"] == {"has_appointment": False, "scheduling_ready": False, "customers_with_phone": 1}

    # The first confirmation remains a fact after cancellation and outside the
    # selected report period. Merely reserving a hold did not complete it.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET status='cancelled',hold_expires_at=NULL WHERE id=:id"), {"id": UUID(visit)})
        await db.execute(text("INSERT INTO appointment_history (tenant_id,appointment_id,event,operator) VALUES (:tenant,:id,'confirmed','support')"),
                         {"tenant": d["a"], "id": UUID(visit)})
        await db.execute(text("INSERT INTO professional_services (tenant_id,professional_id,service_id) VALUES (:tenant,:pro,:service)"),
                         {"tenant": d["a"], "pro": d["pro"], "service": d["service"]})
    current = await snapshot()
    assert current["summary"]["appointments"] == 0
    assert current["onboarding"] == {"has_appointment": True, "scheduling_ready": False, "customers_with_phone": 1}
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO working_hours (tenant_id,professional_id,weekday,start_time,end_time) VALUES (:tenant,:pro,0,'09:00','18:00')"),
                         {"tenant": d["a"], "pro": d["pro"]})
    assert (await snapshot())["onboarding"]["scheduling_ready"] is True
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET accepts_appointments=false WHERE id=:id"), {"id": d["pro"]})
    assert (await snapshot())["onboarding"]["scheduling_ready"] is False
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET accepts_appointments=true WHERE id=:id"), {"id": d["pro"]})
        await db.execute(text("UPDATE services SET active=false WHERE id=:id"), {"id": d["service"]})
    assert (await snapshot())["onboarding"]["scheduling_ready"] is False
    tomorrow = str(d["now"].astimezone(ZoneInfo("America/Sao_Paulo")).date() + timedelta(days=1))
    assert (await snapshot(tomorrow))["onboarding"] == {"has_appointment": True, "scheduling_ready": False, "customers_with_phone": 1}


@pytest.mark.asyncio
async def test_customer_crud_idempotency_nullable_phone_search_and_export(management_db):
    d = management_db
    c, base = d["client"], d["base"]
    payload = {"name": "=HYPERLINK(\"x\")", "instagram": "@cliente", "city": "São Paulo", "idempotency_key": "customer-same-key"}
    original = await c.post(base + "/customers", json=payload)
    replay = await c.post(base + "/customers", json=payload)
    assert original.status_code == replay.status_code == 201
    item = original.json()
    assert item["id"] == replay.json()["id"] and item["phone"] is None
    assert (await c.post(base + "/customers", json={**payload, "name": "Diferente"})).status_code == 409
    await customer(d, name="Outro cliente sem telefone")
    assert (await c.get(base + "/customers")).json()["total"] == 2
    assert (await c.get(d["other_base"] + "/customers")).json()["items"] == []
    changed = await c.patch(base + "/customers/" + item["id"], json={"phone": "(11) 91234-5678", "active": False, "expected_version": 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()["phone"] == "+5511912345678" and changed.json()["version"] == 2
    assert (await c.patch(base + "/customers/" + item["id"], json={"name": "Stale", "expected_version": 1})).status_code == 409
    filtered = await c.get(base + "/customers", params={"status": "inactive", "q": "HYPERLINK"})
    assert filtered.status_code == 200 and filtered.json()["total"] == 1
    exported = await c.get(base + "/customers/export.csv", params={"status": "inactive"})
    assert exported.status_code == 200 and "'=HYPERLINK" in exported.text
    removed = await c.request("DELETE", base + "/customers/" + item["id"], json={"expected_version": 2})
    assert removed.status_code == 200 and removed.json()["deleted_at"]
    assert (await c.get(base + "/customers/" + item["id"])).status_code == 404
    assert (await c.get(base + "/customers")).json()["total"] == 1
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 2


@pytest.mark.asyncio
async def test_management_roles_and_verified_membership_scope(management_db):
    d = management_db
    c, base = d["client"], d["base"]
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    assert (await c.get(base + "/dashboard", headers=viewer)).status_code == 200
    assert (await c.get(d["other_base"] + "/dashboard", headers=viewer)).status_code == 403
    assert (await c.post(base + "/customers", headers=viewer, json={"name": "No", "idempotency_key": "viewer-blocked-key"})).status_code == 403
    # Even a forged cached actor scope is ignored in favor of the persisted membership.
    async def member_actor():
        return Actor("member", tenant_id=d["b"], account_id=d["account"], membership_role="owner")
    d["app"].dependency_overrides[authenticate] = member_actor
    assert (await c.get(base + "/subscription")).status_code == 200
    assert (await c.get(d["other_base"] + "/subscription")).status_code == 403
    result = await c.post(base + "/customers", json={"name": "Membro autorizado", "idempotency_key": "member-allowed-key"})
    assert result.status_code == 201, result.text
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE account_id=:account"), {"account": d["account"]})
    assert (await c.get(base + "/customers")).status_code == 403


@pytest.mark.asyncio
async def test_rls_fail_closed_cross_tenant_fk_and_append_only_grants(management_db):
    d = management_db
    a_customer = await customer(d)
    b_customer = await customer(d, other=True)
    item = await entry(d, customer_id=a_customer["id"])
    assert (await d["client"].post(d["base"] + "/finance", json={"kind": "expense", "description": "Wrong scope", "amount_cents": 100,
        "occurred_at": d["now"].isoformat(), "customer_id": b_customer["id"], "idempotency_key": "cross-scope-key"})).status_code == 404
    async with d["runtime"].begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM financial_entries"))).scalar_one() == 0
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE id=:id"), {"id": UUID(item["id"])})).scalar_one() == 0
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            await db.execute(text("""INSERT INTO financial_entries (tenant_id,kind,description,amount_cents,occurred_at,customer_id)
                VALUES (:tenant,'income','Bad scoped FK',100,now(),:customer)"""), {"tenant": d["a"], "customer": UUID(b_customer["id"])})
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
            await db.execute(text("""INSERT INTO financial_entries (tenant_id,kind,description,amount_cents,occurred_at)
                VALUES (:tenant,'income','Bad RLS',100,now())"""), {"tenant": d["a"]})
    for command in ("UPDATE loyalty_transactions SET reason='changed'", "DELETE FROM financial_entries"):
        with pytest.raises(DBAPIError):
            async with d["runtime"].begin() as db:
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
                await db.execute(text(command))
    with pytest.raises(DBAPIError):
        async with d["support"].begin() as db:
            await db.execute(text("SELECT * FROM financial_entries"))
    async with d["migrator"].connect() as db:
        flags = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('financial_entries','loyalty_transactions','management_requests','management_site_preferences')"))).all()
        assert len(flags) == 4 and all(enabled and forced for enabled, forced in flags)


@pytest.mark.asyncio
async def test_concurrent_ledger_creation_and_versioned_cancellation(management_db):
    d = management_db
    payload = {"kind": "income", "description": "Idempotente", "amount_cents": 2500, "payment_method": "pix",
               "occurred_at": d["now"].isoformat(), "idempotency_key": "concurrent-ledger-key"}
    responses = await asyncio.gather(*(d["client"].post(d["base"] + "/finance", json=payload) for _ in range(8)))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    assert len({response.json()["id"] for response in responses}) == 1
    await entry(d, kind="expense", amount_cents=500, category="Materiais")
    await entry(d, amount_cents=700, status="pending")
    result = (await d["client"].get(d["base"] + "/finance", params=d["period"])).json()
    assert result["total"] == 3
    assert result["summary"]["income_cents"] == 2500
    assert result["summary"]["expense_cents"] == 500
    assert result["summary"]["pending_income_cents"] == 700
    assert result["summary"]["balance_cents"] == 2000
    identity = responses[0].json()["id"]
    cancelled = await d["client"].request("DELETE", d["base"] + "/finance/" + identity, json={"expected_version": 1})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    stale = await d["client"].patch(d["base"] + "/finance/" + identity, json={"amount_cents": 4000, "expected_version": 1})
    assert stale.status_code == 409
    exported = await d["client"].get(d["base"] + "/finance/export.csv", params=d["period"])
    assert exported.status_code == 200 and "Materiais" in exported.text


@pytest.mark.asyncio
async def test_completion_snapshot_history_idempotency_and_future_guard(management_db):
    d = management_db
    person = await customer(d)
    visit = await appointment(d, person["id"])
    payload = {"expected_status": "confirmed", "idempotency_key": "complete-once-key"}
    results = await asyncio.gather(*(d["client"].post(d["base"] + f"/appointments/{visit}/complete", json=payload) for _ in range(6)))
    assert all(response.status_code == 200 for response in results), [response.text for response in results]
    assert results[0].json()["status"] == "completed" and results[0].json()["commission_cents"] == 2500
    async with d["migrator"].begin() as db:
        history = (await db.execute(text("SELECT event,operator FROM appointment_history WHERE tenant_id=:tenant AND appointment_id=:id"),
                                   {"tenant": d["a"], "id": UUID(visit)})).all()
        assert history == [("completed", "support")]
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT appointment_id,action,status FROM calendar_outbox WHERE tenant_id=:tenant"),
                                {"tenant": d["a"]})).all() == [(UUID(visit), "update", "pending")]
        await db.execute(text("UPDATE professionals SET commission_percent=50 WHERE id=:id"), {"id": d["pro"]})
    report = (await d["client"].get(d["base"] + "/finance/commissions", params=d["period"])).json()
    assert report["items"][0]["earned_cents"] == 2500 and report["items"][0]["legacy_rate_estimates"] == 0
    daily = await d["client"].get(d["base"] + "/performance/daily", params={**d["period"], "professional_id": str(d["pro"])})
    assert daily.status_code == 200, daily.text
    assert daily.json()["items"][0]["commission_cents"] == 2500 and daily.json()["items"][0]["completed"] == 1
    future = await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    assert (await d["client"].post(d["base"] + f"/appointments/{future}/complete", json={"idempotency_key": "future-key-001"})).status_code == 409
    absent = await appointment(d, person["id"], start_at=d["now"] - timedelta(hours=1))
    response = await d["client"].post(d["base"] + f"/appointments/{absent}/no-show", json={"idempotency_key": "absent-key-001"})
    assert response.status_code == 200 and response.json()["status"] == "no_show" and response.json()["commission_cents"] is None
    assert (await d["client"].post(d["other_base"] + f"/appointments/{visit}/complete", json=payload)).status_code == 404
    dash = (await d["client"].get(d["base"] + "/dashboard", params=d["period"])).json()
    assert dash["summary"]["completed"] == 1 and dash["summary"]["no_show"] == 1
    assert dash["summary"]["completed_revenue_cents"] == 10000
    assert dash["services"][0]["revenue_cents"] == 10000


@pytest.mark.asyncio
async def test_dre_and_advances_do_not_double_count_commission_or_linked_revenue(management_db):
    d = management_db
    person = await customer(d)
    visit = await appointment(d, person["id"])
    assert (await d["client"].post(d["base"] + f"/appointments/{visit}/complete", json={"idempotency_key": "dre-complete-key"})).status_code == 200
    await entry(d, amount_cents=10000, appointment_id=visit, customer_id=person["id"])
    await entry(d, amount_cents=800, description="Venda avulsa")
    await entry(d, kind="expense", amount_cents=500, category="Materiais")
    await entry(d, kind="expense", amount_cents=1000, category="Vale", entry_type="advance", professional_id=str(d["pro"]))
    await entry(d, kind="expense", amount_cents=500, category="Comissão", entry_type="commission_payment", professional_id=str(d["pro"]))
    result = (await d["client"].get(d["base"] + "/finance/dre", params=d["period"])).json()["summary"]
    assert result["gross_revenue_cents"] == 10800
    assert result["commissions_cents"] == 2500 and result["operating_expenses_cents"] == 500
    assert result["net_result_cents"] == 7800
    report = (await d["client"].get(d["base"] + "/finance/commissions", params=d["period"])).json()["items"][0]
    assert report["balance_cents"] == 1000 and report["advances_cents"] == 1000 and report["payments_cents"] == 500
    filtered = await d["client"].get(d["base"] + "/finance", params={**d["period"], "entry_type": "advance", "professional_id": str(d["pro"])})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1 and filtered.json()["summary"]["average_ticket_cents"] == 10000


@pytest.mark.asyncio
async def test_point_ledger_serializes_redemptions_and_prevents_duplicate_credits(management_db):
    d = management_db
    person = await customer(d)
    base = d["base"] + "/loyalty/transactions"
    credit = {"customer_id": person["id"], "points_delta": 10, "reason": "Crédito manual", "idempotency_key": "credit-once-key"}
    responses = await asyncio.gather(*(d["client"].post(base, json=credit) for _ in range(5)))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    assert len({response.json()["id"] for response in responses}) == 1
    debits = await asyncio.gather(*(d["client"].post(base, json={**credit, "points_delta": -8, "reason": "Resgate", "idempotency_key": f"debit-key-{index:03}"}) for index in range(2)))
    assert sorted(response.status_code for response in debits) == [201, 409]
    history = (await d["client"].get(base, params={"customer_id": person["id"]})).json()
    assert history["total"] == 2 and sum(item["points_delta"] for item in history["items"]) == 2
    assert (await d["client"].get(d["other_base"] + "/loyalty/transactions")).json()["total"] == 0


@pytest.mark.asyncio
async def test_risk_birthday_filters_goals_and_drafts_use_persisted_data(management_db):
    d = management_db
    person = await customer(d, name="Cliente em risco", birth_date=d["now"].astimezone(ZoneInfo("America/Sao_Paulo")).replace(year=2000).date().isoformat())
    never_visited = await customer(d, name="Nunca visitou")
    await appointment(d, person["id"], status="completed", start_at=d["now"] - timedelta(days=45))
    response = await d["client"].get(d["base"] + "/customers", params={"status": "at_risk", "risk_days": 30})
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {person["id"]}
    assert (await d["client"].get(d["base"] + "/customers", params={"status": "at_risk", "risk_days": 60})).json()["total"] == 0
    birthday = await d["client"].get(d["base"] + "/customers", params={"status": "birthday"})
    assert birthday.status_code == 200 and birthday.json()["total"] == 1
    await appointment(d, person["id"], start_at=d["now"] + timedelta(days=1))
    assert (await d["client"].get(d["base"] + "/customers", params={"status": "at_risk"})).json()["total"] == 0
    goal = await d["client"].post(d["base"] + "/goals", json={"professional_id": str(d["pro"]), "metric": "appointments",
        **d["period"], "target_value": 2, "idempotency_key": "goal-counter-key"})
    assert goal.status_code == 201, goal.text
    targets = (await d["client"].get(d["base"] + "/goals", params=d["period"])).json()["items"]
    assert targets[0]["actual_value"] == 1 and targets[0]["progress_percent"] == 50
    preference = await d["client"].put(d["base"] + "/site", json={"title": "Nossa empresa", "slug": "draft-" + str(d["a"]), "expected_version": 0})
    assert preference.status_code == 200 and preference.json()["published"] is False
    assert (await d["client"].put(d["base"] + "/site", json={"title": "Stale", "expected_version": 0})).status_code == 409
    draft = await d["client"].post(d["base"] + "/marketing", json={"name": "Campanha", "message": "Olá", "idempotency_key": "marketing-draft-key"})
    assert draft.status_code == 201 and draft.json()["status"] == "draft"
    campaigns = (await d["client"].get(d["base"] + "/marketing")).json()
    assert campaigns["delivery_enabled"] is False and campaigns["total"] == 1
    subscription = (await d["client"].get(d["base"] + "/subscription")).json()
    assert subscription["subscription"] is None and subscription["checkout_enabled"] is False
    assert never_visited["id"] != person["id"]


async def loyalty_rule(d, **fields):
    payload = {"name": "Programa sintético", "earning_mode": "both", "points_per_visit": 3,
        "spend_step_cents": 100, "points_per_step": 1, "reward_points": 50, "reward_description": "Desconto de dez por cento",
        "reward_type": "percent", "reward_percent": 10, "reward_valid_days": 30, "active": True, "idempotency_key": str(uuid4()), **fields}
    response = await d["client"].post(d["base"] + "/loyalty/rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


@pytest.mark.asyncio
async def test_automatic_loyalty_issues_one_coupon_preserves_excess_and_redeems_at_completion(management_db):
    d = management_db
    person = await customer(d)
    rule, _ = await loyalty_rule(d)
    visit = await appointment(d, person["id"])
    payload = {"idempotency_key": "automatic-loyalty-once"}
    responses = await asyncio.gather(*(d["client"].post(d["base"] + f"/appointments/{visit}/complete", json=payload) for _ in range(5)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    coupons = (await d["client"].get(d["base"] + "/loyalty/coupons", params={"customer_id": person["id"], "status": "available"})).json()
    assert coupons["total"] == 1
    coupon = coupons["items"][0]
    assert coupon["points_spent"] == 50 and coupon["rule_version"] == rule["version"]
    client = (await d["client"].get(d["base"] + "/customers/" + person["id"])).json()
    assert client["loyalty_points"] == 53  # 3 per visit + 100 per spend - one reward of 50.
    near = (await d["client"].get(d["base"] + "/loyalty/ranking", params={"near_reward": "true"})).json()
    assert near["total"] == 1 and near["items"][0]["points"] == 53
    second = await appointment(d, person["id"], start_at=d["now"] - timedelta(hours=2))
    applied = await d["client"].post(d["base"] + f"/appointments/{second}/complete", json={"coupon_id": coupon["id"], "idempotency_key": "coupon-redeem-key"})
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert (result["original_total_cents"], result["discount_cents"], result["total_cents"], result["commission_cents"]) == (10000, 1000, 9000, 2250)
    assert result["loyalty"]["points_earned"] == 93 and result["loyalty"]["points_balance"] == 96
    used = (await d["client"].get(d["base"] + "/loyalty/coupons", params={"status": "redeemed"})).json()
    assert used["total"] == 1 and used["items"][0]["redeemed_appointment_id"] == second
    summary = (await d["client"].get(d["base"] + "/loyalty")).json()
    assert summary["automatic_accrual_enabled"] and summary["max_automatic_coupons_per_appointment"] == 1
    assert summary["summary"]["coupons_generated"] == 2 and summary["summary"]["coupons_used"] == 1
    dashboard = (await d["client"].get(d["base"] + "/dashboard", params=d["period"])).json()
    assert dashboard["summary"]["completed_revenue_cents"] == 19000
    service = dashboard["services"][0]
    assert (service["gross_revenue_cents"], service["discount_cents"], service["revenue_cents"]) == (20000, 1000, 19000)
    assert (await d["client"].get(d["other_base"] + "/loyalty/coupons")).json()["total"] == 0


@pytest.mark.asyncio
async def test_rule_activation_is_atomic_and_replay_does_not_reactivate_older_program(management_db):
    d = management_db
    first, original_payload = await loyalty_rule(d)
    second, _ = await loyalty_rule(d, name="Programa novo", reward_type="fixed", reward_value_cents=500)
    response = await d["client"].patch(d["base"] + "/loyalty/rules/" + first["id"], json={"active": True, "expected_version": first["version"]})
    assert response.status_code == 409
    replay = await d["client"].post(d["base"] + "/loyalty/rules", json=original_payload)
    assert replay.status_code == 201 and replay.json()["active"] is False
    rules = (await d["client"].get(d["base"] + "/loyalty")).json()["rules"]
    assert [rule["id"] for rule in rules if rule["active"]] == [second["id"]]


@pytest.mark.asyncio
async def test_coupon_consumption_concurrent_across_professionals_is_single_use(management_db):
    d = management_db
    person = await customer(d)
    await loyalty_rule(d, earning_mode="visit", points_per_visit=1, reward_points=10, reward_type="fixed", reward_value_cents=4000)
    credit = await d["client"].post(d["base"] + "/loyalty/transactions", json={"customer_id": person["id"], "points_delta": 10,
        "reason": "Saldo anterior", "idempotency_key": "previous-points-key"})
    assert credit.status_code == 201
    issued = await asyncio.gather(*(d["client"].post(d["base"] + "/loyalty/coupons", json={"customer_id": person["id"], "idempotency_key": "manual-coupon-once"}) for _ in range(4)))
    assert all(response.status_code == 201 for response in issued), [response.text for response in issued]
    assert len({response.json()["id"] for response in issued}) == 1
    coupon = issued[0].json()
    extra_professional = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO professionals (id,tenant_id,name,commission_percent) VALUES (:id,:tenant,'Outro profissional',25)"),
                         {"id": extra_professional, "tenant": d["a"]})
    a = await appointment(d, person["id"])
    b = await appointment(d, person["id"], professional_id=extra_professional)
    outcomes = await asyncio.gather(*(d["client"].post(d["base"] + f"/appointments/{identity}/complete", json={"coupon_id": coupon["id"], "idempotency_key": "consume-" + identity}) for identity in (a, b)))
    assert sorted(response.status_code for response in outcomes) == [200, 409], [response.text for response in outcomes]
    successful = next(response.json() for response in outcomes if response.status_code == 200)
    assert successful["total_cents"] == 6000 and successful["discount_cents"] == 4000
    summary = (await d["client"].get(d["base"] + "/dashboard", params=d["period"])).json()["summary"]
    assert summary["completed"] == 1 and summary["confirmed"] == 1
    person_after = (await d["client"].get(d["base"] + "/customers/" + person["id"])).json()
    assert person_after["loyalty_points"] == 1
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            await db.execute(text("UPDATE loyalty_coupons SET reward_value_cents=999 WHERE id=:id"), {"id": UUID(coupon["id"])})


@pytest.mark.asyncio
async def test_coupon_customer_expiry_and_free_service_validation_leave_booking_unchanged(management_db):
    d = management_db
    owner = await customer(d)
    stranger = await customer(d, name="Outro cliente")
    await loyalty_rule(d, earning_mode="visit", points_per_visit=1, reward_points=1, reward_type="free_service", reward_service_id=str(d["service"]))
    credit = await d["client"].post(d["base"] + "/loyalty/transactions", json={"customer_id": owner["id"], "points_delta": 3, "reason": "Saldo de teste", "idempotency_key": "free-service-points"})
    assert credit.status_code == 201
    coupon = (await d["client"].post(d["base"] + "/loyalty/coupons", json={"customer_id": owner["id"], "idempotency_key": "free-service-coupon"})).json()
    unrelated = await appointment(d, stranger["id"])
    bad_customer = await d["client"].post(d["base"] + f"/appointments/{unrelated}/complete", json={"coupon_id": coupon["id"], "idempotency_key": "wrong-client-coupon"})
    assert bad_customer.status_code == 422
    selected = await appointment(d, owner["id"], start_at=d["now"] - timedelta(hours=2))
    success = await d["client"].post(d["base"] + f"/appointments/{selected}/complete", json={"coupon_id": coupon["id"], "idempotency_key": "free-service-use"})
    assert success.status_code == 200, success.text
    assert success.json()["total_cents"] == success.json()["commission_cents"] == 0
    another_coupon = (await d["client"].get(d["base"] + "/loyalty/coupons", params={"status": "available", "customer_id": owner["id"]})).json()["items"][0]
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE loyalty_coupons SET issued_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE id=:id"),
                         {"id": UUID(another_coupon["id"])})
    last = await appointment(d, owner["id"], start_at=d["now"] - timedelta(hours=1))
    expired = await d["client"].post(d["base"] + f"/appointments/{last}/complete", json={"coupon_id": another_coupon["id"], "idempotency_key": "expired-coupon-use"})
    assert expired.status_code == 409
    async with d["migrator"].connect() as db:
        status = (await db.execute(text("SELECT status,total_cents FROM appointments WHERE id=:id"), {"id": UUID(last)})).one()
        assert status == ("confirmed", 10000)
    expired_list = await d["client"].get(d["base"] + "/loyalty/coupons", params={"status": "expired"})
    assert expired_list.status_code == 200 and expired_list.json()["total"] == 1


async def completed_visit(d, person_id, *, start_at=None, amount=10000):
    identity = await appointment(d, person_id, start_at=start_at, amount=amount)
    response = await d["client"].post(d["base"] + f"/appointments/{identity}/complete", json={"idempotency_key": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


async def settlement_preview(d):
    response = await d["client"].get(d["base"] + "/finance/settlements/preview", params={"professional_id": str(d["pro"])})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_settlement_concurrent_replays_create_one_payment_and_immutable_scoped_history(management_db):
    d = management_db
    c, base = d["client"], d["base"]
    person = await customer(d)
    await completed_visit(d, person["id"])
    first = await entry(d, kind="expense", entry_type="advance", professional_id=str(d["pro"]), amount_cents=500,
                        occurred_at=(d["now"] - timedelta(days=2)).isoformat())
    second = await entry(d, kind="expense", entry_type="advance", professional_id=str(d["pro"]), amount_cents=1000,
                         occurred_at=(d["now"] - timedelta(days=1)).isoformat())
    preview = await settlement_preview(d)
    assert preview["summary"]["payable_cents"] == 1000
    assert preview["summary"]["advance_allocation_cents"] == 1500
    assert [row["id"] for row in preview["advances"]] == [first["id"], second["id"]]
    assert "_snapshot" not in preview and preview["transfers_enabled"] is False
    payload = {"professional_id": str(d["pro"]), "expected_snapshot": preview["snapshot_hash"],
               "payment_method": "pix", "idempotency_key": "same-settlement-request"}
    responses = await asyncio.gather(*(c.post(base + "/finance/settlements", json=payload) for _ in range(6)))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    assert len({response.json()["id"] for response in responses}) == 1
    result = responses[0].json()
    assert result["payment_cents"] == 1000 and result["allocated_cents"] == 1500
    assert sum(row["amount_cents"] for row in result["allocations"]) == 1500
    history = (await c.get(base + "/finance/settlements", params={"professional_id": str(d["pro"]), "limit": 1})).json()
    assert history["total"] == 1 and len(history["items"]) == 1 and "snapshot" not in history["items"][0]
    detail = await c.get(base + "/finance/settlements/" + result["id"])
    assert detail.status_code == 200 and detail.json()["snapshot"]["summary"]["earned_cents"] == 2500
    advances = (await c.get(base + "/finance/advances", params={**d["period"], "status": "settled"})).json()
    assert advances["total"] == 2 and all(row["pending_cents"] == 0 and row["status"] == "paid" for row in advances["items"])
    assert (await c.get(base + "/finance/advances", params={**d["period"], "status": "open"})).json()["total"] == 0
    assert (await c.post(base + "/finance/settlements", json={**payload, "idempotency_key": "new-key-old-snapshot"})).status_code == 409
    assert (await c.post(base + "/finance/settlements", json={**payload, "notes": "Different payload"})).status_code == 409
    assert (await c.get(d["other_base"] + "/finance/settlements/" + result["id"])).status_code == 404
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    assert (await c.post(base + "/finance/settlements", headers=viewer, json=payload)).status_code == 403
    for identity in (first["id"], result["payment_entry_id"]):
        assert (await c.patch(base + "/finance/" + identity, json={"amount_cents": 900, "expected_version": 1})).status_code == 409
        assert (await c.request("DELETE", base + "/finance/" + identity, json={"expected_version": 1})).status_code == 409
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE tenant_id=:tenant AND entry_type='commission_payment'"), {"tenant": d["a"]})).scalar_one() == 1
    async with d["runtime"].begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM commission_settlements"))).scalar_one() == 0
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        assert (await db.execute(text("SELECT count(*) FROM commission_settlements WHERE id=:id"), {"id": UUID(result["id"])})).scalar_one() == 0
    for command in ("UPDATE commission_settlements SET notes='changed'", "DELETE FROM commission_advance_allocations"):
        with pytest.raises(DBAPIError):
            async with d["runtime"].begin() as db:
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
                await db.execute(text(command))


@pytest.mark.asyncio
async def test_partial_advance_is_carried_to_next_settlement_without_counting_cash_twice(management_db):
    d = management_db
    c, base = d["client"], d["base"]
    person = await customer(d)
    await completed_visit(d, person["id"])
    advance = await entry(d, kind="expense", entry_type="advance", professional_id=str(d["pro"]), amount_cents=4000)
    first_preview = await settlement_preview(d)
    assert first_preview["summary"]["payable_cents"] == 0 and first_preview["summary"]["balance_cents"] == -1500
    first = await c.post(base + "/finance/settlements", json={"professional_id": str(d["pro"]),
        "expected_snapshot": first_preview["snapshot_hash"], "idempotency_key": "partial-first-settlement"})
    assert first.status_code == 201, first.text
    assert first.json()["payment_entry_id"] is None and first.json()["allocated_cents"] == 2500
    partial = (await c.get(base + "/finance/advances", params={**d["period"], "status": "partial"})).json()
    assert partial["total"] == 1 and partial["items"][0]["id"] == advance["id"]
    assert partial["items"][0]["allocated_cents"] == 2500 and partial["items"][0]["pending_cents"] == 1500
    await completed_visit(d, person["id"], start_at=d["now"] - timedelta(hours=1))
    second_preview = await settlement_preview(d)
    assert second_preview["summary"]["advance_allocation_cents"] == 1500 and second_preview["summary"]["payable_cents"] == 1000
    second = await c.post(base + "/finance/settlements", json={"professional_id": str(d["pro"]),
        "expected_snapshot": second_preview["snapshot_hash"], "idempotency_key": "partial-second-settlement"})
    assert second.status_code == 201 and second.json()["payment_cents"] == 1000, second.text
    settled = (await c.get(base + "/finance/advances", params={**d["period"], "status": "settled"})).json()
    assert settled["total"] == 1 and settled["items"][0]["pending_cents"] == 0
    finance = (await c.get(base + "/finance/summary", params=d["period"])).json()["summary"]
    assert finance["expense_cents"] == 5000  # R$40 paid as advance, then R$10 as the remaining commission.
    report = (await c.get(base + "/finance/commissions", params=d["period"])).json()["items"][0]
    assert report["balance_cents"] == 0 and report["earned_cents"] == 5000
    dre = (await c.get(base + "/finance/dre", params=d["period"])).json()["summary"]
    assert dre["commissions_cents"] == 5000 and dre["operating_expenses_cents"] == 0
    empty_preview = await settlement_preview(d)
    no_effect = await c.post(base + "/finance/settlements", json={"professional_id": str(d["pro"]),
        "expected_snapshot": empty_preview["snapshot_hash"], "idempotency_key": "nothing-remaining-to-settle"})
    assert no_effect.status_code == 409


@pytest.mark.asyncio
async def test_settlement_rechecks_ledger_changes_and_serializes_different_requests(management_db):
    d = management_db
    c, base = d["client"], d["base"]
    person = await customer(d)
    await completed_visit(d, person["id"])
    old_preview = await settlement_preview(d)
    advance = await entry(d, kind="expense", entry_type="advance", professional_id=str(d["pro"]), amount_cents=1000)
    stale_payload = {"professional_id": str(d["pro"]), "expected_snapshot": old_preview["snapshot_hash"], "idempotency_key": "stale-preview-retry"}
    assert (await c.post(base + "/finance/settlements", json=stale_payload)).status_code == 409
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM management_requests WHERE tenant_id=:tenant AND operation='commission_settlement'"), {"tenant": d["a"]})).scalar_one() == 0
    # An untouched advance remains editable; its version is part of the preview.
    edited = await c.patch(base + "/finance/" + advance["id"], json={"amount_cents": 1200, "expected_version": 1})
    assert edited.status_code == 200, edited.text
    fresh = await settlement_preview(d)
    assert fresh["summary"]["payable_cents"] == 1300
    payloads = [{**stale_payload, "expected_snapshot": fresh["snapshot_hash"]},
                {**stale_payload, "expected_snapshot": fresh["snapshot_hash"], "idempotency_key": "another-settlement-request"}]
    responses = await asyncio.gather(*(c.post(base + "/finance/settlements", json=payload) for payload in payloads))
    assert sorted(response.status_code for response in responses) == [201, 409], [response.text for response in responses]
    successful = next(response.json() for response in responses if response.status_code == 201)
    assert successful["payment_cents"] == 1300 and successful["allocated_cents"] == 1200
    final_preview = await settlement_preview(d)
    assert final_preview["summary"]["payable_cents"] == final_preview["summary"]["balance_cents"] == 0
    assert (await c.get(base + "/finance/settlements")).json()["total"] == 1



@pytest.mark.asyncio
async def test_opt_in_completion_worker_accrues_commission_and_pending_receivable_once(management_db):
    """Use a disposable PostgreSQL tenant, real RLS and the actual completion path."""
    from app.appointment_auto_completion import process_auto_completion

    d = management_db
    person = await customer(d, name="Cliente da automação")
    begin = d["now"] - timedelta(minutes=80)
    appointment_id = await appointment(d, person["id"], start_at=begin, amount=10000)
    async with d["migrator"].connect() as conn:
        expected_end = (await conn.execute(text(
            "SELECT end_at FROM appointments WHERE tenant_id=:tenant AND id=:id"
        ), {"tenant":d["a"],"id":UUID(appointment_id)})).scalar_one()

    # Defaults disabled and never processes bookings ending before activation.
    assert await process_auto_completion({}, str(d["a"]), appointment_id, expected_end.isoformat()) == "not_enabled"
    config = await d["client"].get(d["base"] + "/appointment-auto-completion")
    assert config.status_code == 200 and config.json()["configuration"]["enabled"] is False
    enabled = await d["client"].put(d["base"] + "/appointment-auto-completion", json={
        "expected_version": config.json()["version"],"enabled":True,
    })
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["configuration"]["enabled"] is True
    assert await process_auto_completion({}, str(d["a"]), appointment_id, expected_end.isoformat()) == "not_enabled"

    # Model an appointment whose end time followed a prior opt-in, without
    # waiting in real time or touching any real tenant/booking.
    activation = (d["now"] - timedelta(hours=3)).isoformat()
    async with d["migrator"].begin() as conn:
        await conn.execute(text("""
            UPDATE management_preferences
            SET preferences=jsonb_set(preferences, '{auto_completion,enabled_at}',
                to_jsonb(CAST(:activation AS text)))
            WHERE tenant_id=:tenant
        """), {"tenant":d["a"],"activation":activation})

    first = await process_auto_completion({}, str(d["a"]), appointment_id, expected_end.isoformat())
    assert first == "completed"
    assert await process_auto_completion({}, str(d["a"]), appointment_id, expected_end.isoformat()) == "already_processed"

    async with d["migrator"].connect() as conn:
        booking = (await conn.execute(text("""
            SELECT status,commission_percent_snapshot,commission_cents,total_cents
            FROM appointments WHERE tenant_id=:tenant AND id=:id
        """), {"tenant":d["a"],"id":UUID(appointment_id)})).mappings().one()
        history = (await conn.execute(text("""
            SELECT details FROM appointment_history
            WHERE tenant_id=:tenant AND appointment_id=:id AND event='completed'
        """), {"tenant":d["a"],"id":UUID(appointment_id)})).mappings().all()
        finance = (await conn.execute(text("""
            SELECT kind,status,amount_cents,payment_method
            FROM financial_entries WHERE tenant_id=:tenant AND appointment_id=:id
        """), {"tenant":d["a"],"id":UUID(appointment_id)})).mappings().all()
    assert booking["status"] == "completed"
    assert booking["commission_cents"] == 2500 and booking["commission_percent_snapshot"] == 25
    assert len(history) == 1 and history[0]["details"]["source"] == "system"
    assert history[0]["details"]["actor"]["role"] == "system"
    assert [dict(item) for item in finance] == [{
        "kind":"income","status":"pending","amount_cents":10000,"payment_method":"other"
    }]
    daily = await d["client"].get(d["base"]+"/finance/summary",params={
        "start_date":str(begin.date()),"end_date":str((d["now"]+timedelta(days=1)).date()),
    })
    assert daily.status_code == 200
    totals = daily.json()["summary"]
    assert totals["income_cents"] == 0 and totals["balance_cents"] == 0
    assert totals["pending_income_cents"] == 10000

    dre = await d["client"].get(d["base"]+"/finance/dre",params={
        "start_date":str(begin.date()),"end_date":str((d["now"]+timedelta(days=1)).date()),
    })
    assert dre.status_code == 200
    assert dre.json()["summary"]["service_revenue_cents"] == 10000
    assert dre.json()["summary"]["commissions_cents"] == 2500

    disabled = await d["client"].put(d["base"]+"/appointment-auto-completion",json={
        "expected_version": enabled.json()["version"],"enabled":False,
    })
    assert disabled.status_code == 200 and disabled.json()["configuration"]["enabled"] is False


@pytest.mark.asyncio
async def test_auto_completion_never_rewrites_cancelled_or_rescheduled_appointments(management_db):
    from app.appointment_auto_completion import process_auto_completion
    d=management_db
    user=await customer(d,name="Cliente sem conclusão automática")
    start=d["now"]-timedelta(hours=4)
    one=await appointment(d,user["id"],start_at=start)
    two=await appointment(d,user["id"],start_at=start+timedelta(hours=1))
    async with d["migrator"].begin() as conn:
        await conn.execute(text("""
           INSERT INTO management_preferences(tenant_id,preferences)
           VALUES (:tenant,CAST(:body AS jsonb))
        """),{"tenant":d["a"],"body":'{"auto_completion":{"enabled":true,"enabled_at":"2020-01-01T00:00:00+00:00"}}'})
        await conn.execute(text("""
          UPDATE appointments SET status='cancelled',hold_expires_at=NULL
          WHERE tenant_id=:tenant AND id=:id
        """),{"tenant":d["a"],"id":UUID(one)})
    async with d["migrator"].connect() as conn:
        old=(await conn.execute(text("SELECT end_at FROM appointments WHERE id=:id"),{"id":UUID(one)})).scalar_one()
        new=(await conn.execute(text("SELECT end_at FROM appointments WHERE id=:id"),{"id":UUID(two)})).scalar_one()
    assert await process_auto_completion({},str(d["a"]),one,old.isoformat())=="already_processed"
    assert await process_auto_completion({},str(d["a"]),two,(new+timedelta(hours=2)).isoformat())=="rescheduled"
    async with d["migrator"].connect() as conn:
        count=(await conn.execute(text("SELECT count(*) FROM financial_entries WHERE tenant_id=:tenant AND appointment_id IN (:one,:two)"),{"tenant":d["a"],"one":UUID(one),"two":UUID(two)})).scalar_one()
    assert count==0
