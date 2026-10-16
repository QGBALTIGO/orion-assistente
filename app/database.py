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
    return create_async_engine(url, pool_pre_ping=True)


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
