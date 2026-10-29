"""Persistência com papel runtime e contexto de tenant por transação.

A identidade do tenant deve ser obtida no SERVIDOR após autenticação ou
validação de webhook nas próximas fatias. Nunca aceitar tenant_id do cliente.
"""
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def engine_pool_options() -> dict:
    """One bounded per-process pool contract for runtime and support engines."""
    values = {}
    for name, option, default, minimum in (
        ("ORION_DB_POOL_SIZE", "pool_size", 5, 1),
        ("ORION_DB_MAX_OVERFLOW", "max_overflow", 10, 0),
    ):
        raw = os.getenv(name)
        if raw is None:
            value = default
        elif not raw.isascii() or not raw.isdecimal() or not 1 <= len(raw) <= 2:
            raise RuntimeError("Configuração dos pools PostgreSQL inválida")
        else:
            value = int(raw)
        if not minimum <= value <= 20:
            raise RuntimeError("Configuração dos pools PostgreSQL inválida")
        values[option] = value
    if os.getenv("PYTEST_CURRENT_TEST"):
        return {"pool_pre_ping": True, "poolclass": NullPool}
    return {"pool_pre_ping": True, **values}


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        raise RuntimeError("APP_DATABASE_URL não configurada (papel app_runtime)")
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("APP_DATABASE_URL exige PostgreSQL com asyncpg")
    if make_url(url).username != "app_runtime":
        raise RuntimeError("A API deve conectar somente com o papel app_runtime")
    return create_async_engine(url, **engine_pool_options())


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def tenant_transaction(
    session: AsyncSession, tenant_id: UUID, *, repeatable_read: bool = False
) -> AsyncIterator[AsyncSession]:
    """SET LOCAL dura somente a transação; RLS nega se contexto ausente.

    Somente o código servidor já autenticado pode fornecer o UUID.
    """
    if not isinstance(tenant_id, UUID):
        raise ValueError("tenant_id interno deve ser UUID verificado")
    async with session.begin():
        # Publication spans multiple catalog reads. Opt in before the first
        # statement so one snapshot is used without changing booking isolation.
        if repeatable_read:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        # Import lazily: channel registry imports the session factory. Unbound
        # legacy/owner calls execute no channel SQL and need no new schema.
        from app.qr_channel_scope import transaction_guard
        async with transaction_guard(session, tenant_id):
            from app.inbox_control import guard_transaction, _assistant_scope
            scope = _assistant_scope.get()
            if scope is not None and scope[0] != tenant_id:
                raise ValueError("Authenticated inbox tenant mismatch")
            await guard_transaction(session)
            from app.communication_events import guard_meta_transaction
            await guard_meta_transaction(session, tenant_id)
            from app.appointment_access import guard as appointment_access_guard
            await appointment_access_guard(session, tenant_id)
            yield session
            await appointment_access_guard(session, tenant_id)
            await guard_meta_transaction(session, tenant_id)
            await guard_transaction(session)
