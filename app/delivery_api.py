"""Authenticated routes for the Delivery and Distribution review environment."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import delivery_catalog as catalog
from app import delivery_containers as containers
from app import delivery_logistics as logistics
from app import delivery_orders as orders
from app import delivery_payments as payments
from app import delivery_reports as reports
from app import delivery_reports_ops
from app.catalog_api import Actor, authenticate
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response
from app.onboarding_service import access_session
from app.plan_permissions import lock_plan
from app.tenant_access import check_access
from app.workspace_policy import delivery_preview_enabled


router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/delivery", tags=["Delivery e Distribuição"])


async def run(actor, tenant, operation, *args, write=False, publish=False, response_status=200, **kwargs):
    if actor.role != "member" or actor.maintenance or not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Use a conta vinculada à empresa de homologação")
    try:
        async with access_session(actor, tenant) as access:
            check_access(access.role, "publish" if publish else "draft" if write else "read")
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
    response = result if isinstance(result, Response) else json_response(result, status_code=response_status)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/catalog")
async def catalog_items(tenant_id: UUID, search: str = Query(default="", max_length=120),
                        category: str = Query(default="", max_length=80),
                        state: Literal["all", "active", "inactive"] = "all",
                        low_stock: bool = False, offset: int = Query(default=0, ge=0, le=100_000),
                        sale_mode: orders.OrderMode | None = None,
                        actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.list_items, search=search, category=category,
                     status_filter=state, low_stock=low_stock, offset=offset, sale_mode=sale_mode)


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
    return await run(actor, tenant_id, catalog.create_item, payload, write=True, response_status=201)


@router.post("/catalog/products/{product_id}/variants", status_code=201)
async def create_variant(tenant_id: UUID, product_id: UUID, payload: catalog.VariantCreate,
                         actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.create_item, payload, product_id=product_id, write=True, response_status=201)


@router.put("/catalog/variants/{variant_id}")
async def edit_item(tenant_id: UUID, variant_id: UUID, payload: catalog.ItemUpdate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.update_item, variant_id, payload, write=True)


@router.post("/catalog/variants/{variant_id}/adjustments")
async def adjust_stock(tenant_id: UUID, variant_id: UUID, payload: catalog.Adjustment, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, catalog.adjust, variant_id, payload, write=True)


@router.get("/orders")
async def order_list(tenant_id: UUID, search: str = Query(default="", max_length=120),
                     state: orders.OrderState = "all", mode: Literal["all", "counter", "pickup", "delivery"] = "all",
                     start: date | None = None, end: date | None = None,
                     offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.list_orders, search=search, state=state, mode=mode,
                     start=start, end=end, offset=offset)


@router.get("/overview")
async def delivery_overview(tenant_id: UUID, day: date | None = None, actor: Actor = Depends(authenticate)):
    if day is not None and not 1900 <= day.year <= 2100:
        raise HTTPException(422, "Use uma data entre 1900 e 2100")
    return await run(actor, tenant_id, orders.overview, day)


@router.get("/credit")
async def delivery_credit(tenant_id: UUID, search: str = Query(default="", max_length=120),
                          status: orders.CreditStatus = "all", offset: int = Query(default=0, ge=0, le=100_000),
                          actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.list_credit, search=search, status=status, offset=offset)


@router.get("/order-customers")
async def order_customers(tenant_id: UUID, search: str = Query(max_length=120), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.lookup_customers, search=search)


@router.post("/order-customers", status_code=201)
async def add_order_customer(tenant_id: UUID, payload: orders.QuickCustomer, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.create_customer, payload, write=True, response_status=201)


@router.get("/order-customers/{customer_id}/previous-address")
async def address_from_previous_delivery(tenant_id: UUID, customer_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.previous_address, customer_id)


@router.post("/orders/estimate")
async def estimate_order(tenant_id: UUID, payload: orders.OrderEstimate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.estimate, payload)


@router.get("/orders/{order_id}")
async def order_detail(tenant_id: UUID, order_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.detail, order_id)


@router.post("/orders", status_code=201)
async def new_order(tenant_id: UUID, payload: orders.ManualOrder, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.create, payload, write=True, response_status=201)


@router.post("/orders/{order_id}/transitions")
async def change_order(tenant_id: UUID, order_id: UUID, payload: orders.Transition, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, orders.transition, order_id, payload, write=True)


@router.get("/containers")
async def container_list(tenant_id: UUID, kind: containers.ContainerFilter = "all",
                         search: str = Query(default="", max_length=120),
                         offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, containers.list_containers, kind=kind, search=search, offset=offset)


@router.post("/containers", status_code=201)
async def new_container(tenant_id: UUID, payload: containers.ContainerCreate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, containers.create, payload, write=True, response_status=201)


@router.post("/containers/{container_id}/movements")
async def move_container(tenant_id: UUID, container_id: UUID, payload: containers.Movement, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, containers.adjust, container_id, payload, write=True)


@router.get("/containers/{container_id}/movements")
async def container_history(tenant_id: UUID, container_id: UUID, offset: int = Query(default=0, ge=0, le=100_000),
                            actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, containers.history, container_id, offset=offset)


@router.get("/containers/{container_id}/loans")
async def container_loans(tenant_id: UUID, container_id: UUID, offset: int = Query(default=0, ge=0, le=100_000),
                          actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, containers.loans, container_id, offset=offset)


@router.get("/reports/sales")
async def sales_report(tenant_id: UUID, start: date | None = None, end: date | None = None,
                       state: orders.OrderState = "all", mode: Literal["all", "counter", "pickup", "delivery"] = "all",
                       receipt: reports.ReceiptFilter = "all", offset: int = Query(default=0, ge=0, le=100_000),
                       actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, reports.sales, start=start, end=end, state=state, mode=mode,
                     receipt=receipt, offset=offset)


@router.get("/reports/sales.csv")
async def sales_export(tenant_id: UUID, start: date | None = None, end: date | None = None,
                       state: orders.OrderState = "all", mode: Literal["all", "counter", "pickup", "delivery"] = "all",
                       receipt: reports.ReceiptFilter = "all", actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, reports.sales, start=start, end=end, state=state, mode=mode,
                     receipt=receipt, export=True)


@router.post("/orders/{order_id}/payment-receipts")
async def register_credit_receipt(tenant_id: UUID, order_id: UUID, payload: payments.CreditPayment,
                                  actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, payments.collect, order_id, payload, write=True)


@router.get("/logistics")
async def logistics_policy(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, logistics.get_policy)


@router.put("/logistics")
async def update_logistics(tenant_id: UUID, payload: logistics.PolicyUpdate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, logistics.save_policy, payload, write=True, publish=True)


@router.get("/logistics/couriers")
async def couriers(tenant_id: UUID, search: str = Query(default="", max_length=120), active_only: bool = False,
                   offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, logistics.list_couriers, search=search, active_only=active_only, offset=offset)


@router.post("/logistics/couriers", status_code=201)
async def add_courier(tenant_id: UUID, payload: logistics.CourierCreate, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, logistics.create_courier, payload, write=True, response_status=201)


@router.put("/logistics/couriers/{courier_id}")
async def change_courier(tenant_id: UUID, courier_id: UUID, payload: logistics.CourierUpdate,
                          actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, logistics.update_courier, courier_id, payload, write=True)


delivery_reports_ops.register(router, run)
