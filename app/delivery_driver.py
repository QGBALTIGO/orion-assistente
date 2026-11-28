"""Driver-specific operations for a verified Orion account, scoped to one courier."""
import re
from datetime import timedelta
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from typing import Literal

from fastapi import HTTPException
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator
from sqlalchemy import text

from app import commercial_service as commerce, delivery_catalog as catalog, delivery_logistics as logistics
from app import delivery_route_batches as route_batches
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.delivery_payments import ReceivedMethod
from app.management_models import audit
from app.tenant_access import TenantAccess


class Grant(ProfileModel):
    email: str | None = Field(default=None,max_length=254)
    expected_revision: StrictInt = Field(ge=0)

    @field_validator("email")
    @classmethod
    def valid_email(cls,value):
        if value is None: return None
        value=value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value):
            raise ValueError("Informe o e-mail verificado que o entregador usa na Orion")
        return value


class Shift(ProfileModel):
    state: Literal["available","unavailable","off"]


class Position(ProfileModel):
    sharing_key: UUID
    latitude: float = Field(ge=-90,le=90,allow_inf_nan=False)
    longitude: float = Field(ge=-180,le=180,allow_inf_nan=False)
    observed_at: AwareDatetime


async def start_position(db,tenant,person):
    if not person["shift_started_at"]: raise HTTPException(409,"Inicie o expediente antes de compartilhar a posição.")
    key=uuid4()
    await db.execute(text("""UPDATE delivery_driver_access SET sharing_key=:key,latitude=NULL,longitude=NULL,location_at=NULL
        WHERE tenant_id=:t AND courier_id=:c"""),{"t":tenant,"c":person["courier_id"],"key":key})
    return {"sharing_key":key}


async def set_position(db,tenant,person,payload):
    if not person["shift_started_at"]: raise HTTPException(409,"Inicie o expediente antes de compartilhar a posição.")
    now=commerce.now_utc()
    if payload.observed_at < max(person["shift_started_at"],now-timedelta(minutes=2)) or payload.observed_at>now+timedelta(seconds=10):
        raise HTTPException(422,"A posição precisa ter sido observada recentemente, durante o expediente.")
    updated=await db.scalar(text("""UPDATE delivery_driver_access SET latitude=:lat,longitude=:lon,location_at=:stamp
        WHERE tenant_id=:t AND courier_id=:c AND sharing_key=:key AND (location_at IS NULL OR location_at<=:stamp) RETURNING true"""),
        {"t":tenant,"c":person["courier_id"],"lat":payload.latitude,"lon":payload.longitude,"stamp":payload.observed_at,"key":payload.sharing_key})
    if not updated: raise HTTPException(409,"O compartilhamento encerrou ou já existe uma posição mais recente.")
    return {"ok":True}


async def clear_position(db,tenant,person):
    await db.execute(text("""UPDATE delivery_driver_access SET latitude=NULL,longitude=NULL,location_at=NULL,sharing_key=NULL
        WHERE tenant_id=:t AND courier_id=:c"""),{"t":tenant,"c":person["courier_id"]})
    return {"ok":True}


