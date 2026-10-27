"""Real local PostgreSQL/Redis readiness, query/auth failures and cancellation."""
import os

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import health_api, public_observability
from app import public_site_api

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def readiness_dependencies(monkeypatch):
    database_url, redis_url = os.getenv("APP_DATABASE_URL"), os.getenv("REDIS_URL")
    if not database_url or not redis_url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1" or os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Local PostgreSQL and Redis are required for readiness verification")
        pytest.skip("Requires synthetic local PostgreSQL and Redis")
    database_url = database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    # No provider credentials, real tenants or migrations are needed by SELECT 1.
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    app = FastAPI()
    app.include_router(health_api.router)
    app.state.panel_session_redis = redis
    monkeypatch.setattr(health_api, "get_session_factory", lambda: async_sessionmaker(engine))
    monkeypatch.setattr(public_observability, "_runtime", public_observability.PublicRuntimeStatistics())
    try:
        yield {"app": app, "engine": engine, "redis": redis, "redis_url": redis_url}
    finally:
        await redis.aclose()
        await engine.dispose()


async def probe(d):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]), base_url="http://test") as client:
        return await client.get("/health/ready")


async def test_runtime_readiness_queries_real_runtime_database_and_shared_redis(readiness_dependencies):
    d = readiness_dependencies
    response = await probe(d)
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {"postgres": "ok", "redis": "ok"}}
    # The readiness request did not close the application-owned pool.
    assert await d["redis"].ping()
    assert public_observability.runtime_snapshot()["readiness"]["checks"] == 1


async def test_runtime_readiness_fails_closed_on_real_postgres_query_error(readiness_dependencies, monkeypatch):
    d = readiness_dependencies
    class FaultSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            return await super().execute(text("SELECT 1/0"))
    monkeypatch.setattr(health_api, "get_session_factory", lambda: async_sessionmaker(d["engine"], class_=FaultSession))
    response = await probe(d)
    assert response.status_code == 503
    assert response.json()["dependencies"] == {"postgres": "unavailable", "redis": "ok"}
    assert "division" not in response.text and "SELECT" not in response.text


async def test_runtime_readiness_fails_closed_on_real_redis_auth_error(readiness_dependencies):
    d = readiness_dependencies
    # Rejected synthetic authentication exercises a real Redis error without
    # modifying the Redis configuration, clearing keys or restarting anything.
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(d["redis_url"])
    rejected = urlunsplit((parsed.scheme, "synthetic-invalid-user:synthetic-invalid-password@" + parsed.netloc.split("@")[-1],
                          parsed.path, parsed.query, parsed.fragment))
    invalid = Redis.from_url(rejected, socket_connect_timeout=1, socket_timeout=1)
    d["app"].state.panel_session_redis = invalid
    try:
        response = await probe(d)
        assert response.status_code == 503
        assert response.json()["dependencies"] == {"postgres": "ok", "redis": "unavailable"}
        assert "synthetic-invalid" not in response.text and "AUTH" not in response.text
    finally:
        await invalid.aclose()


async def test_runtime_readiness_times_out_a_real_postgres_wait_and_returns_connection(readiness_dependencies, monkeypatch):
    d = readiness_dependencies
    class SlowSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            return await super().execute(text("SELECT pg_sleep(3)"))
    monkeypatch.setattr(health_api, "get_session_factory", lambda: async_sessionmaker(d["engine"], class_=SlowSession))
    monkeypatch.setattr(health_api, "CHECK_TIMEOUT_SECONDS", 0.05)
    response = await probe(d)
    assert response.status_code == 503
    assert response.json()["dependencies"] == {"postgres": "timeout", "redis": "ok"}
    # Cancellation must leave no checked-out runtime connection behind.
    assert d["engine"].pool.checkedout() == 0
    async with d["engine"].connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


def public_app(d):
    app = FastAPI()
    app.add_middleware(public_observability.PublicRuntimeMiddleware)
    app.include_router(public_site_api.router)
    app.state.panel_session_redis = d["redis"]
    return app


async def public_availability(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        return await client.get("/api/v1/public/sites/synthetic-site/availability", params={
            "professional_id": "10000000-0000-4000-8000-000000000001",
            "service_id": "20000000-0000-4000-8000-000000000002", "day": "2030-01-01"})


async def skip_network_quota(*args, **kwargs):
    # The separate DB12 contract suite exercises real quotas. These dependency
    # tests must not touch the surrounding runner's Redis network counters.
    pass


async def test_public_availability_real_connection_refusal_is_retryable_without_exposing_dsn(readiness_dependencies, monkeypatch):
    d = readiness_dependencies
    target = make_url(str(d["engine"].url.render_as_string(hide_password=False)))
    assert target.host in {"127.0.0.1", "localhost", "::1"}
    refused = create_async_engine(target.set(port=1), connect_args={"timeout": 0.5})
    monkeypatch.setattr(public_site_api, "public_network_limit", skip_network_quota)
    monkeypatch.setattr(public_site_api, "get_session_factory", lambda: async_sessionmaker(refused))
    try:
        response = await public_availability(public_app(d))
        assert response.status_code == 503, response.text
        assert response.headers["retry-after"] == "2"
        assert response.json()["detail"]["code"] == "dependency_unavailable"
        assert "127.0.0.1" not in response.text and "app_runtime" not in response.text
        assert not target.password or target.password not in response.text
        assert refused.pool.checkedout() == 0
    finally:
        await refused.dispose()


async def test_public_availability_real_postgres_wait_is_cancelled_by_request_deadline(readiness_dependencies, monkeypatch):
    d = readiness_dependencies
    class SlowSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            return await super().execute(text("SELECT pg_sleep(3)"))
    monkeypatch.setattr(public_site_api, "public_network_limit", skip_network_quota)
    monkeypatch.setattr(public_site_api, "get_session_factory", lambda: async_sessionmaker(d["engine"], class_=SlowSession))
    monkeypatch.setattr(public_observability, "PUBLIC_READ_TIMEOUT_SECONDS", 0.05)
    response = await public_availability(public_app(d))
    assert response.status_code == 503
    assert d["engine"].pool.checkedout() == 0
    assert public_observability.runtime_snapshot()["dependency_failures"]["deadline"] == 1
    async with d["engine"].connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


async def test_public_availability_real_query_bug_is_not_disguised_as_dependency_outage(readiness_dependencies, monkeypatch):
    d = readiness_dependencies
    class FaultSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            return await super().execute(text("SELECT 1/0"))
    monkeypatch.setattr(public_site_api, "public_network_limit", skip_network_quota)
    monkeypatch.setattr(public_site_api, "get_session_factory", lambda: async_sessionmaker(d["engine"], class_=FaultSession))
    response = await public_availability(public_app(d))
    assert response.status_code == 500
    assert "division" not in response.text and "SELECT" not in response.text
    assert sum(public_observability.runtime_snapshot()["dependency_failures"].values()) == 0
