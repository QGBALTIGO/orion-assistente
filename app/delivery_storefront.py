"""Owner-published Delivery catalogue, checkout and private order progress."""

import hashlib
import hmac
import json
import os
import re
import secrets
import unicodedata
from contextlib import asynccontextmanager
from datetime import time, timedelta
from html import escape
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app import commercial_service as commerce
from app.catalog_api import Actor, authenticate
from app.database import get_session_factory, tenant_transaction
from app.delivery_api import run
from app.delivery_catalog import run_once
from app.delivery_orders import OrderEstimate, OrderItems, priced_order
from app.business_profile_schemas import BusinessHours, ProfileModel
from app.http_json import json_response
from app.management_schemas import MAX_MONEY, normalize_phone
from app.onboarding_schemas import Address
from app.onboarding_service import require_operational_ready
from app.panel_auth import _same_origin
from app.plan_permissions import lock_plan
from app.public_origins import canonical_public_origin
from app.public_site_api import checked_slug, public_limit
from app.tenant_access import TenantAccess, check_access
from app.workspace_policy import DELIVERY_NICHE, delivery_preview_enabled


router = APIRouter(tags=["Loja Delivery"])
STATIC = Path(__file__).parent / "static"
ADMIN_PATH = "/api/v1/tenants/{tenant_id}/delivery/storefront"
PUBLIC_PATH = "/api/v1/delivery-store/{slug}"
PUBLIC_ASSETS = {
    "orion-delivery-store.css": "text/css",
    "orion-delivery-store.js": "text/javascript",
}
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
}
PUBLIC_ACTOR = TenantAccess("customer")
CollectionMethod = Literal["cash", "debit_card", "credit_card", "pix"]
PublicKind = Literal["water", "prepared_food", "grocery", "household"]


class PublicItem(ProfileModel):
    variant_id: UUID
    product_revision: StrictInt = Field(ge=1)
    variant_revision: StrictInt = Field(ge=1)
    label: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=600)
    kind: PublicKind


class Storefront(ProfileModel):
    revision: StrictInt = Field(ge=0)
    slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=600)
    enabled: StrictBool = False
    allow_pickup: StrictBool = True
    allow_delivery: StrictBool = False
    pickup_details: str | None = Field(default=None, min_length=1, max_length=300)
    opening_hours: list[BusinessHours] = Field(default_factory=list, max_length=28)
    payment_methods: list[CollectionMethod] = Field(min_length=1, max_length=4)
    max_open_orders: StrictInt = Field(default=30, ge=1, le=300)
    items: list[PublicItem] = Field(default_factory=list, max_length=200)
    confirm_catalogue: StrictBool = False

    @model_validator(mode="after")
    def check_store(self):
        if len({item.variant_id for item in self.items}) != len(self.items):
            raise ValueError("Selecione cada variação uma vez")
        if len(set(self.payment_methods)) != len(self.payment_methods):
            raise ValueError("Selecione cada forma de pagamento uma vez")
        intervals = sorted(self.opening_hours, key=lambda item: (item.weekday, item.opens))
        for before, after in zip(intervals, intervals[1:]):
            if before.weekday == after.weekday and before.closes > after.opens:
                raise ValueError("Os horários de um mesmo dia não podem se sobrepor")
        if self.enabled and (not self.confirm_catalogue or not self.items):
            raise ValueError("Confirme e selecione o catálogo antes de publicar")
        if self.enabled and not (self.allow_pickup or self.allow_delivery):
            raise ValueError("Habilite retirada ou entrega")
        if self.enabled and self.allow_pickup and not self.pickup_details:
            raise ValueError("Informe o endereço público da retirada")
        return self


class Unpublish(ProfileModel):
    revision: StrictInt = Field(ge=1)


