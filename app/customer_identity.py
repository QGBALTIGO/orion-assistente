"""One customer per tenant and explicitly supplied Brazilian phone.

Identity never comes from a name or an opaque WhatsApp JID/LID. Keep the exact
normalized telephone contract used by authenticated contact ownership checks.
"""
from sqlalchemy import text

from app.management_schemas import normalize_phone


CUSTOMER_FIELDS = frozenset({
    "name", "whatsapp_e164", "instagram", "neighborhood", "city", "email",
    "birth_date", "birth_month", "birth_day", "notes", "active", "deleted_at",
})


async def resolve_customer(db, tenant, phone, name, *, create=True, attributes=None, lock=False):
    """Return (row, created), preserving all existing customer fields.

The database's unique tenant/phone constraint arbitrates concurrent creators,
including older app workers that do not use this helper. Caller owns the tenant
transaction and decides whether an existing placeholder name may be enriched.
"""
    normalized = normalize_phone(phone)
    if normalized is None:
        raise ValueError("Customer identity requires an explicit phone")
    parameters = {"tenant": tenant, "phone": normalized}
    if create:
        values = {**(attributes or {}), "whatsapp_e164": normalized, "name": name}
        if not set(values) <= CUSTOMER_FIELDS:
            raise ValueError("Unsupported customer fields")
        columns = list(values)
        created = (await db.execute(text(
            "INSERT INTO customers (tenant_id," + ",".join(columns) + ") VALUES (:tenant," +
            ",".join(":" + column for column in columns) + ") "
            "ON CONFLICT (tenant_id,whatsapp_e164) DO NOTHING RETURNING *"
        ), {"tenant": tenant, **values})).mappings().one_or_none()
        if created is not None:
            return dict(created), True
    current = (await db.execute(text("""SELECT * FROM customers
        WHERE tenant_id=:tenant AND whatsapp_e164=:phone""" + (" FOR UPDATE" if lock else "")), parameters)).mappings().one_or_none()
    return (dict(current) if current is not None else None), False
