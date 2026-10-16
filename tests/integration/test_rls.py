"""Testes de segurança contra PostgreSQL REAL; SQLite/mocks não são aceitos."""
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.database import tenant_transaction

pytestmark = pytest.mark.integration


def async_url(value: str) -> str:
    return (
        value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
        .replace("postgres://", "postgresql+asyncpg://", 1)
    )


@pytest_asyncio.fixture
async def db():
    runtime_url = os.getenv("APP_DATABASE_URL")
    migrator_url = os.getenv("MIGRATION_DATABASE_URL")
    if not runtime_url or not migrator_url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Faltam URLs para teste real de PostgreSQL")
        pytest.skip("RLS exige PostgreSQL real (configurar URLs e migrations)")

    # Pool de uma única conexão permite provar ausência de vazamento transacional.
    runtime = create_async_engine(async_url(runtime_url), pool_size=1, max_overflow=0)
    migrator = create_async_engine(async_url(migrator_url))
    a, b = uuid4(), uuid4()
    try:
        async with migrator.begin() as con:
            assert (await con.execute(text("SELECT current_user"))).scalar_one() == "app_migrator"
            for key, slug in ((a, "a"), (b, "b")):
                await con.execute(
                    text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                    {"id": key, "name": f"Teste sintético {slug}", "slug": f"teste-{key}"},
                )
        yield runtime, migrator, a, b
    finally:
        async with migrator.begin() as con:
            await con.execute(
                text("DELETE FROM tenant_probe WHERE tenant_id IN (:a, :b)"),
                {"a": a, "b": b},
            )
            await con.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": a, "b": b},
            )
        await runtime.dispose()
        await migrator.dispose()


async def create_probe(runtime: AsyncEngine, tenant: UUID, label: str) -> UUID:
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    probe_id = uuid4()
    async with factory() as session:
        async with tenant_transaction(session, tenant):
            await session.execute(
                text("INSERT INTO tenant_probe (id, tenant_id, label) VALUES (:id, :tenant, :label)"),
                {"id": probe_id, "tenant": tenant, "label": label},
            )
    return probe_id


@pytest.mark.asyncio
async def test_tenant_b_cannot_read_a(db) -> None:
    runtime, _, a, b = db
    await create_probe(runtime, a, "visível somente A")
    factory = async_sessionmaker(runtime)
    async with factory() as session:
        async with tenant_transaction(session, b):
            rows = (await session.execute(text("SELECT label FROM tenant_probe"))).scalars().all()
            assert rows == []
            tenants = (await session.execute(text("SELECT id FROM tenants"))).scalars().all()
            assert tenants == [b]


@pytest.mark.asyncio
async def test_tenant_b_cannot_write_as_a(db) -> None:
    runtime, _, a, b = db
    factory = async_sessionmaker(runtime)
    async with factory() as session:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(session, b):
                await session.execute(
                    text("INSERT INTO tenant_probe (id, tenant_id, label) VALUES (:id, :a, 'tentativa')"),
                    {"id": uuid4(), "a": a},
                )


@pytest.mark.asyncio
async def test_no_context_is_fail_closed_for_reads_and_writes(db) -> None:
    runtime, _, a, _ = db
    await create_probe(runtime, a, "restrito")
    factory = async_sessionmaker(runtime)
    async with factory() as session:
        async with session.begin():
            assert (await session.execute(text("SELECT id FROM tenants"))).all() == []
            assert (await session.execute(text("SELECT id FROM tenant_probe"))).all() == []
    async with factory() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text("INSERT INTO tenant_probe (id, tenant_id, label) VALUES (:id, :a, 'negado')"),
                    {"id": uuid4(), "a": a},
                )


@pytest.mark.asyncio
async def test_pool_does_not_leak_tenant_context(db) -> None:
    runtime, _, a, b = db
    await create_probe(runtime, a, "do A")
    factory = async_sessionmaker(runtime)
    async with factory() as session:
        async with tenant_transaction(session, a):
            assert (await session.execute(text("SELECT label FROM tenant_probe"))).scalar_one() == "do A"
    # Mesma conexão física, contexto SET LOCAL encerrado.
    async with factory() as session:
        async with session.begin():
            assert (await session.execute(text("SELECT label FROM tenant_probe"))).all() == []
    async with factory() as session:
        async with tenant_transaction(session, b):
            assert (await session.execute(text("SELECT label FROM tenant_probe"))).all() == []


@pytest.mark.asyncio
async def test_runtime_is_not_owner_admin_or_migrator(db) -> None:
    runtime, _, _, _ = db
    async with runtime.connect() as con:
        row = (await con.execute(text("""
            SELECT current_user AS role,
                   r.rolsuper AS superuser,
                   r.rolbypassrls AS bypass_rls,
                   pg_has_role(current_user, 'app_migrator', 'MEMBER') AS is_migrator_member,
                   has_schema_privilege(current_user, 'public', 'CREATE') AS can_create,
                   (SELECT pg_get_userbyid(relowner)
                    FROM pg_class WHERE oid = 'public.tenant_probe'::regclass) AS owner
            FROM pg_roles r WHERE r.rolname = current_user
        """))).mappings().one()
    assert row["role"] == "app_runtime"
    assert row["superuser"] is False
    assert row["bypass_rls"] is False
    assert row["is_migrator_member"] is False
    assert row["can_create"] is False
    assert row["owner"] == "app_migrator"