async def positions(db,tenant):
    # This is an operational snapshot, not a GPS provider or a distance estimate.
    # Include only orders and locations belonging to this company.
    queue = """
        SELECT o.id,o.status,d.courier_id,d.scheduled_at,
            CASE WHEN a.status='offered' AND a.expires_at>now() AND o.status='confirmed'
                THEN a.courier_id END AS offered_to,
            CASE WHEN a.status='reserved' AND o.status='confirmed' THEN a.courier_id END AS reserved_to
        FROM commercial_orders o
        JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        LEFT JOIN delivery_driver_assignments a ON (a.tenant_id,a.order_id)=(o.tenant_id,o.id)
        WHERE o.tenant_id=:t AND o.origin='delivery' AND d.mode='delivery'
            AND o.status IN ('confirmed','dispatched')
    """
    counts=(await db.execute(text(f"""
        WITH queue AS ({queue})
        SELECT count(*) FILTER (WHERE status='confirmed' AND offered_to IS NULL AND reserved_to IS NULL
                    AND (scheduled_at IS NULL OR scheduled_at<=now())) AS waiting_dispatch,
            count(*) FILTER (WHERE offered_to IS NOT NULL) AS waiting_acceptance,
            count(*) FILTER (WHERE reserved_to IS NOT NULL) AS waiting_load,
            count(*) FILTER (WHERE status='dispatched') AS on_route,
            count(*) FILTER (WHERE status='confirmed' AND scheduled_at>now()) AS scheduled,
            count(*) FILTER (WHERE scheduled_at<now()) AS scheduled_past_due,
            count(*) FILTER (WHERE status='dispatched' AND courier_id IS NULL) AS unlinked_on_route,
            now() AS observed_at
        FROM queue
    """),{"t":tenant})).mappings().one()
    rows=(await db.execute(text(f"""
        WITH queue AS ({queue}), workloads AS (
            SELECT courier_id,count(*) AS on_route FROM queue
            WHERE status='dispatched' AND courier_id IS NOT NULL GROUP BY courier_id
        ), offers AS (
            SELECT offered_to AS courier_id,count(*) AS waiting_acceptance FROM queue
            WHERE offered_to IS NOT NULL GROUP BY offered_to
        )
        SELECT c.name,a.courier_id,a.available,a.shift_started_at,a.latitude,a.longitude,a.location_at,
            a.location_at<now()-interval '3 minutes' AS stale,
            coalesce(w.on_route,0) AS on_route,coalesce(f.waiting_acceptance,0) AS waiting_acceptance
        FROM delivery_driver_access a JOIN delivery_couriers c
            ON (c.tenant_id,c.id)=(a.tenant_id,a.courier_id)
        LEFT JOIN workloads w ON w.courier_id=a.courier_id
        LEFT JOIN offers f ON f.courier_id=a.courier_id
        WHERE a.tenant_id=:t AND c.active AND a.shift_started_at IS NOT NULL
        ORDER BY a.available DESC,coalesce(w.on_route,0)+coalesce(f.waiting_acceptance,0),lower(c.name),c.id
    """),{"t":tenant})).mappings().all()
    return {"items":[dict(r) for r in rows],"summary":{**dict(counts),
        "in_shift":len(rows),"available":sum(bool(r["available"]) for r in rows),
        "fresh_positions":sum(r["location_at"] is not None and r["stale"] is False for r in rows)}}


class Action(ProfileModel):
    action: Literal["accept","refuse","arrive","complete","absent"]
    expected_revision: StrictInt = Field(ge=1)
    payment_method: ReceivedMethod | None = None
    received: StrictBool = False
    containers_confirmed: StrictBool = False
    returned_to_stock: StrictBool = False
    idempotency_key: IdempotencyKey


async def grant_state(db,tenant,courier):
    await logistics.active_courier(db,tenant,courier)
    row=(await db.execute(text("""SELECT email,revision,shift_started_at,available FROM delivery_driver_access
        WHERE tenant_id=:t AND courier_id=:c"""),{"t":tenant,"c":courier})).mappings().first()
    return {**(dict(row) if row else {"email":None,"revision":0,"shift_started_at":None,"available":False}),
            "portal_path":f"/entregador/{tenant}"}


async def set_grant(db,tenant,access,courier,payload):
    await logistics.lock_policy(db,tenant,exclusive=True)
    await logistics.active_courier(db,tenant,courier,lock=True)
    await route_batches.release_reservations(db,tenant,courier)
    args={"t":tenant,"c":courier,"email":payload.email,"actor":access.account_id,"rev":payload.expected_revision}
    if payload.expected_revision==0:
        revision=await db.scalar(text("""INSERT INTO delivery_driver_access(tenant_id,courier_id,email,updated_by)
            VALUES (:t,:c,:email,:actor) ON CONFLICT(tenant_id,courier_id) DO NOTHING RETURNING revision"""),args)
    else:
        revision=await db.scalar(text("""UPDATE delivery_driver_access SET email=:email,account_id=NULL,updated_by=:actor,
            revision=revision+1,shift_started_at=NULL,available=false,latitude=NULL,longitude=NULL,location_at=NULL,sharing_key=NULL
            WHERE tenant_id=:t AND courier_id=:c AND revision=:rev RETURNING revision"""),args)
    if revision is None: raise HTTPException(409,"O vínculo mudou. Abra o cadastro novamente.")
    await audit(db,tenant,"driver_access_changed","delivery_courier",courier)
    return await grant_state(db,tenant,courier)


