"""Read Delivery reports from orders, receipts and the inventory ledgers."""
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app.csv_export import csv_document
from app.delivery_catalog import ITEMS_FROM, LIMIT
from app.delivery_containers import LOAN_DELTA
from app.delivery_orders import ORDERS
from app.delivery_product_media import image_url
from app.delivery_reports import MAX_EXPORT_ROWS, amount_label
from app.management_models import period_for, report_period


ReportKind = Literal["products", "customers", "inventory", "movements", "credit", "containers", "activity"]
OrderMode = Literal["all", "counter", "pickup", "delivery"]

PERIOD_ORDERS = ORDERS + """
    AND o.status='fulfilled' AND o.created_at>=:lower AND o.created_at<:upper
    AND (:mode='all' OR d.mode=:mode)
"""
PRODUCTS = f"""
    WITH sales AS ({PERIOD_ORDERS})
    SELECT CAST(l.variant_id AS text) AS row_id,p.name AS product,v.name AS variant,m.preset_image,m.photo_url,
        v.sku,count(*) AS orders,sum(l.quantity) AS quantity,
        CAST(sum(CAST(l.snapshot->>'total_cents' AS bigint)) AS bigint) AS revenue_cents
    FROM sales
    JOIN commercial_quote_items l ON l.tenant_id=:t AND l.quote_id=sales.quote_id
    JOIN commercial_variants v ON (v.tenant_id,v.id)=(l.tenant_id,l.variant_id)
    JOIN commercial_products p ON (p.tenant_id,p.id)=(l.tenant_id,l.product_id)
    LEFT JOIN delivery_product_profiles m ON (m.tenant_id,m.product_id)=(p.tenant_id,p.id)
    WHERE position(lower(:search) in lower(concat_ws(' ',p.name,v.name,v.sku)))>0
    GROUP BY l.variant_id,p.name,v.name,v.sku,m.preset_image,m.photo_url
"""
CUSTOMERS = f"""
    WITH sales AS ({PERIOD_ORDERS}), customer_sales AS (
      SELECT sales.id,sales.customer_id,sales.total_cents,sales.receipt_status,sales.created_at,
          CASE WHEN sales.customer_id IS NULL THEN 'Vendas sem cliente vinculado'
               ELSE coalesce(c.name,'Cliente removido') END AS customer
      FROM sales LEFT JOIN customers c ON c.tenant_id=:t AND c.id=sales.customer_id AND c.deleted_at IS NULL
    )
    SELECT coalesce(CAST(customer_id AS text),'avulsa') AS row_id,customer,count(*) AS orders,
        CAST(sum(total_cents) AS bigint) AS revenue_cents,max(created_at) AS last_order
    FROM customer_sales
    WHERE position(lower(:search) in lower(customer))>0
    GROUP BY customer_id,customer
"""
INVENTORY = f"""
    WITH inventory AS (
      SELECT cast(v.id AS text) AS row_id,p.name AS product,v.name AS variant,v.sku,d.category,m.preset_image,m.photo_url,
          coalesce(d.unit,'un') AS unit,(p.active AND v.active) AS active,
          i.quantity_on_hand,i.reserved_quantity,
          CASE WHEN i.mode='tracked' AND i.observed_at<=now() AND i.valid_until>now()
            THEN i.quantity_on_hand-i.reserved_quantity END AS available,
          coalesce(d.minimum_quantity,0) AS minimum_quantity,
          CASE WHEN i.mode<>'tracked' THEN 'unknown'
               WHEN i.observed_at>now() OR i.valid_until<=now() THEN 'stale'
               WHEN p.active AND v.active
                 AND i.quantity_on_hand-i.reserved_quantity<=coalesce(d.minimum_quantity,0) THEN 'low'
               ELSE 'tracked' END AS stock_status
      {ITEMS_FROM}
      WHERE v.tenant_id=:t
        AND position(lower(:search) in lower(concat_ws(' ',p.name,v.name,v.sku,d.category,d.barcode)))>0
    )
    SELECT * FROM inventory WHERE :status='all' OR stock_status=:status
        OR (:status='inactive' AND NOT active)
"""
MOVEMENTS = """
    WITH ledger AS (
      SELECT CAST(e.id AS text) AS row_id,e.created_at,p.name AS product,v.name AS variant,v.sku,
          m.preset_image,m.photo_url,
          coalesce(e.details->>'movement',e.action) AS movement,
          CASE WHEN jsonb_typeof(e.details->'quantity')='number'
            THEN CAST(e.details->>'quantity' AS bigint) END AS quantity,
          CASE WHEN jsonb_typeof(e.details->'after')='number'
            THEN CAST(e.details->>'after' AS bigint) END AS balance,
          e.details->>'note' AS note
      FROM commercial_events e
      JOIN commercial_variants v ON (v.tenant_id,v.id)=(e.tenant_id,e.entity_id)
      JOIN commercial_products p ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id)
      LEFT JOIN delivery_product_profiles m ON (m.tenant_id,m.product_id)=(p.tenant_id,p.id)
      WHERE e.tenant_id=:t AND e.entity='stock'
        AND (e.details ? 'movement' OR e.action IN ('reserved','released'))
        AND e.created_at>=:lower AND e.created_at<:upper
        AND position(lower(:search) in lower(concat_ws(' ',p.name,v.name,v.sku)))>0
    )
    SELECT * FROM ledger WHERE :status='all' OR movement=:status
"""
CREDIT = f"""
    WITH sales AS ({ORDERS} AND d.payment_due_date BETWEEN :start_date AND :end_date), receivables AS (
      SELECT CAST(sales.id AS text) AS row_id,
          upper(left(CAST(sales.id AS text),8)) AS order_code,
          CASE WHEN sales.customer_id IS NULL THEN 'Cliente removido'
               ELSE coalesce(c.name,'Cliente removido') END AS customer,
          sales.payment_due_date AS due_date,sales.total_cents AS original_cents,
          CASE WHEN sales.receipt_status='pending' THEN f.amount_cents ELSE 0 END AS pending_cents,
          CASE WHEN sales.receipt_status='paid' THEN sales.total_cents
               WHEN sales.receipt_status='pending' THEN greatest(sales.total_cents-f.amount_cents,0)
               ELSE 0 END AS received_cents,
          CASE WHEN sales.receipt_status='pending' AND sales.payment_due_date<:today
               THEN 'overdue' ELSE sales.receipt_status END AS credit_status
      FROM sales JOIN financial_entries f ON f.tenant_id=:t AND f.id=sales.financial_entry_id
      LEFT JOIN customers c ON c.tenant_id=:t AND c.id=sales.customer_id AND c.deleted_at IS NULL
    )
    SELECT * FROM receivables
    WHERE (:status='all' OR credit_status=:status OR (:status='pending' AND credit_status='overdue'))
        AND position(lower(:search) in lower(concat_ws(' ',customer,row_id)))>0
"""
CONTAINERS = f"""
    WITH loans AS (
      SELECT container_id,sum({LOAN_DELTA}) AS quantity
      FROM delivery_container_movements
      WHERE tenant_id=:t AND customer_id IS NOT NULL
      GROUP BY container_id
    )
    SELECT CAST(c.id AS text) AS row_id,c.name AS container,c.kind AS container_kind,
        c.empty_quantity,coalesce(l.quantity,0) AS loaned
    FROM delivery_containers c LEFT JOIN loans l ON l.container_id=c.id
    WHERE c.tenant_id=:t AND (:status='all' OR c.kind=:status)
        AND position(lower(:search) in lower(c.name))>0
"""
ACTIVITY = """
    WITH events AS (
      SELECT CAST(e.id AS text) AS row_id,e.created_at,'orders' AS module,
          upper(left(CAST(o.id AS text),8)) AS reference,e.action,e.actor_role
      FROM commercial_events e
      JOIN commercial_orders o ON (o.tenant_id,o.id)=(e.tenant_id,e.entity_id) AND o.origin='delivery'
      WHERE e.tenant_id=:t AND e.entity='order' AND e.created_at>=:lower AND e.created_at<:upper
      UNION ALL
      SELECT CAST(e.id AS text),e.created_at,CASE WHEN e.entity='stock' THEN 'stock' ELSE 'catalog' END,
          concat_ws(' · ',p.name,v.name),coalesce(e.details->>'movement',e.action),e.actor_role
      FROM commercial_events e
      LEFT JOIN commercial_variants v ON (v.tenant_id,v.id)=(e.tenant_id,e.entity_id) AND e.entity IN ('variant','stock')
      JOIN commercial_products p ON p.tenant_id=e.tenant_id AND p.id=coalesce(v.product_id,e.entity_id)
      WHERE e.tenant_id=:t AND e.entity IN ('product','variant','stock') AND e.created_at>=:lower AND e.created_at<:upper
      UNION ALL
      SELECT CAST(m.id AS text),m.occurred_at,'packaging',c.name,m.kind,'staff'
      FROM delivery_container_movements m
      JOIN delivery_containers c ON (c.tenant_id,c.id)=(m.tenant_id,m.container_id)
      WHERE m.tenant_id=:t AND m.occurred_at>=:lower AND m.occurred_at<:upper
    )
    SELECT * FROM events WHERE (:status='all' OR module=:status)
      AND position(lower(:search) in lower(concat_ws(' ',reference,action)))>0
"""