class CheckoutDraft(OrderItems):
    mode: Literal["pickup", "delivery"]
    address: Address | None = None
    scheduled_at: AwareDatetime | None = None
    payment_preference: CollectionMethod

    @model_validator(mode="after")
    def check_fulfillment(self):
        if (self.mode == "delivery") != (self.address is not None):
            raise ValueError("Informe o endereço somente para entrega")
        if self.mode == "pickup" and self.scheduled_at is not None:
            raise ValueError("Agendamento disponível somente na entrega")
        if sum(item.quantity for item in self.items) > 100:
            raise ValueError("O carrinho aceita até 100 unidades por pedido")
        return self


class Checkout(CheckoutDraft):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=10, max_length=40)
    confirmed: StrictBool
    quote: str = Field(pattern=r"^\d{10}\.[0-9a-f]{64}$")
    # High-entropy browser UUIDs avoid colliding with operator operation keys.
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    @field_validator("customer_phone")
    @classmethod
    def phone(cls, value):
        normalized = normalize_phone(value)
        if normalized is None:
            raise ValueError("Informe DDD e telefone")
        return normalized

    @field_validator("confirmed")
    @classmethod
    def confirmation(cls, value):
        if not value:
            raise ValueError("Confirme os itens, o recebimento e o total do pedido")
        return value


class Tracking(ProfileModel):
    tracking_code: str = Field(pattern=r"^[a-zA-Z0-9_-]{43}$")


def configured_public(tenant, niche):
    values = os.getenv("ORION_DELIVERY_PUBLIC_TENANTS", "").split(",")
    return delivery_preview_enabled(tenant, niche) and str(tenant) in {value.strip() for value in values}


RESTRICTED = re.compile(
    r"\b(?:gas|glp|botij(?:ao|oes)|bujao|propano|butano|p[ -]?(?:13|20|45)|cigarros?|tabaco|"
    r"cervejas?|vinhos?|destilados?|whisky|vodka|cachaca|medicamentos?)\b"
)


def normalized_text(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c)).casefold()


def eligible(offering, item, *source_fields):
    combined = " ".join(normalized_text(value) for value in (item.label, item.description, *source_fields))
    combined = re.sub(r"\bagua\s+(?:com|sem)\s+gas\b", "agua", combined)
    return (not RESTRICTED.search(combined)
            and (offering != "gas_agua" or item.kind == "water" and bool(re.search(r"\bagua\b", combined))))


PRODUCTS = """
    SELECT v.id AS variant_id,p.revision AS product_revision,v.revision AS variant_revision,
        p.name,v.name AS variant_name,p.description AS product_description,d.category,
        p.active AND v.active AS active,v.price_kind,v.unit_price_cents
    FROM commercial_variants v
    JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
    JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
    WHERE v.tenant_id=:tenant
"""
ITEMS = """
    SELECT s.variant_id,s.label,s.description,s.kind,s.product_revision,s.variant_revision,
        v.unit_price_cents,p.name,v.name AS variant_name,p.description AS product_description,d.category,
        i.mode,i.observed_at,i.valid_until,i.quantity_on_hand,i.reserved_quantity
    FROM delivery_storefront_items s
    JOIN commercial_variants v ON (v.tenant_id,v.id)=(s.tenant_id,s.variant_id)
    JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
    JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
    JOIN commercial_inventory i ON (i.tenant_id,i.variant_id)=(v.tenant_id,v.id)
    WHERE s.tenant_id=:tenant AND v.active AND p.active AND v.price_kind='fixed'
      AND s.product_revision=p.revision AND s.variant_revision=v.revision
"""


