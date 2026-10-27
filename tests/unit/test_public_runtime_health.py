"""Readiness and bounded operational signals never expose request identities."""
import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException
import httpx
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError, TimeoutError as PoolTimeoutError

from app import health_api, public_observability
from app.catalog_api import Actor, authenticate
from app.metrics_api import router as metrics_router
from app.public_observability import PublicRuntimeMiddleware


async def test_cache_builder_deadline_does_not_abort_another_http_request(monkeypatch):
    from uuid import uuid4
    from fastapi import Request
    from app import public_site_runtime
    monkeypatch.setattr(public_observability,'PUBLIC_READ_TIMEOUT_SECONDS',.2)
    app=FastAPI();app.add_middleware(PublicRuntimeMiddleware)
    key=(str(uuid4()),1,'UTC',0)
    entered=asyncio.Event()
    @app.get('/api/v1/public/sites/synthetic')
    async def catalogue(request:Request):
        async def build():
            if request.query_params.get('owner')=='yes':
                entered.set()
                await asyncio.Event().wait()
            return {'services':['Published']}
        return await public_site_runtime.cached_catalogue(key,build)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        first=asyncio.create_task(client.get('/api/v1/public/sites/synthetic?owner=yes'))
        await entered.wait()
        await asyncio.sleep(.1)
        second=asyncio.create_task(client.get('/api/v1/public/sites/synthetic'))
        original,follower=await asyncio.gather(first,second)
    assert original.status_code==503 and original.json()['detail']['retryable']
    assert follower.status_code==200 and follower.json()=={'services':['Published']}
    public_site_runtime.clear_catalogue_cache(key[0])

SECRET = "synthetic-secret-that-must-not-appear"
PRIVATE = "postgresql+asyncpg://app_runtime:" + SECRET + "@private.invalid/database?tenant=private-customer"


class FakeRedis:
    def __init__(self, *, error=None, stall=False):
        self.error = error
        self.stall = stall
        self.closed = 0

    async def ping(self):
        if self.stall:
            await asyncio.Event().wait()
        if self.error:
            raise RuntimeError(PRIVATE)
        return True

    async def aclose(self):
        self.closed += 1


class FakeSession:
    error = False
    stall = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, statement):
        assert str(statement) == "SELECT 1"
        if self.stall:
            await asyncio.Event().wait()
        if self.error:
            raise RuntimeError(PRIVATE)
        return self

    def scalar_one(self):
        return 1


@pytest.fixture
def health_app(monkeypatch):
    app = FastAPI()
    app.include_router(health_api.router)
    app.include_router(metrics_router)
    app.add_middleware(PublicRuntimeMiddleware)
    monkeypatch.setattr(public_observability, "_runtime", public_observability.PublicRuntimeStatistics())
    monkeypatch.setattr(health_api, "get_session_factory", lambda: FakeSession)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-runtime-support-key-at-least-32-characters")
    return app


@pytest.mark.parametrize("failure", [None, "postgres", "redis", "timeout_postgres", "timeout_redis"])
async def test_readiness_fails_closed_without_exposing_dependency_errors(health_app, monkeypatch, caplog, failure):
    caplog.set_level(logging.INFO, logger="orion.public_runtime")
    monkeypatch.setattr(FakeSession, "error", failure == "postgres")
    monkeypatch.setattr(FakeSession, "stall", failure == "timeout_postgres")
    redis = FakeRedis(error=failure == "redis", stall=failure == "timeout_redis")
    health_app.state.panel_session_redis = redis
    monkeypatch.setattr(health_api, "CHECK_TIMEOUT_SECONDS", 0.02)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == (200 if failure is None else 503)
    body = response.json()
    assert set(body) == {"status", "dependencies"}
    assert set(body["dependencies"]) == {"postgres", "redis"}
    if failure:
        dependency = failure.removeprefix("timeout_")
        assert body["dependencies"][dependency] == ("timeout" if failure.startswith("timeout_") else "unavailable")
    assert response.headers["cache-control"] == "no-store"
    assert redis.closed == 0  # A shared application pool remains owned by lifespan.
    raw = response.text + " ".join(record.getMessage() for record in caplog.records)
    for private in (PRIVATE, SECRET, "private.invalid", "private-customer", "app_runtime"):
        assert private not in raw
    snapshot = public_observability.runtime_snapshot()
    assert snapshot["readiness"]["checks"] == 1
    assert snapshot["readiness"]["failures"] == (0 if failure is None else 1)


