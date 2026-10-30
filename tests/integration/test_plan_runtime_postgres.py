"""Real isolated PostgreSQL limits/downgrade tests. No provider is started."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import tenant_transaction
from app.plan_limits import ensure_professional_batch
from app.plan_permissions import has_feature, plan_state
from app.professional_commissions import (
    ProfessionalConfiguration, ProfessionalStatusChange,
    change_professional_status, save_professional_configuration,
)

pytestmark = pytest.mark.integration


def _local_url(name, role):
    source = os.getenv(name)
    if not source:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("The disposable PostgreSQL plan gate is mandatory")
        pytest.skip("Disposable PostgreSQL roles are unavailable locally")
    url = make_url(source)
    if url.host not in {"localhost", "127.0.0.1", "::1"} or url.username != role:
        pytest.fail("Only loopback PostgreSQL with dedicated test roles is allowed")
    return str(url.set(drivername="postgresql+asyncpg")) if not url.password else url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


@pytest_asyncio.fixture
async def plan_case(monkeypatch):
    urls = {"runtime": _local_url("APP_DATABASE_URL", "app_runtime"),
            "migration": _local_url("MIGRATION_DATABASE_URL", "app_migrator")}
    _local_url("SUPPORT_DATABASE_URL", "app_support")
    for name in ("CAKTO_BILLING_LIVE_ENABLED", "META_LIVE_SEND", "QR_AI_ENABLED",
                 "QR_CHANNELS_ENABLED", "MARKETING_EXECUTION_ENABLED", "INBOX_SEND_ENABLED"):
        monkeypatch.setenv(name, "0")
    engines = {name:create_async_engine(url, poolclass=NullPool) for name,url in urls.items()}
    runtime = async_sessionmaker(engines["runtime"], expire_on_commit=False)
    a, b, admin = uuid4(), uuid4(), uuid4()
    try:
        async with engines["migration"].begin() as db:
            for tenant in (a,b):
                await db.execute(text("INSERT INTO tenants(id,name,slug,status) VALUES(:id,'Empresa sintética de plano',:slug,'active')"),
                                 {"id":tenant,"slug":"plan-runtime-"+str(tenant)})
            await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(:id,'Admin sintético',:email,'unused-test-hash','admin')"),
                             {"id":admin,"email":str(admin)+"@example.test"})
            for tenant in (a,b):
                await db.execute(text("""INSERT INTO tenant_plan_controls
                    (tenant_id,manual_plan_code,manual_started_at,manual_actor_id)
                    VALUES(:t,'essencial',clock_timestamp()-interval '1 day',:admin)"""),
                    {"t":tenant,"admin":admin})
        async def grant(code, *, tenant=a):
            async with engines["migration"].begin() as db:
                await db.execute(text("""UPDATE tenant_plan_controls SET manual_plan_code=:code,
                    version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t"""),
                    {"code":code,"t":tenant})
        yield SimpleNamespace(a=a,b=b,admin=admin,runtime=runtime,
                              migration=engines["migration"],grant=grant)
    finally:
        async with engines["migration"].begin() as db:
            for table in ("appointment_items","appointment_history","calendar_outbox","appointments","customers",
                          "professional_commission_rules","professional_services","management_requests",
                          "tenant_audit","professionals","tenant_plan_audit","tenant_plan_controls"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a":a,"b":b})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a":a,"b":b})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:id"), {"id":admin})
        for engine in engines.values(): await engine.dispose()


async def _create(case, *, active=True, identity=None):
    payload = ProfessionalConfiguration(name="Profissional sintético", active=active,
        idempotency_key=identity or "staff-"+str(uuid4()))
    async with case.runtime() as db:
        async with tenant_transaction(db, case.a):
            return await save_professional_configuration(db,case.a,payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,limit", [("essencial",2),("profissional",6),("elite",10)])
async def test_twelve_concurrent_creations_cannot_exceed_active_professional_limit(plan_case, code, limit):
    c = plan_case
    await c.grant(code)
    results = await asyncio.wait_for(asyncio.gather(*(_create(c) for _ in range(12)), return_exceptions=True), 30)
    winners = [row for row in results if isinstance(row,dict)]
    assert len(winners) == limit
    assert all(isinstance(row,dict) or isinstance(row,HTTPException) and row.status_code == 409 for row in results)
    async with c.runtime() as db:
        async with tenant_transaction(db,c.a):
            state = await plan_state(db,c.a)
            assert state["limits"]["active_professionals"] == limit
            assert state["limits"]["excess_professionals"] == 0


@pytest.mark.asyncio
async def test_replayed_creation_does_not_consume_another_slot(plan_case):
    c = plan_case
    first = await _create(c,identity="synthetic-staff-replay")
    await _create(c)
    replay = await _create(c,identity="synthetic-staff-replay")
    assert replay["id"] == first["id"]
    with pytest.raises(HTTPException) as error: await _create(c)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_downgrade_preserves_eight_professionals_and_commissions_without_new_activation(plan_case):
    c = plan_case
    await c.grant("elite")
    rows = [await _create(c) for _ in range(8)]
    inactive = await _create(c,active=False)
    identity = rows[0]["id"]
    customer,appointment=uuid4(),uuid4()
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE professionals SET commission_percent=25 WHERE tenant_id=:t AND id=:id"),
                         {"t":c.a,"id":identity})
        await db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164) VALUES(:id,:t,'Cliente sintético','+5567999999901')"),
                         {"id":customer,"t":c.a})
        starts=datetime.now(timezone.utc)+timedelta(days=2)
        await db.execute(text("""INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,
            idempotency_key,request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents)
            VALUES(:id,:t,:customer,:pro,'AG-ABCDEF',:key,:hash,:start,:end,:start,:end,'confirmed',4000)"""),
            {"id":appointment,"t":c.a,"customer":customer,"pro":identity,"key":str(uuid4()),"hash":"a"*64,
             "start":starts,"end":starts+timedelta(minutes=30)})
    await c.grant("essencial")
    async with c.runtime() as db:
        async with tenant_transaction(db,c.a):
            edited = await save_professional_configuration(db,c.a,
                ProfessionalConfiguration(name="Nome atualizado",active=True,expected_version=rows[0]["commission_config_version"]),identity)
            assert edited["commission_percent"] == 25
            state = await plan_state(db,c.a)
            assert state["limits"] == {"max_professionals":2,"active_professionals":8,"excess_professionals":6}
    async with c.runtime() as db:
        with pytest.raises(HTTPException) as error:
            async with tenant_transaction(db,c.a):
                await change_professional_status(db,c.a,identity,
                    ProfessionalStatusChange(active=False,expected_version=edited["commission_config_version"]))
        assert error.value.status_code == 409 and "Reagende" in error.value.detail
    with pytest.raises(HTTPException) as error: await _create(c)
    assert error.value.status_code == 409
    async with c.runtime() as db:
        with pytest.raises(HTTPException) as error:
            async with tenant_transaction(db,c.a):
                await change_professional_status(db,c.a,inactive["id"],
                    ProfessionalStatusChange(active=True,expected_version=inactive["commission_config_version"]))
        assert error.value.status_code == 409
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM professionals WHERE tenant_id=:t"),{"t":c.a})).scalar_one() == 9
        saved=(await db.execute(text("SELECT status,total_cents,professional_id FROM appointments WHERE tenant_id=:t AND id=:id"),
                               {"t":c.a,"id":appointment})).mappings().one()
        assert dict(saved) == {"status":"confirmed","total_cents":4000,"professional_id":identity}


@pytest.mark.asyncio
async def test_onboarding_batch_cannot_activate_three_professionals_on_essential(plan_case):
    c = plan_case
    rows = [await _create(c,active=False) for _ in range(3)]
    async with c.runtime() as db:
        with pytest.raises(HTTPException) as error:
            async with tenant_transaction(db,c.a):
                await ensure_professional_batch(db,c.a,{str(row["id"]):True for row in rows})
        assert error.value.status_code == 409
    async with c.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM professionals WHERE tenant_id=:t AND active"),{"t":c.a})).scalar_one() == 0


@pytest.mark.asyncio
async def test_runtime_snapshot_and_limit_queries_cannot_read_other_tenant(plan_case):
    c = plan_case
    await c.grant("elite",tenant=c.b)
    async with c.runtime() as db:
        async with tenant_transaction(db,c.a):
            assert not await has_feature(db,c.a,"conversations.manual")
            assert await has_feature(db,c.a,"performance.advanced")
            assert await has_feature(db,c.a,"notifications.basic")
            assert not await has_feature(db,c.a,"reception.ai")
        with pytest.raises(DBAPIError) as error:
            async with tenant_transaction(db,c.a): await plan_state(db,c.b)
        assert "tenant_plan_scope_required" in str(error.value)
