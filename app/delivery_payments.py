"""Record Delivery collections that a staff member has already verified."""
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, field_validator
from sqlalchemy import text

from app import commercial_service as commerce, management_models as management
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.delivery_catalog import run_once
from app.management_schemas import MAX_MONEY


ReceivedMethod = Literal["cash", "debit_card", "credit_card", "pix"]

COLLECTION_TOTAL_JOIN = """
    LEFT JOIN LATERAL (
        SELECT sum(e.amount_cents) AS collected_cents
        FROM delivery_credit_collections c
        JOIN financial_entries e ON (e.tenant_id,e.id)=(c.tenant_id,c.financial_entry_id)
        WHERE c.tenant_id=d.tenant_id AND c.order_id=d.order_id AND e.kind='income' AND e.status='paid'
    ) pc ON true
"""
COLLECTED_CENTS = "coalesce(pc.collected_cents,0)+CASE WHEN f.status='paid' THEN f.amount_cents ELSE 0 END"
OUTSTANDING_CENTS = "CASE WHEN f.status='pending' THEN f.amount_cents ELSE 0 END"
RECEIPT_LINKS = """
    SELECT tenant_id,financial_entry_id FROM delivery_order_details
    WHERE tenant_id=:t AND financial_entry_id IS NOT NULL
    UNION ALL
    SELECT tenant_id,financial_entry_id FROM delivery_credit_collections WHERE tenant_id=:t
"""


class Tender(ProfileModel):
    method: ReceivedMethod
    amount_cents: StrictInt = Field(ge=1, le=MAX_MONEY)


class CreditPayment(ProfileModel):
    amount_cents: StrictInt = Field(gt=0, le=MAX_MONEY)
    expected_revision: StrictInt = Field(ge=1)
    payment_method: ReceivedMethod
    received: StrictBool
    idempotency_key: IdempotencyKey

    @field_validator("received")
    @classmethod
    def confirmation_required(cls, value):
        if not value:
            raise ValueError("Confirme que o valor foi recebido antes de registrar")
        return value


async def record_completion(db, tenant, order, method, due_date, tenders):
    """Post each confirmed tender to Finance. Store one primary link and any additional receipts."""
    amounts = [(t.method, t.amount_cents) for t in tenders] or [(method, order["total_cents"])]
    primary = None
    stamp = commerce.now_utc()
    for index, (tender_method, amount) in enumerate(amounts):
        receipt = await management.insert_row(db, tenant, "financial_entries", {
            "kind":"income", "description":f"Pedido {str(order['id'])[:8].upper()}",
            "amount_cents":amount, "category":"Delivery", "payment_method":"other" if due_date else tender_method,
            "status":"pending" if due_date else "paid", "occurred_at":stamp, "customer_id":order["customer_id"],
        }, str(order["id"]) if index == 0 else f"split:{order['id']}:{tender_method}", operation="delivery_order_receipt")
        if index == 0:
            primary = receipt
            await db.execute(text("""
                UPDATE delivery_order_details SET financial_entry_id=:f,payment_due_date=:due
                WHERE tenant_id=:t AND order_id=:o
            """), {"t":tenant, "o":order["id"], "f":receipt["id"], "due":due_date})
        else:
            await db.execute(text("""
                INSERT INTO delivery_credit_collections(tenant_id,order_id,financial_entry_id) VALUES (:t,:o,:f)
            """), {"t":tenant, "o":order["id"], "f":receipt["id"]})
    return primary


async def apply_collection(db, tenant, access, order, method, *, amount_cents=None):
    """The caller holds the order lock throughout the credit transition."""
    if order["status"] != "fulfilled" or order["payment_due_date"] is None or order["financial_entry_id"] is None:
        commerce.conflict("Somente pedidos a prazo com recebimento pendente podem ser baixados")
    receivable = await management.get_row(db, tenant, "financial_entries", order["financial_entry_id"], lock=True)
    if receivable["status"] != "pending" or receivable["kind"] != "income":
        commerce.conflict("O recebimento foi alterado. Confira o lançamento.")
    balance = receivable["amount_cents"]
    received = balance if amount_cents is None else amount_cents
    if received > balance:
        raise HTTPException(422, "O valor recebido é maior que o saldo a receber")
    outstanding = balance - received
    now = commerce.now_utc()
    receipt_id = receivable["id"]
    if outstanding:
        installment = await management.insert_row(db, tenant, "financial_entries", {
            "kind": "income",
            "description": f"Parcela do pedido {str(order['id'])[:8].upper()}",
            "amount_cents": received,
            "category": "Delivery",
            "payment_method": method,
            "status": "paid",
            "occurred_at": now,
            "customer_id": order["customer_id"],
        }, f"collection:{order['id']}:{order['revision']}", operation="delivery_order_receipt")
        receipt_id = installment["id"]
        await db.execute(text("""
            INSERT INTO delivery_credit_collections(tenant_id,order_id,financial_entry_id)
            VALUES (:t,:o,:f)
        """), {"t": tenant, "o": order["id"], "f": receipt_id})
        await db.execute(text("""
            UPDATE financial_entries SET amount_cents=:amount,updated_at=now(),version=version+1
            WHERE tenant_id=:t AND id=:f
        """), {"t": tenant, "f": receivable["id"], "amount": outstanding})
    await db.execute(text("""
        UPDATE financial_entries SET status='paid',received_at=:now,payment_method=:method,
            receipt_recorded_at=:now,receipt_source='manual',receipt_actor_id=:actor,
            updated_at=:now,version=version+1
        WHERE tenant_id=:t AND id=:f
    """), {"t": tenant, "f": receipt_id, "method": method, "actor": access.account_id, "now": now})
    await management.audit(db, tenant, "management_updated", "financial_entries", receivable["id"])
    return {"id": receipt_id, "amount_cents": received, "outstanding_cents": outstanding}


