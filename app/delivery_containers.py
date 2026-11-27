"""Movement history for warehouse empties and packaging with customers."""
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import text

from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.delivery_catalog import LIMIT, run_once


MAX_QUANTITY = 10_000_000
ContainerKind = Literal["cylinder", "jug"]
ContainerFilter = Literal["all", "cylinder", "jug"]
LOAN_DELTA = "CASE kind WHEN 'lend' THEN quantity WHEN 'return' THEN -quantity ELSE 0 END"


class ContainerCreate(ProfileModel):
    kind: ContainerKind
    name: str = Field(min_length=1, max_length=120)
    quantity: StrictInt = Field(ge=0, le=MAX_QUANTITY)
    idempotency_key: IdempotencyKey


class Movement(ProfileModel):
    kind: Literal["in", "out", "count", "lend", "return"]
    quantity: StrictInt = Field(ge=0, le=MAX_QUANTITY)
    customer_id: UUID | None = None
    note: str | None = Field(default=None, max_length=300)
    expected_revision: StrictInt = Field(ge=1)
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def requirements(self):
        if self.quantity == 0 and self.kind != "count":
            raise ValueError("Informe uma quantidade maior que zero")
        if (self.kind in {"lend","return"}) != (self.customer_id is not None):
            raise ValueError("Vincule um cliente para empréstimos e devoluções")
        return self


def page(rows, offset):
    return {"items":[dict(row) for row in rows[:LIMIT]],"limit":LIMIT,"offset":offset,"has_more":len(rows)>LIMIT}


async def list_containers(db, tenant, *, kind, search, offset):
    rows = (await db.execute(text(f"""
        SELECT v.id,v.kind,v.name,v.empty_quantity,v.revision,
            coalesce((SELECT sum({LOAN_DELTA}) FROM delivery_container_movements m
                WHERE m.tenant_id=v.tenant_id AND m.container_id=v.id AND m.customer_id IS NOT NULL),0) AS loaned
        FROM delivery_containers v WHERE v.tenant_id=:t
            AND (:kind='all' OR v.kind=:kind) AND position(lower(:search) in lower(v.name))>0
        ORDER BY lower(v.name),v.id LIMIT :limit OFFSET :offset
    """), {"t":tenant,"kind":kind,"search":search.strip(),"limit":LIMIT+1,"offset":offset})).mappings().all()
    return page(rows, offset)


async def get_container(db, tenant, container_id, *, lock=False):
    found = (await db.execute(text("""
        SELECT id,kind,name,empty_quantity,revision FROM delivery_containers WHERE tenant_id=:t AND id=:id
    """ + (" FOR UPDATE" if lock else "")), {"t":tenant,"id":container_id})).mappings().first()
    if found is None:
        raise HTTPException(404, "Vasilhame não encontrado")
    return dict(found)


async def record(db, tenant, access, container_id, *, kind, quantity, before, after, customer=None, note=None):
    await db.execute(text("""
        INSERT INTO delivery_container_movements
            (tenant_id,id,container_id,customer_id,actor_id,kind,quantity,balance_before,balance_after,note)
        VALUES (:t,:id,:c,:customer,:actor,:kind,:q,:before,:after,:note)
    """), {"t":tenant,"id":uuid4(),"c":container_id,"customer":customer,"actor":access.account_id,
            "kind":kind,"q":quantity,"before":before,"after":after,"note":note})


async def create(db, tenant, access, payload):
    async def write():
        container_id = uuid4()
        await db.execute(text("""
            INSERT INTO delivery_containers (tenant_id,id,kind,name,empty_quantity)
            VALUES (:t,:id,:kind,:name,:q)
        """), {"t":tenant,"id":container_id,"kind":payload.kind,"name":payload.name,"q":payload.quantity})
        await record(db,tenant,access,container_id,kind="initial",quantity=payload.quantity,before=0,after=payload.quantity)
        return await get_container(db,tenant,container_id)
    return await run_once(db, tenant, payload, "container_create", None, write)


async def adjust(db, tenant, access, container_id, payload):
    async def write():
        current = await get_container(db,tenant,container_id,lock=True)
        if current["revision"] != payload.expected_revision:
            raise HTTPException(409, "O saldo foi movimentado. Abra o vasilhame novamente.")
        before = current["empty_quantity"]
        if payload.kind == "lend":
            active = (await db.execute(text("""
                SELECT id FROM customers WHERE tenant_id=:t AND id=:c AND active AND deleted_at IS NULL FOR SHARE
            """), {"t":tenant,"c":payload.customer_id})).scalar_one_or_none()
            if active is None:
                raise HTTPException(404, "Cliente ativo não encontrado")
        if payload.kind == "return":
            loaned = (await db.execute(text(f"""
                SELECT coalesce(sum({LOAN_DELTA}),0) FROM delivery_container_movements
                WHERE tenant_id=:t AND container_id=:id AND customer_id=:c
            """), {"t":tenant,"id":container_id,"c":payload.customer_id})).scalar_one()
            if loaned < payload.quantity:
                raise HTTPException(409, "A devolução supera os vasilhames que estão com o cliente.")
        if payload.kind == "count":
            after = payload.quantity
        else:
            delta = -payload.quantity if payload.kind in {"lend","out"} else payload.quantity
            after = before + delta
        if not 0 <= after <= MAX_QUANTITY:
            raise HTTPException(409, "A movimentação excede o saldo ou o limite do estoque.")
        await db.execute(text("""
            UPDATE delivery_containers SET empty_quantity=:q,revision=revision+1 WHERE tenant_id=:t AND id=:id
        """), {"t":tenant,"id":container_id,"q":after})
        await record(db,tenant,access,container_id,kind=payload.kind,quantity=payload.quantity,before=before,
                     after=after,customer=payload.customer_id,note=payload.note)
        return await get_container(db,tenant,container_id)
    return await run_once(db, tenant, payload, "container_movement", container_id, write)


async def history(db, tenant, container_id, *, offset):
    await get_container(db,tenant,container_id)
    rows = (await db.execute(text("""
        SELECT m.id,m.kind,m.quantity,m.balance_before,m.balance_after,m.note,m.occurred_at,c.name AS customer_name
        FROM delivery_container_movements m LEFT JOIN customers c ON (c.tenant_id,c.id)=(m.tenant_id,m.customer_id)
        WHERE m.tenant_id=:t AND m.container_id=:id
        ORDER BY m.occurred_at DESC,m.id DESC LIMIT :limit OFFSET :offset
    """), {"t":tenant,"id":container_id,"limit":LIMIT+1,"offset":offset})).mappings().all()
    return page(rows, offset)


async def loans(db, tenant, container_id, *, offset):
    await get_container(db,tenant,container_id)
    rows = (await db.execute(text(f"""
        WITH amounts AS (
            SELECT customer_id,sum({LOAN_DELTA}) AS quantity FROM delivery_container_movements
            WHERE tenant_id=:t AND container_id=:id AND customer_id IS NOT NULL
            GROUP BY customer_id HAVING sum({LOAN_DELTA})>0
        )
        SELECT c.id AS customer_id,c.name AS customer_name,amounts.quantity
        FROM amounts JOIN customers c ON c.tenant_id=:t AND c.id=amounts.customer_id
        ORDER BY lower(c.name),c.id LIMIT :limit OFFSET :offset
    """), {"t":tenant,"id":container_id,"limit":LIMIT+1,"offset":offset})).mappings().all()
    return page(rows, offset)