async def identity(db,tenant,claims):
    if claims.get("role")!="member" or claims.get("maintenance") or claims.get("email_verified") is not True:
        raise HTTPException(403,"Use a conta pessoal com e-mail verificado liberada para as entregas.")
    from app.workspace_policy import delivery_preview_enabled
    await commerce.tenant_exists(db,tenant,active=True)
    niche=await db.scalar(text("SELECT configuration->>'niche' FROM business_onboarding WHERE tenant_id=:t"),{"t":tenant})
    if not delivery_preview_enabled(tenant,niche): raise HTTPException(403,"Área do entregador indisponível.")
    account=UUID(str(claims["account_id"]))
    row=(await db.execute(text("""SELECT a.courier_id,a.account_id,a.shift_started_at,a.available,a.revision,c.name
        FROM delivery_driver_access a JOIN delivery_couriers c ON (c.tenant_id,c.id)=(a.tenant_id,a.courier_id)
        WHERE a.tenant_id=:t AND c.active AND a.email=:email AND (a.account_id IS NULL OR a.account_id=:account)
        FOR UPDATE OF a FOR SHARE OF c"""),{"t":tenant,"email":str(claims.get("email") or "").strip().lower(),"account":account})).mappings().first()
    if row is None: raise HTTPException(403,"Sua conta não tem acesso às entregas desta empresa.")
    return {**row,"account_id":account}


async def shift(db,tenant,person,payload):
    active=payload.state!="off"
    if not active and (route_batches.enabled(tenant) or await db.scalar(text("SELECT 1 FROM delivery_driver_assignments WHERE tenant_id=:t AND courier_id=:c AND status='reserved' LIMIT 1"),{"t":tenant,"c":person['courier_id']})):
        if await route_batches.active_count(db,tenant,person):
            raise HTTPException(409,"Conclua ou libere os pedidos antes de encerrar o expediente.")
    await db.execute(text("""UPDATE delivery_driver_access SET account_id=:account,
        shift_started_at=CASE WHEN :active THEN coalesce(shift_started_at,now()) ELSE NULL END,available=:available,
        latitude=CASE WHEN :active THEN latitude END,longitude=CASE WHEN :active THEN longitude END,
        location_at=CASE WHEN :active THEN location_at END,sharing_key=CASE WHEN :active THEN sharing_key END
        WHERE tenant_id=:t AND courier_id=:c"""),{"t":tenant,"c":person["courier_id"],"account":person["account_id"],"active":active,"available":payload.state=="available"})
    await audit(db,tenant,"driver_shift_"+payload.state,"delivery_courier",person["courier_id"])
    return {"state":payload.state}


async def offer(db,tenant,order,courier):
    ready=await db.scalar(text("""SELECT a.revision FROM delivery_driver_access a JOIN delivery_couriers c
        ON (c.tenant_id,c.id)=(a.tenant_id,a.courier_id) WHERE a.tenant_id=:t AND a.courier_id=:c
        AND c.active AND a.available AND a.shift_started_at IS NOT NULL AND a.account_id IS NOT NULL FOR SHARE OF a,c"""),
        {"t":tenant,"c":courier})
    if not ready: raise HTTPException(409,"O entregador precisa estar em expediente e disponível no aplicativo.")
    expires=await db.scalar(text("""INSERT INTO delivery_driver_assignments(tenant_id,order_id,courier_id,access_revision,status,expires_at)
        VALUES (:t,:o,:c,:rev,'offered',now()+interval '5 minutes')
        ON CONFLICT(tenant_id,order_id) DO UPDATE SET courier_id=excluded.courier_id,status='offered',
            access_revision=excluded.access_revision,offered_at=now(),updated_at=now(),expires_at=excluded.expires_at,
            route_batch_id=NULL,route_position=NULL
        WHERE delivery_driver_assignments.status IN ('refused','released') OR
            (delivery_driver_assignments.status='offered' AND delivery_driver_assignments.expires_at<=now())
        RETURNING expires_at"""),{"t":tenant,"o":order,"c":courier,"rev":ready})
    if expires is None: raise HTTPException(409,"Já existe uma atribuição ativa para este pedido.")
    return expires


