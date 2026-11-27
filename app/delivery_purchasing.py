"""Goods received from suppliers, valued at purchase cost and posted to Finance."""
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, delivery_catalog as catalog, management_models as management
from app.brazilian_fields import normalize_tax_document
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.management_schemas import MAX_MONEY, normalize_phone


class Supplier(ProfileModel):
    name: str = Field(min_length=1, max_length=140)
    cnpj: str | None = Field(default=None, max_length=18)
    phone: str | None = Field(default=None, max_length=40)
    brands: str | None = Field(default=None, max_length=240)
    active: StrictBool = True

    _phone = field_validator("phone")(normalize_phone)

    @field_validator("cnpj")
    @classmethod
    def valid_document(cls, v):
        return normalize_tax_document("cnpj", v)


class SupplierCreate(Supplier):
    idempotency_key: IdempotencyKey


class SupplierUpdate(Supplier):
    expected_revision: StrictInt = Field(ge=1)


class Line(ProfileModel):
    variant_id: UUID
    quantity: StrictInt = Field(ge=1, le=catalog.MAX_QUANTITY)
    unit_cost_cents: StrictInt = Field(ge=0, le=100_000_000)


class Purchase(ProfileModel):
    supplier_id: UUID
    reference: str = Field(min_length=1, max_length=80)
    items: list[Line] = Field(min_length=1, max_length=60)
    due_on: date | None = None
    payment_method: Literal["cash","pix","debit_card","credit_card","bank_transfer","other"] | None = None
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def consistent(self):
        if len({item.variant_id for item in self.items}) != len(self.items):
            raise ValueError("Informe cada variação uma única vez")
        if not 0 < sum(item.unit_cost_cents * item.quantity for item in self.items) <= MAX_MONEY:
            raise ValueError("Confira o valor total da compra")
        if (self.due_on is None) == (self.payment_method is None):
            raise ValueError("Informe o vencimento a pagar ou a forma de pagamento já realizado")
        if self.due_on is not None and not 1900 <= self.due_on.year <= 2100:
            raise ValueError("Vencimento fora do intervalo permitido")
        return self


async def suppliers(db, tenant, *, search, offset, active_only):
    rows = (await db.execute(text("""SELECT id,name,cnpj,phone,brands,active,revision FROM delivery_suppliers
        WHERE tenant_id=:t AND (:active=false OR active) AND
        position(lower(:q) in lower(concat_ws(' ',name,cnpj,brands)))>0
        ORDER BY lower(name),id LIMIT :limit OFFSET :offset"""),
        {"t":tenant,"q":search.strip(),"active":active_only,"limit":catalog.LIMIT+1,"offset":offset})).mappings().all()
    return {"items":[dict(r) for r in rows[:catalog.LIMIT]],"has_more":len(rows)>catalog.LIMIT,"offset":offset,"limit":catalog.LIMIT}


async def add_supplier(db, tenant, access, payload):
    async def persist():
        identity = uuid4()
        await db.execute(text("""INSERT INTO delivery_suppliers(tenant_id,id,name,cnpj,phone,brands,active,updated_by)
            VALUES (:t,:id,:name,:cnpj,:phone,:brands,:active,:actor)"""),
            {"t":tenant,"id":identity,"actor":access.account_id,**payload.model_dump()})
        await management.audit(db,tenant,"supplier_created","delivery_supplier",identity)
        return {"id":identity,"revision":1}
    return await catalog.run_once(db,tenant,payload,"supplier_create",None,persist)


async def update_supplier(db, tenant, access, identity, payload):
    version = await db.scalar(text("""UPDATE delivery_suppliers SET name=:name,cnpj=:cnpj,phone=:phone,brands=:brands,
        active=:active,updated_by=:actor,revision=revision+1 WHERE tenant_id=:t AND id=:id AND revision=:expected_revision RETURNING revision"""),
        {"t":tenant,"id":identity,"actor":access.account_id,**payload.model_dump()})
    if version is None:
        raise HTTPException(409, "O cadastro mudou. Reabra o fornecedor antes de salvar.")
    await management.audit(db,tenant,"supplier_updated","delivery_supplier",identity)
    return {"id":identity,"revision":version}