async def business(db, tenant):
    row = (await db.execute(text("""
        SELECT t.name,t.slug,t.timezone,t.status,b.configuration->>'niche' AS niche,
               b.configuration->>'delivery_offering' AS offering
        FROM tenants t LEFT JOIN business_onboarding b ON b.tenant_id=t.id WHERE t.id=:tenant
    """), {"tenant": tenant})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Estabelecimento indisponível")
    return dict(row)


async def store_state(db, tenant):
    shop = await business(db, tenant)
    row = (await db.execute(text("""
        SELECT slug,enabled,revision,settings FROM delivery_storefronts WHERE tenant_id=:tenant
    """), {"tenant": tenant})).mappings().one_or_none()
    items = (await db.execute(text("""
        SELECT variant_id,product_revision,variant_revision,label,description,kind
        FROM delivery_storefront_items WHERE tenant_id=:tenant ORDER BY lower(label),variant_id
    """), {"tenant": tenant})).mappings().all()
    defaults = {"slug": shop["slug"], "enabled": False, "revision": 0, "title": shop["name"],
                "description": None, "allow_pickup": True, "allow_delivery": False, "pickup_details": None,
                "opening_hours": [], "payment_methods": ["cash"], "max_open_orders": 30}
    current = {**defaults, **row["settings"], **{key: row[key] for key in ("slug", "enabled", "revision")}} if row else defaults
    available = configured_public(tenant, shop["niche"])
    return {**current, "items": [dict(item) for item in items], "publication_available": available,
            "public_url": canonical_public_origin() + "/loja/" + current["slug"] if current["enabled"] and available else None,
            "offering": shop["offering"]}


async def operator_items(db, tenant, search, offset):
    rows = (await db.execute(text(PRODUCTS + """
        AND p.active AND v.active AND v.price_kind='fixed'
        AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',p.name,v.name)))>0)
        ORDER BY lower(p.name),lower(v.name),v.id LIMIT 26 OFFSET :offset
    """), {"tenant": tenant, "search": search, "offset": offset})).mappings().all()
    return {"items": [dict(row) for row in rows[:25]], "has_more": len(rows) > 25}


async def save_store(db, tenant, access, payload):
    check_access(access.role, "publish")
    shop = await business(db, tenant)
    if payload.enabled:
        if not configured_public(tenant, shop["niche"]):
            raise HTTPException(403, "A publicação da loja ainda não está liberada para esta empresa")
        await commerce.tenant_exists(db, tenant, active=True)
        await require_operational_ready(db, tenant, "product_orders")
        signing_key()
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                     {"scope": f"delivery-storefront-config:{tenant}"})
    revision = (await db.execute(text("""
        SELECT revision FROM delivery_storefronts WHERE tenant_id=:tenant FOR UPDATE
    """), {"tenant": tenant})).scalar_one_or_none() or 0
    if revision != payload.revision:
        commerce.conflict("A loja foi alterada em outra sessão. Atualize a página e confira.")
    if payload.enabled and payload.allow_delivery:
        # Like public checkout, lock storefront first, then coverage policy.
        from app.delivery_logistics import public_delivery_available
        if not await public_delivery_available(db, tenant):
            raise HTTPException(422, "Cadastre pelo menos uma área de entrega ativa")

    rows = (await db.execute(text(PRODUCTS + """
        AND v.id=ANY(:ids) ORDER BY v.id FOR SHARE OF p,v,d
    """), {"tenant": tenant, "ids": [item.variant_id for item in payload.items]})).mappings().all()
    indexed = {row["variant_id"]: row for row in rows}
    for item in payload.items:
        source = indexed.get(item.variant_id)
        if (not source or not source["active"] or source["price_kind"] != "fixed"
                or source["product_revision"] != item.product_revision
                or source["variant_revision"] != item.variant_revision):
            commerce.conflict("Um produto foi alterado ou ficou indisponível. Selecione novamente a variação.")
        if not eligible(shop["offering"], item, source["name"], source["variant_name"],
                        source["product_description"], source["category"]):
            raise HTTPException(422, "A loja pública aceita somente as categorias de produtos autorizadas")
    await db.execute(text("""
        INSERT INTO delivery_storefronts (tenant_id,slug,enabled,revision,settings)
        VALUES (:tenant,:slug,:enabled,1,CAST(:settings AS jsonb))
        ON CONFLICT (tenant_id) DO UPDATE SET slug=excluded.slug,enabled=excluded.enabled,
            revision=delivery_storefronts.revision+1,settings=excluded.settings,updated_at=now()
    """), {"tenant": tenant, "slug": payload.slug, "enabled": payload.enabled,
            "settings": payload.model_dump_json(exclude={"slug", "enabled", "revision", "items", "confirm_catalogue"})})
    await db.execute(text("DELETE FROM delivery_storefront_items WHERE tenant_id=:tenant"), {"tenant": tenant})
    if payload.items:
        await db.execute(text("""
            INSERT INTO delivery_storefront_items
                (tenant_id,variant_id,product_revision,variant_revision,label,description,kind)
            VALUES (:tenant,:variant_id,:product_revision,:variant_revision,:label,:description,:kind)
        """), [{"tenant": tenant, **item.model_dump()} for item in payload.items])
    return await store_state(db, tenant)


