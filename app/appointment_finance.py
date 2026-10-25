"""Book one receivable when a service is completed, without claiming cash received.

The appointment itself is the source of accrual revenue in dashboard and DRE.
This entry is a pending receivable, not a second sale or a bank transaction.
"""
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from app.catalog_api import audit


async def record_service_receivable(db, tenant_id, appointment):
    amount = int(appointment["total_cents"])
    if amount <= 0:
        return None
    appointment_id = appointment["id"]
    entry_id = uuid5(NAMESPACE_URL, f"orion:completed-service:{tenant_id}:{appointment_id}")
    # Completion and the receivable share one transaction, with a stable identity.
    # A manually registered linked receipt must never be overwritten.
    inserted = (await db.execute(text("""
        INSERT INTO financial_entries
            (id,tenant_id,kind,description,amount_cents,category,payment_method,
             status,entry_type,occurred_at,customer_id,professional_id,appointment_id,notes)
        SELECT :entry,:tenant,'income',:description,:amount,'Serviços','other',
               'pending','general',:occurred,:customer,:professional,:appointment,:notes
        WHERE NOT EXISTS (
            SELECT 1 FROM financial_entries
            WHERE tenant_id=:tenant AND appointment_id=:appointment
              AND kind='income' AND status <> 'cancelled'
        )
        ON CONFLICT (tenant_id,id) DO NOTHING
        RETURNING id
    """), {
        "entry": entry_id, "tenant": tenant_id, "appointment": appointment_id,
        "description": ("Atendimento " + str(appointment["protocol"]))[:255],
        "amount": amount, "occurred": appointment["end_at"],
        "customer": appointment["customer_id"], "professional": appointment["professional_id"],
        "notes": "Receita de serviço concluído. Marque como pago somente após receber o valor.",
    })).scalar_one_or_none()
    if inserted is not None:
        await audit(db, tenant_id, "management_created", "financial_entries", inserted)
    return inserted
