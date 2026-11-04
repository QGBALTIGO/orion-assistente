"""Historical silence and effective completion time on disposable PostgreSQL.

The automation transport is always a spy, and automatic completion only writes
synthetic tenant data. The fixtures use runtime, support and migrator roles.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import auto_completion_worker as automatic, automation_runtime as runtime
from app.booking_service import protocol
from app.database import tenant_transaction
from app.management_models import transition_appointment
from tests.integration.test_automation_postgres import automation_db, queue, process
from test_management import appointment, customer, management_db

pytestmark = pytest.mark.integration


async def completed_history(ctx, mode):
    identity, professional = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:t,'Profissional sintético')"),
                         {"id": professional, "t": ctx.tenant})
        row = (await db.execute(text("""INSERT INTO appointments
            (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
             start_at,end_at,occupied_from,occupied_until,status,total_cents,booking_mode)
            VALUES(:id,:t,:customer,:professional,:protocol,:key,:fp,:start,:end,:start,:end,
                   'completed',3500,:mode) RETURNING *"""),
            {"id": identity, "t": ctx.tenant, "customer": ctx.customer, "professional": professional,
             "protocol": protocol(), "key": str(uuid4()), "fp": "a"*64, "mode": mode,
             "start": now-timedelta(days=20), "end": now-timedelta(days=20)+timedelta(minutes=30)})).mappings().one()
        occurred = (await db.execute(text("""INSERT INTO appointment_history
            (tenant_id,appointment_id,event,operator,occurred_at)
            VALUES(:t,:id,'completed','owner',clock_timestamp()) RETURNING occurred_at"""),
            {"t": ctx.tenant, "id": identity})).scalar_one()
        rules = [{"event": "after_service", "requested_enabled": True,
                  "template": "Obrigado pela visita, {cliente}."}]
        await db.execute(text("""UPDATE management_preferences SET preferences=jsonb_set(
            preferences,'{automations,rules}',CAST(:rules AS jsonb)) WHERE tenant_id=:t"""),
            {"t": ctx.tenant, "rules": json.dumps(rules)})
    return {**dict(row), "appointment_id": identity, "occurred_at": occurred}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("standard", 1), ("exceptional", 1), ("retroactive", 0)])
async def test_planner_ignores_retroactive_completion_without_suppressing_normal_visits(automation_db, mode, expected):
    ctx = automation_db
    facts = await completed_history(ctx, mode)
    assert await runtime.plan_tenant(ctx.tenant) == expected
    async with ctx.migration.connect() as db:
        rows = (await db.execute(text("""SELECT event,status FROM automation_jobs
            WHERE tenant_id=:t AND appointment_id=:id"""),
            {"t": ctx.tenant, "id": facts["id"]})).all()
    assert rows == ([("after_service", "pending")] if expected else [])
    assert not ctx.sent


@pytest.mark.asyncio
async def test_historical_job_already_queued_is_cancelled_before_any_attempt(automation_db):
    ctx = automation_db
    facts = await completed_history(ctx, "retroactive")
    # Model a job written by an older producer that did not include provenance.
    legacy_facts = {key: value for key, value in facts.items() if key != "booking_mode"}
    created, job = await queue(ctx, event="after_service", facts=legacy_facts,
                               labels={"cliente": "Cliente sintético"})
    assert created and job["status"] == "pending"
    assert await process(ctx, job) == "retroactive_appointment"
    async with ctx.migration.connect() as db:
        row = (await db.execute(text("""SELECT status,last_error,attempted_at FROM automation_jobs
            WHERE tenant_id=:t AND id=:id"""), {"t": ctx.tenant, "id": job["id"]})).mappings().one()
    assert dict(row) == {"status": "cancelled", "last_error": "retroactive_appointment", "attempted_at": None}
    assert not ctx.sent


async def preserved_booking(d, actual_minutes, added_minutes):
    person = await customer(d)
    identity = UUID(await appointment(d, person["id"], start_at=d["now"]-timedelta(minutes=45)))
    service = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET auto_complete_after=:after WHERE id=:t"),
                         {"after": d["now"]-timedelta(days=1), "t": d["a"]})
        await db.execute(text("UPDATE appointments SET actual_duration_min=:actual WHERE tenant_id=:t AND id=:id"),
                         {"actual": actual_minutes, "t": d["a"], "id": identity})
        await db.execute(text("""INSERT INTO services(id,tenant_id,name,duration_min,price_cents)
            VALUES(:id,:t,'Adicional sintético',30,1000)"""), {"id": service, "t": d["a"]})
        await db.execute(text("""INSERT INTO appointment_items
            (tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,price_snapshot_cents,
             prep_snapshot_min,cleanup_snapshot_min,item_order,item_origin,added_at,added_duration_min)
            VALUES(:t,:id,:service,'Adicional sintético',30,1000,0,0,1,'extra',clock_timestamp(),:added)"""),
            {"t": d["a"], "id": identity, "service": service, "added": added_minutes})
    return identity


async def candidate_ids(d):
    async with d["support"].connect() as db:
        rows = (await db.execute(text("SELECT tenant_id,appointment_id FROM public.orion_due_auto_completions(200)"))).all()
        return {row.appointment_id for row in rows if row.tenant_id == d["a"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("actual,added,due", [
    (None, 30, False), (60, 0, False), (40, 30, True),
    (None, 0, True), (20, 30, True), (70, 30, False),
])
async def test_scanner_and_worker_agree_on_preserved_and_actual_duration(management_db, monkeypatch, actual, added, due):
    d = management_db
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0")
    identity = await preserved_booking(d, actual, added)
    assert (identity in await candidate_ids(d)) is due
    assert await automatic.complete_due_appointment({}, str(d["a"]), str(identity)) == ("completed" if due else "not_due")
    async with d["migrator"].connect() as db:
        row = (await db.execute(text("""SELECT status,end_at-start_at AS reservation FROM appointments
            WHERE tenant_id=:t AND id=:id"""), {"t": d["a"], "id": identity})).mappings().one()
        count = (await db.execute(text("""SELECT count(*) FROM financial_entries
            WHERE tenant_id=:t AND appointment_id=:id"""), {"t": d["a"], "id": identity})).scalar_one()
    assert row["status"] == ("completed" if due else "confirmed")
    assert row["reservation"] == timedelta(minutes=30)
    assert count == (1 if due else 0)
    if not due:
        # Advance only these synthetic dates; completion still runs automatically
        # after effective service time without moving the reserved duration.
        async with d["migrator"].begin() as db:
            await db.execute(text("""UPDATE appointments SET
                start_at=start_at-interval '2 hours',end_at=end_at-interval '2 hours',
                occupied_from=occupied_from-interval '2 hours',occupied_until=occupied_until-interval '2 hours',
                updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": d["a"], "id": identity})
        assert identity in await candidate_ids(d)
        assert await automatic.complete_due_appointment({}, str(d["a"]), str(identity)) == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("actual,added", [(None, 30), (60, 0)])
async def test_motor_under_lock_rejects_premature_automatic_completion(management_db, monkeypatch, actual, added):
    d = management_db
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0")
    identity = await preserved_booking(d, actual, added)
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as db:
        with pytest.raises(HTTPException) as error:
            async with tenant_transaction(db, d["a"]):
                version = (await db.execute(text("SELECT updated_at FROM appointments WHERE tenant_id=:t AND id=:id"),
                    {"t": d["a"], "id": identity})).scalar_one()
                payload = SimpleNamespace(expected_status="confirmed", expected_updated_at=version,
                    coupon_id=None, final_total_cents=None, idempotency_key=str(uuid4()))
                await transition_appointment(db, d["a"], identity, "completed", payload, "system", automated=True)
        assert error.value.status_code == 409
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT status FROM appointments WHERE tenant_id=:t AND id=:id"),
                                {"t": d["a"], "id": identity})).scalar_one() == "confirmed"
        assert (await db.execute(text("""SELECT count(*) FROM appointment_history
            WHERE tenant_id=:t AND appointment_id=:id AND event='completed'"""),
            {"t": d["a"], "id": identity})).scalar_one() == 0
