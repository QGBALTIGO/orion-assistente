"""Tenant-authorized product operations; no public checkout or payment provider."""
from uuid import UUID
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app import commercial_service as service
from app.catalog_api import Actor, authenticate
from app.commercial_schemas import (
    Product, ReplaceProduct, Variant, ReplaceVariant, ReplaceStock, RelatedComplement,
    EstimateRequest, CreateQuote, CreateOrder, ConfirmOrder, CancelOrder,
)
from app.database import get_session_factory
from app.tenant_access import resolve_tenant_access

def commerce_enabled():
    if os.getenv("COMMERCIAL_CATALOG_ENABLED", "0") != "1":
        raise HTTPException(423, "O catálogo comercial ainda não está habilitado")


router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/commerce", tags=["Catálogo e pedidos comerciais"],
                   dependencies=[Depends(commerce_enabled)])


@router.get("/status")
async def status(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id)
    editable = access.role in {"owner", "editor", "support", "admin"}
    return {"enabled": True, "access": {"role": access.role, "can_edit_catalog": editable,
            "can_create_orders": editable}, "payments_enabled": False,
            "assistant_connected": os.getenv("QR_AI_COMMERCE_ENABLED", "0") == "1",
            "fulfillment": await run(service.receiving_options, tenant_id)}


async def run(operation, *args, **kwargs):
    try:
        async with get_session_factory()() as session:
            return jsonable_encoder(await operation(session, *args, **kwargs))
    except IntegrityError:
        raise HTTPException(409, "Cadastro duplicado ou referência inválida neste estabelecimento") from None


@router.get("/products")
async def products(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return JSONResponse(await run(service.catalog, tenant_id))


@router.get("/pickup-appointments")
async def pickup_appointments(tenant_id: UUID,
        contact_e164: str = Query(pattern=r"^\+[1-9][0-9]{7,14}$"), actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.pickup_candidates, tenant_id, contact_e164)


@router.post("/products", status_code=201)
async def create_product(tenant_id: UUID, payload: Product, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.save_product, tenant_id, payload, access)


@router.put("/products/{product_id}")
async def replace_product(tenant_id: UUID, product_id: UUID, payload: ReplaceProduct, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.save_product, tenant_id, payload.product, access,
                     product_id=product_id, expected_revision=payload.expected_revision)


@router.post("/products/{product_id}/variants", status_code=201)
async def create_variant(tenant_id: UUID, product_id: UUID, payload: Variant, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.save_variant, tenant_id, product_id, payload, access)


@router.put("/products/{product_id}/variants/{variant_id}")
async def replace_variant(tenant_id: UUID, product_id: UUID, variant_id: UUID, payload: ReplaceVariant,
                          actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.save_variant, tenant_id, product_id, payload.variant, access,
                     variant_id=variant_id, expected_revision=payload.expected_revision)


@router.put("/variants/{variant_id}/stock")
async def stock(tenant_id: UUID, variant_id: UUID, payload: ReplaceStock, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.observe_stock, tenant_id, variant_id, payload, access)


@router.put("/products/{product_id}/complements")
async def complement(tenant_id: UUID, product_id: UUID, payload: RelatedComplement, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.save_complement, tenant_id, product_id, payload, access)


@router.get("/products/{product_id}/complements")
async def complements(tenant_id: UUID, product_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return await run(service.approved_complements, tenant_id, product_id)


@router.post("/estimate")
async def estimate(tenant_id: UUID, payload: EstimateRequest, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return await run(service.estimate, tenant_id, payload)


@router.post("/quotes", status_code=201)
async def quote(tenant_id: UUID, payload: CreateQuote, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.create_quote, tenant_id, payload, access)


@router.get("/quotes/{quote_id}")
async def quote_detail(tenant_id: UUID, quote_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return await run(service.get_quote, tenant_id, quote_id)


@router.post("/orders", status_code=201)
async def order(tenant_id: UUID, payload: CreateOrder, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.create_order, tenant_id, payload, access)


@router.get("/orders/{order_id}")
async def order_detail(tenant_id: UUID, order_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return await run(service.get_order, tenant_id, order_id)


@router.get("/orders/by-quote/{quote_id}")
async def order_for_quote(tenant_id: UUID, quote_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    return await run(service.get_order_by_quote, tenant_id, quote_id)


@router.post("/orders/{order_id}/confirm")
async def confirm(tenant_id: UUID, order_id: UUID, payload: ConfirmOrder, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.confirm_order, tenant_id, order_id, payload, access)


@router.post("/orders/{order_id}/cancel")
async def cancel(tenant_id: UUID, order_id: UUID, payload: CancelOrder, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    return await run(service.cancel_order, tenant_id, order_id, payload, access)
