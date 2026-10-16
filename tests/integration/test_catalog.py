"""FATIA 1: banco real, RLS, integridade referencial e autenticação do painel."""
import os
from uuid import uuid4, UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.catalog_api import support_session_factory
from app.database import get_engine, tenant_transaction
from app.catalog_schemas import RESOURCE_MODELS
from app.catalog_api import authenticate

pytestmark = pytest.mark.integration


def pgurl(s):
    return s.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture
async def setup_catalog(monkeypatch):
    runtime_url = os.getenv("APP_DATABASE_URL")
    mig_url = os.getenv("MIGRATION_DATABASE_URL")
    support_url = os.getenv("SUPPORT_DATABASE_URL")
    if not all((runtime_url, mig_url, support_url)):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("URLs runtime/migrator/support obrigatórias para integrar")
        pytest.skip("Requer PostgreSQL real")
    if not os.getenv("SUPPORT_API_TOKEN"):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("SUPPORT_API_TOKEN não configurado para CI")
        pytest.skip("Token de suporte não configurado")
    a, b = uuid4(), uuid4()
    migrator = create_async_engine(pgurl(mig_url))
    runtime = create_async_engine(runtime_url)
    support_session_factory.cache_clear()
    get_engine.cache_clear()
    monkeypatch.setenv("VIEWER_API_TOKEN", "viewer-ci-test-token-with-at-least-32-characters")
    monkeypatch.setenv("VIEWER_TENANT_ID", str(a))
    try:
        async with migrator.begin() as con:
            for t in (a, b):
                await con.execute(text("""
                    INSERT INTO tenants (id, name, slug)
                    VALUES (:id, :name, :slug)
                """), dict(id=t, name="Barbearia Teste Sintética", slug=f"test-{t}"))
        yield a, b, runtime, migrator
    finally:
        async with migrator.begin() as con:
            for table in [
                "working_breaks", "professional_services", "schedule_blocks",
                "time_off", "working_hours", "services", "authorized_staff",
                "professionals", "tenant_audit",
            ]:
                await con.execute(text(
                    f"DELETE FROM {table} WHERE tenant_id IN (:a, :b)"
                ), dict(a=a, b=b))
            await con.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"), dict(a=a, b=b)
            )
        await runtime.dispose()
        await migrator.dispose()
        support_session_factory.cache_clear()
        get_engine.cache_clear()


def headers(role="support"):
    if role == "support":
        key = os.environ["SUPPORT_API_TOKEN"]
    elif role == "viewer":
        key = os.environ["VIEWER_API_TOKEN"]
    else:
        key = "invalid-token"
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.asyncio
async def test_auth_rbac_and_tenant_scope(setup_catalog):
    a, b, *_ = setup_catalog
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/v1/tenants")).status_code == 401
        assert (await c.get("/api/v1/tenants", headers=headers("bad"))).status_code == 401
        assert (await c.get("/api/v1/tenants", headers=headers("viewer"))).status_code == 403
        assert (await c.get("/api/v1/tenants", headers=headers())).status_code == 200
        assert (await c.get(f"/api/v1/tenants/{a}/professionals", headers=headers("viewer"))).status_code == 200
        assert (await c.get(f"/api/v1/tenants/{b}/professionals", headers=headers("viewer"))).status_code == 403
        assert (await c.post(f"/api/v1/tenants/{a}/professionals", json={"name": "Vedado"}, headers=headers("viewer"))).status_code == 403
        assert (await c.get(f"/api/v1/tenants/{a}/unknown", headers=headers())).status_code == 404


@pytest.mark.asyncio
async def test_service_catalog_isolated_and_audited(setup_catalog):
    a, b, _, migrator = setup_catalog
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        path = f"/api/v1/tenants/{a}/services"
        response = await c.post(path, headers=headers(), json={
            "name": "Corte", "duration_min": 40, "price_cents": 3500,
            "prep_min": 5, "cleanup_min": 10,
        })
        assert response.status_code == 201, response.text
        service = response.json()
        assert service["price_cents"] == 3500
        assert (await c.get(f"/api/v1/tenants/{b}/services", headers=headers())).json() == []
        assert len((await c.get(path, headers=headers())).json()) == 1
        invalid = await c.post(path, headers=headers(), json={"name": "Barba", "duration_min": -1, "price_cents": -400})
        assert invalid.status_code == 422
        invalid = await c.post(path, headers=headers(), json={
            "name": "Ataque", "duration_min": 20, "price_cents": 1000, "tenant_id": str(b)
        })
        assert invalid.status_code == 422  # não aceitar tenant_id de payload
        upd = await c.put(path + "/" + service["id"], headers=headers(), json={
            "name": "Corte premium", "duration_min": 45, "price_cents": 4000
        })
        assert upd.status_code == 200, upd.text
        assert upd.json()["price_cents"] == 4000
        trail = (await c.get(f"/api/v1/tenants/{a}/audit", headers=headers())).json()
        assert {"created", "updated"}.issubset({x["action"] for x in trail})
        dele = await c.delete(path + "/" + service["id"], headers=headers())
        assert dele.status_code == 204
        assert (await c.get(path, headers=headers())).json() == []


