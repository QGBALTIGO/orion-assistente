"""Real PostgreSQL commission-plan, settlement and RLS regression tests."""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_management import management_db, appointment, customer

pytestmark = pytest.mark.integration


def plan_url(d, professional=None, *, other=False):
    return (d["other_base"] if other else d["base"]) + "/professionals/" + str(professional or d["pro"]) + "/commission-plan"


async def configure(d, *, rules=None, mode="per_service", version=1, professional=None):
    data = {"mode": mode, "expected_version": version,
            "services": rules if rules is not None else [
                {"service_id": str(d["service"]), "kind": "percent", "percent": "10.00"},
            ]}
    return await d["client"].put(plan_url(d, professional), json=data)


async def second_service(d, name="Barba", cents=5000):
    identity = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services(id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,:name,20,:price)
        """), {"id": identity, "tenant": d["a"], "name": name, "price": cents})
    return identity


async def second_item(d, visit, service, *, cents=5000):
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointments SET total_cents=total_cents+:additional WHERE id=:id
        """), {"id": UUID(visit), "additional": cents})
        await db.execute(text("""
            INSERT INTO appointment_items
              (tenant_id,appointment_id,service_id,name_snapshot,
               duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,
               cleanup_snapshot_min,item_order)
            VALUES (:tenant,:id,:service,'Barba',20,:price,0,0,1)
        """), {"tenant": d["a"], "id": UUID(visit), "service": service, "price": cents})


@pytest.mark.asyncio
async def test_scoped_plan_atomic_validation_and_stale_revision(management_db):
    d = management_db
    url = plan_url(d)
    viewer = {"Authorization": "Bearer management-test-viewer-token-of-at-least-32-characters"}
    assert (await d["client"].put(url, headers=viewer, json={"mode": "percent",
        "expected_version": 1, "services": [{"service_id": str(d["service"])}]})).status_code == 403
    assert (await d["client"].put(plan_url(d, other=True), json={
        "mode": "percent", "expected_version": 1,
        "services": [{"service_id": str(d["service"])}]})).status_code == 404
    missing = await configure(d, rules=[{"service_id": str(d["service"])}])
    assert missing.status_code == 422, missing.text
    wrong_tenant = await configure(d, rules=[{
        "service_id": str(uuid4()), "kind": "fixed", "fixed_cents": 1000}])
    assert wrong_tenant.status_code == 422, wrong_tenant.text
    result = await configure(d)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["commission_mode"] == "per_service"
    assert saved["commission_plan_version"] == 2
    assert saved["commission_rules"][str(d["service"])] == {
        "kind": "percent", "percent": "10.00"}
    async with d["migrator"].begin() as db:
        assert (await db.execute(text("""
            SELECT count(*) FROM professional_services
            WHERE tenant_id=:tenant AND professional_id=:pro
        """), {"tenant": d["a"], "pro": d["pro"]})).scalar_one() == 1
    stale = await configure(d, version=1)
    assert stale.status_code == 409, stale.text
    result = await d["client"].put(url, json={"mode": "percent", "expected_version": 2,
        "services": [{"service_id": str(d["service"])}]})
    assert result.status_code == 200, result.text
    assert result.json()["commission_mode"] == "percent"
    assert result.json()["commission_plan_version"] == 3
    # Returning to per-service restores historical chosen rates, not zeros.
    restored = await configure(d, version=3)
    assert restored.status_code == 200, restored.text
    assert restored.json()["commission_rules"][str(d["service"])]["percent"] == "10.00"