async def collect(db, tenant, access, order_id, payload):
    from app.delivery_orders import one_order

    async def write():
        order = await one_order(db, tenant, order_id, lock=True)
        if order["revision"] != payload.expected_revision:
            commerce.conflict("O pedido foi alterado. Confira o saldo antes de registrar o recebimento.")
        receipt = await apply_collection(
            db, tenant, access, order, payload.payment_method, amount_cents=payload.amount_cents
        )
        revision = (await db.execute(text("""
            UPDATE commercial_orders SET revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND id=:o RETURNING revision
        """), {"t": tenant, "o": order_id})).scalar_one()
        payment_status = "pending" if receipt["outstanding_cents"] else "paid"
        result = {
            "id": order_id,
            "status": order["status"],
            "revision": revision,
            "financial_entry_id": receipt["id"],
            "amount_cents": receipt["amount_cents"],
            "outstanding_cents": receipt["outstanding_cents"],
            "receipt_status": payment_status,
        }
        await commerce.audit(db, tenant, access, "order", order_id,
                             "partially_received" if payment_status == "pending" else "received", {
                                 "financial_entry_id": receipt["id"],
                                 "payment_method": payload.payment_method,
                                 "amount_cents": receipt["amount_cents"],
                                 "outstanding_cents": receipt["outstanding_cents"],
                             })
        return result

    return await run_once(db, tenant, payload, "credit_collection", order_id, write)


async def return_payment(db, tenant, access, order, *, refunded):
    """Close the outstanding balance and refund only money actually received."""
    main_id = order["financial_entry_id"]
    main = await management.get_row(db, tenant, "financial_entries", main_id, lock=True) if main_id else None
    paid = (await db.execute(text("""
        SELECT f.amount_cents,f.payment_method FROM financial_entries f
        WHERE f.tenant_id=:t AND f.kind='income' AND f.status='paid'
          AND (f.id=:main OR EXISTS (
            SELECT 1 FROM delivery_credit_collections c
            WHERE c.tenant_id=f.tenant_id AND c.order_id=:o AND c.financial_entry_id=f.id
          ))
    """), {"t": tenant, "main": main_id, "o": order["id"]})).mappings().all()
    refund_amount = sum(receipt["amount_cents"] for receipt in paid)
    outstanding = main["amount_cents"] if main and main["status"] == "pending" else 0
    if refund_amount + outstanding != order["total_cents"]:
        commerce.conflict("Confira os lançamentos de Delivery antes de registrar a devolução")
    if bool(refund_amount) != refunded:
        raise HTTPException(422, "Confirme o estorno realizado" if refund_amount else "Este pedido não tem pagamento recebido")
    if outstanding:
        await db.execute(text("""
            UPDATE financial_entries SET status='cancelled',updated_at=now(),version=version+1
            WHERE tenant_id=:t AND id=:f
        """), {"t": tenant, "f": main_id})
        await management.audit(db, tenant, "management_updated", "financial_entries", main_id)
    if not refund_amount:
        return None
    methods = {receipt["payment_method"] for receipt in paid}
    refund = await management.insert_row(db, tenant, "financial_entries", {
        "kind": "expense",
        "description": f"Estorno do pedido {str(order['id'])[:8].upper()}",
        "amount_cents": refund_amount,
        "category": "Delivery",
        "payment_method": next(iter(methods)) if len(methods) == 1 else "other",
        "status": "paid",
        "occurred_at": commerce.now_utc(),
        "customer_id": order["customer_id"],
    }, str(order["id"]), operation="delivery_order_refund")
    await db.execute(text("""
        UPDATE delivery_order_details SET return_financial_entry_id=:f WHERE tenant_id=:t AND order_id=:o
    """), {"t": tenant, "o": order["id"], "f": refund["id"]})
    return refund["id"]
