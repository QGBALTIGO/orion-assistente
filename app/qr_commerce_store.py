"""Customer commerce, scoped to the server-authenticated tenant AND contact.

No HTTP endpoint accepts this authority. The QR handler must construct the scope
after authenticating the bridge and resolving its tenant/phone. Customer actions
never impersonate an operator, mutate catalog prices, or confirm a payment.
"""
from dataclasses import dataclass
from datetime import datetime,timedelta
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from uuid import UUID, uuid5

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import commercial_service as commercial
from app import commercial_fulfillment as fulfillment_service
from app.commercial_schemas import EstimateRequest, QuoteLineRequest
from app.database import get_session_factory, tenant_transaction
from app.qr_intake_revision import check_before_write


@dataclass(frozen=True)
class CustomerScope:
    tenant: UUID
    phone: str
    message_id: str
    role: str = "customer"
    account_id: None = None

    def __post_init__(self):
        if not isinstance(self.tenant, UUID) or not re.fullmatch(r"[1-9][0-9]{7,14}", self.phone):
            raise ValueError("Authenticated tenant and contact required")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,160}", self.message_id):
            raise ValueError("Validated inbound message identity required")
        if self.role != "customer" or self.account_id is not None:
            raise ValueError("Customer authority cannot impersonate an operator")

    @property
    def contact(self):
        return "+" + self.phone

    def identity(self, purpose):
        return uuid5(self.tenant, "qr-commerce:" + self.phone + ":" + purpose + ":" + self.message_id)


def confirmation_token(scope, quote_id):
    secret = os.getenv("QR_BRIDGE_TOKEN", "")
    if len(secret.encode()) < 32:
        raise HTTPException(503, "A confirmação de pedidos está temporariamente indisponível")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"",
               info=b"orion:commercial-confirmation:v1").derive(secret.encode())
    value = hmac.new(key, scope.tenant.bytes + scope.contact.encode() + quote_id.bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


async def owned_quote(db, scope, quote_id):
    # Ownership is always a database predicate, not a later response filter.
    exists = (await db.execute(text("SELECT id FROM commercial_quotes WHERE tenant_id=:t AND id=:q AND contact_e164=:contact"),
        {"t": scope.tenant, "q": quote_id, "contact": scope.contact})).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, "Orçamento não encontrado para este contato")
    return await commercial._quote(db, scope.tenant, quote_id)


async def owned_order(db, scope, order_id, *, lock=False):
    row = (await db.execute(text("""
        SELECT o.id,o.quote_id,o.status,o.payment_status,o.revision,o.created_at,o.updated_at
        FROM commercial_orders o JOIN commercial_quotes q ON q.tenant_id=o.tenant_id AND q.id=o.quote_id
        WHERE o.tenant_id=:t AND o.id=:id AND q.contact_e164=:contact
    """ + (" FOR UPDATE OF o" if lock else "")),
        {"t": scope.tenant, "id": order_id, "contact": scope.contact})).mappings().first()
    if row is None:
        raise HTTPException(404, "Pedido não encontrado para este contato")
    return dict(row)


async def active_catalog(scope):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            await commercial.tenant_exists(db, scope.tenant, active=True)
            products = (await db.execute(text("""SELECT id,name,description,synonyms,faqs,official_benefits,revision
                FROM commercial_products WHERE tenant_id=:t AND active ORDER BY name,id LIMIT 200"""), {"t": scope.tenant})).mappings().all()
            variants = (await db.execute(text("""SELECT v.id,v.product_id,v.name,v.attributes,v.price_kind,v.unit_price_cents,v.revision
                FROM commercial_variants v JOIN commercial_products p ON p.tenant_id=v.tenant_id AND p.id=v.product_id
                WHERE v.tenant_id=:t AND v.active AND p.active ORDER BY v.name,v.id"""), {"t": scope.tenant})).mappings().all()
            return [{**dict(p), "variants": [dict(v) for v in variants if v["product_id"] == p["id"]]}
                    for p in products if any(v["product_id"] == p["id"] for v in variants)]


async def estimate(scope, items, *, fulfillment=None):
    payload = EstimateRequest(items=items, contact_e164=scope.contact, fulfillment=fulfillment)
    async with get_session_factory()() as db:
        return await commercial.estimate(db, scope.tenant, payload)


async def fulfillment_options(scope):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            return fulfillment_service.public_options(await fulfillment_service.options(db, scope.tenant))


async def pickup_appointments(scope):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            return await fulfillment_service.pickup_appointments(db, scope.tenant, scope.contact)