@pytest.mark.asyncio
async def test_percent_plus_fixed_per_service_ledger_dre_settlement_and_immutability(management_db):
    d = management_db
    beard = await second_service(d)
    rates = [
        {"service_id": str(d["service"]), "kind": "percent", "percent": "10.00"},
        {"service_id": str(beard), "kind": "fixed", "fixed_cents": 1000},
    ]
    set_response = await configure(d, rules=rates)
    assert set_response.status_code == 200, set_response.text
    person = await customer(d)
    visit = await appointment(d, person["id"], amount=10000)
    await second_item(d, visit, beard)
    response = await d["client"].post(d["base"] + "/appointments/" + visit + "/complete", json={
        "idempotency_key": "per-service-first-visit"})
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["total_cents"] == 15000
    assert row["commission_mode_snapshot"] == "per_service"
    assert row["commission_percent_snapshot"] is None
    assert row["commission_cents"] == 2000
    assert [part["service_net_cents"] for part in row["commission_breakdown"]] == [10000, 5000]
    assert [part["commission_cents"] for part in row["commission_breakdown"]] == [1000, 1000]

    report = await d["client"].get(d["base"] + "/finance/commissions", params=d["period"])
    assert report.status_code == 200, report.text
    staff = report.json()["items"][0]
    assert staff["commission_mode"] == "per_service"
    assert staff["earned_cents"] == staff["balance_cents"] == 2000
    assert staff["scheduled_cents"] == 0
    dre = (await d["client"].get(d["base"] + "/finance/dre", params=d["period"])).json()["summary"]
    assert dre["commissions_cents"] == 2000
    assert dre["service_revenue_cents"] == 15000 and dre["net_result_cents"] == 13000
    preview = await d["client"].get(d["base"] + "/finance/settlements/preview",
                                     params={"professional_id": str(d["pro"])})
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["earned_cents"] == 2000
    assert preview.json()["summary"]["payable_cents"] == 2000
    settings = await configure(d, version=2, rules=[
        {"service_id": str(d["service"]), "kind": "percent", "percent": "30.00"},
        {"service_id": str(beard), "kind": "fixed", "fixed_cents": 500},
    ])
    assert settings.status_code == 200, settings.text
    historic = await d["client"].get(d["base"] + "/appointments/" + visit)
    # The generic booking endpoint is on the catalog router and can be absent
    # from this management-only test app; read the persisted immutable rows.
    async with d["migrator"].begin() as db:
        old = (await db.execute(text("""
            SELECT commission_cents,commission_mode_snapshot,commission_breakdown
            FROM appointments WHERE id=:id AND tenant_id=:tenant
        """), {"id": UUID(visit), "tenant": d["a"]})).one()
        assert old[0] == 2000 and old[1] == "per_service"
        assert sum(x["commission_cents"] for x in old[2]) == 2000
    next_visit = await appointment(d, person["id"],
                                   start_at=d["now"]-timedelta(hours=6), amount=10000)
    other = await d["client"].post(d["base"] + "/appointments/" + next_visit + "/complete",
                                   json={"idempotency_key": "per-service-second-visit"})
    assert other.status_code == 200, other.text
    assert other.json()["commission_cents"] == 3000
    newest = (await d["client"].get(d["base"] + "/finance/commissions", params=d["period"])).json()["items"][0]
    assert newest["earned_cents"] == newest["balance_cents"] == 5000
    latest_preview = (await d["client"].get(d["base"] + "/finance/settlements/preview",
                                           params={"professional_id": str(d["pro"])})).json()
    assert latest_preview["summary"]["earned_cents"] == 5000


@pytest.mark.asyncio
async def test_variable_priced_completion_uses_final_total_and_fixed_commission(management_db):
    d = management_db
    saved = await configure(d, rules=[{
        "service_id": str(d["service"]), "kind": "fixed", "fixed_cents": 1200}])
    assert saved.status_code == 200, saved.text
    person = await customer(d)
    visit = await appointment(d, person["id"], amount=10000)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointment_items SET price_mode_snapshot='from'
            WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["a"], "visit": UUID(visit)})
    url = d["base"] + "/appointments/" + visit + "/complete"
    missing = await d["client"].post(url, json={"idempotency_key": "variable-no-amount"})
    assert missing.status_code == 422
    accepted = await d["client"].post(url, json={
        "idempotency_key": "variable-priced-actual",
        "final_total_cents": 18000})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["total_cents"] == 18000
    assert accepted.json()["commission_cents"] == 1200
    assert accepted.json()["commission_breakdown"][0]["service_net_cents"] == 18000
    assert (await d["client"].get(d["base"] + "/finance/dre", params=d["period"])).json()["summary"]["commissions_cents"] == 1200
    async with d["migrator"].begin() as db:
        result = (await db.execute(text("""
            SELECT amount_cents,status FROM financial_entries
            WHERE tenant_id=:tenant AND appointment_id=:visit
        """), {"tenant": d["a"], "visit": UUID(visit)})).all()
        assert result == [(18000, "pending")]


@pytest.mark.asyncio
async def test_existing_percent_profile_and_no_show_continue_to_work(management_db):
    d = management_db
    person = await customer(d)
    visit = await appointment(d, person["id"], amount=10000)
    result = await d["client"].post(d["base"] + "/appointments/" + visit + "/complete",
                                    json={"idempotency_key": "legacy-percent-preserved"})
    assert result.status_code == 200, result.text
    assert result.json()["commission_cents"] == 2500
    assert result.json()["commission_mode_snapshot"] == "percent"
    assert result.json()["commission_percent_snapshot"] == 25
    absent = await appointment(d, person["id"], start_at=d["now"]-timedelta(hours=7))
    response = await d["client"].post(d["base"] + "/appointments/" + absent + "/no-show",
                                      json={"idempotency_key": "legacy-no-show-preserved"})
    assert response.status_code == 200, response.text
    assert response.json()["commission_cents"] is None
    assert response.json()["commission_mode_snapshot"] is None


@pytest.mark.asyncio
async def test_zero_and_discounted_service_with_fixed_policy_never_overpays(management_db):
    d = management_db
    result = await configure(d, rules=[{
        "service_id": str(d["service"]), "kind": "fixed", "fixed_cents": 10000}])
    assert result.status_code == 200, result.text
    person = await customer(d)
    visit = await appointment(d, person["id"], amount=1000)
    closed = await d["client"].post(d["base"] + "/appointments/" + visit + "/complete",
                                    json={"idempotency_key": "fixed-capped-to-earned"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["commission_cents"] == 1000
