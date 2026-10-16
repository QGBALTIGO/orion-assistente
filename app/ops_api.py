"""Leitura operacional restrita: health de dependências e fila, sem PII."""
import os
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from redis.asyncio import from_url

from app.catalog_api import Actor, authenticate, support_only, support_session_factory

router=APIRouter(prefix="/api/v1/ops",tags=["Operação"])


@router.get("/status")
async def operator_status(actor:Actor=Depends(authenticate)):
    support_only(actor)
    async with support_session_factory()() as s:
        await s.execute(text("SELECT 1"))
        wa=(await s.execute(text("""
            SELECT count(*) FROM inbound_events WHERE status IN ('pending','processing')
        """))).scalar_one()
        calendar=(await s.execute(text("""
            SELECT count(*) FROM calendar_outbox WHERE status='pending'
        """))).scalar_one()
    redis=from_url(os.getenv("REDIS_URL","redis://localhost:6379/0"))
    try:ready=bool(await asyncio.wait_for(redis.ping(),timeout=2))
    except (ConnectionError,TimeoutError,OSError):ready=False
    finally:await redis.aclose()
    return {"database":"ok","redis":"ok" if ready else "unavailable",
            "pending_whatsapp":wa,"pending_calendar":calendar}
