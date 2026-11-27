"""Owner-published Delivery catalogue, checkout and private order progress."""

import hashlib
import hmac
import json
import os
import re
import secrets
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, delivery_container_orders as packaging
from app.catalog_api import Actor, authenticate
from app.database import get_session_factory, tenant_transaction
from app.delivery_api import run
from app.delivery_catalog import order_lines, run_once
from app.delivery_product_media import image_url, product_image_response, tenant_photo
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
PREVIEW_PATH = "/painel/loja-delivery/{tenant_id}/prever"
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
    accepting_orders: StrictBool = True
    closed_message: str | None = Field(default=None, min_length=1, max_length=300)
    logo_url: str | None = Field(default=None, max_length=140)
    cover_url: str | None = Field(default=None, max_length=140)
    brand_color: str = Field(default="#2e7bf6", pattern=r"^#[0-9a-fA-F]{6}$")
    public_phone: str | None = Field(default=None, max_length=40)
    layout: Literal["grid", "list"] = "grid"
    hide_unavailable: StrictBool = False
    minimum_subtotal_cents: StrictInt = Field(default=0, ge=0, le=MAX_MONEY)
    estimate_min_minutes: StrictInt | None = Field(default=None, ge=5, le=1440)
    estimate_max_minutes: StrictInt | None = Field(default=None, ge=5, le=1440)
    closed_dates: list[date] = Field(default_factory=list, max_length=60)
    allow_pickup: StrictBool = True
    allow_delivery: StrictBool = False
    pickup_details: str | None = Field(default=None, min_length=1, max_length=300)
    opening_hours: list[BusinessHours] = Field(default_factory=list, max_length=28)
    payment_methods: list[CollectionMethod] = Field(min_length=1, max_length=4)
    max_open_orders: StrictInt = Field(default=30, ge=1, le=300)
    items: list[PublicItem] = Field(default_factory=list, max_length=200)
    confirm_catalogue: StrictBool = False
    replace_items: StrictBool = True

    _phone = field_validator("public_phone")(normalize_phone)

    @model_validator(mode="after")
    def check_store(self):
        if len({item.variant_id for item in self.items}) != len(self.items):
            raise ValueError("Selecione cada variação uma vez")
        if len(set(self.payment_methods)) != len(self.payment_methods):
            raise ValueError("Selecione cada forma de pagamento uma vez")
        if len(set(self.closed_dates)) != len(self.closed_dates):
            raise ValueError("Informe cada data especial uma única vez")
        minimum, maximum = self.estimate_min_minutes, self.estimate_max_minutes
        if (minimum is None) != (maximum is None) or minimum is not None and minimum > maximum:
            raise ValueError("Informe uma previsão mínima e máxima válidas")
        intervals = sorted(self.opening_hours, key=lambda item: (item.weekday, item.opens))
        for before, after in zip(intervals, intervals[1:]):
            if before.weekday == after.weekday and before.closes > after.opens:
                raise ValueError("Os horários de um mesmo dia não podem se sobrepor")
        if self.enabled and self.replace_items and (not self.confirm_catalogue or not self.items):
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
    change_for_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    accept_exchange: StrictBool = False

    @model_validator(mode="after")
    def check_fulfillment(self):
        if (self.mode == "delivery") != (self.address is not None):
            raise ValueError("Informe o endereço somente para entrega")
        if self.mode == "pickup" and self.scheduled_at is not None:
            raise ValueError("Agendamento disponível somente na entrega")
        if sum(item.quantity for item in self.items) > 100:
            raise ValueError("O carrinho aceita até 100 unidades por pedido")
        if self.change_for_cents is not None and self.payment_preference != "cash":
            raise ValueError("O troco só pode ser solicitado para pagamento em dinheiro")
        return self


class Checkout(CheckoutDraft):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=10, max_length=40)
    buyer_note: str | None = Field(default=None, min_length=1, max_length=300)
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
        p.active AND v.active AS active,v.price_kind,v.unit_price_cents,
        d.allow_storefront,d.allow_pickup,d.allow_delivery,m.brand,m.preset_image,m.photo_url
    FROM commercial_variants v
    JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
    JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
    LEFT JOIN delivery_product_profiles m ON (m.tenant_id,m.product_id)=(p.tenant_id,p.id)
    WHERE v.tenant_id=:tenant
