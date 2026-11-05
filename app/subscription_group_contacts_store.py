"""Private expiring snapshots and atomic additive imports into local groups."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from pydantic import Field, StrictInt, ValidationError, model_validator
from sqlalchemy import text

from app import qr_channels as channels
from app import subscription_chatbot_store as shared
from app import subscription_campaign_store as campaigns
from app import subscription_devices as devices
from app import subscription_group_contacts_schemas as schemas

FEATURE = "group_contacts.manage"
ORIGIN = "group_contacts"
PURPOSE = "orion.subscription.group-contacts.preview.v1"
encode, fingerprint, clock = shared.encode, shared.fingerprint, campaigns.clock
ERRORS = {
    "GROUP_CONTACTS_INVALID": "Confira o grupo e os contatos selecionados.",
    "GROUP_CONTACTS_LIMIT": "A seleção ou o grupo ultrapassa os limites disponíveis.",
    "GROUP_CONTACTS_NOT_FOUND": "Grupo ou pedido não encontrado nesta empresa.",
    "GROUP_CONTACTS_PREVIEW_INVALID": "A prévia não está disponível neste contexto. Carregue os membros novamente.",
    "GROUP_CONTACTS_PREVIEW_EXPIRED": "A prévia expirou. Carregue os membros novamente.",
    "GROUP_CONTACTS_CONTEXT_CHANGED": "O dispositivo mudou. Atualize e carregue os membros novamente.",
    "GROUP_CONTACTS_DEVICE_UNAVAILABLE": "Conecte um dispositivo desta empresa para consultar seus grupos.",
    "GROUP_CONTACTS_BRIDGE_UNAVAILABLE": "Não foi possível consultar os grupos do dispositivo. Confira sua conexão.",
    "GROUP_CONTACTS_CONFLICT": "O grupo local mudou. Atualize os grupos antes de salvar.",
    "GROUP_CONTACTS_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
}


def fail(code, status=409, **extra):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "Operação indisponível."), **extra},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


def device_version(row):
    return fingerprint({key: row[key] for key in (
        "id", "tenant_id", "channel_id", "owner_account_id", "generation", "revision")})


def transport_scope(row):
    return {"device_id": str(row["id"]), "generation": str(row["generation"]), "settings_revision": row["revision"]}


async def scope(db, tenant, identity, actor):
    try:
        row, transport = await devices._device(db, tenant, identity, actor=actor)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise fail("GROUP_CONTACTS_NOT_FOUND", 404) from None
        if exc.status_code == 409:
            raise fail("GROUP_CONTACTS_CONTEXT_CHANGED") from None
        raise
    if (row["status"] in {"disabled", "removing", "removed"} or not transport["enabled"]
            or transport["allowed_senders"]):
        raise fail("GROUP_CONTACTS_DEVICE_UNAVAILABLE")
    return row, transport


def public_device(row, *, available=True, blocked_reason=None):
    return {"id": row["id"], "name": row["name"], "status": row["status"], "available": available,
            "blocked_reason": blocked_reason, "version": device_version(row)}


async def device_options(db, tenant, actor):
    items = await campaigns.device_options(db, tenant, actor)
    rows = (await db.execute(text("""SELECT * FROM subscription_devices WHERE tenant_id=:t
        AND owner_account_id=:a AND removed_at IS NULL ORDER BY created_at,id"""),
        {"t": tenant, "a": actor.account_id})).mappings().all()
    lookup = {row["id"]: row for row in rows}
    return [public_device(lookup[item["id"]], available=item["available"],
                          blocked_reason=None if item["available"] else ERRORS["GROUP_CONTACTS_DEVICE_UNAVAILABLE"])
            for item in items if item["id"] in lookup]


class Preview(schemas.BridgeMembers):
    purpose: Literal["orion.subscription.group-contacts.preview.v1"]
    tenant_id: UUID
    actor_account_id: UUID
    channel_id: UUID
    issued_at: StrictInt = Field(ge=1)
    expires_at: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def lifetime(self):
        if self.expires_at - self.issued_at != schemas.PREVIEW_SECONDS:
            raise ValueError("Invalid preview lifetime")
        return self


def create_preview(tenant, actor, row, members, now):
    issued = int(now.timestamp())
    snapshot = Preview.model_validate({**members.model_dump(mode="json"), "purpose": PURPOSE,
        "tenant_id": str(tenant), "actor_account_id": str(actor.account_id), "channel_id": str(row["channel_id"]),
        "issued_at": issued, "expires_at": issued + schemas.PREVIEW_SECONDS})
    raw = encode(snapshot.model_dump(mode="json")).encode("utf-8")
    if len(raw) > schemas.MAX_PREVIEW_BYTES:
        raise fail("GROUP_CONTACTS_LIMIT", 422)
    token = channels._cipher().encrypt(raw).decode("ascii")
    if len(token) > schemas.MAX_TOKEN:
        raise fail("GROUP_CONTACTS_LIMIT", 422)
    return token, datetime.fromtimestamp(snapshot.expires_at, timezone.utc)


def decode_preview(token, tenant, actor, now):
    try:
        raw = channels._cipher().decrypt(token.encode("ascii"))
        if len(raw) > schemas.MAX_PREVIEW_BYTES:
            raise ValueError()
        snapshot = Preview.model_validate_json(raw)
    except (InvalidToken, ValueError, UnicodeError, ValidationError):
        raise fail("GROUP_CONTACTS_PREVIEW_INVALID", 422) from None
    if snapshot.tenant_id != tenant or snapshot.actor_account_id != actor.account_id:
        raise fail("GROUP_CONTACTS_PREVIEW_INVALID", 422)
    timestamp = now.timestamp()
    if snapshot.issued_at > timestamp + 1:
        raise fail("GROUP_CONTACTS_PREVIEW_INVALID", 422)
    if timestamp >= snapshot.expires_at:
        raise fail("GROUP_CONTACTS_PREVIEW_EXPIRED", 410)
    return snapshot


def validate_preview_scope(snapshot, row):
    if (snapshot.device_id != row["id"] or snapshot.generation != row["generation"]
            or snapshot.channel_id != row["channel_id"] or snapshot.settings_revision != row["revision"]):
        raise fail("GROUP_CONTACTS_CONTEXT_CHANGED")


async def request_row(db, tenant, actor, key, *, mutation=False):
    row = (await db.execute(text("""SELECT * FROM subscription_campaign_requests
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": key})).mappings().one_or_none()
    if row is None:
        return None
    if row["actor_account_id"] != actor.account_id:
        raise fail("GROUP_CONTACTS_NOT_FOUND", 404)
    if (row["kind"] not in {"group_create", "group_update"} or row["status"] != "succeeded"
            or not isinstance(row["result"], dict) or row["result"].get("origin") != ORIGIN):
        raise fail("GROUP_CONTACTS_IDEMPOTENCY_CONFLICT" if mutation else "GROUP_CONTACTS_NOT_FOUND",
                   409 if mutation else 404)
    return dict(row)


