"""Account-owned business identity, independent of public-site customization.

All writes are tied to the current session account and its one active owner
membership. This endpoint never accepts a tenant ID supplied by the browser.
"""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.account_auth import _storage
from app.account_business import lock_account_business, panel_businesses
from app.management_schemas import normalize_phone
from app.panel_auth import panel_actor

router = APIRouter(prefix="/api/v1/account", tags=["Minha conta"])


class BusinessIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    contact_phone: str = Field(min_length=10, max_length=40)
    expected_name: str = Field(min_length=1, max_length=160)
    expected_phone: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def readable_name(cls, value):
        normalized = " ".join(value.split())
        if not normalized or any(ord(c) < 32 for c in normalized):
            raise ValueError("Informe um nome de empresa válido")
        return normalized

    @field_validator("contact_phone")
    @classmethod
    def valid_phone(cls, value):
        result = normalize_phone(value)
        if not result:
            raise ValueError("Informe o WhatsApp comercial")
        return result

    @field_validator("expected_phone")
    @classmethod
    def previous_phone(cls, value):
        return normalize_phone(value)


def _account_id(data):
    if data.get("auth_provider") == "legacy_admin" or data.get("role") != "member" or not data.get("account_id"):
        raise HTTPException(403, "Entre com a conta proprietária da empresa")
    return UUID(str(data["account_id"]))


async def _owner_business(db, account_id):
    # Lock the account before membership reads, matching the registration
    # lock order and preventing a business switch during this operation.
    await lock_account_business(db, account_id)
    rows = (await db.execute(text("""
        SELECT t.id,t.name,m.role FROM tenant_memberships m
        JOIN tenants t ON t.id=m.tenant_id
        WHERE m.account_id=:account AND m.active
        ORDER BY t.id LIMIT 2
    """), {"account": account_id})).mappings().all()
    if len(rows) != 1:
        raise HTTPException(409, "Confira o vínculo único da sua empresa")
    if rows[0]["role"] != "owner":
        raise HTTPException(403, "Somente o proprietário pode alterar os dados da empresa")
    return rows[0]


async def _business_details(db, account_id):
    business = await _owner_business(db, account_id)
    onboarding = (await db.execute(text("""
        SELECT configuration FROM business_onboarding WHERE tenant_id=:tenant
    """), {"tenant": business["id"]})).mappings().first()
    config = onboarding["configuration"] if onboarding else {}
    phone = config.get("contact_phone")
    return business, config, phone, onboarding is not None


@router.get("/business-identity")
async def get_business_identity(data: dict = Depends(panel_actor)):
    account_id = _account_id(data)
    async with _storage()() as db:
        async with db.begin():
            business, _, phone, _ = await _business_details(db, account_id)
    return {"name": business["name"], "contact_phone": phone,
            "can_edit": True}


@router.put("/business-identity")
async def update_business_identity(payload: BusinessIdentity,
                                   data: dict = Depends(panel_actor)):
    account_id = _account_id(data)
    async with _storage()() as db:
        async with db.begin():
            business, config, phone, has_onboarding = await _business_details(db, account_id)
            if business["name"] != payload.expected_name or phone != payload.expected_phone:
                raise HTTPException(409, "Os dados da empresa foram alterados. Atualize antes de salvar.")
            if business["name"] != payload.name:
                await db.execute(text("""
                    UPDATE tenants SET name=:name WHERE id=:tenant
                """), {"name": payload.name, "tenant": business["id"]})
            # The same transaction updates the tenant and its contact number.
            # For legacy businesses, create the non-onboarding metadata row.
            await db.execute(text("""
                INSERT INTO business_onboarding(tenant_id,created_by,is_new,
                                                configuration,current_step,version)
                VALUES(:tenant,:account,false,
                       jsonb_build_object('contact_phone',CAST(:phone AS text)),
                       'review',1)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    configuration=jsonb_set(
                        business_onboarding.configuration,
                        '{contact_phone}',to_jsonb(CAST(:phone AS text)),true),
                    version=business_onboarding.version+1,
                    updated_at=now()
            """), {"phone": payload.contact_phone, "tenant": business["id"],
                   "account": account_id})
    return {"ok": True, "name": payload.name, "contact_phone": payload.contact_phone}