MOVEMENT_LABELS = {
    "initial": "Saldo inicial", "purchase": "Entrada", "count": "Contagem", "out": "Saída",
    "reserved": "Reserva", "released": "Liberação", "sold": "Venda",
    "sale_returned": "Devolução de pedido",
}
CREDIT_LABELS = {
    "pending": "A receber", "overdue": "Em atraso", "paid": "Recebido",
    "refunded": "Estornado", "cancelled": "Cancelado",
}
CELL_LABELS = {
    "movement": MOVEMENT_LABELS,
    "credit_status": CREDIT_LABELS,
    "stock_status": {"tracked": "Atualizado", "low": "Estoque baixo", "stale": "Contagem vencida", "unknown": "Sem contagem"},
    "container_kind": {"cylinder": "Botijão", "jug": "Garrafão"},
    "active": {True: "Ativo", False: "Inativo"},
    "module": {"orders":"Pedidos","catalog":"Catálogo","stock":"Estoque","packaging":"Vasilhames"},
    "action": {**MOVEMENT_LABELS, "created":"Cadastro criado", "updated":"Cadastro atualizado",
        "confirmed":"Pedido reservado", "dispatched":"Pedido em rota", "fulfilled":"Pedido concluído",
        "cancelled":"Pedido cancelado", "returned":"Pedido devolvido", "received":"Recebimento",
        "partially_received":"Recebimento parcial", "delivery_rescheduled":"Entrega reagendada",
        "delivery_recipient_absent":"Cliente ausente", "observed":"Contagem",
        "in":"Entrada", "lend":"Comodato", "return":"Devolução de vasilhame"},
    "actor_role": {"owner":"Proprietário","editor":"Equipe","staff":"Equipe","customer":"Loja rápida"},
}