async def release(db,tenant,order,*,require_offer=False):
    status=await db.scalar(text("""UPDATE delivery_driver_assignments SET status='released',updated_at=now()
        WHERE tenant_id=:t AND order_id=:o AND status IN ('offered','reserved','accepted','arrived') RETURNING status"""),{"t":tenant,"o":order})
    if require_offer and not status: raise HTTPException(409,"Não há solicitação de aceite para retirar.")


async def dashboard(db,tenant,person):
    rows=[]
    if person['shift_started_at']:
        rows=(await db.execute(text("""SELECT a.order_id AS id,a.status,a.expires_at,a.route_batch_id,a.route_position,o.revision,q.total_cents,
            (SELECT CASE WHEN p.confirmed_at IS NULL THEN 'pending' ELSE 'confirmed' END
             FROM delivery_pix_orders p WHERE (p.tenant_id,p.order_id)=(a.tenant_id,a.order_id)) AS pix_state,
            CASE WHEN a.status!='offered' THEN q.customer_name END AS customer_name,
            CASE WHEN a.status='offered' THEN jsonb_build_object('neighborhood',d.address->>'neighborhood')
                ELSE d.address END AS address,
            CASE WHEN a.status!='offered' THEN coalesce(q.contact_e164,c.whatsapp_e164) END AS phone,
            coalesce(s.payment_preference,d.tender_preference) AS payment_preference,
            coalesce(s.change_for_cents,d.change_for_cents) AS change_for_cents,
            EXISTS(SELECT 1 FROM delivery_order_container_plans cp WHERE cp.tenant_id=a.tenant_id AND cp.order_id=a.order_id) AS containers,
            (SELECT jsonb_agg(jsonb_build_object('name',v.name,'exchange',cp.exchange_quantity,'loan',cp.loan_quantity)
                ORDER BY v.name,cp.variant_id) FROM delivery_order_container_plans cp JOIN delivery_containers v
                ON (v.tenant_id,v.id)=(cp.tenant_id,cp.container_id) WHERE cp.tenant_id=a.tenant_id AND cp.order_id=a.order_id) AS container_items,
            (SELECT jsonb_agg(jsonb_build_object('variant_id',i.variant_id,'name',i.snapshot->>'product_name','variant',i.snapshot->>'variant_name','quantity',i.quantity)
                ORDER BY i.item_order) FROM commercial_quote_items i WHERE i.tenant_id=o.tenant_id AND i.quote_id=o.quote_id) AS items
            FROM delivery_driver_assignments a JOIN commercial_orders o ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
            LEFT JOIN delivery_storefront_orders s ON (s.tenant_id,s.order_id)=(o.tenant_id,o.id)
            LEFT JOIN customers c ON (c.tenant_id,c.id)=(d.tenant_id,d.customer_id) AND c.deleted_at IS NULL
            WHERE a.tenant_id=:t AND a.courier_id=:c AND a.access_revision=:rev AND ((a.status='offered' AND a.expires_at>now() AND o.status='confirmed')
                OR (a.status='reserved' AND o.status='confirmed')
                OR (a.status IN ('accepted','arrived') AND o.status='dispatched'))
            ORDER BY a.route_position NULLS LAST,a.offered_at,a.order_id"""),{"t":tenant,"c":person["courier_id"],"rev":person["revision"]})).mappings().all()
    return {"name":person["name"],"state":"off" if not person["shift_started_at"] else "available" if person["available"] else "unavailable",
            "shift_started_at":person["shift_started_at"],"orders":[dict(r) for r in rows],
            "route_v2":route_batches.enabled(tenant) or any(r['status']=='reserved' for r in rows),
            "available_orders":await route_batches.available(db,tenant,person),"max_stops":route_batches.MAX_STOPS}


