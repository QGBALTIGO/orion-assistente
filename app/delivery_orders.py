"""Manual sales with the shared commercial reservation and financial ledgers."""
from datetime import timedelta
import hashlib
import json
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, management_models as management
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey, QuoteLineRequest
from app.delivery_catalog import LIMIT, run_once
from app.management_schemas import MAX_MONEY
from app.onboarding_service import require_operational_ready


class OrderItems(ProfileModel):
    items: list[QuoteLineRequest] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def no_duplicates(self):
        if len({item.variant_id for item in self.items}) != len(self.items):
            raise ValueError("Agrupe a quantidade de cada variação em uma única linha")
        return self


class ManualOrder(OrderItems):
    mode: Literal["counter", "pickup"] = "counter"
    customer_id: UUID | None = None
    estimate_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def customer_for_pickup(self):
        if self.mode == "pickup" and self.customer_id is None:
            raise ValueError("Selecione um cliente para a retirada")
        return self


class Transition(ProfileModel):
    action: Literal["fulfill", "cancel"]
    expected_revision: StrictInt = Field(ge=1)
    payment_method: Literal["cash", "debit_card", "credit_card"] | None = None
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def payment_at_completion(self):
        if self.action == "cancel" and self.payment_method is not None:
            raise ValueError("O cancelamento não aceita recebimento")
        return self


ORDERS = """
    SELECT o.id,o.quote_id,o.status,o.revision,o.created_at,d.mode,d.customer_id,
        d.financial_entry_id,q.customer_name,q.total_cents
    FROM commercial_orders o JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
    JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
    WHERE o.tenant_id=:t AND o.origin='delivery'
"""


async def list_orders(db, tenant, *, search, state, start, end, offset):
    lower, upper = None, None
    if start is not None or end is not None:
        _, lower, upper = await management.period_for(db, tenant, start, end)
    rows = (await db.execute(text(ORDERS + """
        AND (:state='all' OR o.status=:state)
        AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',q.customer_name,cast(o.id as text))))>0)
        AND (CAST(:lower AS timestamptz) IS NULL OR o.created_at>=:lower)
        AND (CAST(:upper AS timestamptz) IS NULL OR o.created_at<:upper)
        ORDER BY o.created_at DESC,o.id DESC LIMIT :limit OFFSET :offset
    """), {"t":tenant,"state":state,"search":search.strip(),"lower":lower,"upper":upper,
            "limit":LIMIT+1,"offset":offset})).mappings().all()
    return {"items":[dict(row) for row in rows[:LIMIT]],"limit":LIMIT,"offset":offset,"has_more":len(rows)>LIMIT}


