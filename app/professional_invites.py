"""Owner-issued invitations bound to a real professional, never a generic employee.

A verified acceptance grants only the professional membership role. It never
grants editor/owner privileges, and it never creates a professional profile.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_session_factory, tenant_transaction
from app import email_outbox, email_transport
from app.account_business import lock_account_business, ensure_membership_target
from app.management_team import team_session, membership_event, notify_access
from app.public_origins import canonical_public_origin

router = APIRouter(
    prefix="/api/v1/tenants/{tenant_id}/management/team/professionals",
    tags=["Acessos profissionais"],
)


class InviteProfessional(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=5, max_length=254)
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        try:
            return email_transport.normalize_recipient(value)
        except ValueError:
            raise ValueError("Informe um e-mail válido") from None


class CompanyBlockPermission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_manage_owner_blocks: bool
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def valid_version(cls, value):
        if value.tzinfo is None:
            raise ValueError("Atualize a equipe antes de alterar permissões")
        return value


async def require_unique_professional_email(db, tenant_id, professional_id, email):
    conflict=(await db.execute(text("""SELECT 1 FROM professional_portal_links
        WHERE tenant_id=:tenant AND email=:email AND professional_id<>:professional
        LIMIT 1"""), {"tenant":tenant_id,"email":email,
                       "professional":professional_id})).scalar_one_or_none()
    if conflict:
        raise HTTPException(409, "Este e-mail já pertence a outro profissional cadastrado nesta empresa")


async def approved_invitation_is_active(db, invitation):
    """An old approved token is never proof that its account still has access."""
    if invitation["role"] != "professional":
        return invitation["status"] == "approved"
    if invitation["status"] != "approved" or invitation["claimed_by"] is None:
        return False
    row=(await db.execute(text("""SELECT l.updated_at
        FROM professional_portal_links l JOIN tenant_memberships m
          ON m.tenant_id=l.tenant_id AND m.account_id=l.account_id
        WHERE l.tenant_id=:tenant AND l.professional_id=:professional
          AND l.account_id=:account AND l.active
          AND m.active AND m.role='professional'"""),
        {"tenant":invitation["tenant_id"],
         "professional":invitation["professional_id"],
         "account":invitation["claimed_by"]})).mappings().first()
    if row is None:
        return False
    async with get_session_factory()() as runtime:
        async with tenant_transaction(runtime,invitation["tenant_id"]):
            staff=(await runtime.execute(text("""SELECT active,last_archived_at
                FROM professionals WHERE tenant_id=:tenant AND id=:professional"""),
                {"tenant":invitation["tenant_id"],
                 "professional":invitation["professional_id"]})).mappings().first()
    return bool(staff and staff["active"] and
        (staff["last_archived_at"] is None or
         row["updated_at"] > staff["last_archived_at"]))


class ChangeProfessionalAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(suspend|reactivate|revoke)$")
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Atualize a equipe antes de modificar o acesso")
        return value


async def require_active_professional(tenant_id, professional_id):
    """Never treat an invitation as evidence that a professional still exists."""
    async with get_session_factory()() as runtime:
        async with tenant_transaction(runtime, tenant_id):
            result = (await runtime.execute(text(
                "SELECT name,active FROM professionals WHERE tenant_id=:t AND id=:p"
            ), {"t": tenant_id, "p": professional_id})).mappings().first()
            if not result:
                raise HTTPException(404, "Profissional não encontrado nesta empresa")
            if not result["active"]:
                raise HTTPException(409, "Ative o profissional antes de conceder acesso")
            return result["name"]


class SetProfessionalEmail(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not value:
            return None
        try:
            return email_transport.normalize_recipient(value)
        except ValueError:
            raise ValueError("Informe um e-mail válido") from None


@router.put("/{professional_id}/email")
async def set_professional_email(
    tenant_id: UUID, professional_id: UUID, payload: SetProfessionalEmail,
    actor: Actor = Depends(authenticate),
):
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        await require_active_professional(tenant_id, professional_id)
        current = (await db.execute(text("""SELECT * FROM professional_portal_links
            WHERE tenant_id=:t AND professional_id=:p FOR UPDATE"""),
            {"t": tenant_id, "p": professional_id})).mappings().first()
        if current and current["account_id"] is not None:
            raise HTTPException(409, "Revogue o acesso atual antes de alterar o e-mail")
        if payload.email:
            await require_unique_professional_email(db,tenant_id,professional_id,payload.email)
        if current and current["email"] == payload.email:
            return {"email": payload.email, "saved": True}
        await db.execute(text("""UPDATE management_team_invitations SET status='revoked',
            version=version+1,updated_at=now(),reviewed_by=:actor
            WHERE tenant_id=:t AND professional_id=:p AND status IN ('pending','claimed')"""),
            {"t": tenant_id, "p": professional_id, "actor": actor.account_id})
        if payload.email:
            await db.execute(text("""INSERT INTO professional_portal_links
                (tenant_id,professional_id,email,active) VALUES(:t,:p,:email,false)
                ON CONFLICT(tenant_id,professional_id) DO UPDATE
                    SET email=EXCLUDED.email,account_id=NULL,active=false,updated_at=now()"""),
                {"t": tenant_id, "p": professional_id, "email": payload.email})
        elif current:
            await db.execute(text("""DELETE FROM professional_portal_links
                WHERE tenant_id=:t AND professional_id=:p AND account_id IS NULL"""),
                {"t": tenant_id, "p": professional_id})
        return {"email": payload.email, "saved": True}


@router.post("/{professional_id}/invite", status_code=201)
async def invite_professional(
    tenant_id: UUID, professional_id: UUID, payload: InviteProfessional,
    response: Response, actor: Actor = Depends(authenticate),
):
    response.headers["Cache-Control"] = "no-store"
    from app.management_team import clean_invitation
    fingerprint = hashlib.sha256(json.dumps(
        {"professional_id": str(professional_id), "email": payload.email}, sort_keys=True
    ).encode()).hexdigest()
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        name = await require_active_professional(tenant_id, professional_id)
        # Replay cannot regenerate the secret. Do not revoke a pending link first.
        prior = (await db.execute(text("""SELECT * FROM management_team_invitations
            WHERE tenant_id=:t AND idempotency_key=:key"""),
            {"t": tenant_id, "key": payload.idempotency_key})).mappings().first()
        if prior:
            if prior["fingerprint"] != fingerprint or prior["role"] != "professional":
                raise HTTPException(409, "Chave já utilizada com outro convite")
            return {**clean_invitation(prior), "link": None, "replayed": True,
                "email_delivery": await email_outbox.invitation_delivery(db, prior)}
        current = (await db.execute(text("""SELECT * FROM professional_portal_links
            WHERE tenant_id=:t AND professional_id=:p FOR UPDATE"""),
            {"t": tenant_id, "p": professional_id})).mappings().first()
        if current and current["account_id"] is not None:
            raise HTTPException(409, "Revogue o acesso anterior deste profissional antes de enviar um novo convite")
        await require_unique_professional_email(db,tenant_id,professional_id,payload.email)
        count = (await db.execute(text("""SELECT count(*) FROM management_team_invitations
            WHERE tenant_id=:t AND status IN ('pending','claimed') AND expires_at>now()"""),
            {"t": tenant_id})).scalar_one()
        if count >= 50:
            raise HTTPException(429, "Revogue convites antigos antes de convidar outra pessoa")
        try:
            origin = canonical_public_origin()
        except ValueError:
            raise HTTPException(503, "Endereço público de convites inválido") from None
        await db.execute(text("""UPDATE management_team_invitations SET status='revoked',
            version=version+1,updated_at=now(),reviewed_by=:actor
            WHERE tenant_id=:t AND professional_id=:p AND status IN ('pending','claimed')"""),
            {"t": tenant_id, "p": professional_id, "actor": actor.account_id})
        await db.execute(text("""INSERT INTO professional_portal_links
            (tenant_id,professional_id,email,active) VALUES(:t,:p,:email,false)
            ON CONFLICT(tenant_id,professional_id) DO UPDATE SET email=EXCLUDED.email,
              account_id=NULL,active=false,updated_at=now()"""),
            {"t": tenant_id, "p": professional_id, "email": payload.email})
        token = secrets.token_urlsafe(32)
        row = (await db.execute(text("""INSERT INTO management_team_invitations
            (tenant_id,professional_id,email,role,token_digest,
             idempotency_key,fingerprint,expires_at,created_by)
            VALUES(:t,:p,:email,'professional',:digest,:key,:fingerprint,:expires,:actor)
            RETURNING *"""),
            {"t": tenant_id, "p": professional_id, "email": payload.email,
             "digest": hashlib.sha256(token.encode()).hexdigest(),
             "key": payload.idempotency_key, "fingerprint": fingerprint,
             "expires": datetime.now(timezone.utc)+timedelta(days=7),
             "actor": actor.account_id})).mappings().one()
        business_name=(await db.execute(text(
            "SELECT name FROM tenants WHERE id=:tenant"),
            {"tenant":tenant_id})).scalar_one()
        delivery = await email_outbox.enqueue(
            db, kind="team_invite", recipient=payload.email,
            dedupe_key="team-invite:"+str(row["id"]), tenant_id=tenant_id,
            invitation_id=row["id"], expires_at=row["expires_at"],
            invitation_token=token, business_name=business_name, professional_portal=True,
        )
        return {**clean_invitation(row),
            "link": origin+"/convite#token="+token, "replayed": False,
            "email_delivery": delivery}


@router.patch("/{professional_id}/block-permissions")
async def set_company_block_permission(
    tenant_id: UUID, professional_id: UUID,
    payload: CompanyBlockPermission, actor: Actor = Depends(authenticate),
):
    async with team_session(tenant_id,actor,manage=True) as (db,_):
        row=(await db.execute(text("""SELECT account_id,updated_at FROM professional_portal_links
            WHERE tenant_id=:t AND professional_id=:p FOR UPDATE"""),
            {"t":tenant_id,"p":professional_id})).mappings().first()
        if row is None or row["account_id"] is None:
            raise HTTPException(404, "Convide o profissional antes de configurar o acesso")
        if row["updated_at"] != payload.expected_updated_at:
            raise HTTPException(409, "O acesso foi alterado. Atualize a equipe")
        changed=(await db.execute(text("""UPDATE professional_portal_links
            SET can_manage_owner_blocks=:grant,updated_at=now()
            WHERE tenant_id=:t AND professional_id=:p
            RETURNING account_id,active,email,updated_at,can_manage_owner_blocks"""),
            {"t":tenant_id,"p":professional_id,
             "grant":payload.can_manage_owner_blocks})).mappings().one()
        return dict(changed)


@router.patch("/{professional_id}/access")
async def change_professional_access(
    tenant_id: UUID, professional_id: UUID, payload: ChangeProfessionalAccess,
    actor: Actor = Depends(authenticate),
):
    async with team_session(tenant_id, actor, manage=True) as (db, _):
        link = (await db.execute(text("""SELECT * FROM professional_portal_links
            WHERE tenant_id=:t AND professional_id=:p FOR UPDATE"""),
            {"t": tenant_id, "p": professional_id})).mappings().first()
        if not link or link["account_id"] is None:
            raise HTTPException(404, "Este profissional ainda não possui acesso aprovado")
        if link["updated_at"] != payload.expected_updated_at:
            raise HTTPException(409, "O acesso foi alterado. Atualize a equipe")
        if payload.action == "reactivate":
            await require_active_professional(tenant_id, professional_id)
        account = link["account_id"]
        await lock_account_business(db, account)
        if payload.action == "reactivate":
            await ensure_membership_target(db, account, tenant_id)
        membership = (await db.execute(text("""SELECT role,active FROM tenant_memberships
            WHERE tenant_id=:t AND account_id=:a FOR UPDATE"""),
            {"t": tenant_id, "a": account})).mappings().first()
        if membership is None or membership["role"] != "professional":
            raise HTTPException(409, "Este acesso profissional não está mais vinculado à conta")
        enable = payload.action == "reactivate"
        await db.execute(text("""UPDATE tenant_memberships SET active=:enabled,updated_at=now()
            WHERE tenant_id=:t AND account_id=:a"""),
            {"t": tenant_id, "a": account, "enabled": enable})
        updated = (await db.execute(text("""UPDATE professional_portal_links
            SET active=:enabled,account_id=CASE WHEN :revoke THEN NULL ELSE account_id END,
            updated_at=now() WHERE tenant_id=:t AND professional_id=:p
            RETURNING tenant_id,professional_id,email,account_id,active,updated_at"""),
            {"t": tenant_id, "p": professional_id, "enabled": enable,
             "revoke": payload.action == "revoke"})).mappings().one()
        await membership_event(db, tenant_id, account, actor, "professional", enable)
        if membership["active"] != enable:
            await notify_access(db, tenant_id, account, active=enable,
                dedupe_key="portal-access:"+str(tenant_id)+":"+str(account)+":"+updated["updated_at"].isoformat())
        return dict(updated)


async def accept_professional_invitation(db, row, actor):
    """Called with the invitation row locked, after the tenant decision lock."""
    from app.management_team import clean_invitation
    if row["role"] != "professional" or row["professional_id"] is None:
        raise HTTPException(409, "Convite não é de acesso profissional")
    if row["status"] == "approved":
        if row["claimed_by"] != actor.account_id:
            raise HTTPException(409, "Convite já utilizado")
        if not await approved_invitation_is_active(db,row):
            raise HTTPException(410, "Acesso revogado. Solicite um novo convite ao proprietário")
        return {"status": "approved", "business_name": row["business_name"]}
    if row["status"] != "pending":
        raise HTTPException(409, "Este convite não está mais disponível")
    if not isinstance(actor.account_id, UUID) or actor.role != "member":
        raise HTTPException(403, "Entre em uma conta pessoal para aceitar")
    await lock_account_business(db, actor.account_id)
    await ensure_membership_target(db, actor.account_id, row["tenant_id"])
    account = (await db.execute(text("""SELECT email,verified_at FROM panel_accounts
        WHERE id=:a FOR SHARE"""), {"a": actor.account_id})).mappings().first()
    if not account or account["verified_at"] is None or account["email"] != row["email"]:
        raise HTTPException(403, "Confirme o e-mail convidado na sua própria conta")
    link = (await db.execute(text("""SELECT * FROM professional_portal_links
        WHERE tenant_id=:t AND professional_id=:p FOR UPDATE"""),
        {"t": row["tenant_id"], "p": row["professional_id"]})).mappings().first()
    if not link or link["email"] != account["email"] or link["active"] or link["account_id"]:
        raise HTTPException(409, "O convite foi substituído ou o acesso já está vinculado")
    await require_active_professional(row["tenant_id"], row["professional_id"])
    existing = (await db.execute(text("""SELECT role,active FROM tenant_memberships
        WHERE tenant_id=:t AND account_id=:a FOR UPDATE"""),
        {"t": row["tenant_id"], "a": actor.account_id})).mappings().first()
    if existing and (existing["active"] or existing["role"] == "owner"):
        raise HTTPException(409, "Esta conta já possui um acesso a esta empresa")
    await db.execute(text("""INSERT INTO tenant_memberships
        (tenant_id,account_id,role,active) VALUES(:t,:a,'professional',true)
        ON CONFLICT(tenant_id,account_id) DO UPDATE SET
            role='professional',active=true,updated_at=now()"""),
        {"t": row["tenant_id"], "a": actor.account_id})
    await db.execute(text("""UPDATE professional_portal_links
        SET account_id=:a,active=true,updated_at=now()
        WHERE tenant_id=:t AND professional_id=:p"""),
        {"t": row["tenant_id"], "p": row["professional_id"], "a": actor.account_id})
    await membership_event(db, row["tenant_id"], actor.account_id, actor, "professional", True)
    updated = (await db.execute(text("""UPDATE management_team_invitations
        SET status='approved',claimed_by=:a,reviewed_by=created_by,
            version=version+1,updated_at=now() WHERE id=:id RETURNING *"""),
        {"a": actor.account_id, "id": row["id"]})).mappings().one()
    await notify_access(db, row["tenant_id"], actor.account_id, active=True,
        invitation_id=row["id"], dedupe_key="portal-approved:"+str(row["id"]))
    return {"status": "approved", "business_name": row["business_name"]}
