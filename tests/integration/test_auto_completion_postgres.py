"""Prospective end-time completion, commissions, RLS, and receivables on real PostgreSQL.

No production data. The fixture creates disposable tenants, customers,
appointments, and a protected migration activation timestamp.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import auto_completion_worker as automatic
from app.database import tenant_transaction
from test_management import appointment, customer, management_db

pytestmark = pytest.mark.integration


class QueueSpy:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, tenant, appointment_id, **opts):
        self.jobs.append((name, tenant, appointment_id, opts["_job_id"]))
        return True


async def mark_activation(d, when):
    async with d["migrator"].begin() as conn:
        await conn.execute(text("""
            UPDATE tenants SET auto_complete_after=:after
            WHERE id=:tenant
        """), {"tenant": d["a"], "after": when})


@pytest.mark.asyncio
async def test_automatic_completion_runs_at_end_without_charging_a_customer(management_db, monkeypatch):
    d = management_db
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS", "1")
    await mark_activation(d, d["now"] - timedelta(hours=2))
    person = await customer(d)
    appt = await appointment(d, person["id"], start_at=d["now"]-timedelta(hours=1), amount=10000)
    spy = QueueSpy()
    assert await automatic.scan_due_auto_completions({"redis": spy}) >= 1
    assert any(job[1]==str(d["a"]) and job[2]==appt for job in spy.jobs)
    results = await asyncio.gather(*(
        automatic.complete_due_appointment({}, str(d["a"]), appt) for _ in range(3)
    ))
    # A duplicate caller may receive the idempotent completed receipt; that is
    # not an additional completion. The database assertions below prove once-only
    # financial/history/commission effects.
    assert set(results) <= {"completed", "not_confirmed", "changed_before_completion"}, results
    assert "completed" in results
    async with d["migrator"].connect() as conn:
        row = (await conn.execute(text("""
            SELECT status,commission_percent_snapshot,commission_cents
            FROM appointments WHERE tenant_id=:tenant AND id=:appt
        """), {"tenant":d["a"],"appt":UUID(appt)})).mappings().one()
        assert row["status"] == "completed"
        assert float(row["commission_percent_snapshot"]) == 25
        assert row["commission_cents"] == 2500
        entries = (await conn.execute(text("""
            SELECT status,kind,amount_cents,payment_method,appointment_id
            FROM financial_entries WHERE tenant_id=:tenant AND appointment_id=:appt
        """), {"tenant": d["a"], "appt": UUID(appt)})).mappings().all()
        assert len(entries)==1
        assert entries[0]["amount_cents"]==10000
        assert entries[0]["kind"]=="income"
        assert entries[0]["status"]=="pending"
        assert entries[0]["payment_method"]=="other"
        history = (await conn.execute(text("""
            SELECT operator,details->'actor'->>'role' AS role,
                   details->>'source' AS source
            FROM appointment_history WHERE tenant_id=:tenant
              AND appointment_id=:appt AND event='completed'
        """), {"tenant":d["a"],"appt":UUID(appt)})).mappings().all()
        assert len(history)==1
        assert (history[0]["operator"],history[0]["role"],history[0]["source"])==(
            "system","system","system"
        )
    finance = (await d["client"].get(d["base"] + "/finance/summary",
       params={"start_date":d["now"].date().isoformat(),"end_date":d["now"].date().isoformat()})).json()
    assert finance["summary"]["pending_income_cents"]>=10000
    # Uncollected receipts never magically increase paid cash.
    assert finance["summary"]["balance_cents"]==0
    again = await automatic.complete_due_appointment({},str(d["a"]),appt)
    assert again=="not_confirmed"


@pytest.mark.asyncio
async def test_exact_end_boundary_activation_and_non_confirmed_states_are_respected(management_db,monkeypatch):
    d=management_db
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","1")
    person=await customer(d)
    # Existing overdue booking is deliberately older than feature cutover.
    older=await appointment(d,person["id"],start_at=d["now"]-timedelta(hours=2))
    newer=await appointment(d,person["id"],start_at=d["now"]+timedelta(minutes=15))
    cancelled=await appointment(d,person["id"],status="cancelled",
                                start_at=d["now"]-timedelta(hours=3))
    missing=await appointment(d,person["id"],status="no_show",
                              start_at=d["now"]-timedelta(hours=4))
    await mark_activation(d,d["now"]-timedelta(minutes=15))
    spy=QueueSpy()
    assert await automatic.scan_due_auto_completions({"redis":spy})==0
    for identity in (older,newer,cancelled,missing):
        outcome=await automatic.complete_due_appointment({},str(d["a"]),identity)
        assert outcome in {"not_due","not_confirmed"},(identity,outcome)
    async with d["migrator"].connect() as db:
        count=(await db.execute(text("""
           SELECT count(*) FROM financial_entries
           WHERE tenant_id=:tenant AND appointment_id=ANY(CAST(:ids AS uuid[]))
        """),{"tenant":d["a"],"ids":[UUID(x) for x in (older,newer,cancelled,missing)]})).scalar_one()
        assert count==0


@pytest.mark.asyncio
async def test_existing_paid_income_prevents_duplicate_receivable(management_db,monkeypatch):
    d=management_db
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","1")
    await mark_activation(d,d["now"]-timedelta(hours=3))
    person=await customer(d)
    appt=await appointment(d,person["id"],start_at=d["now"]-timedelta(hours=1))
    from test_management import entry
    await entry(d,description="Pagamento sintético já identificado",amount_cents=10000,
                appointment_id=appt,professional_id=str(d["pro"]),status="paid")
    assert await automatic.complete_due_appointment({},str(d["a"]),appt)=="completed"
    async with d["migrator"].connect() as db:
        result=(await db.execute(text("""
            SELECT count(*),min(status),max(status) FROM financial_entries
            WHERE tenant_id=:tenant AND appointment_id=:appt
        """),{"tenant":d["a"],"appt":UUID(appt)})).one()
        assert result==(1,"paid","paid")


@pytest.mark.asyncio
async def test_support_role_can_discover_opaque_due_ids_but_cannot_read_bookings(management_db,monkeypatch):
    d=management_db
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","1")
    await mark_activation(d,d["now"]-timedelta(hours=2))
    person=await customer(d)
    appt=await appointment(d,person["id"],start_at=d["now"]-timedelta(hours=1))
    async with d["support"].connect() as db:
        rows=(await db.execute(text("SELECT * FROM public.orion_due_auto_completions(100)"))).all()
        assert any(row.tenant_id==d["a"] and str(row.appointment_id)==appt for row in rows)
        from sqlalchemy.exc import DBAPIError
        try:
            await db.execute(text("SELECT * FROM public.appointments LIMIT 1"))
        except DBAPIError:
            await db.rollback()
        else:
            raise AssertionError("Support role cannot read appointment PII")


@pytest.mark.asyncio
async def test_auto_completion_disabled_remains_read_only(management_db,monkeypatch):
    d=management_db
    monkeypatch.delenv("ORION_AUTO_COMPLETE_APPOINTMENTS",raising=False)
    spy=QueueSpy()
    assert await automatic.scan_due_auto_completions({"redis":spy})==0
    assert not spy.jobs
    assert await automatic.complete_due_appointment({},str(d["a"]),str(d["pro"]))=="disabled"
