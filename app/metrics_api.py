"""Consumo por tenant, visível apenas a operador de suporte autorizado."""
from fastapi import APIRouter,Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,support_only,support_session_factory
from app.public_observability import runtime_snapshot

router=APIRouter(prefix="/api/v1/admin",tags=["Métricas"])


@router.get("/public-runtime")
async def public_runtime(actor:Actor=Depends(authenticate)):
    support_only(actor)
    return JSONResponse(runtime_snapshot(), headers={"Cache-Control": "no-store"})


@router.get("/usage")
async def usage_by_tenant(actor:Actor=Depends(authenticate)):
    support_only(actor)
    async with support_session_factory()() as s:
        data=(await s.execute(text("""
            SELECT tenant_id,period,inbound_messages,outbound_messages,
                   bookings,estimated_cost_cents
            FROM usage_counters ORDER BY period DESC,tenant_id LIMIT 500
        """))).mappings().all()
        return jsonable_encoder(data)
