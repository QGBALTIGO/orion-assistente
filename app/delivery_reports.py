"""Order reports from the canonical Delivery and Finance records."""
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Response
from sqlalchemy import text

from app.csv_export import csv_document
from app.delivery_catalog import LIMIT
from app.delivery_orders import ORDERS, RECEIPT_STATUS, SALES_CHANNEL
from app.management_models import period_for


ReceiptFilter = Literal["all", "pending", "paid", "refunded", "none"]
ChannelFilter = Literal["all", "pos", "phone", "attendant", "storefront", "manual"]
CHANNEL_LABELS = {"pos":"Ponto de venda","phone":"Telefone","attendant":"Atendimento humano",
                  "storefront":"Loja pública","manual":"Painel"}
MAX_EXPORT_ROWS = 20_000
FILTER = f"""
    AND o.created_at>=:lower AND o.created_at<:upper
    AND (:state='all' OR o.status=:state)
    AND (:mode='all' OR d.mode=:mode)
    AND (:channel='all' OR ({SALES_CHANNEL})=:channel)
    AND (:receipt='all' OR coalesce(({RECEIPT_STATUS}),'none')=:receipt)
"""
STATE_LABELS = {"confirmed":"Reservado","dispatched":"Em rota","fulfilled":"Concluído","cancelled":"Cancelado"}
MODE_LABELS = {"counter":"Balcão","pickup":"Retirada","delivery":"Entrega"}
RECEIPT_LABELS = {"paid":"Recebido","pending":"A receber","refunded":"Estornado","cancelled":"Cancelado"}


async def sales(db, tenant, *, start, end, state, mode, receipt, channel="all", offset=0, export=False):
    period, lower, upper = await period_for(db, tenant, start, end)
    scope = ORDERS + FILTER
    params = {"t":tenant,"lower":lower,"upper":upper,"state":state,"mode":mode,"receipt":receipt,"channel":channel,"offset":offset}
    if export:
        rows = (await db.execute(text(scope + """
            ORDER BY o.created_at DESC,o.id DESC LIMIT :limit
        """), {**params,"limit":MAX_EXPORT_ROWS+1})).mappings().all()
        if len(rows) > MAX_EXPORT_ROWS:
            raise HTTPException(413, "Refine os filtros para exportar até 20 mil pedidos por arquivo")
        header = ["Pedido","Data","Cliente","Atendimento","Status","Recebimento","Total (R$)","Taxa de entrega (R$)","Origem"]
        zone = ZoneInfo(period["timezone"])
        records = [(row["id"],row["created_at"].astimezone(zone).strftime("%d/%m/%Y %H:%M"),
            row["customer_name"] or "Venda avulsa",MODE_LABELS[row["mode"]],STATE_LABELS[row["status"]],
            RECEIPT_LABELS.get(row["receipt_status"],"Sem recebimento"),
            amount_label(row["total_cents"]),amount_label(row["delivery_fee_cents"]),CHANNEL_LABELS[row["sales_channel"]]) for row in rows]
        return Response(csv_document(header,records),media_type="text/csv",
                        headers={"Content-Disposition":'attachment; filename="orion-delivery-vendas.csv"'})
    result = (await db.execute(text("""
        WITH filtered AS (""" + scope + """),
        page AS (SELECT * FROM filtered ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset)
        SELECT count(*) AS orders, count(*) FILTER (WHERE status='cancelled') AS cancelled,
            coalesce(sum(collected_cents) FILTER (WHERE receipt_status IN ('paid','pending')),0) AS paid_cents,
            coalesce(sum(outstanding_cents),0) AS pending_cents,
            coalesce((SELECT jsonb_agg(to_jsonb(page) ORDER BY created_at DESC,id DESC) FROM page),'[]') AS items
        FROM filtered
    """), {**params,"limit":LIMIT+1})).mappings().one()
    return {"period":period,"items":result["items"][:LIMIT],"offset":offset,"limit":LIMIT,"has_more":len(result["items"])>LIMIT,
            "summary":{key:result[key] for key in ("orders","cancelled","paid_cents","pending_cents")}}


def amount_label(cents):
    # PostgreSQL SUM(bigint) is Decimal, while the ledger stores exact cents.
    # Format the magnitude so a negative net balance keeps its correct sign.
    amount = int(cents)
    sign = "-" if amount < 0 else ""
    reais, centavos = divmod(abs(amount), 100)
    return f"{sign}{reais},{centavos:02d}"
