"""Opt-in route foundation. One order remains the source of truth.

Reservation changes assignment only; dispatch uses the existing order transition.
No financial or inventory posting is introduced by reserving or loading.
"""
import os
from collections import Counter
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictBool, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, delivery_catalog as catalog
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.tenant_access import TenantAccess

MAX_STOPS = 5  # pilot workload limit; not a vehicle weight or safety limit
QUEUE_LIMIT = 100


def enabled(tenant):
    allowed = {v.strip() for v in os.getenv("ORION_DELIVERY_ROUTE_V2_TENANTS", "").split(",") if v.strip()}
    preview = {v.strip() for v in os.getenv("ORION_DELIVERY_PREVIEW_TENANTS", "").split(",") if v.strip()}
    return str(tenant) in allowed or ("@preview" in allowed and str(tenant) in preview)


class OrderRef(ProfileModel):
    order_id: UUID
    expected_revision: StrictInt = Field(ge=1)


class Selection(ProfileModel):
    orders: list[OrderRef] = Field(min_length=1, max_length=MAX_STOPS)
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def distinct(self):
        if len({x.order_id for x in self.orders}) != len(self.orders):
            raise ValueError("Selecione cada pedido apenas uma vez")
        return self


class LoadLine(ProfileModel):
    variant_id: UUID
    quantity: StrictInt = Field(ge=1, le=150000)


class StartRoute(Selection):
    load: list[LoadLine] = Field(min_length=1, max_length=150)
    checked: StrictBool

    @model_validator(mode="after")
    def distinct_load(self):
        if not self.checked:
            raise ValueError("Confirme a conferência física da carga")
        if len({x.variant_id for x in self.load}) != len(self.load):
            raise ValueError("Agrupe cada variante em uma linha da carga")
        return self


async def available(db, tenant, person):
    if not enabled(tenant) or not person["shift_started_at"] or not person["available"]:
        return []
    rows = (await db.execute(text("""
        SELECT o.id,o.revision,q.total_cents,o.created_at,d.scheduled_at,
            jsonb_build_object('neighborhood',d.address->>'neighborhood',
                'city',d.address->>'city','state',d.address->>'state') AS address,
            coalesce(s.payment_preference,d.tender_preference) AS payment_preference,
            (SELECT jsonb_agg(jsonb_build_object('variant_id',i.variant_id,
                'name',i.snapshot->>'product_name','variant',i.snapshot->>'variant_name',
                'quantity',i.quantity) ORDER BY i.item_order)
                FROM commercial_quote_items i WHERE (i.tenant_id,i.quote_id)=(o.tenant_id,o.quote_id)) AS items
        FROM commercial_orders o JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
        LEFT JOIN delivery_driver_assignments a ON (a.tenant_id,a.order_id)=(o.tenant_id,o.id)
        LEFT JOIN delivery_storefront_orders s ON (s.tenant_id,s.order_id)=(o.tenant_id,o.id)
        WHERE o.tenant_id=:t AND o.origin='delivery' AND o.status='confirmed' AND d.mode='delivery'
          AND d.courier_id IS NULL AND (d.scheduled_at IS NULL OR d.scheduled_at<=now())
          AND (a.order_id IS NULL OR a.status IN ('refused','released')
            OR (a.status='offered' AND a.expires_at<=now()))
        ORDER BY coalesce(d.scheduled_at,o.created_at),o.created_at,o.id LIMIT :lim
    """), {"t":tenant,"lim":QUEUE_LIMIT})).mappings().all()
    return [dict(r) for r in rows]


async def active_count(db, tenant, person):
    return int(await db.scalar(text("""
        SELECT count(*) FROM delivery_driver_assignments a
        JOIN commercial_orders o ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
        WHERE a.tenant_id=:t AND a.courier_id=:c
          AND ((a.status='reserved' AND o.status='confirmed') OR
               (a.status IN ('accepted','arrived') AND o.status='dispatched'))
    """), {"t":tenant,"c":person["courier_id"]}))


