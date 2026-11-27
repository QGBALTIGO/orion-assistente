"""Apply confirmed packaging exchanges and customer loans with a Delivery order."""
from collections import defaultdict
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import text

from app.business_profile_schemas import ProfileModel
from app.delivery_containers import LOAN_DELTA, MAX_QUANTITY, get_container


class ContainerOrderItem(ProfileModel):
    variant_id: UUID
    container_id: UUID
    exchange_quantity: StrictInt = Field(default=0, ge=0, le=1000)
    loan_quantity: StrictInt = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def movement_quantity(self):
        if not 1 <= self.exchange_quantity + self.loan_quantity <= 1000:
            raise ValueError("Informe quantos vasilhames serão trocados ou emprestados")
        return self


def required_exchanges(lines, selections=()):
    indexed = {entry.variant_id: entry for entry in selections}
    if len(indexed) != len(selections):
        raise HTTPException(422, "Agrupe os vasilhames de cada variação em uma única linha")
    for line in lines:
        container = line.get("exchange_container_id")
        if container is None:
            continue
        variant = line["variant_id"]
        expected = ContainerOrderItem(variant_id=variant, container_id=container, exchange_quantity=line["quantity"])
        if variant in indexed and indexed[variant] != expected:
            raise HTTPException(422, "A variação selecionada exige a troca de um vasilhame por unidade")
        indexed[variant] = expected
    return list(indexed.values())


async def save_plan(db, tenant, order_id, customer_id, lines, plans):
    if not plans:
        return
    if len({entry.variant_id for entry in plans}) != len(plans):
        raise HTTPException(422, "Agrupe os vasilhames de cada variação em uma única linha")
    available = {UUID(str(line["variant_id"])): line["quantity"] for line in lines}
    for entry in plans:
        if entry.exchange_quantity + entry.loan_quantity > available.get(entry.variant_id, 0):
            raise HTTPException(422, "A troca ou o comodato supera a quantidade do produto no pedido")
        if entry.loan_quantity and customer_id is None:
            raise HTTPException(422, "Vincule o cliente para registrar o comodato")
    for container_id in sorted({entry.container_id for entry in plans}):
        await get_container(db, tenant, container_id)
    await db.execute(text("""
        INSERT INTO delivery_order_container_plans
            (tenant_id,order_id,variant_id,container_id,exchange_quantity,loan_quantity)
        VALUES (:tenant,:order_id,:variant_id,:container_id,:exchange_quantity,:loan_quantity)
    """), [
        {"tenant": tenant, "order_id": order_id, **entry.model_dump()}
        for entry in plans
    ])


async def get_plan(db, tenant, order_id):
    rows = (await db.execute(text("""
        SELECT p.variant_id,p.container_id,c.name AS container_name,p.exchange_quantity,p.loan_quantity
        FROM delivery_order_container_plans p
        JOIN delivery_containers c ON (c.tenant_id,c.id)=(p.tenant_id,p.container_id)
        WHERE p.tenant_id=:t AND p.order_id=:o
        ORDER BY p.variant_id
    """), {"t": tenant, "o": order_id})).mappings().all()
    return [dict(row) for row in rows]


def remaining_loan(original_quantity, earlier_balance, repayments):
    """Assign generic repayments to the oldest active loans under the container lock.

    A linked reversal consumes only its own loan, even when other loans are older.
    """
    remaining = original_quantity
    for quantity, manual, reverses_earlier in repayments:
        if manual:
            applied_earlier = min(earlier_balance, quantity)
            earlier_balance -= applied_earlier
            remaining = max(0, remaining - (quantity - applied_earlier))
        elif reverses_earlier:
            earlier_balance = max(0, earlier_balance - quantity)
    return remaining