async def order_action(db,tenant,person,order,payload):
    from app import delivery_orders as orders
    access=TenantAccess("courier",person["account_id"])
    async def write():
        if not person["shift_started_at"]: raise HTTPException(409,"Inicie o expediente.")
        item=(await db.execute(text("""SELECT status,expires_at>now() AS fresh FROM delivery_driver_assignments
            WHERE tenant_id=:t AND order_id=:o AND courier_id=:c AND access_revision=:rev"""),{"t":tenant,"o":order,"c":person["courier_id"],"rev":person["revision"]})).mappings().first()
        if item is None: raise HTTPException(404,"Entrega não encontrada.")
        row=await orders.one_order(db,tenant,order,lock=True)
        if row["revision"]!=payload.expected_revision: raise HTTPException(409,"A entrega mudou. Confira a fila.")
        action=payload.action
        if action in {"accept","refuse"}:
            if item["status"]!="offered" or not item["fresh"] or row["status"]!="confirmed":
                raise HTTPException(409,"Esta solicitação de aceite já encerrou.")
            if action=="accept" and not person["available"]: raise HTTPException(409,"Marque-se como disponível para aceitar.")
            if action=="accept" and route_batches.enabled(tenant):
                result=await route_batches.reserve_locked(db,tenant,person,route_batches.OrderRef(order_id=order,expected_revision=payload.expected_revision),offered=True)
                return {"status":result["status"],"revision":result["revision"]}
            status="accepted" if action=="accept" else "refused"
        else:
            if item["status"] not in {"accepted","arrived"} or row["status"]!="dispatched" or row["courier_id"]!=person["courier_id"]:
                raise HTTPException(409,"Esta entrega não está em sua rota.")
            status={"arrive":"arrived","complete":"fulfilled","absent":"released"}[action]
            if action=="arrive" and item["status"]=="arrived": raise HTTPException(409,"A chegada já foi registrada.")
        if action in {"accept","complete","absent"}:
            fields={"expected_revision":row["revision"],"idempotency_key":str(uuid5(NAMESPACE_URL,f"orion:driver:{tenant}:{order}:{person['courier_id']}:{payload.idempotency_key}"))}
            if action=="accept":
                fields.update(action="dispatch",courier_id=person["courier_id"])
            elif action=="complete":
                if row["pix_state"] == "pending":
                    raise HTTPException(409, "Aguardando conferência do Pix pelo proprietário.")
                if row["total_cents"] and row["pix_state"] != "confirmed" and not (payload.received and payload.payment_method):
                    raise HTTPException(422,"Confirme o recebimento e a forma de pagamento.")
                fields.update(action="fulfill",payment_method=payload.payment_method,containers_confirmed=payload.containers_confirmed)
            else:
                fields.update(action="recipient_absent",returned_to_stock=payload.returned_to_stock)
            result=await orders.transition(db,tenant,access,order,orders.Transition(**fields))
            revision=result["revision"]
        else:
            revision=await db.scalar(text("""UPDATE commercial_orders SET revision=revision+1,updated_at=now()
                WHERE tenant_id=:t AND id=:o RETURNING revision"""),{"t":tenant,"o":order})
        await db.execute(text("""UPDATE delivery_driver_assignments SET status=:s,updated_at=now()
            WHERE tenant_id=:t AND order_id=:o"""),{"t":tenant,"o":order,"s":status})
        await commerce.audit(db,tenant,access,"order",order,"driver_"+action,{"courier_id":person["courier_id"]})
        return {"status":status,"revision":revision}
    return await catalog.run_once(db,tenant,payload,"driver_"+str(person["account_id"]),order,write)