@pytest.mark.asyncio
async def test_cross_tenant_assignment_rejected(setup_catalog):
    a, b, runtime, _ = setup_catalog
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        pa = (await c.post(f"/api/v1/tenants/{a}/professionals", headers=headers(), json={"name":"Pessoa A"})).json()
        sb = (await c.post(f"/api/v1/tenants/{b}/services", headers=headers(), json={
            "name": "Serviço B", "duration_min":20, "price_cents":2000
        })).json()
        bad = await c.post(f"/api/v1/tenants/{a}/professional_services",
            headers=headers(), json={"professional_id":pa["id"], "service_id":sb["id"]})
        assert bad.status_code == 409, bad.text
    factory = async_sessionmaker(runtime)
    async with factory() as session:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(session, a):
                await session.execute(text("""
                    INSERT INTO services (tenant_id, name, duration_min, price_cents)
                    VALUES (:b, 'invasão', 20, 100)
                """), {"b":b})


@pytest.mark.asyncio
async def test_hours_breaks_validation_and_blocks(setup_catalog):
    a, *_ = setup_catalog
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        base=f"/api/v1/tenants/{a}"
        professional=(await c.post(base+"/professionals",headers=headers(),json={"name":"Barbeiro Piloto"})).json()["id"]
        hours_payload={"professional_id":professional,"weekday":0,"start_time":"09:00","end_time":"18:00"}
        hours=await c.post(base+"/working_hours", headers=headers(), json=hours_payload)
        assert hours.status_code==201, hours.text
        assert (await c.post(base+"/working_hours", headers=headers(),json=hours_payload)).status_code==409
        assert (await c.post(base+"/working_hours", headers=headers(),json={
            **hours_payload, "start_time":"19:00", "end_time":"18:00"
        })).status_code==422
        block=await c.post(base+"/working_breaks",headers=headers(),json={
            "working_hour_id":hours.json()["id"],"start_time":"12:00","end_time":"13:00"
        })
        assert block.status_code==201, block.text
        assert (await c.post(base+"/working_breaks",headers=headers(),json={
            "working_hour_id":hours.json()["id"],"start_time":"12:30","end_time":"13:30"
        })).status_code==409
        assert (await c.post(base+"/working_breaks",headers=headers(),json={
            "working_hour_id":hours.json()["id"],"start_time":"08:00","end_time":"09:30"
        })).status_code==422
        assert (await c.post(base+"/time_off",headers=headers(),json={
            "professional_id": professional, "starts_at":"2026-12-15T09:00:00-04:00",
            "ends_at":"2026-12-15T18:00:00-04:00", "reason":"férias"
        })).status_code==201
        assert (await c.post(base+"/schedule_blocks",headers=headers(),json={
            "professional_id": professional, "starts_at":"2026-12-16T09:00:00",
            "ends_at":"2026-12-16T18:00:00"
        })).status_code==422  # sem fuso explícito


@pytest.mark.asyncio
async def test_no_context_denies_catalog_and_support_is_not_bypass(setup_catalog):
    a, b, runtime, migrator = setup_catalog
    async with runtime.begin() as con:
        assert (await con.execute(text("SELECT * FROM services"))).all()==[]
        assert (await con.execute(text("SELECT * FROM authorized_staff"))).all()==[]
        assert (await con.execute(text("SELECT * FROM tenants"))).all()==[]
    async with runtime.begin() as con:
        role = (await con.execute(text("""
            SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user
        """))).one()
        assert role[0]=="app_runtime" and role[1] is False
    factory = support_session_factory()
    async with factory() as session:
        async with session.begin():
            role=(await session.execute(text("""
                SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user
            """))).one()
            assert role[0]=="app_support" and role[1] is False
            # app_support não ganha leitura global de dados de clientes
            assert (await session.execute(text("""
                SELECT has_table_privilege(current_user, 'services', 'SELECT')
            """))).scalar_one() is False


@pytest.mark.asyncio
async def test_tenant_creation_is_global_support_only(setup_catalog):
    a,b,_,mig = setup_catalog
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        body={"name":"Piloto Sintético C","slug":"piloto-sintetico-"+uuid4().hex[:8],
              "timezone":"America/Campo_Grande"}
        assert (await c.post("/api/v1/tenants",headers=headers("viewer"),json=body)).status_code == 403
        res=await c.post("/api/v1/tenants",headers=headers(),json=body)
        assert res.status_code == 201,res.text
        created=UUID(res.json()["id"])
        assert (await c.post("/api/v1/tenants",headers=headers(),json=body)).status_code == 409
        async with mig.begin() as con:
            assert (await con.execute(text("""
                SELECT count(*) FROM platform_audit WHERE tenant_id=:id
            """), {"id":created})).scalar_one() == 1
            await con.execute(text("DELETE FROM platform_audit WHERE tenant_id=:id"),{"id":created})
            await con.execute(text("DELETE FROM tenants WHERE id=:id"), {"id":created})
