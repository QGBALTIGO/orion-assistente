"""Authenticated routes for the Delivery and Distribution review environment."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import delivery_catalog as catalog
from app import delivery_orders as orders
from app.catalog_api import Actor, authenticate
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response
from app.onboarding_service import access_session
from app.plan_permissions import lock_plan
from app.tenant_access import check_access
from app.workspace_policy import delivery_preview_enabled


router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/delivery", tags=["Delivery e Distribuição"])


async def run(actor, tenant, operation, *args, write=False, status=200, **kwargs):
    if actor.role != "member" or actor.maintenance or not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Use a conta vinculada à empresa de homologação")
    try:
        async with access_session(actor, tenant) as access:
            check_access(access.role, "draft" if write else "read")
            async with get_session_factory()() as db:
                async with tenant_transaction(db, tenant):
                    # Onboarding takes the exclusive lock when changing a niche.
                    await lock_plan(db, tenant)
                    niche = (await db.execute(text("""
                        SELECT configuration->>'niche' FROM business_onboarding WHERE tenant_id=:t
                    """), {"t": tenant})).scalar_one_or_none()
                    if not delivery_preview_enabled(tenant, niche):
                        raise HTTPException(403, "Delivery e Distribuição ainda está em homologação")
                    if write:
                        args = (access, *args)
                    result = await operation(db, tenant, *args, **kwargs)
    except IntegrityError:
        raise HTTPException(409, "Os dados já foram usados ou mudaram. Atualize a tela e confira.") from None
    response = json_response(result, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/catalog")
async def catalog_items(tenant_id: UUID, search: str = Query(default="", max_length=120),
                        category: str = Query(default="", max_length=80),
                        state: Literal["all", "active", "inactive"] = "all",
                        low_stock: bool = False, offset: int = Query(default=0, ge=0, le=100_000),
                        actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.list_items, search=search, category=category,
                     status_filter=state, low_stock=low_stock, offset=offset)


@router.get("/catalog/history")
async def movements(tenant_id: UUID, start: date | None = None, end: date | None = None,
                    offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    if (start and not 1900 <= start.year <= 2100) or (end and not 1900 <= end.year <= 2100):
        raise HTTPException(422, "Use datas entre 1900 e 2100")
    if start and end and start > end:
        raise HTTPException(422, "A data final deve ser igual ou posterior à inicial")
    return await run(actor, tenant_id, catalog.history, start=start, end=end, offset=offset)


@router.post("/catalog", status_code=201)
async def create_product(tenant_id: UUID, payload: catalog.ProductCreate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.create_item, payload, write=True, status=201)


@router.post("/catalog/products/{product_id}/variants", status_code=201)
async def create_variant(tenant_id: UUID, product_id: UUID, payload: catalog.VariantCreate,
                         actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.create_item, payload, product_id=product_id, write=True, status=201)


@router.put("/catalog/variants/{variant_id}")
async def edit_item(tenant_id: UUID, variant_id: UUID, payload: catalog.ItemUpdate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.update_item, variant_id, payload, write=True)


@router.post("/catalog/variants/{variant_id}/adjustments")
async def adjust_stock(tenant_id: UUID, variant_id: UUID, payload: catalog.Adjustment, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.adjust, variant_id, payload, write=True)


@router.get("/orders")
async def order_list(tenant_id: UUID, search: str = Query(default="", max_length=120),
                     state: Literal["all", "confirmed", "fulfilled", "cancelled"] = "all",
                     start: date | None = None, end: date | None = None,
                     offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.list_orders, search=search, state=state, start=start, end=end, offset=offset)


@router.get("/order-customers")
async def order_customers(tenant_id: UUID, search: str = Query(max_length=120), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.lookup_customers, search=search)


@router.post("/orders/estimate")
async def estimate_order(tenant_id: UUID, payload: orders.OrderItems, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.estimate, payload)


@router.get("/orders/{order_id}")
async def order_detail(tenant_id: UUID, order_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.detail, order_id)


@router.post("/orders", status_code=201)
async def new_order(tenant_id: UUID, payload: orders.ManualOrder, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.create, payload, write=True, status=201)


@router.post("/orders/{order_id}/transitions")
async def change_order(tenant_id: UUID, order_id: UUID, payload: orders.Transition, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.transition, order_id, payload, write=True)