async def close_store(db, tenant, access, payload):
    check_access(access.role, "publish")
    row = (await db.execute(text("""
        UPDATE delivery_storefronts SET enabled=false,revision=revision+1,updated_at=now()
        WHERE tenant_id=:tenant AND revision=:revision RETURNING revision
    """), {"tenant": tenant, "revision": payload.revision})).scalar_one_or_none()
    if row is None:
        commerce.conflict("A loja foi alterada. Atualize a página e confira.")
    return await store_state(db, tenant)


@router.get(ADMIN_PATH)
async def admin_state(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, store_state)


@router.get(ADMIN_PATH + "/items")
async def admin_items(tenant_id: UUID, search: str = Query(default="", max_length=120),
                      offset: int = Query(default=0, ge=0, le=100_000), actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, operator_items, search.strip(), offset)


@router.put(ADMIN_PATH)
async def admin_save(tenant_id: UUID, payload: Storefront, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, save_store, payload, write=True, publish=True)


@router.post(ADMIN_PATH + "/unpublish")
async def admin_close(tenant_id: UUID, payload: Unpublish, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, close_store, payload, write=True, publish=True)


async def order_contact(db, tenant, order_id):
    row = (await db.execute(text("""
        SELECT q.customer_name,q.contact_e164,s.payment_preference
        FROM delivery_storefront_orders s
        JOIN commercial_orders o ON (o.tenant_id,o.id)=(s.tenant_id,s.order_id)
        JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
        WHERE s.tenant_id=:tenant AND s.order_id=:order_id
    """), {"tenant": tenant, "order_id": order_id})).mappings().one_or_none()
    return dict(row) if row else None


@router.get(ADMIN_PATH + "/orders/{order_id}")
async def admin_order_contact(tenant_id: UUID, order_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, order_contact, order_id)


@asynccontextmanager
async def public_session(slug, *, lock=False):
    checked_slug(slug)
    async with get_session_factory()() as resolver:
        tenant = (await resolver.execute(text("SELECT public.resolve_delivery_storefront(:slug)"),
                                         {"slug": slug})).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(404, "Loja indisponível", headers=HEADERS)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await lock_plan(db, tenant)
            shop = await business(db, tenant)
            if shop["status"] != "active" or not configured_public(tenant, shop["niche"]):
                raise HTTPException(404, "Loja indisponível", headers=HEADERS)
            row = (await db.execute(text("""
                SELECT slug,revision,settings FROM delivery_storefronts
                WHERE tenant_id=:tenant AND slug=:slug AND enabled
            """ + (" FOR SHARE" if lock else "")), {"tenant": tenant, "slug": slug})).mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Loja indisponível", headers=HEADERS)
            yield db, tenant, {**row["settings"], **shop, "slug": row["slug"], "revision": row["revision"],
                               "title": row["settings"]["title"]}


def is_open(shop, now):
    slots = shop["opening_hours"]
    if not slots:
        return True
    local = now.astimezone(ZoneInfo(shop["timezone"]))
    return any(local.weekday() == slot["weekday"] and
               time.fromisoformat(slot["opens"]) <= local.time() < time.fromisoformat(slot["closes"]) for slot in slots)