"""
ITEMS = """
    SELECT s.variant_id,s.label,s.description,s.kind,s.product_revision,s.variant_revision,
        v.unit_price_cents,p.name,v.name AS variant_name,p.description AS product_description,d.category,s.sort_order,
        d.allow_pickup,d.allow_delivery,d.exchange_container_id,m.brand,m.preset_image,m.photo_url,
        i.mode,i.observed_at,i.valid_until,i.quantity_on_hand,i.reserved_quantity
    FROM delivery_storefront_items s
    JOIN commercial_variants v ON (v.tenant_id,v.id)=(s.tenant_id,s.variant_id)
    JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
    JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
    LEFT JOIN delivery_product_profiles m ON (m.tenant_id,m.product_id)=(p.tenant_id,p.id)
    JOIN commercial_inventory i ON (i.tenant_id,i.variant_id)=(v.tenant_id,v.id)
    WHERE s.tenant_id=:tenant AND v.active AND p.active AND v.price_kind='fixed'
      AND d.allow_storefront
      AND s.product_revision=p.revision AND s.variant_revision=v.revision
"""

STORE_OPTIONS = {
    "accepting_orders": True, "closed_message": None, "logo_url": None, "cover_url": None,
    "brand_color": "#2e7bf6", "public_phone": None, "layout": "grid", "hide_unavailable": False,
    "minimum_subtotal_cents": 0, "estimate_min_minutes": None, "estimate_max_minutes": None, "closed_dates": [],
}


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
        SELECT s.variant_id,s.product_revision,s.variant_revision,s.label,s.description,s.kind,
            p.active AND v.active AND v.price_kind='fixed' AND d.allow_storefront AND
            s.product_revision=p.revision AND s.variant_revision=v.revision AS selection_valid,
            m.preset_image,m.photo_url
        FROM delivery_storefront_items s
        JOIN commercial_variants v ON (v.tenant_id,v.id)=(s.tenant_id,s.variant_id)
        JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
        LEFT JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
        LEFT JOIN delivery_product_profiles m ON (m.tenant_id,m.product_id)=(p.tenant_id,p.id)
        WHERE s.tenant_id=:tenant ORDER BY s.sort_order,lower(s.label),s.variant_id
    """), {"tenant": tenant})).mappings().all()
    defaults = {**STORE_OPTIONS, "slug": shop["slug"], "enabled": False, "revision": 0, "title": shop["name"],
                "description": None, "allow_pickup": True, "allow_delivery": False, "pickup_details": None,
                "opening_hours": [], "payment_methods": ["cash"], "max_open_orders": 30}
    current = {**defaults, **row["settings"], **{key: row[key] for key in ("slug", "enabled", "revision")}} if row else defaults
    available = configured_public(tenant, shop["niche"])
    selections = [{**{key: item[key] for key in PublicItem.model_fields}, "selection_valid": bool(item["selection_valid"]),
                   "image_url": image_url(tenant, item["preset_image"], item["photo_url"])} for item in items]
    return {**current, "items": selections, "publication_available": available,
            "public_url": canonical_public_origin() + "/loja/" + current["slug"] if current["enabled"] and available else None,
            "offering": shop["offering"], "open": is_open({**shop, **current}, commerce.now_utc()),
            "preview_url": f"/painel/loja-delivery/{tenant}/prever"}


async def operator_items(db, tenant, search, offset):
    rows = (await db.execute(text(PRODUCTS + """
        AND p.active AND v.active AND v.price_kind='fixed' AND d.allow_storefront
        AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',p.name,v.name)))>0)
        ORDER BY lower(p.name),lower(v.name),v.id LIMIT 26 OFFSET :offset
    """), {"tenant": tenant, "search": search, "offset": offset})).mappings().all()
    return {"items": [{**row, "image_url": image_url(tenant, row["preset_image"], row["photo_url"])}
                      for row in rows[:25]], "has_more": len(rows) > 25}