@pytest.mark.parametrize("stall", [False, True])
async def test_readiness_owns_and_closes_only_its_fallback_redis(health_app, monkeypatch, stall):
    fallback = FakeRedis(stall=stall)
    monkeypatch.setattr("app.panel_auth._redis", lambda: fallback)
    monkeypatch.setattr(health_api, "CHECK_TIMEOUT_SECONDS", 0.02)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == (503 if stall else 200)
    assert fallback.closed == 1


async def test_readiness_checks_dependencies_in_parallel(health_app, monkeypatch):
    postgres_started, redis_started = asyncio.Event(), asyncio.Event()
    class CoordinatedSession(FakeSession):
        async def execute(self, statement):
            postgres_started.set()
            await redis_started.wait()
            return self
    class CoordinatedRedis(FakeRedis):
        async def ping(self):
            redis_started.set()
            await postgres_started.wait()
            return True
    monkeypatch.setattr(health_api, "get_session_factory", lambda: CoordinatedSession)
    health_app.state.panel_session_redis = CoordinatedRedis()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        assert (await client.get("/health/ready")).status_code == 200
    assert health_api.CHECK_TIMEOUT_SECONDS == 2.0


async def test_public_runtime_authentication_uses_existing_support_boundary(health_app, monkeypatch):
    monkeypatch.setenv("VIEWER_API_TOKEN", "synthetic-runtime-viewer-key-at-least-32-characters")
    monkeypatch.setenv("VIEWER_TENANT_ID", str(UUID(int=1)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        assert (await client.get("/api/v1/admin/public-runtime")).status_code == 401
        assert (await client.get("/api/v1/admin/public-runtime", headers={"Authorization": "Bearer synthetic-runtime-viewer-key-at-least-32-characters"})).status_code == 403
        response = await client.get("/api/v1/admin/public-runtime", headers={"Authorization": "Bearer synthetic-runtime-support-key-at-least-32-characters"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["aggregation"] == "process"
        for role, expected in (("member", 403), ("support", 200), ("admin", 200)):
            health_app.dependency_overrides[authenticate] = lambda: Actor(role)
            assert (await client.get("/api/v1/admin/public-runtime")).status_code == expected


async def test_public_request_metrics_and_logs_have_fixed_cardinality_without_pii(health_app, caplog):
    caplog.set_level(logging.INFO, logger="orion.public_runtime")
    @health_app.get("/api/v1/public/sites/{slug}")
    async def catalogue(slug: str):
        return {"ok": True}
    @health_app.post("/api/v1/public/sites/{slug}/appointments")
    async def booking(slug: str):
        raise HTTPException(409, PRIVATE)
    @health_app.get("/s/{slug}")
    async def page(slug: str):
        raise RuntimeError(PRIVATE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app, raise_app_exceptions=False), base_url="http://test") as client:
        for index in range(125):
            assert (await client.get(f"/api/v1/public/sites/private-person-{index}?phone=5511999999999&token={SECRET}")).status_code == 200
        assert (await client.post("/api/v1/public/sites/private-person/appointments", json={"phone": "5511999999999"})).status_code == 409
        assert (await client.get("/s/private-person")).status_code == 500
    public_observability.observe_gate(0.02)
    public_observability.observe_gate(1.0, admitted=False)
    public_observability.observe_cache(True)
    public_observability.observe_cache(False, kind="page")
    public_observability.observe_cache(True, kind=PRIVATE)
    snapshot = public_observability.runtime_snapshot()
    assert set(snapshot["requests"]) == set(public_observability.ROUTES)
    assert snapshot["requests"]["catalogue"]["status_codes"]["200"] == 125
    assert snapshot["requests"]["booking"]["status_codes"]["409"] == 1
    assert snapshot["requests"]["page"]["status_codes"]["500"] == 1
    assert snapshot["requests"]["catalogue"]["duration_by_status_class"]["2xx"]["count"] == 125
    assert snapshot["gate"]["admitted"] == snapshot["gate"]["rejected"] == 1
    assert snapshot["cache"] == {"catalogue": {"hit": 1, "miss": 0}, "page": {"hit": 0, "miss": 1}}
    assert snapshot["pool_wait"]["available"] is False
    log = " ".join(record.getMessage() for record in caplog.records if record.name == "orion.public_runtime")
    for private in (PRIVATE, SECRET, "private-person", "5511999999999", "phone=", "token=", "private.invalid"):
        assert private not in log + json.dumps(snapshot)
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == "orion.public_runtime"]
    assert all(set(event) == {"event", "route", "status", "duration_ms"} for event in events)


def test_readiness_is_the_deployment_probe_without_changing_liveness():
    root = Path(__file__).resolve().parents[2]
    if not (root / "railway.json").exists():
        pytest.skip("Source deployment manifests are not shipped in the runtime image")
    assert json.loads((root / "railway.json").read_text())["deploy"]["healthcheckPath"] == "/health/ready"
    assert "http://localhost:8000/health/ready" in (root / "compose.yaml").read_text()


@pytest.mark.parametrize("failure", [
    ConnectionRefusedError(1225, PRIVATE),
    OSError("Multiple exceptions: [WinError 1225] " + PRIVATE),
    OSError("Multiple exceptions: [Errno 111] " + PRIVATE),
    PoolTimeoutError(PRIVATE),
    DBAPIError("SELECT synthetic", {}, ValueError(PRIVATE), connection_invalidated=True),
    DBAPIError("SELECT synthetic", {}, ConnectionResetError(PRIVATE)),
    asyncpg.ConnectionDoesNotExistError(PRIVATE),
    asyncpg.AdminShutdownError(PRIVATE),
    asyncpg.InterfaceError("connection is closed"),
])
async def test_public_dependency_connection_failures_become_generic_retryable_503(health_app, caplog, failure):
    caplog.set_level(logging.INFO, logger="orion.public_runtime")
    @health_app.get("/api/v1/public/sites/{slug}/availability")
    async def availability(slug: str):
        raise failure
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/api/v1/public/sites/private-person/availability?phone=5511999999999")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["detail"]["code"] == "dependency_unavailable"
    assert response.json()["detail"]["retryable"] is True
    raw = response.text + " ".join(record.getMessage() for record in caplog.records if record.name == "orion.public_runtime")
    for private in (PRIVATE, SECRET, "private-person", "5511999999999", "private.invalid", "SELECT synthetic"):
        assert private not in raw
    snapshot = public_observability.runtime_snapshot()
    assert sum(snapshot["dependency_failures"].values()) == 1
    assert snapshot["requests"]["availability"]["status_codes"]["503"] == 1


@pytest.mark.parametrize("failure", [
    ValueError(PRIVATE), FileNotFoundError(PRIVATE),
    FileNotFoundError(2, "A file name containing [Errno 111] " + PRIVATE),
    IntegrityError("SELECT synthetic", {}, ConnectionResetError(PRIVATE), connection_invalidated=True),
    ProgrammingError("SELECT synthetic", {}, ValueError(PRIVATE)),
    asyncpg.InterfaceError("another operation is in progress"),
    asyncpg.DivisionByZeroError(PRIVATE),
])
async def test_public_logical_integrity_and_query_failures_keep_server_error_semantics(health_app, failure):
    @health_app.get("/api/v1/public/sites/{slug}/availability")
    async def availability(slug: str):
        raise failure
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/api/v1/public/sites/private-person/availability")
    assert response.status_code == 500
    assert SECRET not in response.text and PRIVATE not in response.text
    assert sum(public_observability.runtime_snapshot()["dependency_failures"].values()) == 0


async def test_public_dependency_wait_has_a_finite_cancelling_deadline(health_app, monkeypatch):
    finished = asyncio.Event()
    monkeypatch.setattr(public_observability, "PUBLIC_READ_TIMEOUT_SECONDS", 0.02)
    @health_app.get("/api/v1/public/sites/{slug}/availability")
    async def availability(slug: str):
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        response = await asyncio.wait_for(client.get("/api/v1/public/sites/synthetic/availability"), timeout=1)
    assert response.status_code == 503 and finished.is_set()
    assert public_observability.runtime_snapshot()["dependency_failures"]["deadline"] == 1


async def test_public_recovery_is_observed_as_bounded_booking_route(health_app):
    @health_app.post("/api/v1/public/sites/{slug}/appointments/recover")
    async def recovery(slug: str):
        return {"status": "confirmed"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app), base_url="http://test") as client:
        assert (await client.post("/api/v1/public/sites/synthetic/appointments/recover")).status_code == 200
    assert public_observability.runtime_snapshot()["requests"]["booking"]["status_codes"]["200"] == 1