async def stock_information(scope, variant_ids):
    """Current observed inventory only; never infer a requested quantity."""
    ids = [UUID(str(value)) for value in variant_ids]
    if not ids or len(ids) > 12 or len(set(ids)) != len(ids):
        raise ValueError("A bounded unique active-variant selection is required")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            await commercial.tenant_exists(db, scope.tenant, active=True)
            rows = (await db.execute(text("""SELECT v.id,i.mode,i.quantity_on_hand,i.reserved_quantity,
                i.observed_at,i.valid_until FROM commercial_variants v
                JOIN commercial_products p ON p.tenant_id=v.tenant_id AND p.id=v.product_id
                JOIN commercial_inventory i ON i.tenant_id=v.tenant_id AND i.variant_id=v.id
                WHERE v.tenant_id=:t AND v.id=ANY(:ids) AND v.active AND p.active"""),
                {"t": scope.tenant, "ids": ids})).mappings().all()
            now = commercial.now_utc()
            return {str(row["id"]): {"available_quantity":
                max(0, row["quantity_on_hand"] - row["reserved_quantity"])
                if row["mode"] == "tracked" and row["observed_at"] <= now < row["valid_until"] else None}
                for row in rows}


async def approved_complements(scope, product_id):
    async with get_session_factory()() as db:
        return await commercial.approved_complements(db,scope.tenant,UUID(str(product_id)))


async def create_quote(scope, items, *, customer_name=None, validate_summary=None, fulfillment=None):
    payload = EstimateRequest(items=items, contact_e164=scope.contact, fulfillment=fulfillment)
    quote_id = scope.identity("quote")
    token = confirmation_token(scope, quote_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            await commercial.tenant_exists(db, scope.tenant, active=True)
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(quote_id)})
            existing = (await db.execute(text("SELECT id FROM commercial_quotes WHERE tenant_id=:t AND id=:id AND contact_e164=:contact"),
                {"t": scope.tenant, "id": quote_id, "contact": scope.contact})).scalar_one_or_none()
            if existing:
                quote = await owned_quote(db, scope, quote_id)
                previous = [(UUID(item["variant_id"]), item["quantity"]) for item in quote["items"]]
                if previous != [(item.variant_id, item.quantity) for item in payload.items] or not fulfillment_service.same_selection(quote, payload.fulfillment):
                    commercial.conflict("Esta mensagem já originou outro orçamento; envie a alteração em uma nova mensagem")
                current = commercial.calculate(await commercial._load_lines(db, scope.tenant, payload.items), commercial.now_utc())
                if quote["expires_at"] <= commercial.now_utc() or any(
                    old[key] != new[key] for old, new in zip(quote["items"], current["items"])
                    for key in ("product_revision", "variant_revision", "inventory_revision")):
                    commercial.conflict("O resumo anterior mudou ou expirou; solicite outro em nova mensagem")
                contract = await fulfillment_service.validate(db, scope.tenant, quote)
                return {**quote, **fulfillment_service.apply(current, contract), "confirmation_token": token}
            now = commercial.now_utc()
            calculated = commercial.calculate(await commercial._load_lines(db, scope.tenant, payload.items), now)
            contract = await fulfillment_service.resolve(db, scope.tenant, scope.contact, payload.fulfillment, lock=True)
            calculated = fulfillment_service.apply(calculated, contract)
            if validate_summary is not None:
                validate_summary(calculated)
            expires = now + timedelta(minutes=15)
            await check_before_write()
            inserted = (await db.execute(text("""INSERT INTO commercial_quotes
                (id,tenant_id,contact_e164,customer_name,total_cents,minimum_total_cents,confirmation_digest,expires_at,
                    fulfillment_mode,pickup_appointment_id,fulfillment_snapshot)
                SELECT :id,:t,:contact,:name,:total,:minimum,:digest,:expires,
                    :fulfillment_mode,:pickup_appointment_id,CAST(:fulfillment_snapshot AS jsonb)
                WHERE """ + fulfillment_service.WRITE_GUARD + " RETURNING id"),
                {"id": quote_id, "t": scope.tenant, "contact": scope.contact,
                 "name": customer_name[:120] if customer_name else None,
                 "total": calculated["total_cents"], "minimum": calculated["minimum_total_cents"],
                 "digest": hashlib.sha256(token.encode()).hexdigest(), "expires": expires,
                 **fulfillment_service.insert_params(contract), **fulfillment_service.guard_params(contract, scope.contact)})).scalar_one_or_none()
            if inserted is None:
                fulfillment_service.conflict()
            for index, item in enumerate(calculated["items"]):
                await db.execute(text("""INSERT INTO commercial_quote_items
                    (tenant_id,quote_id,variant_id,product_id,item_order,quantity,product_revision,variant_revision,inventory_revision,snapshot)
                    VALUES (:t,:q,:v,:p,:position,:qty,:pr,:vr,:ir,CAST(:snapshot AS jsonb))"""),
                    {"t": scope.tenant, "q": quote_id, "v": UUID(item["variant_id"]), "p": UUID(item["product_id"]),
                     "position": index, "qty": item["quantity"], "pr": item["product_revision"],
                     "vr": item["variant_revision"], "ir": item["inventory_revision"], "snapshot": json.dumps(jsonable_encoder(item))})
            await commercial.audit(db, scope.tenant, scope, "quote", quote_id, "customer_requested", {"message_digest": str(scope.identity("message"))})
            return {"id": quote_id, "kind": "quote", "expires_at": expires, "confirmation_token": token,
                    "contact_e164": scope.contact, "customer_name": customer_name, **calculated}