async def save_store(db, tenant, access, payload):
    check_access(access.role, "publish")
    shop = await business(db, tenant)
    if any(url is not None and not tenant_photo(tenant, url) for url in (payload.logo_url, payload.cover_url)):
        raise HTTPException(422, "Use imagens enviadas pela sua empresa")
    if payload.enabled:
        if not configured_public(tenant, shop["niche"]):
            raise HTTPException(403, "A publicação da loja ainda não está liberada para esta empresa")
        await commerce.tenant_exists(db, tenant, active=True)
        await require_operational_ready(db, tenant, "product_orders")
        signing_key()
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                     {"scope": f"delivery-storefront-config:{tenant}"})
    current = (await db.execute(text("""
        SELECT revision,enabled FROM delivery_storefronts WHERE tenant_id=:tenant FOR UPDATE
    """), {"tenant": tenant})).mappings().first()
    if (current["revision"] if current else 0) != payload.revision:
        commerce.conflict("A loja foi alterada em outra sessão. Atualize a página e confira.")
    if not payload.replace_items and payload.enabled != bool(current and current["enabled"]):
        raise HTTPException(422, "Use o controle de publicação para alterar o acesso à loja")
    if payload.enabled and payload.allow_delivery:
        # Like public checkout, lock storefront first, then coverage policy.
        from app.delivery_logistics import public_delivery_available
        if not await public_delivery_available(db, tenant):
            raise HTTPException(422, "Cadastre pelo menos uma área de entrega ativa")

    selections = payload.items if payload.replace_items else []
    rows = (await db.execute(text(PRODUCTS + """
        AND v.id=ANY(:ids) ORDER BY v.id FOR SHARE OF p,v,d
    """), {"tenant": tenant, "ids": [item.variant_id for item in selections]})).mappings().all()
    indexed = {row["variant_id"]: row for row in rows}
    old_selections = {}
    if current and (current["enabled"] or not payload.enabled) and selections:
        previous = (await db.execute(text("""
            SELECT variant_id,product_revision,variant_revision,label,description,kind
            FROM delivery_storefront_items WHERE tenant_id=:tenant
        """), {"tenant": tenant})).mappings().all()
        old_selections = {row["variant_id"]: dict(row) for row in previous}
    for item in selections:
        source = indexed.get(item.variant_id)
        if (not source or not source["active"] or not source["allow_storefront"] or source["price_kind"] != "fixed"
                or source["product_revision"] != item.product_revision
                or source["variant_revision"] != item.variant_revision):
            if item.model_dump() == old_selections.get(item.variant_id):
                continue  # Keep an unchanged, hidden selection while repairing another one.
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
            "settings": payload.model_dump_json(exclude={
                "slug", "enabled", "revision", "items", "confirm_catalogue", "replace_items",
            })})
    if payload.replace_items:
        await db.execute(text("DELETE FROM delivery_storefront_items WHERE tenant_id=:tenant"), {"tenant": tenant})
    if selections:
        await db.execute(text("""
            INSERT INTO delivery_storefront_items
                (tenant_id,variant_id,product_revision,variant_revision,label,description,kind,sort_order)
            VALUES (:tenant,:variant_id,:product_revision,:variant_revision,:label,:description,:kind,:position)
        """), [{"tenant": tenant, "position": position, **item.model_dump()} for position, item in enumerate(selections)])
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
        SELECT q.customer_name,q.contact_e164,s.payment_preference,s.buyer_note,s.change_for_cents
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
    if not shop.get("accepting_orders", True):
        return False
    slots = shop["opening_hours"]
    local = now.astimezone(ZoneInfo(shop["timezone"]))
    if local.date().isoformat() in shop.get("closed_dates", ()):
        return False
    if not slots:
        return True
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
        return public_json(await catalogue(db, tenant, shop))


