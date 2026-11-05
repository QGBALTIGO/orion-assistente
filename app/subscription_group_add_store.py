"""Owner-bound previews and immutable membership work; no messaging ledger is used."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import Actor
from app import subscription_chatbot_store as shared
from app import subscription_group_contacts_store as contacts
from app import subscription_campaign_store as campaigns
from app import subscription_schedule_store as catalog
from app import subscription_group_add_schemas as schemas

FEATURE = "group_add.manage"
encode, fingerprint, clock = shared.encode, shared.fingerprint, campaigns.clock
channels, devices = contacts.channels, contacts.devices
ERRORS = {
    "GROUP_ADD_INVALID": "Confira o grupo, a origem e os contatos selecionados.",
    "GROUP_ADD_LIMIT": "A seleção ultrapassa os limites. Reduza os contatos e carregue novamente.",
    "GROUP_ADD_NOT_FOUND": "Registro não encontrado nesta empresa.",
    "GROUP_ADD_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
    "GROUP_ADD_PREVIEW_EXPIRED": "A prévia expirou. Carregue os contatos novamente.",
    "GROUP_ADD_CONTEXT_CHANGED": "O dispositivo ou a autorização mudou. Carregue novamente.",
    "GROUP_ADD_DEVICE_UNAVAILABLE": "Conecte um dispositivo desta empresa para consultar ou adicionar membros.",
    "GROUP_ADD_ADMIN_REQUIRED": "Você precisa ser administrador do grupo de destino.",
    "GROUP_ADD_SOURCE_CHANGED": "A origem dos contatos mudou. Carregue os contatos novamente.",
    "GROUP_ADD_NO_ELIGIBLE": "Nenhum contato selecionado pode ser adicionado.",
    "GROUP_ADD_BRIDGE_UNAVAILABLE": "Não foi possível consultar o WhatsApp agora. Tente carregar novamente.",
    "GROUP_ADD_BUSY": "Uma inclusão anterior neste dispositivo aguarda conclusão ou confirmação. Acompanhe o pedido anterior.",
    "GROUP_ADD_OPTED_OUT": "A autorização deste contato foi revogada.",
    "GROUP_ADD_PROVIDER_REFUSED": "O WhatsApp não adicionou este contato. A privacidade pode impedir a inclusão.",
    "GROUP_ADD_OUTCOME_UNKNOWN": "Não foi possível confirmar o resultado. Não houve repetição automática.",
    "GROUP_ADD_STOPPED": "A inclusão foi interrompida antes deste contato.",
}


def fail(code, status=409):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "Operação indisponível.")},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


async def scope(db, tenant, identity, actor):
    try:
        return await contacts.scope(db, tenant, identity, actor, require_connected=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise fail("GROUP_ADD_NOT_FOUND", 404) from None
        if exc.status_code == 409:
            raise fail("GROUP_ADD_DEVICE_UNAVAILABLE") from None
        raise


async def device_options(db, tenant, actor):
    items = await contacts.device_options(db, tenant, actor)
    return [{**item, "blocked_reason": None if item["available"] else ERRORS["GROUP_ADD_DEVICE_UNAVAILABLE"]}
            for item in items]


def bounded(value, maximum=16_777_216):
    if len(encode(value).encode("utf-8")) > maximum:
        raise fail("GROUP_ADD_LIMIT", 422)


def safe_name(value):
    # Existing CRM names may be longer than the provider's 100 UTF-16 units.
    # Only the display label is bounded; identity is always the exact phone.
    value = value if isinstance(value, str) else ""
    value = "".join(c for c in value if ord(c) >= 32 and ord(c) != 127).strip()
    while len(value.encode("utf-16-le")) // 2 > 100:
        value = value[:-1]
    return value


LOCAL_SQL = """WITH all_sources AS (
 SELECT substring(c.whatsapp_e164 FROM 2) AS phone,c.name,c.created_at,0 AS priority,c.id::text AS ordering,
   NULL::uuid AS group_id,jsonb_build_object('kind','customer','id',c.id,
     'phone',substring(c.whatsapp_e164 FROM 2),'version',c.version) AS origin
 FROM customers c WHERE c.tenant_id=:t AND c.active AND c.deleted_at IS NULL
   AND c.whatsapp_e164 ~ '^\\+[1-9][0-9]{7,14}$'
 UNION ALL
 SELECT m.phone,m.name,m.created_at,1,g.id::text,g.id,
   jsonb_build_object('kind','group_member','group_id',g.id,'phone',m.phone,
      'created_at',m.created_at,'updated_at',m.updated_at)
 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
   ON g.tenant_id=m.tenant_id AND g.id=m.group_id
 WHERE m.tenant_id=:t AND m.removed_at IS NULL AND g.archived_at IS NULL
   AND m.phone ~ '^[1-9][0-9]{7,14}$'
), selected_sources AS (
 SELECT s.* FROM all_sources s
 WHERE (CAST(:group_id AS uuid) IS NULL OR s.group_id=CAST(:group_id AS uuid))
   AND (NOT :without_group OR NOT EXISTS(SELECT 1 FROM all_sources g
      WHERE g.phone=s.phone AND g.group_id IS NOT NULL))
), grouped AS (
 SELECT phone,array_agg(name ORDER BY priority,ordering) AS names,
   jsonb_agg(origin ORDER BY priority,ordering) AS origins,min(created_at) AS first_created,
   count(*) AS source_count
 FROM selected_sources GROUP BY phone
 HAVING :search='' OR bool_or(position(lower(:search) IN lower(name))>0) OR position(:search IN ('+'||phone))>0
)
 SELECT g.*,COALESCE(p.version,0) AS permission_version,COALESCE(p.revoked,false) AS blocked
 FROM grouped g LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=:t AND p.phone=g.phone
 WHERE (CAST(:created_from AS date) IS NULL OR first_created>=
     (CAST(:created_from AS date)::timestamp AT TIME ZONE :zone))
   AND (CAST(:created_to AS date) IS NULL OR first_created<
     ((CAST(:created_to AS date)+1)::timestamp AT TIME ZONE :zone))
 ORDER BY g.phone LIMIT 5001"""


async def local_contacts(db, tenant, request):
    if request.contact_group_id is not None:
        try:
            group = await campaigns.group_row(db, tenant, request.contact_group_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise fail("GROUP_ADD_NOT_FOUND", 404) from None
            raise
        if group["archived_at"] is not None:
            raise fail("GROUP_ADD_SOURCE_CHANGED")
    zone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant})).scalar_one()
    rows = (await db.execute(text(LOCAL_SQL), {"t": tenant, "group_id": request.contact_group_id,
        "without_group": request.without_group, "search": request.search,
        "created_from": request.created_from, "created_to": request.created_to, "zone": zone})).mappings().all()
    if len(rows) > schemas.MAX_MEMBERS:
        raise fail("GROUP_ADD_LIMIT", 422)
    value = [{"phone": "+" + row["phone"], "name": next((safe_name(n) for n in row["names"] if safe_name(n)), ""),
              "source_context": {"kind": "contacts", "origins": row["origins"]},
              "permission_version": row["permission_version"], "blocked": row["blocked"]} for row in rows]
    bounded(value)
    return value, sum(row["source_count"] - 1 for row in rows)


async def permissions(db, tenant, phones):
    rows = (await db.execute(text("""SELECT phone,version,revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=ANY(CAST(:phones AS text[]))"""),
        {"t": tenant, "phones": [phone[1:] for phone in phones]})).mappings().all()
    return {"+" + row["phone"]: dict(row) for row in rows}


async def unresolved_phones(db, tenant, destination, *, excluding=None):
    if destination is None:
        return set()
    return set((await db.execute(text("""SELECT i.phone FROM subscription_group_add_items i
        JOIN subscription_group_add_batches b ON b.tenant_id=i.tenant_id AND b.id=i.batch_id
        WHERE i.tenant_id=:t AND b.destination_group_id=:destination AND i.status='unknown'
          AND (CAST(:excluding AS uuid) IS NULL OR i.id<>CAST(:excluding AS uuid))"""),
        {"t": tenant, "destination": destination, "excluding": excluding})).scalars().all())


async def remote_contacts(db, tenant, request, provider):
    permitted = await permissions(db, tenant, [member.phone for member in provider.members])
    return [{"phone": member.phone, "name": member.name,
             "source_context": {"kind": "group", "source_group_id": request.source_group_id},
             "permission_version": permitted.get(member.phone, {}).get("version", 0),
             "blocked": permitted.get(member.phone, {}).get("revoked", False)} for member in provider.members]


def filters(request):
    return request.model_dump(mode="json", exclude={"device_id", "destination_group_id", "source"})


def create_preview(tenant, actor, device, request, rows, ready, now):
    seal = schemas.PreviewSeal(purpose="orion.subscription.group-add.preview.v1", tenant_id=tenant,
        actor_account_id=actor.account_id, channel_id=device["channel_id"], generation=device["generation"],
        settings_revision=device["revision"], device_version=contacts.device_version(device), request=request,
        source_hash=fingerprint(rows), phones=sorted(ready), issued_at=int(now.timestamp()),
        expires_at=int(now.timestamp()) + schemas.PREVIEW_SECONDS)
    encoded = encode(seal.model_dump(mode="json")).encode()
    token = channels._cipher().encrypt(encoded).decode()
    if len(token) > schemas.MAX_TOKEN:
        raise fail("GROUP_ADD_LIMIT", 422)
    return token, datetime.fromtimestamp(seal.expires_at, timezone.utc)


def decode_preview(token, tenant, actor, now):
    try:
        raw = channels._cipher().decrypt(token.encode())
        if len(raw) > 256000:
            raise ValueError()
        seal = schemas.PreviewSeal.model_validate_json(raw)
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise fail("GROUP_ADD_INVALID", 422) from None
    if seal.tenant_id != tenant or seal.actor_account_id != actor.account_id:
        raise fail("GROUP_ADD_NOT_FOUND", 404)
    if not seal.issued_at <= int(now.timestamp()) < seal.expires_at:
        raise fail("GROUP_ADD_PREVIEW_EXPIRED", 410)
    return seal


def validate_device(seal, device):
    if contacts.device_version(device) != seal.device_version:
        raise fail("GROUP_ADD_CONTEXT_CHANGED")


async def existing(db, tenant, actor, key, request_fingerprint=None):
    row = (await db.execute(text("""SELECT * FROM subscription_group_add_batches
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": key})).mappings().one_or_none()
    if row and row["owner_account_id"] != actor.account_id:
        raise fail("GROUP_ADD_NOT_FOUND", 404)
    if row and request_fingerprint is not None and row["request_fingerprint"] != request_fingerprint:
        raise fail("GROUP_ADD_IDEMPOTENCY_CONFLICT")
    return dict(row) if row else None