@dataclass(frozen=True)
class Report:
    query: str
    order: str
    columns: tuple[tuple[str, str, str], ...]
    metrics: tuple[tuple[str, str, str, str, str], ...]
    statuses: frozenset[str] = frozenset()
    dated: bool = True


PRODUCT_COLUMNS = (("product", "Produto", "text"), ("variant", "Variação", "text"), ("sku", "SKU", "text"))
REPORTS = {
    "products": Report(PRODUCTS, "quantity DESC, product, variant, row_id", PRODUCT_COLUMNS + (
        ("orders","Pedidos","number"),("quantity","Unidades vendidas","number"),("revenue_cents","Subtotal dos itens","money"),
    ), (
        ("variants","Variações vendidas","number","box","count(*)"),
        ("quantity","Unidades vendidas","number","list","sum(quantity)"),
        ("revenue_cents","Subtotal dos itens","money","wallet","sum(revenue_cents)"),
    )),
    "customers": Report(CUSTOMERS, "orders DESC, customer, row_id", (
        ("customer","Cliente","text"),("orders","Pedidos","number"),("last_order","Último pedido","datetime"),
        ("revenue_cents","Valor dos pedidos","money"),
    ), (
        ("customers","Clientes vinculados","number","users","count(*) FILTER (WHERE row_id<>'avulsa')"),
        ("orders","Pedidos concluídos","number","list","sum(orders)"),
        ("revenue_cents","Valor dos pedidos","money","wallet","sum(revenue_cents)"),
    )),
    "inventory": Report(INVENTORY, "product, variant, row_id", PRODUCT_COLUMNS + (
        ("category","Categoria","text"),("unit","Unidade","text"),("quantity_on_hand","Saldo contado","number"),
        ("reserved_quantity","Reservado","number"),("available","Disponível","number"),
        ("minimum_quantity","Estoque mínimo","number"),("stock_status","Situação da contagem","text"),
        ("active","Cadastro","text"),
    ), (
        ("variants","Variações","number","box","count(*)"),
        ("low","Estoque baixo","number","info","count(*) FILTER (WHERE stock_status='low')"),
        ("needs_count","Precisam de contagem","number","clock","count(*) FILTER (WHERE stock_status IN ('stale','unknown'))"),
    ), frozenset(("low","stale","unknown","inactive")), dated=False),
    "movements": Report(MOVEMENTS, "created_at DESC, row_id DESC", (
        ("created_at","Data","datetime"),
    ) + PRODUCT_COLUMNS + (
        ("movement","Movimento","text"),("quantity","Quantidade","number"),("balance","Saldo contado","number"),("note","Observação","text"),
    ), (
        ("events","Movimentações","number","list","count(*)"),
        ("entries","Entradas registradas","number","box","count(*) FILTER (WHERE movement='purchase')"),
        ("sales","Baixas de venda","number","check","count(*) FILTER (WHERE movement='sold')"),
    ), frozenset(MOVEMENT_LABELS)),
    "credit": Report(CREDIT, "due_date, row_id", (
        ("order_code","Pedido","text"),("customer","Cliente","text"),("due_date","Vencimento","date"),
        ("credit_status","Situação","text"),("original_cents","Valor original","money"),
        ("received_cents","Recebido líquido","money"),("pending_cents","Saldo aberto","money"),
    ), (
        ("orders","Pedidos a prazo","number","list","count(*)"),
        ("pending_cents","Saldo aberto","money","wallet","sum(pending_cents)"),
        ("overdue_cents","Em atraso","money","clock","sum(pending_cents) FILTER (WHERE credit_status='overdue')"),
        ("received_cents","Recebido líquido","money","check","sum(received_cents)"),
    ), frozenset(CREDIT_LABELS)),
    "containers": Report(CONTAINERS, "container, row_id", (
        ("container","Vasilhame","text"),("container_kind","Tipo","text"),
        ("empty_quantity","Vazios em estoque","number"),("loaned","Com clientes","number"),
    ), (
        ("types","Tipos cadastrados","number","box","count(*)"),
        ("empty_quantity","Vazios em estoque","number","list","sum(empty_quantity)"),
        ("loaned","Com clientes","number","users","sum(loaned)"),
    ), frozenset(("cylinder","jug")), dated=False),
    "activity": Report(ACTIVITY, "created_at DESC, row_id DESC", (
        ("created_at","Data","datetime"),("module","Área","text"),("reference","Registro","text"),
        ("action","Ação","text"),("actor_role","Origem","text"),
    ), (
        ("events","Atividades","number","list","count(*)"),
        ("orders","Eventos de pedidos","number","check","count(*) FILTER (WHERE module='orders')"),
        ("stock","Eventos de estoque","number","box","count(*) FILTER (WHERE module='stock')"),
    ), frozenset(CELL_LABELS["module"])),
}