async def outstanding_for_order(db, tenant, container_id, order_id, customer_id):
    refs = {"t": tenant, "c": container_id, "o": order_id, "customer": customer_id}
    original = (await db.execute(text("""
        SELECT ledger_sequence,quantity FROM delivery_container_movements
        WHERE tenant_id=:t AND container_id=:c AND order_id=:o AND customer_id=:customer
            AND order_phase='fulfill' AND kind='lend'
    """), refs)).mappings().first()
    if original is None:
        raise HTTPException(409, "O histórico do comodato precisa de revisão")
    params = {**refs, "sequence": original["ledger_sequence"]}
    previous = (await db.execute(text(f"""
        SELECT coalesce(sum({LOAN_DELTA}),0)
        FROM delivery_container_movements
        WHERE tenant_id=:t AND container_id=:c AND customer_id=:customer
          AND (ledger_sequence IS NULL OR ledger_sequence<:sequence)
    """), params)).scalar_one()
    rows = (await db.execute(text("""
        SELECT m.quantity,m.order_id IS NULL AS manual,
               prior.ledger_sequence<:sequence AS reverses_earlier
        FROM delivery_container_movements m LEFT JOIN delivery_container_movements prior
          ON (prior.tenant_id,prior.container_id,prior.order_id)=(m.tenant_id,m.container_id,m.order_id)
          AND prior.order_phase='fulfill' AND prior.kind='lend'
        WHERE m.tenant_id=:t AND m.container_id=:c AND m.customer_id=:customer
          AND m.kind='return' AND m.ledger_sequence>:sequence
        ORDER BY m.ledger_sequence
    """), params)).all()
    return remaining_loan(original["quantity"], max(0, previous), rows)


async def record_order_movement(db, tenant, access, container_id, order_id, phase, kind, quantity, before, after,
                                *, customer=None):
    await db.execute(text("""
        INSERT INTO delivery_container_movements
            (tenant_id,id,container_id,customer_id,actor_id,kind,quantity,balance_before,balance_after,note,
             order_id,order_phase,occurred_at)
        VALUES (:tenant,:id,:container,:customer,:actor,:kind,:quantity,:before,:after,:note,
                :order_id,:phase,clock_timestamp())
    """), {"tenant": tenant, "id": uuid4(), "container": container_id, "customer": customer,
            "actor": access.account_id, "kind": kind, "quantity": quantity, "before": before, "after": after,
            "note": f"Pedido {str(order_id)[:8].upper()}", "order_id": order_id, "phase": phase})


async def apply(db, tenant, access, order_id, customer_id, confirmed, *, reverse=False):
    """Move packaging in the caller's transaction, with the order already locked."""
    plan = await get_plan(db, tenant, order_id)
    if not plan:
        return
    if not confirmed:
        raise HTTPException(422, "Confirme a movimentação física dos vasilhames do pedido")
    by_container = defaultdict(lambda: [0, 0])
    for entry in plan:
        quantities = by_container[entry["container_id"]]
        quantities[0] += entry["exchange_quantity"]
        quantities[1] += entry["loan_quantity"]

    if any(loan for _, loan in by_container.values()):
        if customer_id is None:
            raise HTTPException(409, "O cliente vinculado ao comodato precisa de revisão")
        if not reverse:
            active = (await db.execute(text("""
                SELECT id FROM customers WHERE tenant_id=:t AND id=:c AND active AND deleted_at IS NULL FOR SHARE
            """), {"t": tenant, "c": customer_id})).scalar_one_or_none()
            if active is None:
                raise HTTPException(409, "O cliente vinculado ao comodato está inativo")

    phase = "return" if reverse else "fulfill"
    for container_id, (exchange, loan) in sorted(by_container.items()):
        current = await get_container(db, tenant, container_id, lock=True)
        before = current["empty_quantity"]
        after = before - exchange if reverse else before + exchange
        if not 0 <= after <= MAX_QUANTITY:
            raise HTTPException(409, "Os vazios da troca não estão disponíveis ou excedem o saldo permitido")
        if reverse and loan:
            loan = await outstanding_for_order(db, tenant, container_id, order_id, customer_id)
        if not exchange and not loan:
            continue
        await db.execute(text("""
            UPDATE delivery_containers SET empty_quantity=:quantity,revision=revision+1
            WHERE tenant_id=:t AND id=:c
        """), {"t": tenant, "c": container_id, "quantity": after})
        if exchange:
            await record_order_movement(db, tenant, access, container_id, order_id, phase,
                                        "out" if reverse else "in", exchange, before, after)
        if loan:
            # The product stock transaction already represents the filled unit leaving or returning.
            await record_order_movement(db, tenant, access, container_id, order_id, phase,
                                        "return" if reverse else "lend", loan, after, after,
                                        customer=customer_id)
