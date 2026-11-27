"""Delivery catalogue and stock movements on the shared commercial inventory."""
import hashlib
import json
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import text

from app import commercial_service as commerce
from app.business_profile_schemas import ProfileModel, ShortText
from app.commercial_schemas import IdempotencyKey, Product, StockObservation, Variant


LIMIT = 25
MAX_QUANTITY = 10_000_000
StockMovement = Literal["purchase", "out", "count"]


class VariantDetails(ProfileModel):
    category: ShortText
    unit: str = Field(default="un", min_length=1, max_length=24)
    purchase_cost_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000)
    minimum_quantity: StrictInt = Field(default=0, ge=0, le=MAX_QUANTITY)
    barcode: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")


class VariantCreate(ProfileModel):
    variant: Variant
    details: VariantDetails
    initial_quantity: StrictInt = Field(ge=0, le=MAX_QUANTITY)
    idempotency_key: IdempotencyKey


class ProductCreate(VariantCreate):
    product: Product


class ItemUpdate(ProfileModel):
    product: Product
    variant: Variant
    details: VariantDetails
    product_revision: StrictInt = Field(ge=1)
    variant_revision: StrictInt = Field(ge=1)


class Adjustment(ProfileModel):
    movement: StockMovement
    quantity: StrictInt = Field(ge=0, le=MAX_QUANTITY)
    note: str | None = Field(default=None, min_length=1, max_length=300)
    expected_revision: StrictInt = Field(ge=1)
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def positive_movement(self):
        if self.movement != "count" and self.quantity == 0:
            raise ValueError("Informe uma quantidade maior que zero para entradas e saídas")
        return self


ITEMS_FROM = """
    FROM commercial_variants v
    JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
    JOIN commercial_inventory i ON (i.tenant_id,i.variant_id)=(v.tenant_id,v.id)
    LEFT JOIN delivery_variant_details d ON (d.tenant_id,d.variant_id)=(v.tenant_id,v.id)
"""


async def list_items(db, tenant, *, search, category, status_filter, low_stock, offset):
    rows = (await db.execute(text("""
        SELECT v.id AS variant_id, v.name AS variant_name, v.sku, v.attributes,
            v.price_kind, v.unit_price_cents, v.active AS variant_active, v.revision AS variant_revision,
            p.id AS product_id, p.name AS name, p.description, p.active AS product_active,
            p.synonyms,p.faqs,p.official_benefits,p.revision AS product_revision,
            i.mode,i.quantity_on_hand,i.reserved_quantity,i.valid_until,i.revision AS inventory_revision,
            d.category,COALESCE(d.unit,'un') AS unit,d.purchase_cost_cents,
            COALESCE(d.minimum_quantity,0) AS minimum_quantity,d.barcode
        """ + ITEMS_FROM + """
        WHERE v.tenant_id=:t
          AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',p.name,v.name,v.sku,d.barcode)))>0)
          AND (:category='' OR d.category=:category)
          AND (:status='all' OR (p.active AND v.active)=(:status='active'))
          AND (NOT :low OR (p.active AND v.active AND i.mode='tracked' AND i.valid_until>now()
                            AND i.quantity_on_hand-i.reserved_quantity<=COALESCE(d.minimum_quantity,0)))
        ORDER BY lower(p.name), lower(v.name),v.id LIMIT :limit OFFSET :offset
    """), {"t": tenant, "search": search.strip(), "category": category, "status": status_filter,
            "low": low_stock, "limit": LIMIT + 1, "offset": offset})).mappings().all()
    categories = (await db.execute(text("""
        SELECT DISTINCT category FROM delivery_variant_details WHERE tenant_id=:t ORDER BY category
    """), {"t": tenant})).scalars().all()
    return {"items": [dict(row) for row in rows[:LIMIT]], "categories": categories,
            "limit": LIMIT, "offset": offset, "has_more": len(rows) > LIMIT}


async def history(db, tenant, *, start: date | None, end: date | None, offset):
    rows = (await db.execute(text("""
        SELECT e.id,e.created_at,p.name AS name,v.name AS variant_name,e.action,e.details
        FROM commercial_events e JOIN commercial_variants v
            ON (v.tenant_id,v.id)=(e.tenant_id,e.entity_id)
        JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
        JOIN tenants t ON t.id=e.tenant_id
        WHERE e.tenant_id=:t AND e.entity='stock'
          AND (e.details ? 'movement' OR e.action IN ('reserved','released'))
          AND (CAST(:start AS date) IS NULL OR e.created_at>=CAST(:start AS date)::timestamp AT TIME ZONE t.timezone)
          AND (CAST(:end AS date) IS NULL OR e.created_at<(CAST(:end AS date)+1)::timestamp AT TIME ZONE t.timezone)
        ORDER BY e.created_at DESC,e.id DESC LIMIT :limit OFFSET :offset
    """), {"t": tenant, "start": start, "end": end, "limit": LIMIT + 1, "offset": offset})).mappings().all()
    return {"items": [dict(row) for row in rows[:LIMIT]], "limit": LIMIT,
            "offset": offset, "has_more": len(rows) > LIMIT}


