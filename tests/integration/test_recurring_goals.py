"""Real PostgreSQL contracts for monthly goals, calendar history and isolation."""
import asyncio
from datetime import datetime, timezone
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import management_models as data
from tests.integration.test_management import appointment, customer, management_db  # noqa: F401

pytestmark = pytest.mark.integration


def clock(monkeypatch, value):
    instant = datetime.fromisoformat(value)
    monkeypatch.setattr(data, "goal_now", lambda: instant)


async def goal(d, **fields):
    payload = {"scope": "company", "professional_id": None, "metric": "revenue_cents", "recurrence": "monthly",
               "target_value": 10000, "idempotency_key": str(uuid4()), **fields}
    response = await d["client"].post(d["base"] + "/goals", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


async def goals(d, start=None, end=None):
    params = {} if start is None else {"start_date": start, "end_date": end or start}
    response = await d["client"].get(d["base"] + "/goals", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_company_and_professional_cycles_count_only_completed_full_local_month(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-27T12:00:00+00:00")
    company, _ = await goal(d)
    individual, _ = await goal(d, scope="professional", professional_id=str(d["pro"]))
    count, _ = await goal(d, metric="appointments", target_value=4)
    person = await customer(d)
    second = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Segundo profissional')"),
                         {"id": second, "tenant": d["a"]})
    for timestamp, amount, professional in (
        ("2026-10-01T02:59:00+00:00", 9000, d["pro"]),  # September in Sao Paulo.
        ("2026-10-01T03:00:00+00:00", 1200, d["pro"]),
        ("2026-11-01T02:59:00+00:00", 2400, second),
        ("2026-11-01T03:00:00+00:00", 700, d["pro"]),
    ):
        await appointment(d, person["id"], status="completed", amount=amount,
                          professional_id=professional, start_at=datetime.fromisoformat(timestamp))
    for day, status in enumerate(("confirmed", "cancelled", "no_show", "hold"), start=10):
        await appointment(d, person["id"], status=status, amount=99000,
                          start_at=datetime(2026, 10, day, 12, tzinfo=timezone.utc))
    other = await customer(d, other=True)
    other_service = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:tenant,'Outro',30,99999)"),
                         {"id": other_service, "tenant": d["b"]})
    await appointment({**d, "a": d["b"], "pro": d["other_pro"], "service": other_service}, other["id"],
                      status="completed", amount=99999, start_at=datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    # A partial report filter intersects a cycle; its progress is the entire
    # goal month, not the selected day and not another tenant's appointments.
    result = await goals(d, "2026-10-15")
    items = {item["id"]: item for item in result["items"]}
    assert items[company["id"]]["actual_value"] == 3600
    assert items[individual["id"]]["actual_value"] == 1200
    assert items[count["id"]]["actual_value"] == 2
    assert items[company["id"]]["progress_percent"] == 36
    assert items[count["id"]]["progress_percent"] == 50
    assert all(item["start_date"] == "2026-10-01" and item["end_date"] == "2026-10-31" for item in items.values())
    assert all(item["is_current_cycle"] and item["can_edit"] for item in items.values())
    assert items[company["id"]]["scope"] == "company" and items[company["id"]]["professional_name"] is None
    assert items[individual["id"]]["scope"] == "professional"
    performance = (await d["client"].get(d["base"] + "/performance", params={"start_date": "2026-10-01", "end_date": "2026-10-31"})).json()
    assert {item["id"] for item in performance["company_goals"]} == {company["id"], count["id"]}
    assert [item["id"] for item in next(item for item in performance["items"] if item["id"] == str(d["pro"]))["goals"]] == [individual["id"]]


@pytest.mark.asyncio
async def test_month_rollover_uses_tenant_midnight_and_needs_no_job_or_reset(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-31T23:00:00+00:00")
    created, _ = await goal(d, metric="appointments", target_value=2)
    person = await customer(d)
    await appointment(d, person["id"], status="completed", start_at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc))
    await appointment(d, person["id"], status="completed", start_at=datetime(2026, 11, 1, 3, tzinfo=timezone.utc))
    clock(monkeypatch, "2026-11-01T02:59:59+00:00")
    before = (await goals(d))["items"][0]
    assert before["start_date"] == "2026-10-01" and before["actual_value"] == 1
    clock(monkeypatch, "2026-11-01T03:00:00+00:00")
    after = (await goals(d))["items"][0]
    assert after["start_date"] == "2026-11-01" and after["end_date"] == "2026-11-30"
    assert after["id"] == created["id"] and after["actual_value"] == 1 and after["version"] == 1
    assert before["cycle_id"] != after["cycle_id"]
    both = (await goals(d, "2026-10-31", "2026-11-01"))["items"]
    assert len(both) == 2 and sum(item["actual_value"] for item in both) == 2
    assert {item["start_date"]: item["can_edit"] for item in both} == {"2026-10-01": False, "2026-11-01": True}
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM professional_goal_revisions WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 2


@pytest.mark.asyncio
async def test_edit_changes_current_configuration_without_rewriting_closed_cycle_or_timezone(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-20T12:00:00+00:00")
    created, _ = await goal(d, scope="professional", professional_id=str(d["pro"]), target_value=1000)
    person = await customer(d)
    await appointment(d, person["id"], status="completed", amount=9000, start_at=datetime(2026, 10, 1, 2, tzinfo=timezone.utc))
    await appointment(d, person["id"], status="completed", amount=500, start_at=datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    second = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Nova pessoa')"), {"id": second, "tenant": d["a"]})
        await db.execute(text("UPDATE tenants SET timezone='UTC' WHERE id=:tenant"), {"tenant": d["a"]})
    await appointment(d, person["id"], status="completed", professional_id=second, start_at=datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    clock(monkeypatch, "2026-11-15T12:00:00+00:00")
    changed = await d["client"].patch(d["base"] + "/goals/" + created["id"], json={
        "expected_version": 1, "professional_id": str(second), "metric": "appointments", "target_value": 4})
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    items = {item["start_date"]: item for item in (await goals(d, "2026-10-01", "2026-12-31"))["items"]}
    assert len(items) == 3
    old = items["2026-10-01"]
    assert (old["target_value"], old["metric"], old["professional_id"], old["actual_value"], old["timezone"]) == (1000, "revenue_cents", str(d["pro"]), 500, "America/Sao_Paulo")
    assert old["cycle_version"] == 1 and old["version"] == old["template_version"] == 2 and old["can_edit"] is False
    current = items["2026-11-01"]
    assert (current["target_value"], current["metric"], current["professional_id"], current["actual_value"], current["timezone"]) == (4, "appointments", str(second), 1, "UTC")
    assert current["cycle_version"] == 2 and current["can_edit"] is True
    assert items["2026-12-01"]["target_value"] == 4 and items["2026-12-01"]["can_edit"] is False
    backdated = await d["client"].patch(d["base"] + "/goals/" + created["id"], json={
        "expected_version": 2, "target_value": 9, "cycle_id": old["cycle_id"]})
    assert backdated.status_code == 422


@pytest.mark.asyncio
async def test_pause_resume_and_delete_keep_closed_months_and_do_not_create_paused_cycles(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-10T12:00:00+00:00")
    created, payload = await goal(d)
    endpoint = d["base"] + "/goals/" + created["id"]
    paused = await d["client"].patch(endpoint, json={"active": False, "expected_version": 1})
    assert paused.status_code == 200 and paused.json()["version"] == 2
    assert (await goals(d))["items"][0]["active"] is False
    clock(monkeypatch, "2026-11-10T12:00:00+00:00")
    november = await goals(d)
    assert november["items"] == []
    assert len(november["templates"]) == 1 and november["templates"][0]["active"] is False
    assert november["templates"][0]["can_edit"] is True
    clock(monkeypatch, "2026-12-10T12:00:00+00:00")
    resumed = await d["client"].patch(endpoint, json={"active": True, "target_value": 20000, "expected_version": 2})
    assert resumed.status_code == 200 and resumed.json()["version"] == 3
    december = (await goals(d))["items"][0]
    assert december["active"] is True and december["target_value"] == 20000
    clock(monkeypatch, "2027-01-10T12:00:00+00:00")
    deleted = await d["client"].request("DELETE", endpoint, json={"expected_version": 3})
    assert deleted.status_code == 200 and deleted.json()["deleted_at"] is not None and deleted.json()["can_edit"] is False
    assert (await goals(d))["items"] == [] and (await goals(d))["templates"] == []
    past = {item["start_date"]: item for item in (await goals(d, "2026-10-01", "2027-02-28"))["items"]}
    assert set(past) == {"2026-10-01", "2026-12-01"}
    assert past["2026-10-01"]["target_value"] == 10000 and past["2026-12-01"]["target_value"] == 20000
    assert all(item["can_edit"] is False for item in past.values())
    assert (await d["client"].patch(endpoint, json={"active": True, "expected_version": 4})).status_code == 404
    replay = await d["client"].post(d["base"] + "/goals", json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == created["id"]
    assert replay.json()["can_edit"] is False and replay.json()["deleted_at"] is not None
    replacement, _ = await goal(d)
    assert replacement["id"] != created["id"]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM professional_goal_revisions WHERE tenant_id=:tenant AND goal_id=:goal"),
                                {"tenant": d["a"], "goal": UUID(created["id"])})).scalar_one() == 4


@pytest.mark.asyncio
async def test_goal_writes_are_idempotent_versioned_and_duplicate_slots_serialize(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-10T12:00:00+00:00")
    payload = {"scope": "company", "metric": "appointments", "target_value": 20, "recurrence": "monthly", "idempotency_key": "same-monthly-key"}
    responses = await asyncio.gather(*(d["client"].post(d["base"] + "/goals", json=payload) for _ in range(5)))
    assert {response.status_code for response in responses} == {201}, [response.text for response in responses]
    assert len({response.json()["id"] for response in responses}) == 1
    created = responses[0].json()
    endpoint = d["base"] + "/goals/" + created["id"]
    attempts = await asyncio.gather(*(d["client"].post(d["base"] + "/goals", json={**payload, "metric": "revenue_cents", "idempotency_key": f"different-goal-key-{index}"}) for index in range(4)))
    assert sorted(response.status_code for response in attempts) == [201, 409, 409, 409]
    edits = await asyncio.gather(*(d["client"].patch(endpoint, json={"target_value": target, "expected_version": 1}) for target in (30, 40)))
    assert sorted(response.status_code for response in edits) == [200, 409]
    assert (await d["client"].request("DELETE", endpoint, json={"expected_version": 1})).status_code == 409
    assert (await d["client"].patch(endpoint, json={"active": False, "expected_version": 2})).status_code == 200
    replacement, _ = await goal(d, metric="appointments")
    assert (await d["client"].patch(endpoint, json={"active": True, "expected_version": 3})).status_code == 409
    # A retry in a later month still resolves the original operation, even
    # though its synthesized calendar dates and current version have changed.
    clock(monkeypatch, "2026-11-10T12:00:00+00:00")
    replay = await d["client"].post(d["base"] + "/goals", json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == created["id"] and replay.json()["version"] == 3
    changed_key = await d["client"].post(d["base"] + "/goals", json={**payload, "target_value": 99})
    assert changed_key.status_code == 409
    current = await goals(d)
    assert replacement["id"] in {item["id"] for item in current["items"]}
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM professional_goal_revisions WHERE tenant_id=:tenant AND goal_id=:goal"),
                                {"tenant": d["a"], "goal": UUID(created["id"])})).scalar_one() == 3


@pytest.mark.asyncio
async def test_goal_tenant_rls_history_immutability_and_viewer_read_only(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-10T12:00:00+00:00")
    created, _ = await goal(d)
    assert (await d["client"].get(d["other_base"] + "/goals")).json()["items"] == []
    assert (await d["client"].get(d["other_base"] + "/goals")).json()["templates"] == []
    cross = await d["client"].post(d["base"] + "/goals", json={"professional_id": str(d["other_pro"]), "metric": "appointments", "target_value": 10, "idempotency_key": "cross-tenant-goal"})
    assert cross.status_code == 404
    for method, payload in (("PATCH", {"target_value": 20, "expected_version": 1}), ("DELETE", {"expected_version": 1})):
        assert (await d["client"].request(method, d["other_base"] + "/goals/" + created["id"], json=payload)).status_code == 404
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    assert (await d["client"].get(d["base"] + "/goals", headers=viewer)).status_code == 200
    assert (await d["client"].patch(d["base"] + "/goals/" + created["id"], headers=viewer, json={"target_value": 99, "expected_version": 1})).status_code == 403
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        assert (await db.execute(text("SELECT count(*) FROM professional_goal_revisions"))).scalar_one() == 0
    for statement in (
        "UPDATE professional_goal_revisions SET target_value=1 WHERE goal_id=:goal",
        "DELETE FROM professional_goal_revisions WHERE goal_id=:goal",
        "UPDATE professional_goals SET target_value=1 WHERE id=:goal",
    ):
        with pytest.raises(DBAPIError):
            async with d["runtime"].begin() as db:
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
                await db.execute(text(statement), {"goal": UUID(created["id"])})
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
            await db.execute(text("""INSERT INTO professional_goal_revisions
                (tenant_id,goal_id,version,effective_month,metric,target_value,active,timezone)
                VALUES (:tenant,:goal,2,'2026-10-01','appointments',1,true,'UTC')"""),
                {"tenant": d["a"], "goal": UUID(created["id"])})
    with pytest.raises(DBAPIError):
        async with d["support"].connect() as db:
            await db.execute(text("SELECT * FROM professional_goal_revisions"))
    assert (await goals(d))["items"][0]["target_value"] == 10000


@pytest.mark.asyncio
async def test_legacy_intervals_remain_fixed_and_new_default_is_monthly(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-10T12:00:00+00:00")
    legacy = {"professional_id": str(d["pro"]), "metric": "appointments", "target_value": 5,
              "start_date": "2026-10-05", "end_date": "2026-12-10", "idempotency_key": "legacy-fixed-goal"}
    response = await d["client"].post(d["base"] + "/goals", json=legacy)
    assert response.status_code == 201, response.text
    assert response.json()["recurrence"] == "none" and response.json()["scope"] == "professional"
    person = await customer(d)
    for month in (10, 11):
        await appointment(d, person["id"], status="completed", start_at=datetime(2026, month, 15, 12, tzinfo=timezone.utc))
    fixed = (await goals(d, "2026-10-06"))["items"][0]
    assert fixed["start_date"] == "2026-10-05" and fixed["end_date"] == "2026-12-10" and fixed["actual_value"] == 2
    replay = await d["client"].post(d["base"] + "/goals", json=legacy)
    assert replay.status_code == 201 and replay.json()["id"] == response.json()["id"]
    monthly = await d["client"].post(d["base"] + "/goals", json={"metric": "appointments", "target_value": 10, "idempotency_key": "new-default-monthly"})
    assert monthly.status_code == 201, monthly.text
    assert monthly.json()["recurrence"] == "monthly" and monthly.json()["scope"] == "company"
    assert monthly.json()["professional_id"] is None
    # A fixed historical interval is not silently promoted by future reads.
    clock(monkeypatch, "2027-01-01T12:00:00+00:00")
    assert {item["id"] for item in (await goals(d))["items"]} == {monthly.json()["id"]}
    assert (await d["client"].patch(d["base"] + "/goals/" + response.json()["id"], json={"target_value": 99, "expected_version": 1})).status_code == 409
    assert next(item for item in (await goals(d, "2026-11-01"))["items"] if item["id"] == response.json()["id"])["target_value"] == 5


@pytest.mark.asyncio
async def test_deleted_fixed_goal_remains_read_only_in_earlier_reports_and_idempotent_replay(management_db, monkeypatch):
    d = management_db
    clock(monkeypatch, "2026-10-10T12:00:00+00:00")
    payload = {"professional_id": str(d["pro"]), "metric": "appointments", "target_value": 5,
               "start_date": "2026-10-01", "end_date": "2026-12-31", "idempotency_key": "fixed-delete-history"}
    created = await d["client"].post(d["base"] + "/goals", json=payload)
    assert created.status_code == 201
    identity = created.json()["id"]
    clock(monkeypatch, "2026-11-10T12:00:00+00:00")
    deleted = await d["client"].request("DELETE", d["base"] + "/goals/" + identity, json={"expected_version": 1})
    assert deleted.status_code == 200 and deleted.json()["can_edit"] is False
    assert (await goals(d))["items"] == []
    historical = (await goals(d, "2026-10-01", "2026-10-31"))["items"][0]
    assert historical["id"] == identity and historical["target_value"] == 5 and historical["can_edit"] is False
    assert historical["start_date"] == "2026-10-01" and historical["end_date"] == "2026-12-31"
    replay = await d["client"].post(d["base"] + "/goals", json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == identity and replay.json()["can_edit"] is False
    assert (await d["client"].patch(d["base"] + "/goals/" + identity, json={"target_value": 9, "expected_version": 2})).status_code == 404
    closed_payload = {**payload, "end_date": "2026-10-31", "idempotency_key": "closed-fixed-delete"}
    closed = await d["client"].post(d["base"] + "/goals", json=closed_payload)
    assert closed.status_code == 201
    assert (await d["client"].request("DELETE", d["base"] + "/goals/" + closed.json()["id"], json={"expected_version": 1})).status_code == 200
    broad = await goals(d, "2026-10-01", "2026-11-30")
    assert next(item for item in broad["items"] if item["id"] == closed.json()["id"])["can_edit"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp,expected_start,expected_end", [
    ("2027-12-31T15:00:00+00:00", "2027-12-01", "2027-12-31"),
    ("2028-02-29T15:00:00+00:00", "2028-02-01", "2028-02-29"),
])
async def test_calendar_month_lengths_and_invalid_monthly_payloads(management_db, monkeypatch, timestamp, expected_start, expected_end):
    d = management_db
    clock(monkeypatch, timestamp)
    created, _ = await goal(d)
    assert (created["start_date"], created["end_date"]) == (expected_start, expected_end)
    invalid = (
        {"scope": "professional", "professional_id": None},
        {"scope": "company", "professional_id": str(d["pro"])},
        {"start_date": "2026-10-01", "end_date": "2026-10-31"},
        {"start_date": expected_start, "end_date": None},
        {"target_value": 1.5},
        {"target_value": True},
    )
    for fields in invalid:
        response = await d["client"].post(d["base"] + "/goals", json={"metric": "appointments", "target_value": 10,
            "recurrence": "monthly", "idempotency_key": str(uuid4()), **fields})
        assert response.status_code == 422, response.text