async def get_quote(scope, quote_id):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            return await owned_quote(db, scope, quote_id)


async def get_order(scope, order_id):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            order = await owned_order(db, scope, order_id)
            return {**order, "quote": await owned_quote(db, scope, order["quote_id"])}


async def create_order(scope, quote_id, *, validate_summary=None):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            await commercial.tenant_exists(db, scope.tenant, active=True)
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(quote_id)})
            quote = await owned_quote(db, scope, quote_id)
            if validate_summary is not None:
                validate_summary(quote)
            existing = (await db.execute(text("""SELECT o.id FROM commercial_orders o JOIN commercial_quotes q
                ON q.tenant_id=o.tenant_id AND q.id=o.quote_id WHERE o.tenant_id=:t AND o.quote_id=:q AND q.contact_e164=:contact"""),
                {"t": scope.tenant, "q": quote_id, "contact": scope.contact})).scalar_one_or_none()
            if existing:
                return await owned_order(db, scope, existing)
            if quote["expires_at"] <= commercial.now_utc():
                commercial.conflict("Orçamento expirado. Um novo resumo é necessário")
            order_id = scope.identity("order")
            key = "qr:" + str(scope.identity("order-key"))
            contract = await fulfillment_service.validate(db, scope.tenant, quote)
            await check_before_write()
            inserted = (await db.execute(text("INSERT INTO commercial_orders(id,tenant_id,quote_id,idempotency_key) SELECT :id,:t,:q,:key WHERE " +
                fulfillment_service.WRITE_GUARD + " RETURNING id"), {"id": order_id, "t": scope.tenant, "q": quote_id, "key": key,
                **fulfillment_service.guard_params(contract, scope.contact)})).scalar_one_or_none()
            if inserted is None:
                fulfillment_service.conflict()
            await commercial.audit(db, scope.tenant, scope, "order", order_id, "customer_draft_requested")
            return await owned_order(db, scope, order_id)