def available_item(shop, row):
    return eligible(shop["offering"], PublicItem.model_validate(
        {key: row[key] for key in PublicItem.model_fields}),
        row["name"], row["variant_name"], row["product_description"], row["category"])


def public_json(value, status=200):
    reply = json_response(value, status_code=status)
    reply.headers.update(HEADERS)
    return reply


async def rate(request, slug, action, limit=60, seconds=300, *, subject=None):
    checked_slug(slug)
    try:
        await public_limit(request, slug, "delivery-"+action, limit, seconds, subject=subject)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise HTTPException(503, "Loja temporariamente indisponível", headers=HEADERS) from None
        raise


@router.get(PUBLIC_PATH)
async def public_catalogue(slug: str, request: Request):
    await rate(request, slug, "catalogue")
    async with public_session(slug) as (db, tenant, shop):
        rows = (await db.execute(text(ITEMS + " ORDER BY lower(s.label),s.variant_id"),
                                 {"tenant": tenant})).mappings().all()
        now = commerce.now_utc()
        items = [{
            **{key: row[key] for key in ("variant_id", "label", "description", "kind", "unit_price_cents")},
            "available": row["mode"] == "tracked" and row["observed_at"] <= now < row["valid_until"]
                         and row["quantity_on_hand"] > row["reserved_quantity"],
        } for row in rows if available_item(shop, row)]
        delivery_areas = []
        if shop["allow_delivery"]:
            from app.delivery_logistics import get_policy
            policy = await get_policy(db, tenant)
            if policy["coverage_mode"] == "areas":
                delivery_areas = [{key: area[key] for key in ("city", "state", "neighborhood", "fee_cents")}
                                  for area in policy["areas"] if area["kind"] == "included"]
    return public_json({**{key: shop[key] for key in (
        "title", "description", "allow_pickup", "pickup_details", "payment_methods", "opening_hours",
    )}, "allow_delivery": bool(delivery_areas), "delivery_areas": delivery_areas,
        "open": is_open(shop, now), "items": items})


def signing_key():
    secret = os.getenv("PUBLIC_SITE_SIGNING_KEY", "")
    if len(secret) < 32:
        raise HTTPException(503, "Loja temporariamente indisponível", headers=HEADERS)
    return hmac.digest(secret.encode(), b"orion-delivery-store-quote:v1", "sha256")


def quote_signature(tenant, shop, payload, estimate, policy_revision, expires):
    facts = [str(tenant), shop["slug"], shop["revision"], CheckoutDraft.model_validate(
        payload.model_dump(include=set(CheckoutDraft.model_fields))).model_dump(mode="json"),
        estimate["estimate_version"], policy_revision, expires]
    return hmac.new(signing_key(), json.dumps(facts, sort_keys=True).encode(), hashlib.sha256).hexdigest()


async def prepare_checkout(db, tenant, shop, payload, *, lock=False):
    now = commerce.now_utc()
    if not is_open(shop, now):
        raise HTTPException(409, "A loja está fechada. Consulte os horários disponíveis.")
    if not shop["allow_"+payload.mode] or payload.payment_preference not in shop["payment_methods"]:
        raise HTTPException(422, "A forma de recebimento ou de pagamento está indisponível")
    logistics = {"delivery_fee_cents": 0, "policy_revision": None, "area": None, "scheduled_at": None}
    if payload.mode == "delivery":
        from app.delivery_logistics import quote_delivery
        logistics = await quote_delivery(db, tenant, payload.address, payload.scheduled_at,
                                         require_coverage=True, lock=lock, now=now)
    lines = await commerce._load_lines(db, tenant, payload.items, lock=lock)
    approved = (await db.execute(text(ITEMS + " AND s.variant_id=ANY(:ids)"),
                                 {"tenant": tenant, "ids": [item.variant_id for item in payload.items]})).mappings().all()
    visible = {row["variant_id"]: row for row in approved if available_item(shop, row)}
    if len(visible) != len(lines):
        commerce.conflict("Um produto está indisponível na loja. Confira seu carrinho.")
    estimate = priced_order(lines, OrderEstimate(mode=payload.mode, items=payload.items,
                                                delivery_fee_cents=logistics["delivery_fee_cents"]), now)
    if not estimate["confirmation_requirements_met"]:
        commerce.conflict("O estoque ou o preço mudou. Confira seu carrinho.")
    if estimate["total_cents"] > MAX_MONEY:
        raise HTTPException(422, "O total excede o limite do pedido")
    for item in estimate["items"]:
        public_item = visible[UUID(item["variant_id"])]
        item["product_name"] = public_item["label"]
    return estimate, lines, logistics