def schedule_options(policy, shop, now):
    if not policy["scheduling_enabled"]:
        return []
    zone = ZoneInfo(shop["timezone"])
    local = now.astimezone(zone)
    earliest = now + timedelta(minutes=policy["lead_minutes"])
    latest = now + timedelta(days=policy["horizon_days"])
    options = []
    for offset in range(min(14, policy["horizon_days"] + 1)):
        day = local.date() + timedelta(days=offset)
        if day.isoformat() in shop.get("closed_dates", ()):
            continue
        for window in sorted(policy["windows"], key=lambda item: item["start"]):
            if window["weekday"] != day.weekday():
                continue
            start = datetime.combine(day, time.fromisoformat(window["start"]), zone)
            end = (datetime.combine(day + timedelta(days=1), time.min, zone) if window["end"] == "24:00"
                   else datetime.combine(day, time.fromisoformat(window["end"]), zone))
            while start < end and len(options) < 60:
                utc = start.astimezone(timezone.utc)
                if earliest <= utc <= latest and utc.astimezone(zone).replace(tzinfo=None) == start.replace(tzinfo=None):
                    options.append({"when": utc.isoformat(), "label": start.strftime("%d/%m às %H:%M")})
                start += timedelta(minutes=30)
            if len(options) == 60:
                return options
    return options


async def catalogue(db, tenant, shop):
    rows = (await db.execute(text(ITEMS + " ORDER BY s.sort_order,lower(s.label),s.variant_id"),
                             {"tenant": tenant})).mappings().all()
    now = commerce.now_utc()
    items = []
    for row in rows:
        if not available_item(shop, row):
            continue
        stock = (row["quantity_on_hand"] - row["reserved_quantity"]
                 if row["mode"] == "tracked" and row["observed_at"] <= now < row["valid_until"] else 0)
        if stock <= 0 and shop.get("hide_unavailable"):
            continue
        items.append({
            **{key: row[key] for key in ("variant_id", "label", "description", "kind", "unit_price_cents",
                                        "allow_pickup", "allow_delivery", "brand")},
            "available": stock > 0, "available_quantity": min(max(stock, 0), 100),
            "requires_exchange": row["exchange_container_id"] is not None,
            "image_url": image_url(tenant, row["preset_image"], row["photo_url"]),
        })
    areas, upcoming = [], []
    if shop["allow_delivery"]:
        from app.delivery_logistics import get_policy
        policy = await get_policy(db, tenant)
        if policy["coverage_mode"] == "areas":
            areas = [{key: area[key] for key in ("city", "state", "neighborhood", "fee_cents")}
                     for area in policy["areas"] if area["kind"] == "included"]
            upcoming = schedule_options(policy, shop, now)
    options = ("brand_color", "logo_url", "cover_url", "layout", "public_phone", "minimum_subtotal_cents",
               "closed_message", "estimate_min_minutes", "estimate_max_minutes")
    return {**{key: shop[key] for key in ("title", "description", "allow_pickup", "pickup_details",
                                         "payment_methods", "opening_hours")},
            **{key: shop.get(key, STORE_OPTIONS[key]) for key in options},
            "allow_delivery": bool(areas), "delivery_areas": areas, "schedule_options": upcoming,
            "open": is_open(shop, now), "items": items}


def signing_key():
    secret = os.getenv("PUBLIC_SITE_SIGNING_KEY", "")
    if len(secret) < 32:
        raise HTTPException(503, "Loja temporariamente indisponível", headers=HEADERS)
    return hmac.digest(secret.encode(), b"orion-delivery-store-quote:v1", "sha256")