async def reserve_locked(db, tenant, person, ref, *, offered=False, capacity_checked=False):
    from app import delivery_orders as orders
    if not enabled(tenant):
        raise HTTPException(403,"Seleção de rotas não habilitada para esta empresa.")
    if not person["shift_started_at"] or not person["available"]:
        raise HTTPException(409,"Fique disponível antes de assumir pedidos.")
    row = await orders.one_order(db,tenant,ref.order_id,lock=True)
    if row["status"]!="confirmed" or row["mode"]!="delivery" or row["revision"]!=ref.expected_revision or row["courier_id"]:
        raise HTTPException(409,"Pedido não está mais disponível. Atualize a fila.")
    if row["scheduled_at"] and row["scheduled_at"]>commerce.now_utc():
        raise HTTPException(409,"O horário da entrega ainda não chegou.")
    if not capacity_checked and await active_count(db,tenant,person)>=MAX_STOPS:
        raise HTTPException(409,"Limite de pedidos simultâneos atingido.")
    params={"t":tenant,"o":ref.order_id,"c":person["courier_id"],"rev":person["revision"],"offered":offered}
    # Tenant-wide logistics advisory lock is held before this order row.
    # ON CONFLICT is the last line of defence against a competing assignment.
    if offered:
        got=await db.scalar(text("""
            UPDATE delivery_driver_assignments SET status='reserved',updated_at=now(),
              route_batch_id=NULL,route_position=NULL
            WHERE tenant_id=:t AND order_id=:o AND courier_id=:c AND access_revision=:rev
              AND status='offered' AND expires_at>now() RETURNING order_id
        """),params)
    else:
        got=await db.scalar(text("""
            INSERT INTO delivery_driver_assignments
              (tenant_id,order_id,courier_id,access_revision,status,expires_at)
            VALUES (:t,:o,:c,:rev,'reserved',now())
            ON CONFLICT(tenant_id,order_id) DO UPDATE SET courier_id=excluded.courier_id,
              access_revision=excluded.access_revision,status='reserved',offered_at=now(),
              updated_at=now(),expires_at=now(),route_batch_id=NULL,route_position=NULL
            WHERE delivery_driver_assignments.status IN ('refused','released')
              OR (delivery_driver_assignments.status='offered' AND delivery_driver_assignments.expires_at<=now())
            RETURNING order_id
        """),params)
    if got is None:
        raise HTTPException(409,"Outro entregador já possui este pedido.")
    revision=await db.scalar(text("""
        UPDATE commercial_orders SET revision=revision+1,updated_at=now()
        WHERE tenant_id=:t AND id=:o RETURNING revision
    """),params)
    await commerce.audit(db,tenant,TenantAccess("courier",person["account_id"]),"order",ref.order_id,
                         "driver_reserved",{"courier_id":person["courier_id"]})
    return {"id":ref.order_id,"status":"reserved","revision":revision}


async def reserve(db,tenant,person,payload):
    async def write():
        if await active_count(db,tenant,person)+len(payload.orders)>MAX_STOPS:
            raise HTTPException(409,"Limite de pedidos simultâneos atingido.")
        # All-or-nothing reservation; stable row-lock ordering.
        result={}
        for ref in sorted(payload.orders,key=lambda r:r.order_id):
            result[ref.order_id]=await reserve_locked(db,tenant,person,ref,capacity_checked=True)
        return {"orders":[result[r.order_id] for r in payload.orders]}
    return await catalog.run_once(db,tenant,payload,"driver_batch_reserve",person["account_id"],write)


async def release_one(db,tenant,person,payload):
    from app import delivery_orders as orders
    async def write():
        ref=payload.orders[0]
        row=await orders.one_order(db,tenant,ref.order_id,lock=True)
        if row["status"]!="confirmed" or row["revision"]!=ref.expected_revision:
            raise HTTPException(409,"O pedido mudou. Atualize a fila.")
        params={"t":tenant,"o":ref.order_id,"c":person["courier_id"],"rev":person["revision"]}
        released=await db.scalar(text("""
            UPDATE delivery_driver_assignments SET status='released',updated_at=now()
            WHERE tenant_id=:t AND order_id=:o AND courier_id=:c AND access_revision=:rev AND status='reserved'
            RETURNING order_id
        """),params)
        if released is None:
            raise HTTPException(409,"O pedido não está separado para você.")
        revision=await db.scalar(text("""
            UPDATE commercial_orders SET revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND id=:o RETURNING revision
        """),params)
        await commerce.audit(db,tenant,TenantAccess("courier",person["account_id"]),"order",ref.order_id,
                             "driver_reservation_released",{"courier_id":person["courier_id"]})
        return {"id":ref.order_id,"status":"released","revision":revision}
    if len(payload.orders)!=1:
        raise HTTPException(422,"Libere um pedido por vez.")
    return await catalog.run_once(db,tenant,payload,"driver_batch_release",person["account_id"],write)


