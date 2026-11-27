"""Commercial customer profile using the tenant's actual orders, receipts and packaging."""
from fastapi import HTTPException
from sqlalchemy import text

from app.delivery_orders import ORDERS
from app.delivery_containers import LOAN_DELTA
from app.delivery_catalog import LIMIT


async def profile(db, tenant, customer, *, offset=0):
    params = {"t": tenant, "customer": customer, "limit": LIMIT + 1, "offset": offset}
    exists = await db.scalar(text("""SELECT id FROM customers WHERE tenant_id=:t
        AND id=:customer AND deleted_at IS NULL"""), params)
    if exists is None:
        raise HTTPException(404, "Cliente não encontrado.")

    # Fulfilled orders that have subsequently been returned are cancelled.
    # Count only still-fulfilled purchases; count open credit separately.
    scope = ORDERS + " AND d.customer_id=:customer"
    metrics = (await db.execute(text(f"""
        WITH sales AS ({scope}), purchases AS (
            SELECT id,created_at,total_cents,collected_cents,
                lag(created_at) OVER (ORDER BY created_at,id) AS previous_order
            FROM sales WHERE status='fulfilled'
        )
        SELECT (SELECT count(*) FROM sales) AS orders,
            (SELECT count(*) FROM purchases) AS completed,
            (SELECT count(*) FROM sales WHERE status IN ('confirmed','dispatched')) AS open_orders,
            (SELECT coalesce(sum(outstanding_cents),0) FROM sales
                WHERE status='fulfilled' AND payment_due_date IS NOT NULL) AS outstanding_cents,
            (SELECT coalesce(sum(total_cents),0) FROM purchases) AS purchased_cents,
            (SELECT coalesce(sum(collected_cents),0) FROM purchases) AS received_cents,
            (SELECT max(created_at) FROM purchases) AS last_purchase_at,
            (SELECT round(avg(extract(epoch FROM (created_at-previous_order)))/86400,1)
                FROM purchases WHERE previous_order IS NOT NULL) AS average_interval_days
    """), params)).mappings().one()
    result = dict(metrics)
    completed = result["completed"]
    result["average_ticket_cents"] = ((int(result["purchased_cents"]) + completed // 2) // completed
                                      if completed else None)
    history = (await db.execute(text(scope + """
        ORDER BY o.created_at DESC,o.id DESC LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    favorites = (await db.execute(text("""
        SELECT i.snapshot->>'product_name' AS product,i.snapshot->>'variant_name' AS variant,
            sum(i.quantity) AS quantity
        FROM delivery_order_details d
        JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
        JOIN commercial_quote_items i ON (i.tenant_id,i.quote_id)=(o.tenant_id,o.quote_id)
        WHERE d.tenant_id=:t AND d.customer_id=:customer AND o.origin='delivery'
            AND o.status='fulfilled'
        GROUP BY i.product_id,i.variant_id,1,2
        ORDER BY quantity DESC,product,variant,i.variant_id LIMIT 5
    """), params)).mappings().all()
    loans = (await db.execute(text(f"""
        SELECT c.name,c.kind,sum({LOAN_DELTA}) AS quantity
        FROM delivery_container_movements m JOIN delivery_containers c
            ON (c.tenant_id,c.id)=(m.tenant_id,m.container_id)
        WHERE m.tenant_id=:t AND m.customer_id=:customer
        GROUP BY c.id,c.name,c.kind HAVING sum({LOAN_DELTA})>0
        ORDER BY c.name,c.id
    """.replace(LOAN_DELTA, LOAN_DELTA.replace("kind", "m.kind").replace("quantity", "m.quantity"))), params)).mappings().all()
    # Address book labels only. The operator opens the selected address in the order editor.
    addresses = (await db.execute(text("""
        SELECT label FROM delivery_customer_addresses
        WHERE tenant_id=:t AND customer_id=:customer AND label IS NOT NULL
        ORDER BY lower(label),id
    """), params)).scalars().all()
    allowed = ("id","status","created_at","mode","sales_channel","total_cents","collected_cents",
               "outstanding_cents","receipt_status","pix_state","payment_due_date")
    return {"summary":result,"items":[{key:row[key] for key in allowed} for row in history[:LIMIT]],
            "limit":LIMIT,"offset":offset,"has_more":len(history)>LIMIT,
            "favorites":[dict(row) for row in favorites],"containers":[dict(row) for row in loans],
            "address_labels":list(addresses)}