def quote_signature(tenant, shop, payload, estimate, policy_revision, expires):
    draft = CheckoutDraft.model_validate(payload.model_dump(include=set(CheckoutDraft.model_fields))).model_dump(mode="json")
    if draft["change_for_cents"] is None:
        del draft["change_for_cents"]
    if not draft["accept_exchange"]:
        del draft["accept_exchange"]
    facts = [str(tenant), shop["slug"], shop["revision"], draft, estimate["estimate_version"], policy_revision, expires]
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
        if payload.scheduled_at and payload.scheduled_at.astimezone(ZoneInfo(shop["timezone"])).date().isoformat() in shop.get("closed_dates", ()):
            raise HTTPException(422, "A loja não atende na data escolhida")
        logistics = await quote_delivery(db, tenant, payload.address, payload.scheduled_at,
                                         require_coverage=True, lock=lock, now=now)
    lines = await order_lines(db, tenant, payload.items, payload.mode, storefront=True, lock=lock)
    approved = (await db.execute(text(ITEMS + " AND s.variant_id=ANY(:ids)"),
                                 {"tenant": tenant, "ids": [item.variant_id for item in payload.items]})).mappings().all()
    visible = {row["variant_id"]: row for row in approved if available_item(shop, row)}
    if len(visible) != len(lines):
        commerce.conflict("Um produto está indisponível na loja. Confira seu carrinho.")
    if any(line["exchange_container_id"] is not None for line in lines) and not payload.accept_exchange:
        raise HTTPException(422, "Confirme a devolução dos vasilhames vazios selecionados")
    estimate = priced_order(lines, OrderEstimate(mode=payload.mode, items=payload.items,
                                                delivery_fee_cents=logistics["delivery_fee_cents"]), now)
    if not estimate["confirmation_requirements_met"]:
        commerce.conflict("O estoque ou o preço mudou. Confira seu carrinho.")
    if estimate["total_cents"] > MAX_MONEY:
        raise HTTPException(422, "O total excede o limite do pedido")
    minimum = shop.get("minimum_subtotal_cents", 0)
    if estimate["subtotal_cents"] < minimum:
        raise HTTPException(422, "O subtotal dos produtos está abaixo do pedido mínimo da loja")
    if payload.change_for_cents is not None and payload.change_for_cents < estimate["total_cents"]:
        raise HTTPException(422, "O valor em dinheiro deve cobrir o total do pedido")
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
        "change_cents": None if payload.change_for_cents is None else payload.change_for_cents - estimate["total_cents"],
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
            INSERT INTO delivery_storefront_orders
                (tenant_id,order_id,tracking_digest,payment_preference,buyer_note,change_for_cents)
            VALUES (:tenant,:id,:digest,:method,:note,:change_for)
        """), {"tenant": tenant, "id": order_id, "digest": hashlib.sha256(tracking.encode()).hexdigest(),
                "method": payload.payment_preference, "note": payload.buyer_note, "change_for": payload.change_for_cents})
        await commerce.reserve_stock(db, tenant, PUBLIC_ACTOR, order_id, lines)
        await packaging.save_plan(db, tenant, order_id, None, lines, packaging.required_exchanges(lines))
        await commerce.audit(db, tenant, PUBLIC_ACTOR, "order", order_id, "confirmed",
                             {"total_cents": estimate["total_cents"], "mode": payload.mode, "source": "storefront"})
        return {"order_code": str(order_id)[:8].upper(), "status": "confirmed",
                "total_cents": estimate["total_cents"], "tracking_code": tracking}

    return await run_once(db, tenant, payload, "storefront_checkout", shop["slug"], write,
                          optional_defaults={"buyer_note": None, "change_for_cents": None, "accept_exchange": False})


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


def render_page(title, slug="", preview=""):
    page = (STATIC / "orion-delivery-store.html").read_text(encoding="utf-8")
    page = page.replace("__STORE_SLUG__", escape(slug, quote=True))
    page = page.replace("__STORE_PREVIEW__", escape(preview, quote=True))
    page = page.replace("__STORE_TITLE__", escape(title, quote=True))
    return HTMLResponse(page, headers=HEADERS)


@router.get("/loja/{slug}", include_in_schema=False)
async def storefront_page(slug: str, request: Request):
    await rate(request, slug, "page")
    async with public_session(slug) as (_, _, shop):
        return render_page(shop["title"], slug=slug)


async def preview_page(db, tenant):
    state = await store_state(db, tenant)
    return render_page(state["title"], preview=state["preview_url"] + "/catalogo")


async def preview_catalogue(db, tenant):
    state = await store_state(db, tenant)
    return await catalogue(db, tenant, {**await business(db, tenant), **state})


@router.get(PREVIEW_PATH, include_in_schema=False)
async def storefront_preview(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, preview_page)


@router.get(PREVIEW_PATH + "/catalogo", include_in_schema=False)
async def storefront_preview_data(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    return await run(actor, tenant_id, preview_catalogue)


@router.get("/delivery-store-assets/products/{preset}.webp", include_in_schema=False)
async def storefront_product_image(preset: str):
    return product_image_response(preset)


@router.get("/delivery-store-assets/{asset}", include_in_schema=False)
async def storefront_asset(asset: str):
    if asset not in PUBLIC_ASSETS:
        raise HTTPException(404, "Arquivo indisponível")
    return FileResponse(STATIC / asset, media_type=PUBLIC_ASSETS[asset], headers=HEADERS)
