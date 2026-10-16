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
    options = {"pool_pre_ping": True}
    if os.getenv("PYTEST_CURRENT_TEST"):
        options["poolclass"] = NullPool
    return create_async_engine(url, **options)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def tenant_transaction(
    session: AsyncSession, tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """SET LOCAL dura somente a transação; RLS nega se contexto ausente.

    Somente o código servidor já autenticado pode fornecer o UUID.
    """
    if not isinstance(tenant_id, UUID):
        raise ValueError("tenant_id interno deve ser UUID verificado")
    async with session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        yield session