@router.post(PUBLIC_PATH + "/quote")
async def public_quote(slug: str, payload: CheckoutDraft, request: Request):
    _same_origin(request)
    await rate(request, slug, "quote", 30)
    async with public_session(slug) as (db, tenant, shop):
        estimate, _, logistics = await prepare_checkout(db, tenant, shop, payload)
        expires = int(commerce.now_utc().timestamp()) + 600
        signature = quote_signature(tenant, shop, payload, estimate, logistics["policy_revision"], expires)
    return public_json({
        "quote": f"{expires}.{signature}", "subtotal_cents": estimate["subtotal_cents"],
        "delivery_fee_cents": estimate["delivery_fee_cents"], "total_cents": estimate["total_cents"],
        "items": [{key: item[key] for key in ("variant_id", "product_name", "quantity", "unit_price_cents", "total_cents")}
                  for item in estimate["items"]],
    })


async def save_checkout(db, tenant, shop, payload):
    async def write():
        # The public backlog cap and stock rows are checked in the same commit.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                         {"scope": f"delivery-storefront-checkout:{tenant}"})
        await require_operational_ready(db, tenant, "product_orders")
        open_orders = (await db.execute(text("""
            SELECT count(*) FROM delivery_storefront_orders s
            JOIN commercial_orders o ON (o.tenant_id,o.id)=(s.tenant_id,s.order_id)
            WHERE s.tenant_id=:tenant AND o.status IN ('confirmed','dispatched')
        """), {"tenant": tenant})).scalar_one()
        if open_orders >= shop["max_open_orders"]:
            raise HTTPException(429, "A loja está atendendo muitos pedidos. Tente novamente em instantes.",
                                headers={"Retry-After": "60"})
        stamp, signature = payload.quote.split(".")
        expires = int(stamp)
        now = commerce.now_utc()
        if not int(now.timestamp()) < expires <= int(now.timestamp()) + 600:
            commerce.conflict("O resumo expirou. Confira novamente o seu pedido.")
        estimate, lines, logistics = await prepare_checkout(db, tenant, shop, payload, lock=True)
        expected = quote_signature(tenant, shop, payload, estimate, logistics["policy_revision"], expires)
        if not hmac.compare_digest(expected, signature):
            commerce.conflict("O carrinho, o estoque ou os dados da entrega mudaram. Confira novamente o resumo.")
        quote_id, order_id = uuid4(), uuid4()
        await commerce.write_quote(db, tenant, PUBLIC_ACTOR, quote_id, secrets.token_urlsafe(32),
                                    payload.customer_phone, payload.customer_name, estimate,
                                    commerce.fulfillment.EMPTY, now + timedelta(minutes=15))
        await db.execute(text("""
            INSERT INTO commercial_orders (id,tenant_id,quote_id,idempotency_key,status,origin)
            VALUES (:id,:tenant,:quote_id,:key,'confirmed','delivery')
        """), {"id": order_id, "tenant": tenant, "quote_id": quote_id, "key": f"delivery-store:{order_id}"})
        await db.execute(text("""
            INSERT INTO delivery_order_details (tenant_id,order_id,mode,address,delivery_fee_cents)
            VALUES (:tenant,:id,:mode,CAST(:address AS jsonb),:fee)
        """), {"tenant": tenant, "id": order_id, "mode": payload.mode,
                "address": payload.address.model_dump_json() if payload.address else None,
                "fee": estimate["delivery_fee_cents"]})
        if payload.mode == "delivery":
            from app.delivery_logistics import save_order_logistics
            await save_order_logistics(db, tenant, order_id, logistics)
        tracking = secrets.token_urlsafe(32)
        await db.execute(text("""
            INSERT INTO delivery_storefront_orders (tenant_id,order_id,tracking_digest,payment_preference)
            VALUES (:tenant,:id,:digest,:method)
        """), {"tenant": tenant, "id": order_id, "digest": hashlib.sha256(tracking.encode()).hexdigest(),
                "method": payload.payment_preference})
        await commerce.reserve_stock(db, tenant, PUBLIC_ACTOR, order_id, lines)
        await commerce.audit(db, tenant, PUBLIC_ACTOR, "order", order_id, "confirmed",
                             {"total_cents": estimate["total_cents"], "mode": payload.mode, "source": "storefront"})
        return {"order_code": str(order_id)[:8].upper(), "status": "confirmed",
                "total_cents": estimate["total_cents"], "tracking_code": tracking}

    return await run_once(db, tenant, payload, "storefront_checkout", shop["slug"], write)