async def release_reservations(db,tenant,courier):
    # Revocation invalidates reservations, not stock or orders already dispatched.
    await db.execute(text("""
        WITH released AS (
          UPDATE delivery_driver_assignments SET status='released',updated_at=now()
          WHERE tenant_id=:t AND courier_id=:c AND status='reserved' RETURNING order_id
        )
        UPDATE commercial_orders o SET revision=revision+1,updated_at=now()
        FROM released r WHERE o.tenant_id=:t AND o.id=r.order_id AND o.status='confirmed'
    """),{"t":tenant,"c":courier})


async def start_route(db,tenant,person,payload):
    from app import delivery_orders as orders
    async def write():
        if not person["shift_started_at"]:
            raise HTTPException(409,"Inicie o expediente.")
        params={"t":tenant,"c":person["courier_id"],"rev":person["revision"]}
        active=await db.scalar(text("""
            SELECT count(*) FROM delivery_driver_assignments a JOIN commercial_orders o
            ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
            WHERE a.tenant_id=:t AND a.courier_id=:c AND a.status IN ('accepted','arrived') AND o.status='dispatched'
        """),params)
        if active:
            raise HTTPException(409,"Conclua a rota em andamento antes de iniciar outra.")
        expected=Counter()
        for ref in sorted(payload.orders,key=lambda r:r.order_id):
            row=await orders.one_order(db,tenant,ref.order_id,lock=True)
            own=await db.scalar(text("""
                SELECT order_id FROM delivery_driver_assignments
                WHERE tenant_id=:t AND order_id=:o AND courier_id=:c AND access_revision=:rev AND status='reserved'
                FOR UPDATE
            """),{**params,"o":ref.order_id})
            if not own or row["status"]!="confirmed" or row["revision"]!=ref.expected_revision:
                raise HTTPException(409,"Uma reserva mudou. Confira a carga novamente.")
            if row["scheduled_at"] and row["scheduled_at"]>commerce.now_utc():
                raise HTTPException(409,"O horário da entrega ainda não chegou.")
            quote=await commerce._quote(db,tenant,row["quote_id"])
            for item in quote["items"]:
                expected[UUID(str(item["variant_id"]))]+=item["quantity"]
        if dict(expected)!={i.variant_id:i.quantity for i in payload.load}:
            raise HTTPException(422,"As quantidades carregadas não correspondem aos pedidos.")
        route_id=uuid4()
        access=TenantAccess("courier",person["account_id"])
        result=[]
        for position,ref in enumerate(payload.orders,1):
            key=str(uuid5(NAMESPACE_URL,f"orion:route:{tenant}:{person['courier_id']}:{ref.order_id}:{payload.idempotency_key}"))
            transition=await orders.transition(db,tenant,access,ref.order_id,orders.Transition(
                action="dispatch",courier_id=person["courier_id"],
                expected_revision=ref.expected_revision,idempotency_key=key))
            await db.execute(text("""
                UPDATE delivery_driver_assignments SET status='accepted',route_batch_id=:route,
                    route_position=:pos,updated_at=now()
                WHERE tenant_id=:t AND order_id=:o AND courier_id=:c AND access_revision=:rev
            """),{**params,"o":ref.order_id,"route":route_id,"pos":position})
            await commerce.audit(db,tenant,access,"order",ref.order_id,"driver_load_dispatched",
                {"route_batch_id":str(route_id),"route_position":position,
                 "load":[{"variant_id":str(i.variant_id),"quantity":i.quantity} for i in payload.load]})
            result.append({"id":ref.order_id,"status":"accepted","revision":transition["revision"],"route_position":position})
        return {"route_batch_id":route_id,"orders":result}
    # Remain able to drain previously reserved orders even if pilot is switched off.
    return await catalog.run_once(db,tenant,payload,"driver_batch_start",person["account_id"],write)