async def receive(db, tenant, access, payload):
    async def persist():
        await commerce.tenant_exists(db,tenant,active=True)
        supplier = await db.scalar(text("""SELECT name FROM delivery_suppliers WHERE tenant_id=:t
            AND id=:id AND active FOR SHARE"""), {"t":tenant,"id":payload.supplier_id})
        if supplier is None:
            raise HTTPException(404, "Fornecedor ativo não encontrado")
        identity = uuid4()
        total = sum(line.quantity*line.unit_cost_cents for line in payload.items)
        stamp = commerce.now_utc()
        entry = await management.insert_row(db,tenant,"financial_entries",{
            "kind":"expense","category":"Compras de distribuição","description":f"Compra {payload.reference} · {supplier}",
            "occurred_at":stamp,"status":"pending" if payload.due_on else "paid",
            "payment_method":payload.payment_method or "other","amount_cents":total,
        },str(identity),operation="delivery_supplier_purchase")
        await db.execute(text("""INSERT INTO delivery_purchases(tenant_id,id,supplier_id,reference,amount_cents,
            due_on,financial_entry_id,received_at,received_by) VALUES (:t,:id,:supplier,:reference,:amount,:due,:entry,:stamp,:actor)"""),
            {"t":tenant,"id":identity,"supplier":payload.supplier_id,"reference":payload.reference,
             "amount":total,"due":payload.due_on,"entry":entry["id"],"stamp":stamp,"actor":access.account_id})
        for line in sorted(payload.items, key=lambda item: item.variant_id):
            inv = await catalog.locked_inventory(db,tenant,line.variant_id)
            if inv["mode"]!="tracked" or not inv["valid_until"] or inv["valid_until"]<=stamp:
                raise HTTPException(409,"Registre uma contagem atual do produto antes de receber a compra.")
            if inv["quantity_on_hand"] + line.quantity > catalog.MAX_QUANTITY:
                raise HTTPException(422,"A entrada excede o limite de estoque")
            params = {"t":tenant,"p":identity,"v":line.variant_id,"qty":line.quantity,"cost":line.unit_cost_cents}
            await db.execute(text("""INSERT INTO delivery_purchase_items(tenant_id,purchase_id,variant_id,label,quantity,unit_cost_cents)
                SELECT :t,:p,v.id,concat_ws(' · ',p.name,v.name),:qty,:cost FROM commercial_variants v JOIN commercial_products p
                ON (p.tenant_id,p.id)=(v.tenant_id,v.product_id) WHERE v.tenant_id=:t AND v.id=:v"""),params)
            # Keep the amount paid on the immutable receipt. The product's reference cost is managed separately.
            await catalog.record_count(db,tenant,access,inv,inv["quantity_on_hand"]+line.quantity,"purchase",line.quantity,
                                       f"Compra {payload.reference}",related_purchase=identity)
        await management.audit(db,tenant,"purchase_received","delivery_purchase",identity)
        return {"id":identity,"financial_entry_id":entry["id"],"amount_cents":total}
    return await catalog.run_once(db,tenant,payload,"supplier_purchase",payload.supplier_id,persist)


async def purchases(db, tenant, *, search, offset):
    rows = (await db.execute(text("""SELECT p.id,p.reference,p.amount_cents,p.due_on,p.received_at,
        s.name AS supplier_name,f.status AS payment_status,
        (SELECT jsonb_agg(jsonb_build_object('label',l.label,'quantity',l.quantity,'unit_cost_cents',l.unit_cost_cents)
         ORDER BY l.label,l.variant_id) FROM delivery_purchase_items l
         WHERE l.tenant_id=p.tenant_id AND l.purchase_id=p.id) AS items
        FROM delivery_purchases p JOIN delivery_suppliers s ON (s.tenant_id,s.id)=(p.tenant_id,p.supplier_id)
        JOIN financial_entries f ON (f.tenant_id,f.id)=(p.tenant_id,p.financial_entry_id)
        WHERE p.tenant_id=:t AND position(lower(:q) in lower(concat_ws(' ',s.name,p.reference)))>0
        ORDER BY p.received_at DESC,p.id LIMIT :limit OFFSET :offset"""),
        {"t":tenant,"q":search.strip(),"limit":catalog.LIMIT+1,"offset":offset})).mappings().all()
    return {"items":[dict(r) for r in rows[:catalog.LIMIT]],"has_more":len(rows)>catalog.LIMIT,"offset":offset,"limit":catalog.LIMIT}