async def confirm_order(scope, order_id, *, revision, token, summary_message_id, validate_summary=None):
    # Correlation alone is not consent. The conversation layer must additionally
    # validate the exact fresh affirmative text and call this only on a new turn.
    if not summary_message_id or scope.message_id == summary_message_id:
        raise HTTPException(409, "A confirmação exige uma nova mensagem após o resumo")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            await commercial.tenant_exists(db, scope.tenant, active=True)
            order = await owned_order(db, scope, order_id, lock=True)
            quote = await owned_quote(db, scope, order["quote_id"])
            if validate_summary is not None:
                validate_summary(quote)
            digest = (await db.execute(text("SELECT confirmation_digest FROM commercial_quotes WHERE tenant_id=:t AND id=:q AND contact_e164=:contact"),
                {"t": scope.tenant, "q": order["quote_id"], "contact": scope.contact})).scalar_one()
            if not isinstance(token, str) or not secrets.compare_digest(digest, hashlib.sha256(token.encode()).hexdigest()):
                raise HTTPException(403, "A confirmação não corresponde ao seu resumo")
            if order["status"] == "confirmed":
                return {**order, "quote": quote}
            if order["status"] != "draft" or order["revision"] != revision or quote["expires_at"] <= commercial.now_utc():
                commercial.conflict("O pedido mudou ou o resumo expirou. Revise um novo resumo")
            contract = await fulfillment_service.validate(db, scope.tenant, quote, confirming=True)
            requests = [QuoteLineRequest(variant_id=item["variant_id"], quantity=item["quantity"]) for item in quote["items"]]
            lines = await commercial._load_lines(db, scope.tenant, requests, lock=True)
            for old, current in zip(quote["items"], lines):
                if any(old[key] != current[key] for key in ("product_revision", "variant_revision", "inventory_revision")):
                    commercial.conflict("O preço, catálogo ou estoque mudou. Revise um novo resumo")
            calculated = commercial.calculate(lines, commercial.now_utc())
            calculated = fulfillment_service.apply(calculated, contract)
            if not calculated["confirmation_requirements_met"] or calculated["total_cents"] != quote["total_cents"]:
                commercial.conflict("Preço ou estoque exige verificação humana; pedido não confirmado")
            if validate_summary is not None:
                validate_summary(calculated)
            await fulfillment_service.fence(db, scope.tenant, scope.contact, contract)
            await check_before_write()
            for item in sorted(lines, key=lambda item: item["variant_id"]):
                await db.execute(text("""UPDATE commercial_inventory SET reserved_quantity=reserved_quantity+:qty,
                    revision=revision+1,updated_at=now() WHERE tenant_id=:t AND variant_id=:v"""),
                    {"t": scope.tenant, "v": item["variant_id"], "qty": item["quantity"]})
                await commercial.audit(db, scope.tenant, scope, "stock", item["variant_id"], "customer_reserved", {"order_id": order_id, "quantity": item["quantity"]})
            changed = (await db.execute(text("UPDATE commercial_orders SET status='confirmed',revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id AND " +
                fulfillment_service.WRITE_GUARD + " RETURNING id"), {"t": scope.tenant, "id": order_id,
                **fulfillment_service.guard_params(contract, scope.contact)})).scalar_one_or_none()
            if changed is None:
                fulfillment_service.conflict()
            await commercial.audit(db, scope.tenant, scope, "order", order_id, "customer_confirmed", {"summary_message_digest": hashlib.sha256(summary_message_id.encode()).hexdigest(), "message_digest": str(scope.identity("message"))})
            return {**await owned_order(db, scope, order_id), "quote": calculated}


def cancellation_live(expires_at):
    try:
        expiry=datetime.fromisoformat(expires_at) if isinstance(expires_at,str) else expires_at
        return isinstance(expiry,datetime) and expiry.tzinfo is not None and commercial.now_utc()<expiry<=commercial.now_utc()+timedelta(minutes=15)
    except (ValueError,TypeError):return False


async def cancel_order(scope, order_id, *, revision, summary_message_id, summary_expires_at=None, validate_summary=None):
    if not summary_message_id or scope.message_id == summary_message_id:
        raise HTTPException(409, "O cancelamento exige nova confirmação após o resumo")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, scope.tenant):
            order = await owned_order(db, scope, order_id, lock=True)
            quote = await owned_quote(db, scope, order["quote_id"])
            if validate_summary is not None:
                validate_summary(quote)
            if order["status"] == "cancelled":
                return {**order, "quote": quote}
            if not cancellation_live(summary_expires_at):
                commercial.conflict('O resumo de cancelamento expirou; solicite uma nova revisão')
            if order["revision"] != revision:
                commercial.conflict("O pedido mudou; confira o estado atual antes de cancelar")
            await check_before_write()
            if order["status"] == "confirmed":
                for item in sorted(quote["items"], key=lambda item: item["variant_id"]):
                    changed = (await db.execute(text("""UPDATE commercial_inventory SET reserved_quantity=reserved_quantity-:qty,
                        revision=revision+1,updated_at=now() WHERE tenant_id=:t AND variant_id=:v
                        AND reserved_quantity>=:qty RETURNING variant_id"""),
                        {"t": scope.tenant, "v": UUID(item["variant_id"]), "qty": item["quantity"]})).scalar_one_or_none()
                    if changed is None:
                        commercial.conflict("A reserva precisa de revisão humana; cancelamento não aplicado")
                    await commercial.audit(db, scope.tenant, scope, "stock", changed, "customer_released", {"order_id": order_id, "quantity": item["quantity"]})
            await db.execute(text("UPDATE commercial_orders SET status='cancelled',revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": scope.tenant, "id": order_id})
            await commercial.audit(db, scope.tenant, scope, "order", order_id, "customer_cancelled", {"message_digest": str(scope.identity("message"))})
            return {**await owned_order(db, scope, order_id), "quote": quote}
