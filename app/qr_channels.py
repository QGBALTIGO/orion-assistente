"""Opt-in channel registry/broker. A channel credential never selects a tenant.

Register ``router`` explicitly in the API. New ingress handlers must call
``authenticated_channel(request, phone=...)`` and revalidate the identity after
long work, immediately before a domain mutation. Legacy QR routes are unchanged.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
from time import monotonic
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import Field, field_validator, model_validator
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app.catalog_schemas import StrictModel
from app.tenant_access import resolve_tenant_access

LEASE_SECONDS = 30
BASE = "/api/v1/tenants/{tenant_id}/qr-channels"
INTERNAL = "/internal/qr/channels/{channel_id}"
SAFE_FIELDS = ("id", "tenant_id", "owner_account_id", "provider", "label", "enabled", "assistant_enabled", "revision")


def enabled():
    if os.getenv("QR_CHANNELS_ENABLED", "0") != "1":
        raise HTTPException(423, "Canais QR adicionais não habilitados")


router = APIRouter(tags=["Canais QR"], dependencies=[Depends(enabled)])


def normalize_sender(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\+?55[1-9]\d(?:9\d{8}|[2-5]\d{7})", value):
        raise ValueError("Destinatário autorizado inválido")
    return value.lstrip("+")


class CreateChannel(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    provider: Literal["baileys"] = "baileys"
    allowed_senders: list[str] = Field(min_length=1, max_length=10)
    idempotency_key: UUID

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Nome inválido")
        return value

    @field_validator("allowed_senders")
    @classmethod
    def senders(cls, value):
        normalized = [normalize_sender(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Destinatário repetido")
        return sorted(normalized)


class ChannelState(StrictModel):
    expected_revision: int = Field(strict=True, ge=1)
    enabled: bool | None = Field(default=None, strict=True)
    assistant_enabled: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def explicit_change(self):
        if self.enabled is None and self.assistant_enabled is None:
            raise ValueError("Informe a alteração explícita do canal ou assistente")
        return self


class LeaseRequest(StrictModel):
    owner: UUID
    fence: int | None = Field(default=None, strict=True, ge=1)


class ChannelCommand(StrictModel):
    expected_revision: int = Field(strict=True, ge=1)


class ProvisionConnection(StrictModel):
    idempotency_key: UUID


class PrepareConnection(ChannelCommand):
    mode: Literal["reconnect", "recover"]


class ChannelPairCommand(ChannelCommand):
    phone: str = Field(strict=True, min_length=8, max_length=15)

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        if not re.fullmatch(r"[1-9][0-9]{7,14}", value):
            raise ValueError("Informe o número com código do país e DDD, somente dígitos")
        return value


@dataclass(frozen=True)
class ChannelIdentity:
    channel_id: UUID
    tenant_id: UUID
    owner_account_id: UUID
    provider: str
    fence: int
    lease_owner: UUID
    allowed_sender: bool
    assistant_enabled: bool = False
    legacy_transport: bool = False
    central_adopted: bool = False

    @property
    def context_namespace(self):
        return f"qr:{self.tenant_id}:{self.channel_id}"

    def conversation_namespace(self, phone):
        return phone if self.legacy_transport else f"{self.context_namespace}:{phone}"


def credential_hash(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 256:
        raise HTTPException(401, "Canal não autorizado")
    return hashlib.sha256(value.encode()).hexdigest()


def _cipher():
    try:
        return Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(503, "Proteção de credenciais ainda não configurada") from None


async def _owner_authorized(db, tenant_id, actor, *, lock=True):
    # Lock the current membership until the channel write commits. An earlier
    # panel/session role cannot authorize a revoked owner's operation.
    if actor.role not in {"member", "admin"} or not isinstance(actor.account_id, UUID):
        return False
    if actor.role == "admin":
        from app.legacy_admin_identity import PROVIDER, lookup_identity
        try:
            identity = await lookup_identity(db, account_id=actor.account_id)
        except ValueError:
            return False
        if (not identity or identity.get("id") != actor.account_id
                or identity.get("auth_provider") != PROVIDER or identity.get("role") != "admin"):
            return False
    role = (await db.execute(text("""SELECT role FROM tenant_memberships
        WHERE tenant_id=:tenant AND account_id=:account AND active""" + (" FOR UPDATE" if lock else "")),
        {"tenant": tenant_id, "account": actor.account_id})).scalar_one_or_none()
    return role == "owner"


async def _owner(db, tenant_id, actor):
    if not await _owner_authorized(db, tenant_id, actor):
        raise HTTPException(403, "Somente o proprietário autenticado")


def _canonical_candidate(rows, pointer):
    """Never use row ordering, labels or phone numbers to choose an identity."""
    from app.qr_legacy import is_alias
    if pointer is not None:
        return next((row for row in rows if row["id"] == pointer), None), "ready"
    if not rows:
        return None, "missing"
    aliases = [row for row in rows if is_alias(row["tenant_id"], row["id"])]
    active = [row for row in rows if row["enabled"]]
    if aliases and all(row["id"] == aliases[0]["id"] for row in active):
        return aliases[0], "ready"
    if len(rows) == 1:
        return rows[0], "ready"
    if len(active) == 1 and not aliases:
        return active[0], "ready"
    return None, "ambiguous"


async def _legacy_binding_valid(row):
    from app import qr_legacy
    try:
        token = os.getenv("QR_BRIDGE_TOKEN", "")
        return (qr_legacy.enabled() and qr_legacy.configured_tenant() == row["tenant_id"]
            and qr_legacy.is_alias(row["tenant_id"], row["id"])
            and secrets.compare_digest(credential_hash(token), row["credential_hash"])
            and secrets.compare_digest(_cipher().decrypt(row["credential_encrypted"].encode()).decode(), token)
            and await qr_legacy.registry_adopted(row["tenant_id"], row["id"]))
    except (HTTPException, InvalidToken, UnicodeError, ValueError):
        return False


async def _canonical(db, tenant_id, *, lock=False):
    tenant = (await db.execute(text("""SELECT id,status,whatsapp_channel_id FROM tenants WHERE id=:tenant"""
        + (" FOR UPDATE" if lock else "")), {"tenant": tenant_id})).mappings().one_or_none()
    if not tenant or tenant["status"] != "active":
        raise HTTPException(404, "Empresa indisponível")
    rows = (await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:tenant ORDER BY id"),
        {"tenant": tenant_id})).mappings().all()
    row, state = _canonical_candidate(rows, tenant["whatsapp_channel_id"])
    if row is None and state == "ready":
        state = "ambiguous"  # A broken binding must never provision a replacement.
    from app.qr_legacy import is_alias
    if row is not None and is_alias(tenant_id, row["id"]) and not await _legacy_binding_valid(row):
        return None, "ambiguous"
    if row is not None and lock and tenant["whatsapp_channel_id"] is None:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=:id WHERE id=:tenant"),
            {"id": row["id"], "tenant": tenant_id})
    return row, state


def _connection_dto(row, can_manage):
    from app.qr_legacy import is_alias
    item = {key: row[key] for key in SAFE_FIELDS}
    legacy = is_alias(row["tenant_id"], row["id"])
    own = can_manage and bool(row.get("_actor_owns", True))
    item.update(legacy_transport=legacy, can_manage=own,
        assistant_activation_allowed=own and bool(row["allowed_senders"]),
        capabilities={"start": own, "stop": own and not legacy, "qr": own, "pair": own,
                      "change_state": own and not legacy, "change_assistant": own and bool(row["allowed_senders"])})
    return item


def _connection_envelope(row, state, can_manage, actor):
    if row is not None:
        row = dict(row)
        from app.qr_legacy import is_alias
        if is_alias(row["tenant_id"], row["id"]) and actor.role != "admin":
            can_manage = False
        row["_actor_owns"] = row["owner_account_id"] == actor.account_id
    connection = _connection_dto(row, can_manage) if row else None
    if connection:
        connection["capabilities"]["prepare"] = bool(connection["legacy_transport"] and connection["can_manage"] and actor.role == "admin")
        connection["capabilities"]["recover"] = connection["capabilities"]["prepare"]
    caps = connection["capabilities"] if connection else dict.fromkeys(("start", "stop", "qr", "pair", "change_state", "change_assistant"), False)
    return {"connection": connection, "items": [connection] if connection else [],
        "canonical_id": str(row["id"]) if row else None, "canonical_state": state,
        "access": {"can_manage": can_manage},
        "capabilities": {**caps, "provision": can_manage and state == "missing", "edit_label": False, "edit_allowlist": False}}


async def _require_canonical(db, tenant_id, channel_id, *, lock=False):
    row, state = await _canonical(db, tenant_id, lock=lock)
    if state != "ready" or row is None or row["id"] != channel_id:
        raise HTTPException(409, "Conexão única indisponível; atualize o estado ou solicite revisão")
    return row


async def create_channel(db, tenant_id, payload, actor):
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    async with db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                         {"scope": f"qr-channel-create:{tenant_id}:{payload.idempotency_key}"})
        await _owner(db, tenant_id, actor)
        canonical, state = await _canonical(db, tenant_id, lock=True)
        # The owner row serializes retries for this tenant/account.
        existing = (await db.execute(text("""SELECT * FROM qr_channels
            WHERE tenant_id=:tenant AND idempotency_key=:key"""),
            {"tenant": tenant_id, "key": payload.idempotency_key})).mappings().one_or_none()
        if existing:
            if state != "ready" or canonical["id"] != existing["id"]:
                raise HTTPException(409, "Esta operação pertence ao histórico da conexão")
            if existing["request_fingerprint"] != fingerprint or existing["owner_account_id"] != actor.account_id:
                raise HTTPException(409, "Chave de operação já usada")
            return {**{k: existing[k] for k in SAFE_FIELDS}, "allowed_senders": existing["allowed_senders"],
                    "can_manage": True, "credential": None, "credential_issued": False}
        if state != "missing":
            raise HTTPException(409, "A empresa já possui uma conexão WhatsApp")
        credential = secrets.token_urlsafe(48)
        encrypted = _cipher().encrypt(credential.encode()).decode()
        row = (await db.execute(text("""INSERT INTO qr_channels
            (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint)
            VALUES (:tenant,:account,:provider,:label,:digest,:encrypted,CAST(:senders AS jsonb),:key,:fingerprint)
            RETURNING *"""), {"tenant": tenant_id, "account": actor.account_id, "provider": payload.provider,
            "label": payload.label, "digest": credential_hash(credential), "encrypted": encrypted, "senders": json.dumps(payload.allowed_senders),
            "key": payload.idempotency_key, "fingerprint": fingerprint})).mappings().one()
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=:id WHERE id=:tenant"),
            {"id": row["id"], "tenant": tenant_id})
        return {**{k: row[k] for k in SAFE_FIELDS}, "allowed_senders": row["allowed_senders"],
                "can_manage": True, "credential": credential, "credential_issued": True}


@router.post(BASE, status_code=201)
async def register(tenant_id: UUID, payload: CreateChannel, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with support_session_factory()() as db:
        return jsonable_encoder(await create_channel(db, tenant_id, payload, actor))


@router.get(BASE)
async def list_channels(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    access = await resolve_tenant_access(actor, tenant_id)
    can_manage = access.role == "owner" and actor.role == "member" and isinstance(actor.account_id, UUID)
    async with support_session_factory()() as db:
        if actor.role == "admin":
            can_manage = await _owner_authorized(db, tenant_id, actor, lock=False)
        row, state = await _canonical(db, tenant_id)
    return jsonable_encoder(_connection_envelope(row, state, can_manage, actor))


@router.post(BASE + "/provision")
async def provision_connection(tenant_id: UUID, payload: ProvisionConnection, response: Response,
                               actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with support_session_factory()() as db:
        async with db.begin():
            await _owner(db, tenant_id, actor)
            row, state = await _canonical(db, tenant_id, lock=True)
            if state == "ambiguous":
                raise HTTPException(409, "Conexões existentes precisam de revisão; nenhum vínculo foi alterado")
            if row is None:
                credential = secrets.token_urlsafe(48)
                row = (await db.execute(text("""INSERT INTO qr_channels
                    (tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,allowed_senders,idempotency_key,request_fingerprint)
                    VALUES (:tenant,:account,'baileys','WhatsApp',:digest,:encrypted,'[]'::jsonb,:key,:fingerprint) RETURNING *"""),
                    {"tenant": tenant_id, "account": actor.account_id, "digest": credential_hash(credential),
                     "encrypted": _cipher().encrypt(credential.encode()).decode(), "key": payload.idempotency_key,
                     "fingerprint": hashlib.sha256(b"orion:single-whatsapp-provision:v1").hexdigest()})).mappings().one()
                await db.execute(text("UPDATE tenants SET whatsapp_channel_id=:id WHERE id=:tenant"),
                    {"id": row["id"], "tenant": tenant_id})
            if row["owner_account_id"] != actor.account_id:
                raise HTTPException(409, "Conexão existente requer revisão do proprietário")
    return jsonable_encoder(_connection_envelope(row, "ready", True, actor))


@router.patch(BASE + "/{channel_id}")
async def change_state(tenant_id: UUID, channel_id: UUID, payload: ChannelState, actor: Actor = Depends(authenticate)):
    from app.qr_legacy import is_alias
    if is_alias(tenant_id, channel_id) and payload.enabled is not None:
        raise HTTPException(409, 'A sessão principal é preservada; use o controle da assistente ou do atendimento')
    async with support_session_factory()() as db:
        async with db.begin():
            await _owner(db, tenant_id, actor)
            current = await _require_canonical(db, tenant_id, channel_id, lock=True)
            if is_alias(tenant_id, channel_id) and actor.role != "admin":
                raise HTTPException(403, "A conexão preservada exige o administrador autenticado")
            if payload.assistant_enabled is True and not current["allowed_senders"]:
                raise HTTPException(409, "O atendimento privado ainda precisa de uma política autorizada")
            row = (await db.execute(text("""UPDATE qr_channels SET enabled=COALESCE(:enabled,enabled),
                assistant_enabled=COALESCE(:assistant,assistant_enabled),revision=revision+1,
                fence=fence+1,lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                WHERE tenant_id=:tenant AND id=:id AND owner_account_id=:account AND revision=:revision
                RETURNING id,tenant_id,owner_account_id,provider,label,enabled,assistant_enabled,revision,allowed_senders"""),
                {"tenant": tenant_id, "id": channel_id, "account": actor.account_id,
                 "revision": payload.expected_revision, "enabled": payload.enabled,
                 "assistant": payload.assistant_enabled})).mappings().one_or_none()
            if not row:
                raise HTTPException(409, "Canal alterado ou indisponível")
    return jsonable_encoder(_connection_dto(dict(row), True))


async def _channel(db, channel_id, credential, *, receipt_only=False):
    digest = credential_hash(credential)
    await _lock_membership(db, channel_id)
    row = (await db.execute(text("""SELECT c.*,clock_timestamp() AS server_now
        FROM qr_channels c JOIN tenant_memberships m ON m.tenant_id=c.tenant_id AND m.account_id=c.owner_account_id
        JOIN tenants t ON t.id=c.tenant_id
        WHERE c.id=:id AND m.active AND m.role='owner'
          AND (:receipt OR (c.enabled AND t.status='active'))
        FOR UPDATE OF c"""), {"id": channel_id, "receipt": receipt_only})).mappings().one_or_none()
    if not row or not secrets.compare_digest(row["credential_hash"], digest):
        raise HTTPException(401, "Canal não autorizado")
    return row


async def _lock_membership(db, channel_id):
    # Same order as owner configuration: membership before channel. Never hold
    # these locks while waiting on a provider or a WhatsApp socket.
    await db.execute(text("""SELECT m.account_id FROM tenant_memberships m
        JOIN qr_channels c ON c.tenant_id=m.tenant_id AND c.owner_account_id=m.account_id
        WHERE c.id=:id FOR UPDATE OF m"""), {"id": channel_id})


def _current(row, owner, fence):
    if (row["lease_owner"] != owner or row["fence"] != fence or not row["lease_expires_at"]
            or row["lease_expires_at"] <= row["server_now"]):
        raise HTTPException(409, "Posse do canal expirada")


async def lease_operation(db, channel_id, credential, payload, operation):
    enabled()
    if operation not in {"claim", "renew", "release", "check", "inspect", "receipt"}:
        raise HTTPException(404)
    async with db.begin():
        row = await _channel(db, channel_id, credential, receipt_only=operation == "receipt")
        if operation == "receipt":
            # Historical receipt lookup grants no lease, current AI identity,
            # sender policy, start permission or send permission.
            return {"channel_id": str(row["id"]), "tenant_id": str(row["tenant_id"]),
                    "provider": row["provider"], "read_only": True}
        live = row["lease_expires_at"] and row["lease_expires_at"] > row["server_now"]
        if operation == "claim":
            # Keep existing ambiguous leases alive; never create a second socket.
            await _require_canonical(db, row["tenant_id"], channel_id, lock=True)
            if live:
                if row["lease_owner"] != payload.owner:
                    raise HTTPException(409, "Canal em uso")
            else:
                row = (await db.execute(text("""UPDATE qr_channels SET lease_owner=:owner,fence=fence+1,
                    lease_expires_at=clock_timestamp()+interval '30 seconds',updated_at=clock_timestamp()
                    WHERE id=:id RETURNING *,clock_timestamp() AS server_now"""),
                    {"owner": payload.owner, "id": channel_id})).mappings().one()
        elif operation != "inspect":
            _current(row, payload.owner, payload.fence)
            if operation in {"renew", "release"}:
                row = (await db.execute(text("""UPDATE qr_channels SET
                    lease_owner=CASE WHEN :release THEN NULL ELSE lease_owner END,
                    lease_expires_at=CASE WHEN :release THEN NULL ELSE clock_timestamp()+interval '30 seconds' END,
                    updated_at=clock_timestamp() WHERE id=:id RETURNING *,clock_timestamp() AS server_now"""),
                    {"id": channel_id, "release": operation == "release"})).mappings().one()
        remaining = max(0, int((row["lease_expires_at"]-row["server_now"]).total_seconds()*1000)) if row["lease_expires_at"] else 0
        from app import qr_legacy
        legacy = qr_legacy.is_alias(row['tenant_id'], row['id'])
        if legacy and (not qr_legacy.enabled() or qr_legacy.configured_tenant() != row['tenant_id']):
            raise HTTPException(423, 'Transição da sessão principal não habilitada')
        central = legacy and await qr_legacy.registry_adopted(row['tenant_id'], row['id'])
        return {"channel_id": str(row["id"]), "tenant_id": str(row["tenant_id"]),
                "owner_account_id": str(row["owner_account_id"]), "provider": row["provider"],
                "owner": str(payload.owner), "fence": row["fence"], "expires_in_ms": remaining,
                "allowed_senders": row["allowed_senders"], "assistant_enabled": row["assistant_enabled"],
                "legacy_transport": legacy,
                "verified_self_phone":qr_legacy.verified_self_phone(row) if legacy else None,
                **({"inbox_control": True} if central or os.getenv("INBOX_CONTROL_ENABLED", "0") == "1" else {})}


@router.post(INTERNAL + "/lease/{operation}")
async def channel_lease(channel_id: UUID, operation: str, payload: LeaseRequest, request: Request):
    async with support_session_factory()() as db:
        return await lease_operation(db, channel_id, request.headers.get("x-orion-channel-key"), payload, operation)


async def authenticated_channel(request: Request, *, phone: str | None = None) -> ChannelIdentity:
    enabled()
    try:
        channel_id = UUID(str(request.path_params["channel_id"]))
        owner = UUID(request.headers["x-orion-channel-owner"])
        fence = int(request.headers["x-orion-channel-fence"])
        if fence < 1 or fence > 2**63-1:
            raise ValueError()
        sender=None
        if phone is not None:
            try:sender=normalize_sender(phone)
            except ValueError:
                if not isinstance(phone,str):raise
                sender=phone.removeprefix('+')
                if sender!=os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','') or not re.fullmatch(r'55[1-9][0-9][2-9][0-9]{7}',sender):raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Canal não autorizado") from None
    async with support_session_factory()() as db:
        async with db.begin():
            row = await _channel(db, channel_id, request.headers.get("x-orion-channel-key"))
            _current(row, owner, fence)
            if sender is not None:
                try:normalize_sender(sender)
                except ValueError:
                    from app.qr_legacy import verified_self_phone
                    if sender!=verified_self_phone(row):raise HTTPException(403,'Identidade do número não autorizada') from None
            allowed = sender is not None and sender in row["allowed_senders"]
            if sender is not None and not allowed:
                raise HTTPException(403, "Remetente não autorizado neste canal")
            from app import qr_legacy
            legacy = qr_legacy.is_alias(row['tenant_id'], row['id'])
            if legacy and (not qr_legacy.enabled() or qr_legacy.configured_tenant() != row['tenant_id']):
                raise HTTPException(423, 'Transição da sessão principal não habilitada')
            # The runtime connection below has the tenant RLS scope; support
            # receives no additional journal privileges.
            central = False
    if legacy:
        from app.database import get_session_factory, tenant_transaction
        async with get_session_factory()() as journal:
            async with tenant_transaction(journal, row['tenant_id']):
                central = await qr_legacy.adopted(journal, row['tenant_id'], row['id'])
    return ChannelIdentity(row["id"], row["tenant_id"], row["owner_account_id"], row["provider"],
        fence, owner, allowed, row["assistant_enabled"], legacy, central)


async def outbound_channel_credentials(tenant_id: UUID, channel_id: UUID, *, read_only: bool = False) -> dict:
    """Server-only; caller must separately prove consent and intent before send.

    No HTTP endpoint exposes this result. The bridge checks the credential,
    allowlist and current lease again; this lookup alone is never send consent.
    """
    enabled()
    if type(read_only) is not bool:
        raise ValueError("Explicit receipt-only mode required")
    async with support_session_factory()() as db:
        async with db.begin():
            await _lock_membership(db, channel_id)
            row = (await db.execute(text("""SELECT c.* FROM qr_channels c
                JOIN tenant_memberships m ON m.tenant_id=c.tenant_id AND m.account_id=c.owner_account_id
                JOIN tenants t ON t.id=c.tenant_id WHERE c.id=:id AND c.tenant_id=:tenant
                AND m.active AND m.role='owner' AND (:receipt OR (c.enabled AND t.status='active'))
                FOR UPDATE OF c"""), {"id": channel_id, "tenant": tenant_id, "receipt": read_only})).mappings().one_or_none()
            if not row:
                raise HTTPException(403, "Canal não autorizado")
            try:
                credential = _cipher().decrypt(row["credential_encrypted"].encode()).decode()
            except (InvalidToken, UnicodeError):
                raise HTTPException(503, "Credencial indisponível") from None
            if not secrets.compare_digest(credential_hash(credential), row["credential_hash"]):
                raise HTTPException(503, "Credencial indisponível")
            return {"channel_id": str(channel_id), "tenant_id": str(tenant_id),
                    "provider": row["provider"], "key": credential,
                    "read_only": read_only,
                    "path": f"/internal/channels/{channel_id}/" + ("operations" if read_only else "send")}


async def _owned_channel(tenant_id, channel_id, actor, expected_revision=None):
    async with support_session_factory()() as db:
        async with db.begin():
            await _owner(db, tenant_id, actor)
            from app.qr_legacy import is_alias
            if is_alias(tenant_id, channel_id) and actor.role != "admin":
                raise HTTPException(403, "A conexão preservada exige o administrador autenticado")
            await _require_canonical(db, tenant_id, channel_id)
            row = (await db.execute(text("""SELECT c.* FROM qr_channels c JOIN tenants t ON t.id=c.tenant_id
                WHERE c.tenant_id=:tenant AND c.id=:id AND c.owner_account_id=:account AND t.status='active'
                FOR UPDATE OF c"""), {"tenant": tenant_id, "id": channel_id, "account": actor.account_id})).mappings().one_or_none()
            if not row:
                raise HTTPException(404, "Canal indisponível")
            if expected_revision is not None and row["revision"] != expected_revision:
                raise HTTPException(409, "Canal alterado; atualize a lista")
            return dict(row)


async def _bridge_control(row, action, method, payload=None):
    """Private fixed destination; no browser credential, URL or redirect input."""
    raw_url = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
    from app.qr_legacy import is_alias
    legacy = is_alias(row["tenant_id"], row["id"])
    if legacy:
        if not await _legacy_binding_valid(row):
            raise HTTPException(409, "Vínculo da conexão preservada indisponível")
        if action not in {"status", "start", "qr", "pair", "reconnect", "recover"}:
            raise HTTPException(409, "Operação indisponível para a conexão preservada")
    try:
        parsed = urlsplit(raw_url)
        valid = (parsed.scheme == "http" and re.fullmatch(r"[a-z0-9-]+\.railway\.internal", parsed.hostname or "")
                 and parsed.port == 3000 and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "Integração WhatsApp ainda não configurada")
    try:
        key = _cipher().decrypt(row["credential_encrypted"].encode()).decode()
        if not secrets.compare_digest(credential_hash(key), row["credential_hash"]):
            raise ValueError()
    except (InvalidToken, UnicodeError, ValueError):
        raise HTTPException(503, "Credencial indisponível") from None
    try:
        async with httpx.AsyncClient(timeout=18, trust_env=False, follow_redirects=False) as client:
            path = ("/internal/" + ("status" if action == "start" else action)) if legacy else f"/internal/channels/{row['id']}/{action}"
            result = await client.request("GET" if legacy and action == "start" else method, raw_url + path,
                headers={"X-Orion-Bridge-Key" if legacy else "X-Orion-Channel-Key": key,
                    **({"X-Orion-Canonical-Prepare": "1"} if legacy and action in {"reconnect", "recover"} else {})},
                json=(payload if payload is not None else {}) if method == "POST" and not (legacy and action == "start") else None)
        if result.status_code not in {200, 202}:
            raise HTTPException(409, "Operação do canal indisponível; atualize o estado")
        if len(result.content) > 256000:
            raise ValueError()
        data = result.json()
        if not isinstance(data, dict):
            raise ValueError()
        if legacy and action in {"qr", "pair"}:
            expiry = data.get("expiresIn", 0)
            if type(expiry) is not int:
                raise ValueError()
            data["expires_in_ms"] = expiry * 1000
        if legacy and data.get("status") == "connected":
            import hmac
            phone = data.get("phone")
            if not isinstance(phone, str) or not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
                data["phone"] = None
            else:
                proof = hmac.new(key.encode(), ("orion:preserved-primary-session:v1\0" + phone).encode(), hashlib.sha256).hexdigest()
                if not secrets.compare_digest(proof, row["request_fingerprint"]):
                    raise HTTPException(409, "A identidade da sessão preservada precisa de revisão")
        return data
    except (httpx.RequestError, ValueError):
        raise HTTPException(503, "Serviço WhatsApp temporariamente inacessível") from None


def _control_result(row, data, action):
    result = {"channel_id": str(row["id"]), "revision": row["revision"],
              "enabled": row["enabled"], "assistant_enabled": row["assistant_enabled"]}
    if action == "qr":
        image, expires = data.get("image"), data.get("expires_in_ms", 0)
        if image is not None and (not isinstance(image, str) or len(image) > 240000
                or not re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/]+=*", image)):
            raise HTTPException(503, "QR temporariamente indisponível")
        if type(expires) is not int or not 0 <= expires <= 45000 or (image is not None and expires == 0):
            raise HTTPException(503, "QR temporariamente indisponível")
        return {**result, "image": image, "expires_in_ms": expires if image else 0}
    if action == "pair":
        code, expires = data.get("code"), data.get("expires_in_ms")
        if (data.get("status") != "pairing" or not isinstance(code, str)
                or not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", code)
                or type(expires) is not int or not 1 <= expires <= 90000):
            raise HTTPException(503, "Código de conexão temporariamente indisponível")
        return {**result, "status": "pairing", "code": code, "expires_in_ms": expires}
    status = data.get("status")
    if status not in {"stopped", "starting", "error", "connecting", "reconnecting", "qr_ready", "qr_delayed", "qr_expired", "connected", "disabled", "pairing", "pair_expired", "pair_failed", "logged_out"}:
        raise HTTPException(503, "Estado do canal indisponível")
    capabilities = data.get("capabilities")
    available = isinstance(capabilities, dict) and capabilities.get("pair") is True and row["enabled"] is True
    phone = data.get("phone")
    verified = status == "connected" and isinstance(phone, str) and bool(re.fullmatch(r"[1-9][0-9]{7,14}", phone)) and data.get("phone_verified") is True
    return {**result, "status": status, "capabilities": {"pair": available}, "phone": phone if verified else None, "phone_verified": verified}


async def _control(tenant_id, channel_id, actor, action, expected_revision=None):
    row = await _owned_channel(tenant_id, channel_id, actor, expected_revision)
    from app.qr_legacy import is_alias
    if is_alias(tenant_id, channel_id) and action == 'stop':
        raise HTTPException(409, 'A conexão preservada não pode ser encerrada por este controle')
    if not row["enabled"]:
        if action == "start":
            raise HTTPException(409, "Habilite a conexão deste canal antes de iniciar")
        # Disabled is configuration, not a fabricated socket status. Existing
        # sockets are fenced immediately and closed by the bounded lease guard.
        data = {"image": None, "expires_in_ms": 0} if action == "qr" else {"status": "disabled"}
    else:
        data = await _bridge_control(row, action, "GET" if action in {"status", "qr"} else "POST")
    # Never return a QR obtained for an account/configuration revoked in flight.
    await _owned_channel(tenant_id, channel_id, actor, row["revision"])
    return _control_result(row, data, action)


@router.get(BASE + "/{channel_id}/{action}")
async def read_control(tenant_id: UUID, channel_id: UUID, action: str, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    if action not in {"status", "qr"}:
        raise HTTPException(404)
    return await _control(tenant_id, channel_id, actor, action)


@router.post(BASE + "/{channel_id}/pair")
async def pair_control(tenant_id: UUID, channel_id: UUID, payload: ChannelPairCommand,
                       response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    row = await _owned_channel(tenant_id, channel_id, actor, payload.expected_revision)
    if not row["enabled"]:
        raise HTTPException(409, "Habilite e inicie a conexão deste canal antes de gerar o código")
    # Runtime discovery is read-only and fail-closed with an older bridge.
    # Pairing never enables, starts or replaces a preserved registered socket.
    status = await _bridge_control(row, "status", "GET")
    await _owned_channel(tenant_id, channel_id, actor, row["revision"])
    if not _control_result(row, status, "status")["capabilities"]["pair"]:
        raise HTTPException(409, "Conexão por código indisponível; prepare o canal e atualize o estado")
    started = monotonic()
    data = await _bridge_control(row, "pair", "POST", {"phone": payload.phone})
    # Never return a code obtained for a revoked owner or stale revision.
    await _owned_channel(tenant_id, channel_id, actor, row["revision"])
    result = _control_result(row, data, "pair")
    # Conservatively include transit/provider time and the ownership recheck;
    # a delayed response must never extend the provider's bounded validity.
    result["expires_in_ms"] -= max(0, int((monotonic()-started)*1000))
    if result["expires_in_ms"] <= 0:
        raise HTTPException(409, "O código expirou; atualize o estado do canal")
    return result


@router.post(BASE + "/{channel_id}/prepare")
async def prepare_control(tenant_id: UUID, channel_id: UUID, payload: PrepareConnection,
                          response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    from app.qr_legacy import is_alias
    row = await _owned_channel(tenant_id, channel_id, actor, payload.expected_revision)
    # _owned_channel freshly verifies the native admin identity and ownership.
    if actor.role != "admin" or not is_alias(tenant_id, channel_id) or not await _legacy_binding_valid(row):
        raise HTTPException(403, "Preparação da conexão não autorizada")
    state = await _bridge_control(row, "status", "GET")
    allowed = {"logged_out", "pair_failed"} if payload.mode == "recover" else {"qr_ready", "qr_expired", "qr_delayed", "pair_expired", "stopped"}
    if state.get("status") not in allowed:
        raise HTTPException(409, "O estado atual não permite preparar uma nova tentativa")
    await _owned_channel(tenant_id, channel_id, actor, row["revision"])
    data = await _bridge_control(row, payload.mode, "POST")
    await _owned_channel(tenant_id, channel_id, actor, row["revision"])
    return _control_result(row, data, "status")


@router.post(BASE + "/{channel_id}/{action}")
async def command_control(tenant_id: UUID, channel_id: UUID, action: str, payload: ChannelCommand,
                          response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    if action not in {"start", "stop"}:
        raise HTTPException(404)
    return await _control(tenant_id, channel_id, actor, action, payload.expected_revision)


@router.get(INTERNAL + "/identity")
async def channel_identity(request: Request):
    identity = await authenticated_channel(request)
    return jsonable_encoder(identity)


@router.get('/internal/qr/legacy/identity', include_in_schema=False)
async def legacy_identity(request: Request):
    from app.qr_legacy import bootstrap
    return await bootstrap(request)
