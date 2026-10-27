"""Owner-reviewed team invitations, with optional transactional email delivery."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.tenant_access import check_access
from app.account_business import lock_account_business, ensure_membership_target, account_membership_role
from app import email_outbox, email_transport
from app.public_origins import canonical_public_origin

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management/team", tags=["Equipe"])
invitation_router = APIRouter(prefix="/api/v1/team-invitations", tags=["Convites de equipe"])


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["editor", "viewer"]
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        if not value:
            return None
        try:
            return email_transport.normalize_recipient(value)
        except ValueError:
            raise ValueError("Informe um e-mail válido")


class InvitationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "revoke"]
    expected_version: int = Field(ge=1)


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["editor", "viewer"]
    active: bool
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("A versão de data precisa incluir o fuso")
        return value


class InvitationToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


@asynccontextmanager
async def team_session(tenant_id, actor, *, manage=False):
    try:
        async with support_session_factory()() as db:
            async with db.begin():
                if manage:
                    # A missing membership has no row to lock. Serialize every
                    # owner decision before reading membership or invitation
                    # rows, so two accepted links cannot both create/replace an
                    # account's access. Taking this before the owner's SHARE
                    # lock also keeps the lock order consistent across owners.
                    if actor.role not in {"admin", "support", "member"}:
                        check_access(None, "publish")
                    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                                     {"scope": "management-team:" + str(tenant_id)})
                if actor.role in {"admin", "support"}:
                    role = actor.role
                elif actor.role == "member" and isinstance(actor.account_id, UUID) and os.getenv("BUSINESS_PROFILES_ENABLED", "0") == "1":
                    role = await account_membership_role(db, actor.account_id, tenant_id, lock=True)
                elif actor.role == "viewer" and actor.tenant_id == tenant_id and not manage:
                    role = "viewer"
                else:
                    role = None
                check_access(role, "publish" if manage else "read")
                found = (await db.execute(text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant_id})).scalar_one_or_none()
                if found is None:
                    raise HTTPException(404, "Empresa não encontrada")
                yield db, role
    except IntegrityError:
        raise HTTPException(409, "O convite ou vínculo foi alterado. Atualize a equipe") from None


def clean_invitation(row):
    result = {key: row.get(key) for key in ("id", "email", "role", "status", "expires_at", "created_at", "version", "claimed_by_name")}
    result["expired"] = row["expires_at"] <= datetime.now(timezone.utc)
    if "email_status" in row:
        status = {"pending": "queued", "dead": "failed"}.get(row["email_status"], row["email_status"])
        result["email_delivery"] = email_outbox.delivery_result(status or ("disabled" if row.get("email") else "manual"))
    return result


async def membership_event(db, tenant_id, account_id, actor, role, active):
    await db.execute(text("""INSERT INTO tenant_membership_events(tenant_id,account_id,actor_account_id,role,active)
        VALUES(:tenant,:account,:actor,:role,:active)"""),
        {"tenant": tenant_id, "account": account_id, "actor": actor.account_id, "role": role, "active": active})


async def notify_access(db, tenant_id, account_id, *, active, dedupe_key, invitation_id=None, email=None):
    if not email_transport.configured():
        return
    if account_id:
        account = (await db.execute(text("SELECT email,verified_at FROM panel_accounts WHERE id=:id"),
                                    {"id": account_id})).mappings().one_or_none()
        if account is None or not account["email"] or account["verified_at"] is None:
            return
        email = account["email"]
    if not email:
        return
    name = (await db.execute(text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant_id})).scalar_one()
    await email_outbox.enqueue(db,
        kind="team_access_approved" if active else "team_access_revoked", recipient=email,
        account_id=account_id, tenant_id=tenant_id, invitation_id=invitation_id,
        dedupe_key=dedupe_key, business_name=name)


@router.get("")
async def team(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with team_session(tenant_id, actor) as (db, role):
        manage = role in {"owner", "support", "admin"}
        members = (await db.execute(text("""SELECT m.account_id,a.name,a.email,m.role,m.active,m.updated_at
            FROM tenant_memberships m JOIN panel_accounts a ON a.id=m.account_id
            WHERE m.tenant_id=:tenant ORDER BY m.active DESC,a.name LIMIT 500"""), {"tenant": tenant_id})).mappings().all()
        invitations = (await db.execute(text("""SELECT i.*,a.name AS claimed_by_name,o.status AS email_status FROM management_team_invitations i
            LEFT JOIN panel_accounts a ON a.id=i.claimed_by
            LEFT JOIN panel_email_outbox o ON o.invitation_id=i.id AND o.kind='team_invite'
            WHERE i.tenant_id=:tenant
            ORDER BY i.created_at DESC LIMIT 100"""), {"tenant": tenant_id})).mappings().all() if manage else []
        return {"members": [dict(row) if manage or row["account_id"] == actor.account_id else {**dict(row), "email": None} for row in members],
                "invitations": [clean_invitation(row) for row in invitations],
                "roles": ["owner", "editor", "viewer"], "can_manage": manage}


@router.post("/invitations", status_code=201)
async def invite(tenant_id: UUID, payload: InvitationCreate, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    fingerprint = hashlib.sha256(json.dumps({"email": payload.email, "role": payload.role}, sort_keys=True).encode()).hexdigest()
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        prior = (await db.execute(text("SELECT * FROM management_team_invitations WHERE tenant_id=:tenant AND idempotency_key=:key"),
            {"tenant": tenant_id, "key": payload.idempotency_key})).mappings().one_or_none()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise HTTPException(409, "Esta chave já foi utilizada com outro convite")
            return {**clean_invitation(prior), "link": None, "replayed": True,
                    "email_delivery": await email_outbox.invitation_delivery(db, prior)}
        count = (await db.execute(text("SELECT count(*) FROM management_team_invitations WHERE tenant_id=:tenant AND status IN ('pending','claimed') AND expires_at>now()"),
            {"tenant": tenant_id})).scalar_one()
        if count >= 50:
            raise HTTPException(429, "Esta empresa já tem 50 convites pendentes. Revogue os que não serão usados")
        token = secrets.token_urlsafe(32)
        row = (await db.execute(text("""INSERT INTO management_team_invitations
            (tenant_id,email,role,token_digest,idempotency_key,fingerprint,expires_at,created_by)
            VALUES(:tenant,:email,:role,:digest,:key,:fingerprint,:expires,:actor) RETURNING *"""),
            {"tenant": tenant_id, "email": payload.email, "role": payload.role, "digest": hashlib.sha256(token.encode()).hexdigest(),
             "key": payload.idempotency_key, "fingerprint": fingerprint, "expires": datetime.now(timezone.utc) + timedelta(days=7),
             "actor": actor.account_id})).mappings().one()
        try:
            origin = canonical_public_origin()
        except ValueError:
            raise HTTPException(503, "O endereço público de convites está inválido") from None
        delivery = email_outbox.delivery_result("manual")
        if payload.email:
            name = (await db.execute(text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant_id})).scalar_one()
            delivery = await email_outbox.enqueue(db, kind="team_invite", recipient=payload.email,
                dedupe_key="team-invite:" + str(row["id"]), tenant_id=tenant_id,
                invitation_id=row["id"], expires_at=row["expires_at"],
                invitation_token=token, business_name=name)
        return {**clean_invitation(row), "link": origin + "/convite#token=" + token,
                "replayed": False, "email_delivery": delivery}


@router.patch("/invitations/{invitation_id}")
async def review(tenant_id: UUID, invitation_id: UUID, payload: InvitationReview, actor: Actor = Depends(authenticate)):
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        row = (await db.execute(text("SELECT * FROM management_team_invitations WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            {"tenant": tenant_id, "id": invitation_id})).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Convite não encontrado")
        if row["version"] != payload.expected_version:
            raise HTTPException(409, "O convite mudou. Atualize a equipe antes de decidir")
        if row["status"] in {"approved", "revoked"}:
            raise HTTPException(409, "Este convite já foi encerrado")
        if payload.action == "approve":
            if row["expires_at"] <= datetime.now(timezone.utc):
                raise HTTPException(410, "O convite expirou")
            if row["status"] != "claimed" or row["claimed_by"] is None:
                raise HTTPException(409, "A pessoa precisa aceitar o convite com sua conta verificada")
            # A member authorized to manage this team is already its owner.
            # Reject self-approval before waiting on the account lock while
            # team_session holds that owner's membership SHARE lock.
            if actor.role == "member" and row["claimed_by"] == actor.account_id:
                raise HTTPException(409, "Esta conta já tem vínculo com a empresa. Gerencie seu acesso na lista da equipe")
            await lock_account_business(db, row["claimed_by"])
            await ensure_membership_target(db, row["claimed_by"], tenant_id)
            account = (await db.execute(text("SELECT email,verified_at FROM panel_accounts WHERE id=:id FOR SHARE"),
                                       {"id": row["claimed_by"]})).mappings().one()
            if account["verified_at"] is None or (row["email"] and row["email"] != account["email"]):
                raise HTTPException(409, "O e-mail do convite precisa estar verificado na conta que o aceitou")
            existing = (await db.execute(text("SELECT role,active FROM tenant_memberships WHERE tenant_id=:tenant AND account_id=:account FOR UPDATE"),
                {"tenant": tenant_id, "account": row["claimed_by"]})).mappings().one_or_none()
            if existing and (existing["active"] or existing["role"] == "owner"):
                raise HTTPException(409, "Esta conta já tem vínculo com a empresa. Gerencie seu acesso na lista da equipe")
            await db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:account,:role,true)
                ON CONFLICT(tenant_id,account_id) DO UPDATE SET role=EXCLUDED.role,active=true,updated_at=now()"""),
                {"tenant": tenant_id, "account": row["claimed_by"], "role": row["role"]})
            await membership_event(db, tenant_id, row["claimed_by"], actor, row["role"], True)
        updated = (await db.execute(text("""UPDATE management_team_invitations SET status=:status,reviewed_by=:actor,
            version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
            {"tenant": tenant_id, "id": invitation_id, "status": "approved" if payload.action == "approve" else "revoked",
             "actor": actor.account_id})).mappings().one()
        await notify_access(db, tenant_id, row["claimed_by"], active=payload.action == "approve",
            invitation_id=row["id"], email=row["email"],
            dedupe_key="team-review:" + str(row["id"]) + ":" + str(updated["version"]))
        return clean_invitation(updated)


@router.patch("/members/{account_id}")
async def change_member(tenant_id: UUID, account_id: UUID, payload: MemberChange, actor: Actor = Depends(authenticate)):
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        if actor.account_id == account_id:
            raise HTTPException(403, "Este formulário não altera proprietários nem seu próprio acesso")
        await lock_account_business(db, account_id)
        if payload.active:
            await ensure_membership_target(db, account_id, tenant_id)
        row = (await db.execute(text("SELECT * FROM tenant_memberships WHERE tenant_id=:tenant AND account_id=:account FOR UPDATE"),
            {"tenant": tenant_id, "account": account_id})).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "Vínculo não encontrado")
        if row["role"] == "owner":
            raise HTTPException(403, "Este formulário não altera proprietários nem seu próprio acesso")
        if row["updated_at"] != payload.expected_updated_at:
            raise HTTPException(409, "O vínculo mudou. Atualize a equipe antes de salvar")
        updated = (await db.execute(text("""UPDATE tenant_memberships SET role=:role,active=:active,updated_at=now()
            WHERE tenant_id=:tenant AND account_id=:account RETURNING account_id,role,active,updated_at"""),
            {"tenant": tenant_id, "account": account_id, "role": payload.role, "active": payload.active})).mappings().one()
        await membership_event(db, tenant_id, account_id, actor, payload.role, payload.active)
        if row["active"] != payload.active:
            await notify_access(db, tenant_id, account_id, active=payload.active,
                dedupe_key="membership:" + str(tenant_id) + ":" + str(account_id) + ":" + updated["updated_at"].isoformat())
        return dict(updated)


async def token_row(db, token, *, lock=False):
    row = (await db.execute(text("""SELECT i.*,t.name AS business_name FROM management_team_invitations i
        JOIN tenants t ON t.id=i.tenant_id WHERE i.token_digest=:digest""" + (" FOR UPDATE OF i" if lock else "")),
        {"digest": hashlib.sha256(token.encode()).hexdigest()})).mappings().one_or_none()
    if row is None or row["status"] == "revoked" or row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "Convite inválido, revogado ou expirado")
    return row


@invitation_router.post("/preview")
async def preview(payload: InvitationToken, request: Request, response: Response):
    from app.panel_auth import _same_origin
    _same_origin(request)
    response.headers["Cache-Control"] = "no-store"
    async with support_session_factory()() as db:
        row = await token_row(db, payload.token)
        return {"business_name": row["business_name"], "role": row["role"], "expires_at": row["expires_at"],
                "status": row["status"], "email_bound": bool(row["email"]), "requires_owner_approval": True}


@invitation_router.post("/accept")
async def accept(payload: InvitationToken, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Entre com uma conta pessoal e confirme seu e-mail para aceitar o convite")
    async with support_session_factory()() as db:
        async with db.begin():
            row = await token_row(db, payload.token, lock=True)
            await lock_account_business(db, actor.account_id)
            await ensure_membership_target(db, actor.account_id, row["tenant_id"])
            if row["status"] in {"claimed", "approved"}:
                if row["claimed_by"] != actor.account_id:
                    raise HTTPException(409, "Este convite já foi usado por outra conta")
                return {"status": row["status"], "business_name": row["business_name"]}
            account = (await db.execute(text("SELECT email,verified_at FROM panel_accounts WHERE id=:id FOR SHARE"),
                {"id": actor.account_id})).mappings().one_or_none()
            if account is None or account["verified_at"] is None:
                raise HTTPException(403, "Confirme seu e-mail em Minha conta antes de aceitar")
            if row["email"] is not None and row["email"] != account["email"]:
                raise HTTPException(403, "Entre com a conta cujo e-mail recebeu este convite")
            exists = (await db.execute(text("SELECT role FROM tenant_memberships WHERE tenant_id=:tenant AND account_id=:account AND active"),
                {"tenant": row["tenant_id"], "account": actor.account_id})).scalar_one_or_none()
            if exists:
                raise HTTPException(409, "Sua conta já faz parte desta empresa")
            await db.execute(text("""UPDATE management_team_invitations SET status='claimed',claimed_by=:account,version=version+1,updated_at=now()
                WHERE id=:id AND tenant_id=:tenant"""), {"account": actor.account_id, "id": row["id"], "tenant": row["tenant_id"]})
            return {"status": "claimed", "business_name": row["business_name"]}
