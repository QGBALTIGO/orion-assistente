"""Consumo por tenant, visível apenas a operador de suporte autorizado."""
from fastapi import APIRouter,Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,support_only,support_session_factory

router=APIRouter(prefix="/api/v1/admin",tags=["Métricas"])


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