def label_cell(key, value):
    if key in CELL_LABELS and value is not None:
        return CELL_LABELS[key].get(value, value)
    return value


def csv_cell(key, style, value, zone):
    if value is None:
        return ""
    if style == "money":
        return amount_label(value)
    if style == "date":
        return value.strftime("%d/%m/%Y")
    if style == "datetime":
        return value.astimezone(ZoneInfo(zone)).strftime("%d/%m/%Y %H:%M")
    return label_cell(key, value)


async def view(db, tenant, *, kind: ReportKind, start=None, end=None, search="", status="all", mode="all",
               offset=0, export=False):
    report = REPORTS[kind]
    if status != "all" and status not in report.statuses:
        raise HTTPException(422, "Selecione um status disponível neste relatório")
    params = {"t": tenant, "search": search.strip(), "status": status, "mode": mode}
    period = None
    if report.dated:
        period, lower, upper = await period_for(db, tenant, start, end)
        params.update(lower=lower, upper=upper, start_date=period["start_date"], end_date=period["end_date"])
        if kind == "credit":
            current, _, _ = report_period(None, None, period["timezone"])
            params["today"] = current["end_date"]

    if export:
        rows = (await db.execute(text(f"""
            SELECT * FROM ({report.query}) AS report_rows ORDER BY {report.order} LIMIT :limit
        """), {**params, "limit": MAX_EXPORT_ROWS+1})).mappings().all()
        if len(rows) > MAX_EXPORT_ROWS:
            raise HTTPException(413, "Refine os filtros para exportar até 20 mil registros por arquivo")
        zone = period["timezone"] if period else None
        records = [tuple(csv_cell(key, style, row[key], zone) for key, _, style in report.columns) for row in rows]
        return Response(
            csv_document([label for _, label, _ in report.columns], records),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="orion-delivery-{kind}.csv"'},
        )

    metrics = ", ".join(
        f"CAST(coalesce({expression},0) AS bigint) AS {key}"
        for key, _, _, _, expression in report.metrics
    )
    packed = (await db.execute(text(f"""
        WITH report_rows AS ({report.query}), page AS (
          SELECT * FROM report_rows ORDER BY {report.order} LIMIT :limit OFFSET :offset
        )
        SELECT {metrics},
            coalesce((SELECT jsonb_agg(to_jsonb(page) ORDER BY {report.order}) FROM page),'[]') AS rows
        FROM report_rows
    """), {**params, "limit": LIMIT+1, "offset": offset})).mappings().one()
    return {
        "period": period,
        "columns": [{"key": key, "label": label, "style": style} for key, label, style in report.columns],
        "items": [
            {**{key: label_cell(key, row[key]) for key, _, _ in report.columns},
             **({"image_url": image_url(tenant, row["preset_image"], row["photo_url"])}
                if "preset_image" in row else {})}
            for row in packed["rows"][:LIMIT]
        ],
        "summary": [
            {"label": label, "style": style, "icon": icon, "value": packed[key]}
            for key, label, style, icon, _ in report.metrics
        ],
        "offset": offset,
        "limit": LIMIT,
        "has_more": len(packed["rows"]) > LIMIT,
    }


def register(router, run):
    @router.get("/reports/operational/{kind}")
    async def delivery_report(tenant_id: UUID, kind: ReportKind, start: date | None = None, end: date | None = None,
                              search: str = Query(default="", max_length=120), status: str = Query(default="all", max_length=30),
                              mode: OrderMode = "all", offset: int = Query(default=0, ge=0, le=100_000),
                              actor: Actor = Depends(authenticate)):
        return await run(actor, tenant_id, view, kind=kind, start=start, end=end, search=search,
                         status=status, mode=mode, offset=offset)

    @router.get("/reports/operational/{kind}/csv")
    async def export_delivery_report(tenant_id: UUID, kind: ReportKind, start: date | None = None, end: date | None = None,
                                     search: str = Query(default="", max_length=120),
                                     status: str = Query(default="all", max_length=30), mode: OrderMode = "all",
                                     actor: Actor = Depends(authenticate)):
        return await run(actor, tenant_id, view, kind=kind, start=start, end=end, search=search,
                         status=status, mode=mode, export=True)
