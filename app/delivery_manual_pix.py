"""Manual Pix. The merchant owns the key and attests each receipt."""
import os
import re
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, model_validator
from sqlalchemy import text

from app import commercial_service as commerce, delivery_payments as payments
from app.brazilian_fields import normalize_tax_document
from app.business_profile_schemas import ProfileModel
from app.commercial_schemas import IdempotencyKey
from app.delivery_catalog import run_once
from app.management_schemas import normalize_phone


class Settings(ProfileModel):
    expected_revision: StrictInt = Field(ge=0)
    key_type: Literal["cpf", "cnpj", "phone", "email", "random"] | None = None
    key: str | None = Field(default=None, min_length=1, max_length=100)
    holder: str | None = Field(default=None, min_length=2, max_length=120)
    remove: StrictBool = False

    @model_validator(mode="after")
    def validate_key(self):
        if self.remove:
            if any((self.key_type, self.key, self.holder)):
                raise ValueError("Para remover a chave, deixe os campos vazios.")
            return self
        if not self.key or not self.key_type or not self.holder or len(self.holder.strip()) < 2:
            raise ValueError("Informe o tipo, a chave e o nome do titular.")
        if self.key_type in {"cpf", "cnpj"}:
            self.key = normalize_tax_document(self.key_type, self.key)
        elif self.key_type == "phone":
            self.key = normalize_phone(self.key)
        elif self.key_type == "random":
            self.key = str(UUID(self.key.strip()))
        else:
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.key):
                raise ValueError("Informe um e-mail válido.")
            self.key = self.key.lower()
        if not self.key:
            raise ValueError("Chave Pix inválida.")
        self.holder = self.holder.strip()
        return self


class Confirmation(ProfileModel):
    expected_revision: StrictInt = Field(ge=1)
    received: StrictBool
    idempotency_key: IdempotencyKey


def owner(access):
    if access.role != "owner" or not access.account_id:
        raise HTTPException(403, "Somente o proprietário pode configurar ou conferir Pix.")


def cipher():
    try:
        return Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode())
    except (KeyError, ValueError):
        raise HTTPException(503, "A configuração segura das chaves Pix está indisponível.") from None


async def settings(db, tenant):
    row = (await db.execute(text("""SELECT revision,key_type,holder,suffix FROM delivery_pix_settings
        WHERE tenant_id=:t"""), {"t": tenant})).mappings().first()
    return dict(row) if row else {"revision": 0, "key_type": None, "holder": None, "suffix": None}


async def save_settings(db, tenant, access, payload):
    owner(access)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                     {"scope": f"delivery-pix:{tenant}"})
    if (await settings(db, tenant))["revision"] != payload.expected_revision:
        commerce.conflict("A chave foi alterada. Reabra a configuração.")
    encrypted = cipher().encrypt(payload.key.encode()) if payload.key else None
    await db.execute(text("""INSERT INTO delivery_pix_settings(tenant_id,revision,key_type,encrypted_key,holder,suffix,updated_by)
        VALUES (:t,1,:kind,:key,:holder,:suffix,:actor) ON CONFLICT(tenant_id) DO UPDATE SET
        revision=delivery_pix_settings.revision+1,key_type=excluded.key_type,encrypted_key=excluded.encrypted_key,
        holder=excluded.holder,suffix=excluded.suffix,updated_by=excluded.updated_by,updated_at=now()"""),
        {"t": tenant, "kind": payload.key_type, "key": encrypted, "holder": payload.holder,
         "suffix": payload.key[-4:] if payload.key else None, "actor": access.account_id})
    return await settings(db, tenant)


async def attach(db, tenant, order_id):
    await db.execute(text("""INSERT INTO delivery_pix_orders(tenant_id,order_id,encrypted_key,holder)
        SELECT :t,:o,s.encrypted_key,s.holder FROM (SELECT 1) AS seed
        LEFT JOIN delivery_pix_settings s ON s.tenant_id=:t"""), {"t": tenant, "o": order_id})


async def instructions(db, tenant, digest):
    row = (await db.execute(text("""SELECT p.encrypted_key,p.holder,p.confirmed_at FROM delivery_pix_orders p
        JOIN delivery_storefront_orders s USING(tenant_id,order_id)
        JOIN commercial_orders o ON (o.tenant_id,o.id)=(p.tenant_id,p.order_id)
        WHERE p.tenant_id=:t AND s.tracking_digest=:digest AND o.status IN ('confirmed','dispatched')"""),
        {"t": tenant, "digest": digest})).mappings().first()
    if not row or row["confirmed_at"]:
        return None
    key = None
    if row["encrypted_key"]:
        try:
            key = cipher().decrypt(bytes(row["encrypted_key"])).decode()
        except InvalidToken:
            raise HTTPException(503, "A loja precisa revisar a configuração da chave Pix.") from None
    return {"key": key, "holder": row["holder"]}


async def confirm(db, tenant, access, order_id, payload):
    from app.delivery_orders import one_order

    owner(access)
    if not payload.received:
        raise HTTPException(422, "Confira o crédito no extrato antes de confirmar.")

    async def write():
        row = await one_order(db, tenant, order_id, lock=True)
        if (row["revision"] != payload.expected_revision or row["status"] not in {"confirmed","dispatched"}
                or row["pix_state"] != "pending" or row["financial_entry_id"] or row["total_cents"] <= 0):
            commerce.conflict("Confira o estado atual do pedido antes de registrar o Pix.")
        receipt = await payments.record_completion(db, tenant, row, "pix", None, [])
        await db.execute(text("""UPDATE delivery_pix_orders SET confirmed_at=now(),confirmed_by=:actor
            WHERE tenant_id=:t AND order_id=:o"""), {"t": tenant, "o": order_id, "actor": access.account_id})
        revision = await db.scalar(text("""UPDATE commercial_orders SET revision=revision+1,updated_at=now()
            WHERE tenant_id=:t AND id=:o RETURNING revision"""), {"t": tenant, "o": order_id})
        await commerce.audit(db, tenant, access, "order", order_id, "pix_manually_received",
                             {"financial_entry_id": receipt["id"], "amount_cents": row["total_cents"]})
        return {"revision": revision, "receipt_status": "paid", "financial_entry_id": receipt["id"]}

    return await run_once(db, tenant, payload, "pix_manual_confirmation", order_id, write)
