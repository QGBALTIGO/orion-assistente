"""Server-priced product operations. No appointment or payment mutations."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.commercial_schemas import QuoteLineRequest
from app.database import tenant_transaction
from app.tenant_access import check_access


def now_utc():
    return datetime.now(timezone.utc)


def conflict(message):
    raise HTTPException(409, message)


async def tenant_exists(db, tenant, *, active=False):
    row = (await db.execute(text("SELECT status FROM tenants WHERE id=:t"), {"t": tenant})).scalar_one_or_none()
    if row is None or (active and row != "active"):
        raise HTTPException(404, "Estabelecimento indisponível")


async def audit(db, tenant, access, entity, entity_id, action, details=None):
    await db.execute(text("""
        INSERT INTO commercial_events (tenant_id,entity,entity_id,action,actor_account_id,actor_role,details)
        VALUES (:t,:entity,:id,:action,:actor,:role,CAST(:details AS jsonb))
    """), {"t": tenant, "entity": entity, "id": entity_id, "action": action,
           "actor": access.account_id, "role": access.role,
           "details": json.dumps(jsonable_encoder(details or {}))})


def product_values(product):
    values = product.model_dump()
    for key in ("synonyms", "faqs", "official_benefits"):
        values[key] = json.dumps(values[key])
    return values


async def save_product(db, tenant, product, access, *, product_id=None, expected_revision=None):
    check_access(access.role, "draft")
    values = product_values(product)
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant)
        if product_id is None:
            product_id = uuid4()
            row = (await db.execute(text("""
                INSERT INTO commercial_products (id,tenant_id,name,description,synonyms,faqs,official_benefits,active)
                VALUES (:id,:t,:name,:description,CAST(:synonyms AS jsonb),CAST(:faqs AS jsonb),CAST(:official_benefits AS jsonb),:active)
                RETURNING *
            """), {"id": product_id, "t": tenant, **values})).mappings().one()
        else:
            row = (await db.execute(text("""
                UPDATE commercial_products SET name=:name,description=:description,
                    synonyms=CAST(:synonyms AS jsonb),faqs=CAST(:faqs AS jsonb),official_benefits=CAST(:official_benefits AS jsonb),
                    active=:active,revision=revision+1,updated_at=now()
                WHERE tenant_id=:t AND id=:id AND revision=:expected RETURNING *
            """), {"id": product_id, "t": tenant, "expected": expected_revision, **values})).mappings().first()
            if row is None:
                conflict("Produto não encontrado ou alterado. Atualize o catálogo")
        await audit(db, tenant, access, "product", product_id, "saved", {"revision": row["revision"], "product": product.model_dump()})
        return dict(row)


async def save_variant(db, tenant, product_id, variant, access, *, variant_id=None, expected_revision=None):
    check_access(access.role, "draft")
    values = {**variant.model_dump(), "attributes": json.dumps(variant.attributes)}
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant)
        if variant_id is None:
            variant_id = uuid4()
            row = (await db.execute(text("""
                INSERT INTO commercial_variants (id,tenant_id,product_id,name,sku,attributes,price_kind,unit_price_cents,active)
                VALUES (:id,:t,:product,:name,:sku,CAST(:attributes AS jsonb),:price_kind,:unit_price_cents,:active)
                RETURNING *
            """), {"id": variant_id, "t": tenant, "product": product_id, **values})).mappings().one()
            await db.execute(text("INSERT INTO commercial_inventory (tenant_id,variant_id) VALUES (:t,:id)"), {"t": tenant, "id": variant_id})
        else:
            row = (await db.execute(text("""
                UPDATE commercial_variants SET name=:name,sku=:sku,attributes=CAST(:attributes AS jsonb),
                    price_kind=:price_kind,unit_price_cents=:unit_price_cents,active=:active,revision=revision+1,updated_at=now()
                WHERE tenant_id=:t AND id=:id AND product_id=:product AND revision=:expected RETURNING *
            """), {"id": variant_id, "t": tenant, "product": product_id, "expected": expected_revision, **values})).mappings().first()
            if row is None:
                conflict("Variante não encontrada ou alterada. Atualize o catálogo")
        await audit(db, tenant, access, "variant", variant_id, "saved", {"revision": row["revision"], "variant": variant.model_dump()})
        return dict(row)


async def observe_stock(db, tenant, variant_id, payload, access):
    check_access(access.role, "draft")
    stock = payload.stock
    if stock.observed_at and stock.observed_at > now_utc() + timedelta(seconds=60):
        raise HTTPException(422, "A observação de estoque não pode estar no futuro")
    async with tenant_transaction(db, tenant):
        current = (await db.execute(text("""
            SELECT * FROM commercial_inventory WHERE tenant_id=:t AND variant_id=:id FOR UPDATE
        """), {"t": tenant, "id": variant_id})).mappings().first()
        if current is None or current["revision"] != payload.expected_revision:
            conflict("Estoque não encontrado ou alterado. Atualize antes de registrar a contagem")
        if stock.mode == "tracked" and stock.quantity_on_hand < current["reserved_quantity"]:
            conflict("A contagem não pode ficar abaixo das unidades já reservadas")
        row = (await db.execute(text("""
            UPDATE commercial_inventory SET mode=:mode,quantity_on_hand=:quantity_on_hand,
                observed_at=:observed_at,source=:source,valid_until=:valid_until,revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND variant_id=:id RETURNING *
        """), {"t": tenant, "id": variant_id, **stock.model_dump()})).mappings().one()
        await audit(db, tenant, access, "stock", variant_id, "observed", {
            "revision": row["revision"], "stock": stock.model_dump(), "reserved_quantity": row["reserved_quantity"]})
        return dict(row)


async def save_complement(db, tenant, source_id, payload, access):
    check_access(access.role, "draft")
    if source_id == payload.target_product_id:
        raise HTTPException(422, "Um complemento deve ser outro produto")
    async with tenant_transaction(db, tenant):
        row = (await db.execute(text("""
            INSERT INTO commercial_complements (tenant_id,source_product_id,target_product_id,reason,approved)
            VALUES (:t,:source,:target,:reason,:approved)
            ON CONFLICT (tenant_id,source_product_id,target_product_id) DO UPDATE
            SET reason=EXCLUDED.reason,approved=EXCLUDED.approved,updated_at=now() RETURNING *
        """), {"t": tenant, "source": source_id, "target": payload.target_product_id,
               "reason": payload.reason, "approved": payload.approved})).mappings().one()
        await audit(db, tenant, access, "complement", row["id"], "saved", payload.model_dump())
        return dict(row)


async def catalog(db, tenant):
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant)
        products = (await db.execute(text("SELECT * FROM commercial_products WHERE tenant_id=:t ORDER BY name,id LIMIT 200"), {"t": tenant})).mappings().all()
        rows = (await db.execute(text("""
            SELECT v.*,i.mode AS stock_mode,i.quantity_on_hand,i.reserved_quantity,i.observed_at,
                i.valid_until,i.source AS stock_source,i.revision AS inventory_revision
            FROM commercial_variants v JOIN commercial_inventory i
                ON i.tenant_id=v.tenant_id AND i.variant_id=v.id
            WHERE v.tenant_id=:t AND v.product_id=ANY(:ids) ORDER BY v.name,v.id
        """), {"t": tenant, "ids": [p["id"] for p in products]})).mappings().all() if products else []
        result = []
        for product in products:
            result.append({**dict(product), "variants": [dict(v) for v in rows if v["product_id"] == product["id"]]})
        return result


async def approved_complements(db, tenant, product_id):
    async with tenant_transaction(db, tenant):
        rows = (await db.execute(text("""
            SELECT c.id AS approved_relation_id,c.source_product_id,c.target_product_id,c.reason,p.name
            FROM commercial_complements c JOIN commercial_products p
                ON p.tenant_id=c.tenant_id AND p.id=c.target_product_id
            JOIN commercial_products origin ON origin.tenant_id=c.tenant_id AND origin.id=c.source_product_id
            WHERE c.tenant_id=:t AND c.source_product_id=:id AND c.approved AND p.active AND origin.active
              AND EXISTS(SELECT 1 FROM commercial_variants v WHERE v.tenant_id=p.tenant_id AND v.product_id=p.id AND v.active)
            ORDER BY p.name,c.id LIMIT 20
        """), {"t": tenant, "id": product_id})).mappings().all()
        return [dict(row) for row in rows]


async def _load_lines(db, tenant, items, *, lock=False):
    rows = (await db.execute(text("""
        SELECT p.id AS product_id,p.name AS product_name,p.revision AS product_revision,
            v.id AS variant_id,v.name AS variant_name,v.attributes,v.price_kind,v.unit_price_cents,v.revision AS variant_revision,
            i.mode,i.quantity_on_hand,i.reserved_quantity,i.observed_at,i.valid_until,i.source,i.revision AS inventory_revision
        FROM commercial_variants v JOIN commercial_products p ON p.tenant_id=v.tenant_id AND p.id=v.product_id
        JOIN commercial_inventory i ON i.tenant_id=v.tenant_id AND i.variant_id=v.id
        WHERE v.tenant_id=:t AND v.id=ANY(:ids) AND v.active AND p.active
        ORDER BY v.id
    """ + (" FOR SHARE OF p,v FOR UPDATE OF i" if lock else "")),
        {"t": tenant, "ids": [item.variant_id for item in items]})).mappings().all()
    found = {row["variant_id"]: dict(row) for row in rows}
    if len(found) != len(items):
        conflict("Há produto ou variante indisponível. Consulte o catálogo novamente")
    return [{**found[item.variant_id], "quantity": item.quantity} for item in items]


def calculate(lines, now):
    """Pure calculation: unknown stock and indicative prices never become promises."""
    snapshots, reasons = [], set()
    total, minimum = 0, 0
    for line in lines:
        exact = line["price_kind"] == "fixed"
        numeric = line["price_kind"] in {"fixed", "from"}
        amount = line["unit_price_cents"] * line["quantity"] if numeric else None
        total = total + amount if total is not None and exact else None
        minimum = minimum + amount if minimum is not None and numeric else None
        if not exact:
            reasons.add("non_fixed_price")
        known = line["mode"] == "tracked"
        fresh = known and line["observed_at"] <= now < line["valid_until"]
        available = line["quantity_on_hand"] - line["reserved_quantity"] if fresh else None
        if not known:
            reasons.add("unknown_stock")
        elif not fresh:
            reasons.add("stale_stock")
        elif available < line["quantity"]:
            reasons.add("insufficient_stock")
        snapshots.append({"product_id": str(line["product_id"]), "variant_id": str(line["variant_id"]),
            "product_name": line["product_name"], "variant_name": line["variant_name"],
            "attributes": line["attributes"], "quantity": line["quantity"], "price_kind": line["price_kind"],
            "unit_price_cents": line["unit_price_cents"], "total_cents": amount if exact else None,
            "minimum_total_cents": amount, "product_revision": line["product_revision"],
            "variant_revision": line["variant_revision"], "inventory_revision": line["inventory_revision"],
            "stock": {"mode": line["mode"], "available_quantity": available,
                "observed_at": line["observed_at"], "valid_until": line["valid_until"], "source": line["source"]}})
    return {"currency": "BRL", "items": snapshots, "total_cents": total, "minimum_total_cents": minimum,
            "confirmation_requirements_met": not reasons, "limitations": sorted(reasons)}


async def estimate(db, tenant, payload):
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant, active=True)
        return {"kind": "estimate", **calculate(await _load_lines(db, tenant, payload.items), now_utc())}


async def create_quote(db, tenant, payload, access):
    check_access(access.role, "draft")
    quote_id, token = uuid4(), secrets.token_urlsafe(32)
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant, active=True)
        lines = await _load_lines(db, tenant, payload.items)
        now = now_utc()
        result = calculate(lines, now)
        expires = now + timedelta(minutes=payload.validity_minutes)
        await db.execute(text("""
            INSERT INTO commercial_quotes (id,tenant_id,contact_e164,customer_name,total_cents,minimum_total_cents,
                confirmation_digest,expires_at,created_by)
            VALUES (:id,:t,:contact,:name,:total,:minimum,:digest,:expires,:actor)
        """), {"id": quote_id, "t": tenant, "contact": payload.contact_e164, "name": payload.customer_name,
               "total": result["total_cents"], "minimum": result["minimum_total_cents"],
               "digest": hashlib.sha256(token.encode()).hexdigest(), "expires": expires, "actor": access.account_id})
        for index, line in enumerate(result["items"]):
            await db.execute(text("""
                INSERT INTO commercial_quote_items (tenant_id,quote_id,variant_id,product_id,item_order,quantity,
                    product_revision,variant_revision,inventory_revision,snapshot)
                VALUES (:t,:q,:v,:p,:position,:quantity,:pr,:vr,:ir,CAST(:snapshot AS jsonb))
            """), {"t": tenant, "q": quote_id, "v": UUID(line["variant_id"]), "p": UUID(line["product_id"]),
                   "position": index, "quantity": line["quantity"], "pr": line["product_revision"],
                   "vr": line["variant_revision"], "ir": line["inventory_revision"], "snapshot": json.dumps(jsonable_encoder(line))})
        await audit(db, tenant, access, "quote", quote_id, "created", {"line_count": len(lines)})
    return {"id": quote_id, "kind": "quote", "expires_at": expires, "confirmation_token": token,
            "contact_e164": payload.contact_e164, "customer_name": payload.customer_name, **result}


async def _quote(db, tenant, quote_id):
    row = (await db.execute(text("""
        SELECT id,contact_e164,customer_name,currency,total_cents,minimum_total_cents,expires_at,created_at
        FROM commercial_quotes WHERE tenant_id=:t AND id=:id
    """), {"t": tenant, "id": quote_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Orçamento não encontrado")
    items = (await db.execute(text("""
        SELECT snapshot FROM commercial_quote_items WHERE tenant_id=:t AND quote_id=:id ORDER BY item_order
    """), {"t": tenant, "id": quote_id})).scalars().all()
    return {**dict(row), "kind": "quote", "items": items}


async def get_quote(db, tenant, quote_id):
    async with tenant_transaction(db, tenant):
        return await _quote(db, tenant, quote_id)


async def _order(db, tenant, order_id, *, lock=False):
    row = (await db.execute(text("""
        SELECT id,quote_id,status,payment_status,revision,created_at,updated_at
        FROM commercial_orders WHERE tenant_id=:t AND id=:id
    """ + (" FOR UPDATE" if lock else "")), {"t": tenant, "id": order_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Pedido não encontrado")
    return dict(row)


async def get_order(db, tenant, order_id):
    async with tenant_transaction(db, tenant):
        row = await _order(db, tenant, order_id)
        return {**row, "quote": await _quote(db, tenant, row["quote_id"])}


async def create_order(db, tenant, payload, access):
    check_access(access.role, "draft")
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant, active=True)
        for key in ("quote:" + str(payload.quote_id), "key:" + payload.idempotency_key):
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": str(tenant) + ":commercial:" + key})
        existing = (await db.execute(text("SELECT id,quote_id FROM commercial_orders WHERE tenant_id=:t AND idempotency_key=:key"),
                                     {"t": tenant, "key": payload.idempotency_key})).mappings().first()
        if existing:
            if existing["quote_id"] != payload.quote_id:
                conflict("Chave de idempotência já usada para outro orçamento")
            return await _order(db, tenant, existing["id"])
        quote = await _quote(db, tenant, payload.quote_id)
        if quote["expires_at"] <= now_utc():
            conflict("Orçamento expirado. Prepare uma nova proposta")
        if (await db.execute(text("SELECT id FROM commercial_orders WHERE tenant_id=:t AND quote_id=:q"),
                             {"t": tenant, "q": payload.quote_id})).scalar_one_or_none():
            conflict("Este orçamento já está vinculado a um pedido")
        order_id = uuid4()
        await db.execute(text("INSERT INTO commercial_orders (id,tenant_id,quote_id,idempotency_key) VALUES (:id,:t,:q,:key)"),
                         {"id": order_id, "t": tenant, "q": payload.quote_id, "key": payload.idempotency_key})
        await audit(db, tenant, access, "order", order_id, "draft_created")
        return await _order(db, tenant, order_id)


async def confirm_order(db, tenant, order_id, payload, access):
    # This is an explicit operator action. A future conversational caller must
    # first bind the customer's acceptance to this exact pending quote; the token
    # proves correlation with a proposal, not that a customer has said "yes".
    check_access(access.role, "draft")
    async with tenant_transaction(db, tenant):
        await tenant_exists(db, tenant, active=True)
        order = await _order(db, tenant, order_id, lock=True)
        quote = await _quote(db, tenant, order["quote_id"])
        digest = (await db.execute(text("SELECT confirmation_digest FROM commercial_quotes WHERE tenant_id=:t AND id=:q"),
                                  {"t": tenant, "q": order["quote_id"]})).scalar_one()
        if not secrets.compare_digest(digest, hashlib.sha256(payload.confirmation_token.encode()).hexdigest()):
            raise HTTPException(403, "Confirmação não corresponde a esta proposta")
        if order["status"] == "confirmed":
            return order  # Same confirmed order can never reserve stock twice.
        if order["status"] != "draft" or order["revision"] != payload.expected_revision:
            conflict("Pedido alterado ou encerrado. Revise antes de confirmar")
        if not quote["contact_e164"]:
            conflict("Identifique o cliente em uma nova proposta antes de confirmar")
        requests = [QuoteLineRequest(variant_id=item["variant_id"], quantity=item["quantity"]) for item in quote["items"]]
        if not requests:
            conflict("Orçamento sem itens válidos")
        lines = await _load_lines(db, tenant, requests, lock=True)
        now = now_utc()
        if quote["expires_at"] <= now:
            conflict("Orçamento expirado. Prepare uma nova proposta e peça confirmação novamente")
        for old, current in zip(quote["items"], lines):
            if any(old[key] != current[key] for key in ("product_revision", "variant_revision", "inventory_revision")):
                conflict("Catálogo ou estoque mudou. Prepare uma nova proposta e peça confirmação novamente")
        result = calculate(lines, now)
        if not result["confirmation_requirements_met"] or result["total_cents"] != quote["total_cents"]:
            conflict("Preço ou disponibilidade exige revisão humana; nenhum pedido foi confirmado")
        for line in sorted(lines, key=lambda item: item["variant_id"]):
            await db.execute(text("""
                UPDATE commercial_inventory SET reserved_quantity=reserved_quantity+:quantity,
                    revision=revision+1,updated_at=now() WHERE tenant_id=:t AND variant_id=:v
            """), {"t": tenant, "v": line["variant_id"], "quantity": line["quantity"]})
            await audit(db, tenant, access, "stock", line["variant_id"], "reserved", {"order_id": order_id, "quantity": line["quantity"]})
        await db.execute(text("UPDATE commercial_orders SET status='confirmed',revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": order_id})
        await audit(db, tenant, access, "order", order_id, "confirmed", {"total_cents": result["total_cents"]})
        return await _order(db, tenant, order_id)


async def cancel_order(db, tenant, order_id, payload, access):
    check_access(access.role, "draft")
    async with tenant_transaction(db, tenant):
        order = await _order(db, tenant, order_id, lock=True)
        if order["status"] == "cancelled":
            return order
        if order["revision"] != payload.expected_revision:
            conflict("Pedido alterado. Revise antes de cancelar")
        if order["status"] == "confirmed":
            quote = await _quote(db, tenant, order["quote_id"])
            for item in sorted(quote["items"], key=lambda line: line["variant_id"]):
                row = (await db.execute(text("""
                    UPDATE commercial_inventory SET reserved_quantity=reserved_quantity-:quantity,
                        revision=revision+1,updated_at=now()
                    WHERE tenant_id=:t AND variant_id=:v AND reserved_quantity>=:quantity RETURNING variant_id
                """), {"t": tenant, "v": UUID(item["variant_id"]), "quantity": item["quantity"]})).scalar_one_or_none()
                if row is None:
                    conflict("Reserva de estoque inconsistente; solicite revisão humana")
                await audit(db, tenant, access, "stock", row, "released", {"order_id": order_id, "quantity": item["quantity"]})
        await db.execute(text("UPDATE commercial_orders SET status='cancelled',revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": order_id})
        await audit(db, tenant, access, "order", order_id, "cancelled")
        return await _order(db, tenant, order_id)
