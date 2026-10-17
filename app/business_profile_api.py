"""Business configuration only: no WhatsApp sends, charges or scheduling mutations."""
from uuid import UUID
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.business_profile_schemas import (
    BusinessProfile, MembershipChange, SaveDraft, SelectVersion, SUPPORTED_CAPABILITIES,
)
from app.business_profile_service import read_profile, save_draft, select_version, profile_history, profile_version
from app.catalog_api import Actor, authenticate, support_only, support_session_factory
from app.database import get_session_factory
from app.tenant_access import resolve_tenant_access

def profiles_enabled():
    if os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        raise HTTPException(423, "A configuração empresarial ainda não está habilitada")


router = APIRouter(tags=["Configuração empresarial"], dependencies=[Depends(profiles_enabled)])
BASE = "/api/v1/tenants/{tenant_id}/business"


@router.get("/api/v1/account/businesses")
async def my_businesses(actor: Actor = Depends(authenticate)):
    account_id = getattr(actor, "account_id", None)
    if not isinstance(account_id, UUID):
        # Legacy support uses the existing global /tenants endpoint.
        return []
    async with support_session_factory()() as session:
        rows = (await session.execute(text("""
            SELECT t.id,t.name,t.slug,t.status,m.role FROM tenant_memberships m
            JOIN tenants t ON t.id=m.tenant_id
            WHERE m.account_id=:account AND m.active ORDER BY t.name LIMIT 200
        """), {"account": account_id})).mappings().all()
    return jsonable_encoder(rows)


@router.put(BASE + "/memberships/{account_id}")
async def set_membership(tenant_id: UUID, account_id: UUID, payload: MembershipChange,
                         actor: Actor = Depends(authenticate)):
    support_only(actor)
    try:
        async with support_session_factory()() as session:
            async with session.begin():
                await session.execute(text("""
                    INSERT INTO tenant_memberships (tenant_id,account_id,role,active)
                    VALUES (:tenant,:account,:role,:active)
                    ON CONFLICT (tenant_id,account_id) DO UPDATE
                    SET role=EXCLUDED.role,active=EXCLUDED.active,updated_at=now()
                """), {"tenant": tenant_id, "account": account_id, **payload.model_dump()})
                await session.execute(text("""
                    INSERT INTO tenant_membership_events
                        (tenant_id,account_id,actor_account_id,role,active)
                    VALUES (:tenant,:account,:actor,:role,:active)
                """), {"tenant": tenant_id, "account": account_id,
                       "actor": getattr(actor, "account_id", None), **payload.model_dump()})
                await session.execute(text("""
                    INSERT INTO platform_audit (tenant_id,action)
                    VALUES (:tenant,'membership_changed')
                """), {"tenant": tenant_id})
    except IntegrityError:
        raise HTTPException(409, "Conta ou estabelecimento não encontrado") from None
    return {"account_id": str(account_id), **payload.model_dump()}


@router.get(BASE + "/profile")
async def get_profile(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id)
    async with get_session_factory()() as session:
        result = await read_profile(session, tenant_id)
    return jsonable_encoder({**result, "draft_template": BusinessProfile().model_dump(mode="json"),
                             "runtime_connected": True,
                             "supported_capabilities": SUPPORTED_CAPABILITIES,
                             "changes_booking_settings": False, "access": {
                                 "role": access.role,
                                 "can_edit_draft": access.role in {"owner", "editor", "admin", "support"},
                                 "can_publish": access.role in {"owner", "admin", "support"},
                                 "can_manage_memberships": actor.role in {"admin", "support"},
                             }})


@router.get(BASE + "/profile/history")
async def get_history(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    async with get_session_factory()() as session:
        return jsonable_encoder(await profile_history(session, tenant_id))


@router.put(BASE + "/profile/draft")
async def draft(tenant_id: UUID, payload: SaveDraft, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "draft")
    async with get_session_factory()() as session:
        return jsonable_encoder(await save_draft(session, tenant_id, payload, access))


@router.get(BASE + "/profile/versions/{version_id}")
async def get_version(tenant_id: UUID, version_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id)
    async with get_session_factory()() as session:
        return jsonable_encoder(await profile_version(session, tenant_id, version_id))


@router.post(BASE + "/profile/publish")
async def publish(tenant_id: UUID, payload: SelectVersion, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "publish")
    async with get_session_factory()() as session:
        return jsonable_encoder(await select_version(session, tenant_id, payload, access))


@router.post(BASE + "/profile/rollback")
async def rollback(tenant_id: UUID, payload: SelectVersion, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "publish")
    async with get_session_factory()() as session:
        return jsonable_encoder(await select_version(session, tenant_id, payload, access, rollback=True))