@router.post(PUBLIC_PATH + "/orders", status_code=201)
async def public_checkout(slug: str, payload: Checkout, request: Request):
    _same_origin(request)
    await rate(request, slug, "checkout-peer", 12, 3600)
    await rate(request, slug, "checkout-phone", 4, 3600, subject=payload.customer_phone)
    await rate(request, slug, "checkout-store", 300, 3600, subject="store")
    async with public_session(slug, lock=True) as (db, tenant, shop):
        result = await save_checkout(db, tenant, shop, payload)
    return public_json(result, 201)


@router.post(PUBLIC_PATH + "/status")
async def public_progress(slug: str, payload: Tracking, request: Request):
    _same_origin(request)
    await rate(request, slug, "status", 45)
    async with public_session(slug) as (db, tenant, _):
        row = (await db.execute(text("""
            SELECT o.id,o.status,q.total_cents,
                CASE WHEN d.return_financial_entry_id IS NOT NULL THEN 'refunded'
                     WHEN f.status='paid' THEN 'received' ELSE 'not_verified' END AS payment_status
            FROM delivery_storefront_orders s
            JOIN commercial_orders o ON (o.tenant_id,o.id)=(s.tenant_id,s.order_id)
            JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(s.tenant_id,s.order_id)
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            LEFT JOIN financial_entries f ON (f.tenant_id,f.id)=(d.tenant_id,d.financial_entry_id)
            WHERE s.tenant_id=:tenant AND s.tracking_digest=:digest
        """), {"tenant": tenant, "digest": hashlib.sha256(payload.tracking_code.encode()).hexdigest()})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Pedido indisponível", headers=HEADERS)
    return public_json({"order_code": str(row["id"])[:8].upper(),
                        **{key: row[key] for key in ("status", "total_cents", "payment_status")}})


@router.get("/loja/{slug}", include_in_schema=False)
async def storefront_page(slug: str, request: Request):
    await rate(request, slug, "page")
    async with public_session(slug) as (_, _, shop):
        page = (STATIC / "orion-delivery-store.html").read_text(encoding="utf-8")
        page = page.replace("__STORE_SLUG__", escape(slug, quote=True))
        page = page.replace("__STORE_TITLE__", escape(shop["title"], quote=True))
    return HTMLResponse(page, headers=HEADERS)


@router.get("/delivery-store-assets/{asset}", include_in_schema=False)
async def storefront_asset(asset: str):
    if asset not in PUBLIC_ASSETS:
        raise HTTPException(404, "Arquivo indisponível")
    return FileResponse(STATIC / asset, media_type=PUBLIC_ASSETS[asset], headers=HEADERS)