def operation_dto(row, now, *, replayed=False):
    return campaigns.operation_dto(row, now, replayed=replayed)


async def import_contacts(db, tenant, actor, payload):
    values = {"origin": ORIGIN, **payload.model_dump(mode="json")}
    existing = await request_row(db, tenant, actor, payload.idempotency_key, mutation=True)
    if existing:
        if existing["request_fingerprint"] != fingerprint(values):
            raise fail("GROUP_CONTACTS_IDEMPOTENCY_CONFLICT")
        return operation_dto(existing, await clock(db), replayed=True)
    now = await clock(db)
    snapshot = decode_preview(payload.preview_token, tenant, actor, now)
    row, _ = await scope(db, tenant, snapshot.device_id, actor)
    validate_preview_scope(snapshot, row)
    members = {member.phone: member for member in snapshot.members}
    if any(phone not in members for phone in payload.phones):
        raise fail("GROUP_CONTACTS_PREVIEW_INVALID", 422)
    selected = [members[phone] for phone in payload.phones]
    phones = [member.phone[1:] for member in selected]
    identity, kind = payload.target_group_id, "group_update" if payload.target_group_id else "group_create"
    if identity is None:
        count = (await db.execute(text("""SELECT count(*) FROM subscription_campaign_groups
            WHERE tenant_id=:t AND archived_at IS NULL"""), {"t": tenant})).scalar_one()
        if count >= schemas.MAX_GROUPS:
            raise fail("GROUP_CONTACTS_LIMIT", 422)
        identity = uuid4()
        await db.execute(text("""INSERT INTO subscription_campaign_groups(id,tenant_id,name)
            VALUES(:id,:t,:name)"""), {"id": identity, "t": tenant, "name": payload.new_group_name})
        active_count, current = 0, {}
    else:
        local = (await db.execute(text("""SELECT * FROM subscription_campaign_groups
            WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one_or_none()
        if local is None or local["archived_at"] is not None:
            raise fail("GROUP_CONTACTS_NOT_FOUND", 404)
        if local["version"] != payload.expected_version:
            extra = {}
            if payload.expected_version < local["version"]:
                extra["rejection"] = {"reason": "stale_group_version", "idempotency_key": str(payload.idempotency_key),
                    "group_id": str(identity), "expected_version": payload.expected_version}
            raise fail("GROUP_CONTACTS_CONFLICT", **extra)
        active_count = (await db.execute(text("""SELECT count(*) FROM subscription_campaign_members
            WHERE tenant_id=:t AND group_id=:g AND removed_at IS NULL"""), {"t": tenant, "g": identity})).scalar_one()
        current = {item["phone"]: item for item in (await db.execute(text("""SELECT phone,removed_at
            FROM subscription_campaign_members WHERE tenant_id=:t AND group_id=:g
              AND phone=ANY(CAST(:phones AS varchar[]))"""), {"t": tenant, "g": identity, "phones": phones})).mappings().all()}
    added = sum(phone not in current for phone in phones)
    restored = sum(phone in current and current[phone]["removed_at"] is not None for phone in phones)
    unchanged = len(phones) - added - restored
    if active_count + added + restored > schemas.MAX_MEMBERS:
        raise fail("GROUP_CONTACTS_LIMIT", 422)
    if kind == "group_update" and added + restored:
        if local["version"] == schemas.MAX_VERSION:
            raise fail("GROUP_CONTACTS_LIMIT", 422)
        await db.execute(text("""UPDATE subscription_campaign_groups SET version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": identity})
    if added + restored:
        # Only selected removed memberships return. Useful existing names,
        # unselected members and global opt-outs are never overwritten.
        await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
            SELECT :t,:g,x.phone,x.name FROM jsonb_to_recordset(CAST(:members AS jsonb))
              AS x(phone varchar(15),name varchar(100))
            ON CONFLICT(tenant_id,group_id,phone) DO UPDATE
              SET removed_at=NULL,updated_at=clock_timestamp()
              WHERE subscription_campaign_members.removed_at IS NOT NULL"""),
            {"t": tenant, "g": identity, "members": encode([
                {"phone": member.phone[1:], "name": member.name or member.phone} for member in selected])})
        from app.subscription_campaign_runtime import invalidate_audiences
        from app.subscription_schedule_runtime import invalidate_schedule_contacts
        from app.subscription_bulk_runtime import invalidate_bulk_contacts
        await invalidate_audiences(db, tenant)
        await invalidate_schedule_contacts(db, tenant)
        await invalidate_bulk_contacts(db, tenant)
    group = (await campaigns.group_summaries(db, tenant, [identity]))[0]
    result = {"origin": ORIGIN, "group": group, "selected_count": len(selected), "added_count": added,
              "existing_count": unchanged, "restored_count": restored}
    saved = await campaigns.save_request(db, tenant, actor, payload.idempotency_key, kind, values,
                                        group_id=identity, result=result, now=now)
    return operation_dto(saved, now)
