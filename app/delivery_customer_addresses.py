"""Tenant-private CRM addresses. Each order retains its own confirmed snapshot."""
from fastapi import HTTPException
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import text

from app.business_profile_schemas import ProfileModel
from app.catalog_api import audit
from app.onboarding_schemas import Address


class AddressEdit(ProfileModel):
    label: str = Field(min_length=1, max_length=60)
    address: Address
    expected_revision: StrictInt = Field(ge=0)

    @field_validator("label", mode="before")
    @classmethod
    def clean_label(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value


async def require_customer(db, tenant, customer, *, lock=False):
    query = """SELECT id FROM customers WHERE tenant_id=:t AND id=:c AND active AND deleted_at IS NULL"""
    result = await db.execute(text(query + (" FOR UPDATE" if lock else "")), {"t": tenant, "c": customer})
    if result.scalar_one_or_none() is None:
        raise HTTPException(404, "Cliente ativo não encontrado")


async def list_addresses(db, tenant, customer):
    await require_customer(db, tenant, customer)
    rows = (await db.execute(text("""
        SELECT id,label,address,revision FROM delivery_customer_addresses
        WHERE tenant_id=:t AND customer_id=:c AND label IS NOT NULL ORDER BY lower(label),id
    """), {"t": tenant, "c": customer})).mappings()
    return {"items": [dict(row) for row in rows]}


async def save_address(db, tenant, access, customer, address_id, payload):
    await require_customer(db, tenant, customer, lock=True)
    params = {"t": tenant, "c": customer, "id": address_id, "label": payload.label,
              "address": payload.address.model_dump_json(), "revision": payload.expected_revision}
    existing = (await db.execute(text("""
        SELECT id,label,address,revision FROM delivery_customer_addresses
        WHERE tenant_id=:t AND customer_id=:c AND id=:id
    """), params)).mappings().one_or_none()
    if existing and existing["label"] is None:
        raise HTTPException(409, "Este endereço foi excluído. Cadastre um novo endereço.")
    if existing and existing["label"] == payload.label and existing["address"] == payload.address.model_dump():
        return dict(existing)
    if (existing["revision"] if existing else 0) != payload.expected_revision:
        raise HTTPException(409, "O endereço mudou. Abra novamente os endereços salvos.")
    if existing is None:
        query = """INSERT INTO delivery_customer_addresses (tenant_id,customer_id,id,label,address)
                   VALUES (:t,:c,:id,:label,CAST(:address AS jsonb))"""
    else:
        query = """UPDATE delivery_customer_addresses SET label=:label,address=CAST(:address AS jsonb),
                   revision=revision+1,updated_at=now() WHERE tenant_id=:t AND customer_id=:c AND id=:id"""
    row = (await db.execute(text(query + " RETURNING id,label,address,revision"), params)).mappings().one()
    await audit(db, tenant, "delivery_address_saved", "delivery_customer_address", address_id)
    return dict(row)


async def remove_address(db, tenant, access, customer, address_id, revision):
    await require_customer(db, tenant, customer, lock=True)
    params = {"t": tenant, "c": customer, "id": address_id, "revision": revision}
    row = await db.execute(text("""UPDATE delivery_customer_addresses
        SET label=NULL,address=NULL,revision=revision+1,updated_at=now()
        WHERE tenant_id=:t AND customer_id=:c AND id=:id AND revision=:revision AND label IS NOT NULL
        RETURNING id"""), params)
    if row.scalar_one_or_none() is None:
        remaining = await db.scalar(text("""SELECT revision FROM delivery_customer_addresses
            WHERE tenant_id=:t AND customer_id=:c AND id=:id AND label IS NOT NULL"""), params)
        if remaining is not None:
            raise HTTPException(409, "O endereço mudou. Abra novamente os endereços salvos.")
    else:
        await audit(db, tenant, "delivery_address_removed", "delivery_customer_address", address_id)
    return {"removed": True}
