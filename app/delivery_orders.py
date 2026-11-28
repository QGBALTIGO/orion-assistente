"""Manual sales with the shared commercial reservation and financial ledgers."""
from datetime import date, timedelta
import hashlib
import json
import re
import secrets
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, management_models as management
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey, QuoteLineRequest
from app import delivery_container_orders as packaging, delivery_logistics as logistics, delivery_payments as payments
from app import delivery_driver as driver
from app.delivery_catalog import LIMIT, MAX_QUANTITY, list_items, order_lines, run_once
from app.delivery_product_media import image_url
from app.management_schemas import MAX_MONEY, normalize_phone
from app.onboarding_schemas import Address
from app.onboarding_service import require_operational_ready


OrderMode = Literal["counter", "pickup", "delivery"]
OrderState = Literal["all", "confirmed", "dispatched", "fulfilled", "cancelled"]
OrderChannel = Literal["all", "pos", "phone", "attendant", "storefront", "manual", "whatsapp"]
CreditStatus = Literal["all", "pending", "overdue", "paid"]


from app import delivery_manual_pix as manual_pix


class QuickCustomer(ProfileModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    idempotency_key: IdempotencyKey

    _phone = field_validator("phone")(normalize_phone)


class OrderItems(ProfileModel):
    items: list[QuoteLineRequest] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def no_duplicates(self):
        if len({item.variant_id for item in self.items}) != len(self.items):
            raise ValueError("Agrupe a quantidade de cada variação em uma única linha")
        return self


class OrderEstimate(OrderItems):
    mode: OrderMode = "counter"
    delivery_fee_cents: StrictInt = Field(default=0, ge=0, le=MAX_MONEY)
    address: Address | None = None
    scheduled_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def delivery_fee_for_delivery(self):
        if self.mode != "delivery" and (self.delivery_fee_cents or self.address or self.scheduled_at):
            raise ValueError("A taxa, o endereço e o agendamento são usados em pedidos para entrega")
        return self


class ManualOrder(OrderEstimate):
    intake_channel: Literal["pos", "phone", "attendant"] | None = None
    tender_preference: Literal["cash", "pix", "debit_card", "credit_card"] | None = None
    change_for_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    operator_note: str | None = Field(default=None, min_length=1, max_length=300)
    customer_id: UUID | None = None
    container_plan: list[packaging.ContainerOrderItem] = Field(default_factory=list, max_length=30)
    estimate_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def receiving_requirements(self):
        if self.change_for_cents is not None and self.tender_preference != "cash":
            raise ValueError("Troco se aplica somente ao pagamento em dinheiro")
        if self.mode != "counter" and self.customer_id is None:
            raise ValueError("Selecione um cliente para a retirada ou entrega")
        if (self.mode == "delivery") != (self.address is not None):
            raise ValueError("Informe o endereço em pedidos com entrega")
        return self


class Transition(ProfileModel):
    action: Literal["dispatch", "offer", "withdraw_offer", "fulfill", "cancel", "settle", "return", "reschedule", "recipient_absent"]
    expected_revision: StrictInt = Field(ge=1)
    payment_method: Literal["cash", "debit_card", "credit_card", "pix", "on_account"] | None = None
    tenders: list[payments.Tender] = Field(default_factory=list, max_length=4)
    payment_due_date: date | None = None
    courier_name: str | None = Field(default=None, min_length=1, max_length=120)
    courier_id: UUID | None = None
    returned_to_stock: StrictBool = False
    refunded: StrictBool = False
    containers_confirmed: StrictBool = False
    scheduled_at: AwareDatetime | None = None
    note: str | None = Field(default=None, max_length=300)
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def payment_at_completion(self):
        if self.tenders:
            if self.action != "fulfill" or self.payment_method is not None or self.payment_due_date is not None:
                raise ValueError("Informe os valores por meio de pagamento apenas na conclusão à vista")
            if len(self.tenders) < 2 or len({t.method for t in self.tenders}) != len(self.tenders):
                raise ValueError("Escolha pelo menos duas formas de pagamento diferentes")
        if self.action not in {"fulfill","settle"} and self.payment_method is not None:
            raise ValueError("Registre o pagamento na conclusão do pedido")
        if self.action == "settle" and self.payment_method in {None,"on_account"}:
            raise ValueError("Informe a forma de pagamento recebida")
        if (self.payment_method == "on_account") != (self.payment_due_date is not None):
            raise ValueError("Informe o vencimento para pedidos a prazo")
        if self.payment_due_date is not None and self.payment_due_date.year > 2100:
            raise ValueError("Use vencimento até o ano de 2100")
        if (self.action in {"dispatch","offer"}) != bool(self.courier_name or self.courier_id):
            raise ValueError("Informe o entregador ao despachar")
        if self.action=="offer" and not self.courier_id:
            raise ValueError("Selecione um entregador cadastrado para solicitar aceite")
        if self.courier_id and self.courier_name:
            raise ValueError("Use o entregador cadastrado ou digite o nome")
        if self.containers_confirmed and self.action not in {"fulfill", "return"}:
            raise ValueError("Confira os vasilhames na conclusão ou devolução do pedido")
        if self.action not in {"cancel","return","reschedule","recipient_absent"} and self.returned_to_stock:
            raise ValueError("Confirme o retorno dos itens ao cancelar uma entrega")
        if ((self.scheduled_at is not None or self.note is not None) and self.action not in {"reschedule","recipient_absent"}
                or self.action == "reschedule" and self.scheduled_at is None):
            raise ValueError("Informe a data e a hora para reagendar a entrega")
        if self.action not in {"return","cancel"} and self.refunded:
            raise ValueError("Confirme o estorno junto com a devolução")
        return self


RECEIPT_STATUS = "CASE WHEN d.return_financial_entry_id IS NOT NULL THEN 'refunded' ELSE f.status END"
DELIVERY_DATE = "coalesce(d.scheduled_at,o.created_at)"
SALES_CHANNEL = "coalesce(d.intake_channel, CASE WHEN shop.order_id IS NOT NULL THEN 'storefront' ELSE 'manual' END)"
ORDERS = f"""
    SELECT o.id,o.quote_id,o.status,o.revision,o.created_at,d.mode,d.customer_id,
        CASE WHEN assignment.status='offered' AND assignment.expires_at<=now() THEN 'expired'
            ELSE assignment.status END AS assignment_status,
        assigned.name AS assignment_courier_name,
        {SALES_CHANNEL} AS sales_channel,
        coalesce(shop.payment_preference,d.tender_preference) AS tender_preference,
        CASE WHEN pix.confirmed_at IS NOT NULL THEN 'confirmed'
            WHEN pix.order_id IS NOT NULL AND o.status IN ('confirmed','dispatched') THEN 'pending' END AS pix_state,
        coalesce(shop.change_for_cents,d.change_for_cents) AS change_for_cents,
        d.financial_entry_id,q.customer_name,q.total_cents,d.delivery_fee_cents,d.courier_name,d.dispatched_at,
        d.payment_due_date,d.return_financial_entry_id,f.payment_method,{RECEIPT_STATUS} AS receipt_status,
        d.scheduled_at,d.courier_id,{payments.COLLECTED_CENTS} AS collected_cents,
        {payments.OUTSTANDING_CENTS} AS outstanding_cents,
        EXISTS (SELECT 1 FROM delivery_order_container_plans p
                WHERE p.tenant_id=o.tenant_id AND p.order_id=o.id) AS has_container_plan
    FROM commercial_orders o JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
    JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
    LEFT JOIN delivery_storefront_orders shop ON (shop.tenant_id,shop.order_id)=(o.tenant_id,o.id)
    LEFT JOIN delivery_driver_assignments assignment ON (assignment.tenant_id,assignment.order_id)=(o.tenant_id,o.id)
    LEFT JOIN delivery_couriers assigned ON (assigned.tenant_id,assigned.id)=(assignment.tenant_id,assignment.courier_id)
    LEFT JOIN delivery_pix_orders pix ON (pix.tenant_id,pix.order_id)=(o.tenant_id,o.id)
    LEFT JOIN financial_entries f ON (f.tenant_id,f.id)=(d.tenant_id,d.financial_entry_id)
    {payments.COLLECTION_TOTAL_JOIN}
    WHERE o.tenant_id=:t AND o.origin='delivery'
"""


async def list_orders(db, tenant, *, search, state, mode, start, end, offset, by_delivery_date=False,
                      channel="all", include_summary=False):
    lower, upper = None, None
    if start is not None or end is not None:
        _, lower, upper = await management.period_for(db, tenant, start, end)
    date_column = DELIVERY_DATE if by_delivery_date and mode == "delivery" else "o.created_at"
    scope = ORDERS + f"""
        AND (:state='all' OR o.status=:state)
        AND (:mode='all' OR d.mode=:mode)
        AND (:channel='all' OR ({SALES_CHANNEL})=:channel)
        AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',q.customer_name,cast(o.id as text))))>0)
        AND (CAST(:lower AS timestamptz) IS NULL OR {date_column}>=:lower)
        AND (CAST(:upper AS timestamptz) IS NULL OR {date_column}<:upper)
    """
    params = {"t":tenant,"state":state,"mode":mode,"channel":channel,"search":search.strip(),
              "lower":lower,"upper":upper,"limit":LIMIT+1,"offset":offset}
    if include_summary:
        # One statement, one snapshot, and totals over the entire filtered
        # set rather than the current page or the visible Kanban cards.
        result = (await db.execute(text("""
            WITH filtered AS (""" + scope + """),
            page AS (SELECT * FROM filtered ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset)
            SELECT count(*) AS orders,
                count(*) FILTER (WHERE sales_channel='whatsapp') AS whatsapp,
                count(*) FILTER (WHERE status='confirmed') AS confirmed,
                count(*) FILTER (WHERE status='dispatched') AS dispatched,
                count(*) FILTER (WHERE status='fulfilled') AS fulfilled,
                count(*) FILTER (WHERE status='cancelled') AS cancelled,
                coalesce((SELECT jsonb_agg(to_jsonb(page) ORDER BY created_at DESC,id DESC) FROM page),'[]') AS items
            FROM filtered
        """),params)).mappings().one()
        return {"items":result["items"][:LIMIT],"limit":LIMIT,"offset":offset,
                "has_more":len(result["items"])>LIMIT,
                "summary":{key:int(result[key]) for key in ("orders","whatsapp","confirmed","dispatched","fulfilled","cancelled")}}
    rows = (await db.execute(text(scope+"""
        ORDER BY o.created_at DESC,o.id DESC LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    return {"items":[dict(row) for row in rows[:LIMIT]],"limit":LIMIT,"offset":offset,"has_more":len(rows)>LIMIT}


async def list_credit(db, tenant, *, search, status, offset):
    period, _, _ = await management.period_for(db, tenant)
    today = period["end_date"]
    rows = (await db.execute(text(ORDERS + f"""
        AND d.payment_due_date IS NOT NULL
        AND (:status='all' OR ({RECEIPT_STATUS})=:status OR
            (:status='overdue' AND f.status='pending' AND d.payment_due_date<:today))
        AND (:search='' OR position(lower(:search) in lower(concat_ws(' ',q.customer_name,cast(o.id as text))))>0)
        ORDER BY d.payment_due_date ASC,o.id LIMIT :limit OFFSET :offset
    """), {"t":tenant,"status":status,"today":today,"search":search.strip(),"limit":LIMIT+1,"offset":offset})).mappings().all()
    sums = (await db.execute(text("""
        SELECT coalesce(sum(f.amount_cents),0) AS open_cents,
            coalesce(sum(f.amount_cents) FILTER (WHERE d.payment_due_date<:today),0) AS overdue_cents
        FROM delivery_order_details d JOIN financial_entries f
            ON (f.tenant_id,f.id)=(d.tenant_id,d.financial_entry_id)
        WHERE d.tenant_id=:t AND d.payment_due_date IS NOT NULL AND f.status='pending'
    """), {"t":tenant,"today":today})).mappings().one()
    return {"items":[dict(row) for row in rows[:LIMIT]],"limit":LIMIT,"offset":offset,"has_more":len(rows)>LIMIT,
            "summary":dict(sums),"today":today}


async def overview(db, tenant, day):
    timezone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t":tenant})).scalar_one()
    today = commerce.now_utc().astimezone(ZoneInfo(timezone)).date()
    selected = day or today
    first = selected - timedelta(days=6)
    _, start, end = management.report_period(first, selected, timezone)
    _, day_start, _ = management.report_period(selected, selected, timezone)
    metrics = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE o.created_at>=:start AND o.created_at<:end) AS orders,
            count(*) FILTER (WHERE o.created_at>=:start AND o.created_at<:end AND o.status='fulfilled') AS fulfilled,
            count(*) FILTER (WHERE d.mode='delivery' AND o.status IN ('confirmed','dispatched')
                AND {DELIVERY_DATE}>=:start AND {DELIVERY_DATE}<:end) AS open_deliveries,
            count(*) FILTER (WHERE d.mode='delivery' AND o.status='dispatched'
                AND {DELIVERY_DATE}>=:start AND {DELIVERY_DATE}<:end) AS in_route
        FROM commercial_orders o
        JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        WHERE o.tenant_id=:t AND o.origin='delivery'
            AND ((o.created_at>=:start AND o.created_at<:end)
              OR (d.mode='delivery' AND {DELIVERY_DATE}>=:start AND {DELIVERY_DATE}<:end))
    """), {"t":tenant,"start":day_start,"end":end})).mappings().one()
    series = (await db.execute(text(f"""
        WITH payment_links AS ({payments.RECEIPT_LINKS}), receipts AS (
            SELECT CAST(coalesce(f.received_at,f.occurred_at) AT TIME ZONE :zone AS date) AS day, sum(f.amount_cents) AS income_cents
            FROM financial_entries f JOIN payment_links d
                ON (d.tenant_id,d.financial_entry_id)=(f.tenant_id,f.id)
            WHERE f.tenant_id=:t AND f.status='paid'
                AND coalesce(f.received_at,f.occurred_at)>=:start AND coalesce(f.received_at,f.occurred_at)<:end
            GROUP BY 1
        ), sales AS (
            SELECT CAST(coalesce(done.created_at,f.occurred_at) AT TIME ZONE :zone AS date) AS day,
                sum(q.total_cents) AS sales_cents,
                count(*) AS completed_sales,
                sum(q.total_cents) FILTER (WHERE d.payment_due_date IS NOT NULL) AS credit_cents
            FROM delivery_order_details d
            JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            JOIN financial_entries f ON (f.tenant_id,f.id)=(d.tenant_id,d.financial_entry_id)
            LEFT JOIN LATERAL (
                SELECT e.created_at FROM commercial_events e WHERE e.tenant_id=o.tenant_id AND e.entity='order'
                    AND e.entity_id=o.id AND e.action='fulfilled' ORDER BY e.created_at LIMIT 1
            ) done ON true
            WHERE d.tenant_id=:t AND o.origin='delivery' AND o.status='fulfilled'
                AND f.kind='income' AND coalesce(done.created_at,f.occurred_at)>=:start AND coalesce(done.created_at,f.occurred_at)<:end
            GROUP BY 1
        )
        SELECT CAST(:first AS date) + n AS date, coalesce(receipts.income_cents,0) AS income_cents,
            coalesce(sales.sales_cents,0) AS sales_cents, coalesce(sales.credit_cents,0) AS credit_cents,
            coalesce(sales.completed_sales,0) AS completed_sales
        FROM generate_series(0,6) AS n
        LEFT JOIN receipts ON receipts.day=CAST(:first AS date) + n
        LEFT JOIN sales ON sales.day=CAST(:first AS date) + n
        ORDER BY n
    """), {"t":tenant,"zone":timezone,"start":start,"end":end,"first":first})).mappings().all()
    recent = await list_orders(db, tenant, search="", state="all", mode="all", start=selected, end=selected, offset=0)
    deliveries = await list_orders(db, tenant, search="", state="all", mode="delivery", start=selected,
                                   end=selected, offset=0, by_delivery_date=True)
    credit_customers = (await db.execute(text("""
        SELECT c.name, count(*) AS orders, sum(f.amount_cents) AS outstanding_cents,
            coalesce(sum(f.amount_cents) FILTER (WHERE d.payment_due_date<:today),0) AS overdue_cents
        FROM delivery_order_details d
        JOIN financial_entries f ON (f.tenant_id,f.id)=(d.tenant_id,d.financial_entry_id)
        JOIN customers c ON (c.tenant_id,c.id)=(d.tenant_id,d.customer_id)
        WHERE d.tenant_id=:t AND d.payment_due_date IS NOT NULL AND f.kind='income' AND f.status='pending'
            AND f.amount_cents>0
        GROUP BY c.id,c.name
        ORDER BY overdue_cents DESC,outstanding_cents DESC,c.id
        LIMIT 5
    """), {"t":tenant,"today":today})).mappings().all()
    low = await list_items(db,tenant,search="",category="",status_filter="active",low_stock=True,offset=0)
    last = series[-1]
    # Preserve the existing daily-series and metric contracts. Additional
    # operational KPIs are derived from the same selected date and SQL ledger.
    completed_sales = int(last["completed_sales"])
    return {"date":selected,"today":today,"metrics":{key:metrics[key] for key in ("orders","fulfilled","open_deliveries")} |
            {"paid_cents":last["income_cents"],"sales_cents":last["sales_cents"],"credit_cents":last["credit_cents"]},
            "operations":{"in_route":int(metrics["in_route"]),
                "awaiting_dispatch":int(metrics["open_deliveries"]-metrics["in_route"]),
                "average_ticket_cents":(int(last["sales_cents"])+completed_sales//2)//completed_sales if completed_sales else None},
            "daily":[{key:row[key] for key in ("date","income_cents","sales_cents","credit_cents")} for row in series],
            "recent":recent["items"][:5],
            "deliveries":deliveries["items"][:5],"credit_customers":[dict(row) for row in credit_customers],
            "low_stock":[{key:item[key] for key in ("name","variant_name","quantity_on_hand","reserved_quantity","unit")}
                         for item in low["items"][:5]]}


async def one_order(db, tenant, order_id, *, lock=False):
    row = (await db.execute(text(ORDERS + " AND o.id=:id" + (" FOR UPDATE OF o" if lock else "")),
                            {"t":tenant,"id":order_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Pedido não encontrado")
    return dict(row)


async def detail(db, tenant, order_id):
    from app.delivery_storefront import order_contact

    row = await one_order(db, tenant, order_id)
    quote = await commerce._quote(db, tenant, row["quote_id"])
    events = (await db.execute(text("""
        SELECT action,created_at,details FROM commercial_events
        WHERE tenant_id=:t AND entity='order' AND entity_id=:o ORDER BY created_at,id
    """), {"t":tenant,"o":order_id})).mappings().all()
    instructions = (await db.execute(text("""
        SELECT address,operator_note FROM delivery_order_details WHERE tenant_id=:t AND order_id=:o
    """), {"t":tenant,"o":order_id})).mappings().one()
    products = [UUID(item["product_id"]) for item in quote["items"]]
    media = (await db.execute(text("""
        SELECT product_id,preset_image,photo_url FROM delivery_product_profiles
        WHERE tenant_id=:t AND product_id=ANY(:ids)
    """), {"t": tenant, "ids": products})).mappings().all()
    images = {str(item["product_id"]): image_url(tenant, item["preset_image"], item["photo_url"]) for item in media}
    for item in quote["items"]:
        item["image_url"] = images.get(item["product_id"])
    return {**row,**instructions,"items":quote["items"],"history":[dict(event) for event in events],
            "container_plan": await packaging.get_plan(db, tenant, order_id),
            "storefront": await order_contact(db, tenant, order_id)}


async def lookup_customers(db, tenant, *, search):
    q = search.strip()
    if len(q) < 2:
        return {"items":[]}
    digits = re.sub(r"\D", "", q) if re.fullmatch(r"[+0-9 ().-]+", q) else ""
    rows = (await db.execute(text("""
        SELECT id,name,whatsapp_e164 FROM customers
        WHERE tenant_id=:t AND active AND deleted_at IS NULL
          AND (position(lower(:q) in lower(concat_ws(' ',name,whatsapp_e164)))>0
            OR (length(:digits)>1 AND position(:digits in coalesce(whatsapp_e164,''))>0))
        ORDER BY lower(name),id LIMIT 20
    """), {"t":tenant,"q":q,"digits":digits})).mappings().all()
    return {"items":[dict(row) for row in rows]}


async def create_customer(db, tenant, access, payload):
    async def write():
        await commerce.tenant_exists(db, tenant, active=True)
        db.info["management_actor_account_id"] = access.account_id
        row = await management.insert_row(db,tenant,"customers",{
            "name":payload.name,"whatsapp_e164":payload.phone,"active":True,
        },payload.idempotency_key,operation="delivery_customer")
        if not row["active"]:
            commerce.conflict("Este telefone já está vinculado a um cliente inativo. Confira no cadastro de clientes.")
        return {key:row[key] for key in ("id","name","whatsapp_e164")}
    return await run_once(db, tenant, payload, "customer_create", None, write)


async def previous_address(db, tenant, customer):
    address = (await db.execute(text("""
        SELECT d.address FROM delivery_order_details d
        JOIN commercial_orders o ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        JOIN customers c ON (d.tenant_id,d.customer_id)=(c.tenant_id,c.id)
        WHERE d.tenant_id=:t AND c.id=:c AND c.active AND c.deleted_at IS NULL
            AND d.mode='delivery' AND o.origin='delivery' AND o.status='fulfilled'
        ORDER BY o.created_at DESC,o.id DESC LIMIT 1
    """), {"t":tenant,"c":customer})).scalar_one_or_none()
    return {"address":address}


async def estimate(db, tenant, payload):
    lines = await order_lines(db, tenant, payload.items, payload.mode)
    now = commerce.now_utc()
    delivery = await delivery_terms(db, tenant, payload, now=now)
    return priced_order(lines, payload, now, delivery=delivery)

async def delivery_terms(db, tenant, payload, *, now, lock=False):
    if payload.mode != "delivery":
        return None
    return await logistics.quote_delivery(db, tenant, payload.address, payload.scheduled_at,
        requested_fee_cents=payload.delivery_fee_cents, lock=lock, now=now)

def priced_order(lines, payload, now, *, delivery=None):
    quote = commerce.calculate(lines, now)
    fee = delivery["delivery_fee_cents"] if delivery is not None else payload.delivery_fee_cents
    subtotal = quote["total_cents"]
    area = (delivery or {}).get("area") or {}
    if subtotal is not None:
        if subtotal < area.get("minimum_cents", 0):
            minimum = area["minimum_cents"]
            raise HTTPException(422, f"Pedido mínimo para este bairro: R$ {minimum // 100},{minimum % 100:02d} em produtos")
        if area.get("free_from_cents") and subtotal >= area["free_from_cents"]:
            fee = 0
        quote["total_cents"] += fee
    if quote["minimum_total_cents"] is not None:
        quote["minimum_total_cents"] += fee
    keys = ("variant_id","quantity","product_revision","variant_revision","inventory_revision")
    facts = [[item[key] for key in keys] for item in quote["items"]]
    signed = [payload.mode, fee, quote["total_cents"], facts]
    if delivery and (delivery["policy_revision"] or delivery["scheduled_at"]):
        signed.append(jsonable_encoder(delivery))
    version = hashlib.sha256(json.dumps(signed).encode()).hexdigest()
    return {**quote,"subtotal_cents":subtotal,"delivery_fee_cents":fee,"estimate_version":version,
            "eta_minutes":area.get("eta_minutes")}


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
        now = commerce.now_utc()
        delivery = await delivery_terms(db, tenant, payload, now=now, lock=True)
        lines = await order_lines(db, tenant, payload.items, payload.mode, lock=True)
        quote = priced_order(lines, payload, now, delivery=delivery)
        if not quote["confirmation_requirements_met"]:
            commerce.conflict("Confira o preço e o estoque de todos os itens antes de criar o pedido.")
        if quote["estimate_version"] != payload.estimate_version:
            commerce.conflict("Preço ou estoque mudou. Calcule o total novamente.")
        if payload.change_for_cents is not None and payload.change_for_cents < quote["total_cents"]:
            raise HTTPException(422, "O valor para troco deve cobrir o total do pedido.")
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
            INSERT INTO delivery_order_details
                (tenant_id,order_id,mode,customer_id,address,delivery_fee_cents,scheduled_at,logistics_snapshot,
                 intake_channel,tender_preference,change_for_cents,operator_note)
            VALUES (:t,:id,:mode,:c,CAST(:address AS jsonb),:fee,:scheduled,CAST(:logistics AS jsonb),
                    :channel,:tender,:change,:note)
        """), {"t":tenant,"id":order_id,"mode":payload.mode,"c":payload.customer_id,
                "address":payload.address.model_dump_json() if payload.address else None,
                "fee":quote["delivery_fee_cents"],"scheduled":delivery["scheduled_at"] if delivery else None,
                "channel":payload.intake_channel,"tender":payload.tender_preference,
                "change":payload.change_for_cents,"note":payload.operator_note,
                "logistics":json.dumps(jsonable_encoder({**delivery, "delivery_fee_cents":quote["delivery_fee_cents"]})) if delivery else None})
        if payload.tender_preference == "pix" and quote["total_cents"] > 0:
            await manual_pix.attach(db, tenant, order_id)
        await commerce.reserve_stock(db, tenant, access, order_id, lines)
        await packaging.save_plan(db, tenant, order_id, payload.customer_id, lines,
                                  packaging.required_exchanges(lines, payload.container_plan))
        await commerce.audit(db, tenant, access, "order", order_id, "confirmed", {"total_cents":quote["total_cents"],"mode":payload.mode})
        return {"id":order_id,"status":"confirmed","revision":1,"total_cents":quote["total_cents"]}

    return await run_once(db, tenant, payload, "order_create", None, write,
                          optional_defaults={"scheduled_at":None,"container_plan":[],"intake_channel":None,
                                             "tender_preference":None,"change_for_cents":None,"operator_note":None})


async def fulfill(db, tenant, access, row, items, method, due_date, tenders=()):
    await commerce.tenant_exists(db, tenant, active=True)
    verified = row["pix_state"] == "confirmed"
    if verified and (method or due_date or tenders):
        commerce.conflict("O Pix deste pedido já foi conferido pelo proprietário.")
    if row["pix_state"] == "pending":
        commerce.conflict("O proprietário precisa conferir o Pix do pedido antes da conclusão.")
    if method == "pix" or any(t.method == "pix" for t in tenders):
        manual_pix.owner(access)
    if tenders and sum(t.amount_cents for t in tenders) != row["total_cents"]:
        raise HTTPException(422, "A soma dos pagamentos deve corresponder ao total do pedido")
    if row["total_cents"] and not method and not tenders and not verified:
        raise HTTPException(422, "Informe a forma de pagamento recebida")
    if not row["total_cents"] and method:
        raise HTTPException(422, "Não há valor a receber neste pedido")
    if row["customer_id"] is None:
        await link_completed_guest(db, tenant, row)
    if due_date is not None:
        period, _, _ = await management.period_for(db, tenant)
        if not row["customer_id"]:
            raise HTTPException(422, "Venda a prazo exige um cliente vinculado ao pedido")
        if due_date < period["end_date"]:
            raise HTTPException(422, "O vencimento deve ser igual ou posterior à data de hoje")
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
    if verified:
        if row["customer_id"]:
            await db.execute(text("""UPDATE financial_entries SET customer_id=:customer
                WHERE tenant_id=:t AND id=:f AND customer_id IS NULL"""),
                {"customer":row["customer_id"], "t":tenant, "f":row["financial_entry_id"]})
        return {"id": row["financial_entry_id"]}
    if row["total_cents"]:
        return await payments.record_completion(db, tenant, row, method, due_date, tenders)
    return None


async def link_completed_guest(db, tenant, row):
    from app.customer_identity import resolve_customer
    from app.delivery_storefront import order_contact

    contact = await order_contact(db, tenant, row["id"])
    if not contact:
        return
    customer, _ = await resolve_customer(db, tenant, contact["contact_e164"], contact["customer_name"])
    if customer["active"] and customer["deleted_at"] is None:
        await db.execute(text("""
            UPDATE delivery_order_details SET customer_id=:customer WHERE tenant_id=:t AND order_id=:id
        """), {"customer": customer["id"], "t": tenant, "id": row["id"]})
        row["customer_id"] = customer["id"]


async def return_order(db, tenant, access, row, items, payload):
    if not payload.returned_to_stock:
        raise HTTPException(422, "Confirme que todos os itens voltaram ao estoque")
    for item in sorted(items,key=lambda item:item["variant_id"]):
        variant=UUID(item["variant_id"])
        amount=item["quantity"]
        after=(await db.execute(text("""
            UPDATE commercial_inventory SET quantity_on_hand=quantity_on_hand+:quantity,
                revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND variant_id=:v AND mode='tracked'
              AND quantity_on_hand<=:room
            RETURNING quantity_on_hand
        """), {"t":tenant,"v":variant,"quantity":amount,"room":MAX_QUANTITY-amount})).scalar_one_or_none()
        if after is None:
            commerce.conflict("Confira o saldo e o limite de estoque antes de registrar a devolução.")
        await commerce.audit(db,tenant,access,"stock",variant,"sale_returned",
            {"order_id":row["id"],"quantity":amount,"after":after,"movement":"sale_returned"})
    return await payments.return_payment(db, tenant, access, row, refunded=payload.refunded)


async def transition(db, tenant, access, order_id, payload):
    # Same lock ordering as the driver reservation and load transactions.
    await logistics.lock_policy(db,tenant,exclusive=True)
    async def write():
        row = await one_order(db, tenant, order_id, lock=True)
        permitted = {"fulfilled"} if payload.action in {"settle","return"} else {"confirmed","dispatched"}
        if row["status"] not in permitted or row["revision"] != payload.expected_revision:
            commerce.conflict("O pedido foi alterado ou encerrado. Confira o histórico.")
        receipt = None
        return_entry = None
        event_details = {}
        if payload.action == "settle":
            receipt = await payments.apply_collection(db,tenant,access,row,payload.payment_method)
            status = "fulfilled"
        elif payload.action in {"offer","withdraw_offer"}:
            if row["mode"]!="delivery" or row["status"]!="confirmed":
                commerce.conflict("O pedido precisa estar aguardando despacho")
            if payload.action=="withdraw_offer":
                await driver.release(db,tenant,order_id,require_offer=True)
            else:
                expires=await driver.offer(db,tenant,order_id,payload.courier_id)
                event_details={"courier_id":payload.courier_id,"expires_at":expires}
            status="confirmed"
        elif payload.action == "dispatch":
            if row["mode"] != "delivery" or row["status"] != "confirmed":
                commerce.conflict("O pedido precisa estar aguardando despacho")
            if row["assignment_status"] in {"offered","reserved"} and access.role!="courier":
                commerce.conflict("Retire a solicitação de aceite ou libere a reserva antes do despacho manual.")
            await commerce.tenant_exists(db, tenant, active=True)
            courier_name = payload.courier_name
            if payload.courier_id is not None:
                courier = await logistics.active_courier(db, tenant, payload.courier_id, lock=True)
                courier_name = courier["name"]
            await db.execute(text("""
                UPDATE delivery_order_details SET courier_name=:courier,courier_id=:courier_id,dispatched_at=now()
                WHERE tenant_id=:t AND order_id=:o
            """), {"t":tenant,"o":order_id,"courier":courier_name,"courier_id":payload.courier_id})
            status = "dispatched"
        elif payload.action in {"reschedule", "recipient_absent"}:
            event_details = await logistics.reschedule_delivery(db, tenant, row, payload.scheduled_at,
                returned_to_stock=payload.returned_to_stock,
                recipient_absent=payload.action == "recipient_absent", note=payload.note)
            status = "confirmed"
        else:
            quote = await commerce._quote(db, tenant, row["quote_id"])
            if payload.action == "return":
                return_entry = await return_order(db,tenant,access,row,quote["items"],payload)
                await packaging.apply(db, tenant, access, order_id, row["customer_id"], payload.containers_confirmed, reverse=True)
                status = "cancelled"
            elif payload.action == "cancel":
                if row["pix_state"] == "confirmed":
                    manual_pix.owner(access)
                    return_entry = await payments.return_payment(db,tenant,access,row,refunded=payload.refunded)
                elif payload.refunded:
                    raise HTTPException(422, "Não há um Pix conferido para estornar.")
                if row["status"] == "dispatched" and not payload.returned_to_stock:
                    raise HTTPException(422, "Confirme que os itens voltaram ao estoque")
                await commerce.release_stock(db, tenant, access, order_id, quote["items"])
                status = "cancelled"
            else:
                if row["mode"] == "delivery" and row["status"] != "dispatched":
                    commerce.conflict("Despache o pedido antes de registrar a entrega")
                receipt = await fulfill(db, tenant, access, row, quote["items"], payload.payment_method, payload.payment_due_date, payload.tenders)
                if payload.tenders:
                    event_details["tenders"] = [t.model_dump() for t in payload.tenders]
                await packaging.apply(db, tenant, access, order_id, row["customer_id"], payload.containers_confirmed)
                status = "fulfilled"
        if access.role!="courier" and row["assignment_status"] in {"offered","reserved","accepted","arrived","expired"} and payload.action in {"cancel","reschedule","recipient_absent","fulfill","dispatch"}:
            await driver.release(db,tenant,order_id)
        revision = (await db.execute(text("""
            UPDATE commercial_orders SET status=:status,revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND id=:o RETURNING revision
        """), {"status":status,"t":tenant,"o":order_id})).scalar_one()
        receipt_id=receipt["id"] if receipt else None
        action = {"settle":"received","return":"returned","offer":"driver_offered","withdraw_offer":"driver_offer_withdrawn","reschedule":"delivery_rescheduled",
                  "recipient_absent":"delivery_recipient_absent"}.get(payload.action,status)
        await commerce.audit(db, tenant, access, "order", order_id, action,
                             {"financial_entry_id":receipt_id,"payment_method":payload.payment_method,
                              "returned_to_stock":payload.returned_to_stock,"payment_due_date":payload.payment_due_date,
                              "return_financial_entry_id":return_entry, **event_details})
        return {"id":order_id,"status":status,"revision":revision,"financial_entry_id":receipt_id,
                "return_financial_entry_id":return_entry}

    return await run_once(db, tenant, payload, "order_transition", order_id, write,
                          optional_defaults={"courier_id":None,"containers_confirmed":False,"scheduled_at":None,"note":None,"tenders":[]})