async def store_details(db, tenant, variant_id, details):
    await db.execute(text("""
        INSERT INTO delivery_variant_details
            (tenant_id,variant_id,category,unit,purchase_cost_cents,minimum_quantity,barcode)
        VALUES (:t,:v,:category,:unit,:purchase_cost_cents,:minimum_quantity,:barcode)
        ON CONFLICT (tenant_id,variant_id) DO UPDATE SET category=EXCLUDED.category,
            unit=EXCLUDED.unit,purchase_cost_cents=EXCLUDED.purchase_cost_cents,
            minimum_quantity=EXCLUDED.minimum_quantity,barcode=EXCLUDED.barcode
    """), {"t": tenant, "v": variant_id, **details.model_dump()})


async def run_once(db, tenant, payload, operation, target, write):
    key = payload.idempotency_key
    scope = f"delivery-catalog:{tenant}:{key}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": scope})
    signature = json.dumps([operation, str(target), payload.model_dump(mode="json")], sort_keys=True)
    fingerprint = hashlib.sha256(signature.encode()).hexdigest()
    previous = (await db.execute(text("""
        SELECT fingerprint,result FROM delivery_catalog_operations WHERE tenant_id=:t AND operation_key=:key
    """), {"t": tenant, "key": key})).mappings().first()
    if previous:
        if previous["fingerprint"] != fingerprint:
            commerce.conflict("A chave já foi usada para outra operação. Confira o cadastro atual.")
        return previous["result"]
    result = await write()
    await db.execute(text("""
        INSERT INTO delivery_catalog_operations (tenant_id,operation_key,fingerprint,result)
        VALUES (:t,:key,:fingerprint,CAST(:result AS jsonb))
    """), {"t": tenant, "key": key, "fingerprint": fingerprint,
            "result": json.dumps(jsonable_encoder(result))})
    return result


async def locked_inventory(db, tenant, variant_id):
    row = (await db.execute(text("""
        SELECT * FROM commercial_inventory WHERE tenant_id=:t AND variant_id=:v FOR UPDATE
    """), {"t": tenant, "v": variant_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Item de estoque não encontrado")
    return row


async def record_count(db, tenant, access, current, quantity, movement, moved, note=None):
    stamp = commerce.now_utc()
    observation = StockObservation(mode="tracked", quantity_on_hand=quantity, observed_at=stamp,
                                   valid_until=stamp + timedelta(hours=24), source="delivery_panel")
    return await commerce.write_stock(db, tenant, current, observation, access, event_details={
        "movement": movement, "quantity": moved, "before": current["quantity_on_hand"],
        "after": quantity, "note": note,
    })


async def create_item(db, tenant, access, payload, product_id=None):
    async def write():
        target = product_id
        if target is None:
            target = (await commerce.write_product(db, tenant, payload.product, access))["id"]
        elif not (await db.execute(text("""
            SELECT id FROM commercial_products WHERE tenant_id=:t AND id=:p FOR SHARE
        """), {"t": tenant, "p": target})).scalar_one_or_none():
            raise HTTPException(404, "Produto não encontrado")
        variant = await commerce.write_variant(db, tenant, target, payload.variant, access)
        variant_id = variant["id"]
        await store_details(db, tenant, variant_id, payload.details)
        current = await locked_inventory(db, tenant, variant_id)
        await record_count(db, tenant, access, current, payload.initial_quantity,
                           "initial", payload.initial_quantity)
        return {"product_id": target, "variant_id": variant_id}

    return await run_once(db, tenant, payload, "create", product_id, write)


async def update_item(db, tenant, access, variant_id, payload):
    product_id = (await db.execute(text("""
        SELECT product_id FROM commercial_variants WHERE tenant_id=:t AND id=:v
    """), {"t": tenant, "v": variant_id})).scalar_one_or_none()
    if product_id is None:
        raise HTTPException(404, "Produto não encontrado")
    await commerce.write_product(db, tenant, payload.product, access,
                                 product_id=product_id, expected_revision=payload.product_revision)
    await commerce.write_variant(db, tenant, product_id, payload.variant, access,
                                 variant_id=variant_id, expected_revision=payload.variant_revision)
    await store_details(db, tenant, variant_id, payload.details)
    return {"product_id": product_id, "variant_id": variant_id}


async def adjust(db, tenant, access, variant_id, payload):
    async def write():
        current = await locked_inventory(db, tenant, variant_id)
        if current["revision"] != payload.expected_revision:
            commerce.conflict("O estoque foi alterado. Confira o saldo antes de registrar a movimentação.")
        quantity = payload.quantity
        if payload.movement != "count":
            if current["mode"] != "tracked" or current["valid_until"] <= commerce.now_utc():
                commerce.conflict("Registre uma contagem atual antes de lançar entradas ou saídas.")
            delta = quantity if payload.movement == "purchase" else -quantity
            quantity = current["quantity_on_hand"] + delta
        if quantity < current["reserved_quantity"]:
            commerce.conflict("O saldo não pode ficar abaixo das unidades reservadas.")
        if quantity > MAX_QUANTITY:
            raise HTTPException(422, "O saldo excede o limite permitido")
        row = await record_count(db, tenant, access, current, quantity, payload.movement,
                                 payload.quantity, payload.note)
        return {"variant_id": variant_id, "quantity_on_hand": row["quantity_on_hand"],
                "reserved_quantity": row["reserved_quantity"], "revision": row["revision"]}

    return await run_once(db, tenant, payload, "adjust", variant_id, write)