async def batch_row(db, tenant, actor, identity):
    row = (await db.execute(text("SELECT * FROM subscription_group_add_batches WHERE tenant_id=:t AND id=:id"),
                            {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None or row["owner_account_id"] != actor.account_id:
        raise fail("GROUP_ADD_NOT_FOUND", 404)
    return dict(row)


async def response(db, batch, *, replayed=False):
    rows = (await db.execute(text("""SELECT id,ordinal,phone,name,status,error_code,dispatch_at,finished_at
        FROM subscription_group_add_items WHERE tenant_id=:t AND batch_id=:b ORDER BY ordinal"""),
        {"t": batch["tenant_id"], "b": batch["id"]})).mappings().all()
    progress = {status: sum(row["status"] == status for row in rows) for status in schemas.STATUSES}
    return {"batch": {"id": batch["id"], "idempotency_key": batch["idempotency_key"],
        "device": {"id": batch["device_id"], "name": batch["device_name"]},
        "destination": {"id": batch["destination_group_id"], "name": batch["destination_name"]},
        "source": batch["source"], "status": batch["status"], "version": batch["version"],
        "interval_seconds": schemas.INTERVAL_SECONDS, "total": batch["total"], "progress": progress,
        "created_at": batch["created_at"], "started_at": batch["started_at"],
        "finished_at": batch["finished_at"], "error_code": batch["error_code"]},
        "members": [{"id": row["id"], "ordinal": row["ordinal"], "phone": row["phone"], "name": row["name"],
            "status": row["status"], "error_code": row["error_code"], "started_at": row["dispatch_at"],
            "finished_at": row["finished_at"]} for row in rows], "replayed": replayed}


async def persist(db, tenant, actor, payload, seal, device, destination, rows, now):
    validate_device(seal, device)
    if not seal.issued_at <= int(now.timestamp()) < seal.expires_at:
        raise fail("GROUP_ADD_PREVIEW_EXPIRED", 410)
    selected = {row["phone"]: row for row in rows}
    if (fingerprint(rows) != seal.source_hash or not set(payload.phones) <= set(seal.phones)
            or any(phone not in selected or selected[phone]["blocked"] for phone in payload.phones)):
        raise fail("GROUP_ADD_SOURCE_CHANGED")
    if set(payload.phones) & await unresolved_phones(db, tenant, destination.id):
        raise fail("GROUP_ADD_NO_ELIGIBLE", 422)
    active = (await db.execute(text("""SELECT id FROM subscription_group_add_batches
        WHERE tenant_id=:t AND device_id=:d AND (status IN ('pending','running') OR EXISTS(
          SELECT 1 FROM subscription_group_add_items i WHERE i.tenant_id=:t AND i.device_id=:d
            AND (i.status='running' OR i.status='unknown' AND NOT i.provider_settled))) LIMIT 1"""),
        {"t": tenant, "d": device["id"]})).scalar_one_or_none()
    if active is not None:
        raise fail("GROUP_ADD_BUSY")
    identity = uuid4()
    batch = (await db.execute(text("""INSERT INTO subscription_group_add_batches
        (id,tenant_id,owner_account_id,device_id,generation,settings_revision,channel_id,device_name,
         destination_group_id,destination_name,source,filters,source_hash,idempotency_key,request_fingerprint,total,
         created_at,next_item_at)
        VALUES(:id,:t,:a,:d,:g,:r,:c,:name,:group,:group_name,:source,CAST(:filters AS jsonb),:hash,:key,:fp,:total,:now,:now)
        RETURNING *"""), {"id": identity, "t": tenant, "a": actor.account_id, "d": device["id"],
        "g": device["generation"], "r": device["revision"], "c": device["channel_id"], "name": device["name"],
        "group": destination.id, "group_name": destination.name, "source": seal.request.source,
        "filters": encode(filters(seal.request)), "hash": seal.source_hash, "key": payload.idempotency_key,
        "fp": fingerprint(payload.model_dump(mode="json")), "total": len(payload.phones), "now": now})).mappings().one()
    values = [{"id": str(uuid4()), "ordinal": index, "phone": phone, "name": selected[phone]["name"],
               "source_context": selected[phone]["source_context"],
               "permission_version": selected[phone]["permission_version"]}
              for index, phone in enumerate(payload.phones, 1)]
    for item in values:
        bounded(item["source_context"], 32768)
    await db.execute(text("""INSERT INTO subscription_group_add_items
        (id,tenant_id,batch_id,device_id,generation,settings_revision,ordinal,phone,name,source_context,permission_version)
        SELECT x.id,:t,:b,:d,:g,:r,x.ordinal,x.phone,x.name,x.source_context,x.permission_version
        FROM jsonb_to_recordset(CAST(:items AS jsonb)) x(id uuid,ordinal integer,phone varchar(16),
            name varchar(100),source_context jsonb,permission_version bigint)"""),
        {"t": tenant, "b": identity, "d": device["id"], "g": device["generation"],
         "r": device["revision"], "items": encode(values)})
    return await response(db, dict(batch))


async def check_item_source(db, batch, item):
    permission = (await permissions(db, batch["tenant_id"], [item["phone"]])).get(item["phone"], {})
    if permission.get("revoked") or permission.get("version", 0) != item["permission_version"]:
        raise fail("GROUP_ADD_OPTED_OUT")
    if item["source_context"].get("kind") == "contacts" and not await catalog.sources_valid(
            db, batch["tenant_id"], item["source_context"].get("origins"), item["phone"][1:]):
        raise fail("GROUP_ADD_SOURCE_CHANGED")
    if item["phone"] in await unresolved_phones(db, batch["tenant_id"], batch["destination_group_id"], excluding=item["id"]):
        raise fail("GROUP_ADD_NO_ELIGIBLE", 422)


async def owner_authority(db, batch):
    await shared.workspace(db, batch["tenant_id"], feature=FEATURE)
    actor = Actor("member", account_id=batch["owner_account_id"])
    await channels._owner(db, batch["tenant_id"], actor)
    status = (await db.execute(text("SELECT status FROM tenants WHERE id=:t FOR UPDATE"),
                              {"t": batch["tenant_id"]})).scalar_one_or_none()
    if status != "active":
        raise fail("GROUP_ADD_CONTEXT_CHANGED")
    device, transport = await scope(db, batch["tenant_id"], batch["device_id"], actor)
    if (device["generation"] != batch["generation"] or device["revision"] != batch["settings_revision"]
            or device["channel_id"] != batch["channel_id"]):
        raise fail("GROUP_ADD_CONTEXT_CHANGED")
    return device, transport