async def one_order(db, tenant, order_id, *, lock=False):
    row = (await db.execute(text(ORDERS + " AND o.id=:id" + (" FOR UPDATE OF o" if lock else "")),
                            {"t":tenant,"id":order_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Pedido não encontrado")
    return dict(row)


async def detail(db, tenant, order_id):
    row = await one_order(db, tenant, order_id)
    quote = await commerce._quote(db, tenant, row["quote_id"])
    events = (await db.execute(text("""
        SELECT action,created_at,details FROM commercial_events
        WHERE tenant_id=:t AND entity='order' AND entity_id=:o ORDER BY created_at,id
    """), {"t":tenant,"o":order_id})).mappings().all()
    return {**row,"items":quote["items"],"history":[dict(event) for event in events]}


async def lookup_customers(db, tenant, *, search):
    q = search.strip()
    if len(q) < 2:
        return {"items":[]}
    rows = (await db.execute(text("""
        SELECT id,name,whatsapp_e164 FROM customers
        WHERE tenant_id=:t AND active AND deleted_at IS NULL
          AND position(lower(:q) in lower(concat_ws(' ',name,whatsapp_e164)))>0
        ORDER BY lower(name),id LIMIT 20
    """), {"t":tenant,"q":q})).mappings().all()
    return {"items":[dict(row) for row in rows]}


async def estimate(db, tenant, payload):
    lines = await commerce._load_lines(db, tenant, payload.items)
    quote = commerce.calculate(lines, commerce.now_utc())
    return {**quote,"estimate_version":estimate_version(quote)}


def estimate_version(quote):
    keys = ("variant_id","quantity","product_revision","variant_revision","inventory_revision")
    facts = [[item[key] for key in keys] for item in quote["items"]]
    return hashlib.sha256(json.dumps([quote["total_cents"],facts]).encode()).hexdigest()


async def create(db, tenant, access, payload):
    async def write():
        await commerce.tenant_exists(db, tenant, active=True)
        await require_operational_ready(db, tenant, "product_orders")
        customer = None
        if payload.customer_id is not None:
            customer = (await db.execute(text("""
                SELECT name,whatsapp_e164 FROM customers WHERE tenant_id=:t AND id=:c
                AND active AND deleted_at IS NULL FOR SHARE
            """), {"t":tenant,"c":payload.customer_id})).mappings().first()
            if customer is None:
                raise HTTPException(404, "Cliente ativo não encontrado")
        lines = await commerce._load_lines(db, tenant, payload.items, lock=True)
        now = commerce.now_utc()
        quote = commerce.calculate(lines, now)
        if not quote["confirmation_requirements_met"]:
            commerce.conflict("Confira o preço e o estoque de todos os itens antes de criar o pedido.")
        if estimate_version(quote) != payload.estimate_version:
            commerce.conflict("Preço ou estoque mudou. Calcule o total novamente.")
        if quote["total_cents"] > MAX_MONEY:
            raise HTTPException(422, "O total excede o limite do Financeiro")
        quote_id, order_id = uuid4(), uuid4()
        await commerce.write_quote(db, tenant, access, quote_id, secrets.token_urlsafe(32),
            customer["whatsapp_e164"] if customer else None, customer["name"] if customer else None,
            quote, commerce.fulfillment.EMPTY, now + timedelta(minutes=15))
        await db.execute(text("""
            INSERT INTO commercial_orders (id,tenant_id,quote_id,idempotency_key,status,origin)
            VALUES (:id,:t,:q,:key,'confirmed','delivery')
        """), {"id":order_id,"t":tenant,"q":quote_id,"key":f"delivery:{order_id}"})
        await db.execute(text("""
            INSERT INTO delivery_order_details (tenant_id,order_id,mode,customer_id)
            VALUES (:t,:id,:mode,:c)
        """), {"t":tenant,"id":order_id,"mode":payload.mode,"c":payload.customer_id})
        await commerce.reserve_stock(db, tenant, access, order_id, lines)
        await commerce.audit(db, tenant, access, "order", order_id, "confirmed", {"total_cents":quote["total_cents"],"mode":payload.mode})
        return {"id":order_id,"status":"confirmed","revision":1,"total_cents":quote["total_cents"]}

    return await run_once(db, tenant, payload, "order_create", None, write)


async def fulfill(db, tenant, access, row, items, method):
    await commerce.tenant_exists(db, tenant, active=True)
    if row["total_cents"] and not method:
        raise HTTPException(422, "Informe a forma de pagamento recebida")
    if not row["total_cents"] and method:
        raise HTTPException(422, "Não há valor a receber neste pedido")
    for item in sorted(items,key=lambda item:item["variant_id"]):
        variant_id=UUID(item["variant_id"])
        updated = (await db.execute(text("""
            UPDATE commercial_inventory SET quantity_on_hand=quantity_on_hand-:qty,
                reserved_quantity=reserved_quantity-:qty,revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND variant_id=:v AND mode='tracked' AND reserved_quantity>=:qty
            RETURNING quantity_on_hand
        """), {"t":tenant,"v":variant_id,"qty":item["quantity"]})).scalar_one_or_none()
        if updated is None:
            commerce.conflict("A contagem ou reserva do estoque precisa de revisão antes da baixa.")
        await commerce.audit(db,tenant,access,"stock",variant_id,"sold",
            {"order_id":row["id"],"quantity":item["quantity"],"after":updated,"movement":"sold"})
    receipt = None
    if row["total_cents"]:
        receipt = await management.insert_row(db, tenant, "financial_entries", {
            "kind":"income","description":f"Pedido {str(row['id'])[:8].upper()}",
            "amount_cents":row["total_cents"],"category":"Delivery","payment_method":method,
            "status":"paid","occurred_at":commerce.now_utc(),"customer_id":row["customer_id"],
        }, str(row["id"]), operation="delivery_order_receipt")
        await db.execute(text("""
            UPDATE delivery_order_details SET financial_entry_id=:f WHERE tenant_id=:t AND order_id=:o
        """), {"t":tenant,"o":row["id"],"f":receipt["id"]})
    return receipt


async def transition(db, tenant, access, order_id, payload):
    async def write():
        row = await one_order(db, tenant, order_id, lock=True)
        if row["status"] != "confirmed" or row["revision"] != payload.expected_revision:
            commerce.conflict("O pedido foi alterado ou encerrado. Confira o histórico.")
        quote = await commerce._quote(db, tenant, row["quote_id"])
        receipt = None
        if payload.action == "cancel":
            await commerce.release_stock(db, tenant, access, order_id, quote["items"])
            status="cancelled"
        else:
            receipt = await fulfill(db,tenant,access,row,quote["items"],payload.payment_method)
            status="fulfilled"
        revision = (await db.execute(text("""
            UPDATE commercial_orders SET status=:status,revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND id=:o RETURNING revision
        """), {"status":status,"t":tenant,"o":order_id})).scalar_one()
        receipt_id=receipt["id"] if receipt else None
        await commerce.audit(db, tenant, access, "order", order_id, status,
                             {"financial_entry_id":receipt_id,"payment_method":payload.payment_method})
        return {"id":order_id,"status":status,"revision":revision,"financial_entry_id":receipt_id}

    return await run_once(db, tenant, payload, "order_transition", order_id, write)
