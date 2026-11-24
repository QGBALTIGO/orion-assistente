"""Tenant-owned audience overlays, additive imports and shared group membership.

Customer/booking rows are never rewritten. A canonical phone may be present in
many source groups, but the public contact catalog presents it once. Archived
audience rows are tombstones so an older customer/group source cannot revive it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import qr_channels as channels
from app import subscription_chatbot_store as shared
from app import subscription_campaign_store as campaigns
from app import subscription_audience_schemas as schemas
from app import subscription_audience_import as importer

FEATURE = "audience.manage"
PURPOSE = "orion.subscription.audience-import.v1"
encode, fingerprint, clock = shared.encode, shared.fingerprint, campaigns.clock


def fail(code="AUDIENCE_INVALID", status=409, message=None):
    messages = {
        "AUDIENCE_INVALID": "Confira os dados do contato, grupo ou arquivo.",
        "AUDIENCE_NOT_FOUND": "Contato, grupo ou pedido não encontrado nesta empresa.",
        "AUDIENCE_CONFLICT": "Os dados mudaram. Atualize antes de salvar.",
        "AUDIENCE_DUPLICATE": "Este WhatsApp ou Telegram já está cadastrado nesta empresa.",
        "AUDIENCE_LIMIT": "O limite foi excedido. Nenhum contato foi descartado; reduza ou divida a seleção.",
        "AUDIENCE_IMPORT_INVALID": "O arquivo contém erros. Corrija-os ou confirme explicitamente importar somente as linhas válidas.",
        "AUDIENCE_PREVIEW_INVALID": "Esta prévia não pertence à empresa ou à conta atual.",
        "AUDIENCE_PREVIEW_EXPIRED": "A prévia expirou. Analise o arquivo novamente.",
        "AUDIENCE_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
    }
    return HTTPException(status, {"code": code, "message": message or messages.get(code, messages["AUDIENCE_INVALID"])},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


# All three source branches are explicitly tenant-scoped. DISTINCT ON is stable
# and selects an audience override, then a customer, then the earliest group ID.
# Legacy customers expose only their existing catalog grant (name, phone,
# version); their private booking notes and email are not copied into Público.
CATALOG_SQL = """WITH candidates AS (
 SELECT CASE WHEN a.phone IS NULL THEN 'contact:'||a.id::text ELSE 'phone:'||a.phone END AS key,
   a.id AS audience_id,a.phone,a.telegram,a.name,a.email,a.notes,a.chatbot_enabled,
   a.audio_transcription_enabled,a.plan_name,a.price_cents,a.due_date,a.service_username,
   (a.service_password_encrypted IS NOT NULL) AS password_configured,
   a.version::bigint AS source_version,a.updated_at AS source_updated,0 AS priority,a.id::text AS ordering
 FROM subscription_audience_contacts a WHERE a.tenant_id=:t AND a.removed_at IS NULL
 UNION ALL
 SELECT 'phone:'||substring(c.whatsapp_e164 FROM 2),NULL::uuid,substring(c.whatsapp_e164 FROM 2),
   NULL::varchar,c.name,NULL::varchar,NULL::text,true,true,NULL::varchar,NULL::bigint,NULL::date,NULL::varchar,false,
   c.version::bigint,NULL::timestamptz,1,c.id::text
 FROM customers c WHERE c.tenant_id=:t AND c.active AND c.deleted_at IS NULL
   AND c.whatsapp_e164 ~ '^\\+[1-9][0-9]{7,14}$'
   AND NOT EXISTS(SELECT 1 FROM subscription_audience_contacts a
       WHERE a.tenant_id=c.tenant_id AND a.phone=substring(c.whatsapp_e164 FROM 2))
 UNION ALL
 SELECT 'phone:'||m.phone,NULL::uuid,m.phone,NULL::varchar,m.name,NULL::varchar,NULL::text,
   true,true,NULL::varchar,NULL::bigint,NULL::date,NULL::varchar,false,
   g.version::bigint,m.updated_at,2,g.id::text
 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
   ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
 WHERE m.tenant_id=:t AND m.removed_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM subscription_audience_contacts a WHERE a.tenant_id=m.tenant_id AND a.phone=m.phone)
), catalog AS (
 SELECT DISTINCT ON(key) * FROM candidates ORDER BY key,priority,ordering
) """


FILTER_SQL = """ WHERE (:q='' OR position(:q in lower(c.name))>0 OR position(:q in COALESCE(c.phone,''))>0
    OR (:phone_q<>'' AND position(:phone_q in COALESCE(c.phone,''))>0)
    OR position(:q in lower(COALESCE(c.telegram,'')))>0 OR position(:q in lower(COALESCE(c.email,'')))>0)
 AND (CAST(:group_id AS uuid) IS NULL OR EXISTS(
    SELECT 1 FROM subscription_campaign_members gm JOIN subscription_campaign_groups gg
      ON gg.tenant_id=gm.tenant_id AND gg.id=gm.group_id AND gg.archived_at IS NULL
    WHERE gm.tenant_id=:t AND gm.group_id=:group_id AND gm.phone=c.phone AND gm.removed_at IS NULL)
 OR EXISTS(SELECT 1 FROM subscription_audience_group_links gl JOIN subscription_campaign_groups gg
      ON gg.tenant_id=gl.tenant_id AND gg.id=gl.group_id AND gg.archived_at IS NULL
    WHERE gl.tenant_id=:t AND gl.group_id=:group_id AND gl.contact_id=c.audience_id))
 AND (NOT :ungrouped OR (NOT EXISTS(
    SELECT 1 FROM subscription_campaign_members um JOIN subscription_campaign_groups ug
      ON ug.tenant_id=um.tenant_id AND ug.id=um.group_id AND ug.archived_at IS NULL
    WHERE um.tenant_id=:t AND um.phone=c.phone AND um.removed_at IS NULL)
   AND NOT EXISTS(SELECT 1 FROM subscription_audience_group_links ul JOIN subscription_campaign_groups ug
      ON ug.tenant_id=ul.tenant_id AND ug.id=ul.group_id AND ug.archived_at IS NULL
    WHERE ul.tenant_id=:t AND ul.contact_id=c.audience_id))) """


async def group_map(db, tenant, rows):
    phones = sorted({row["phone"] for row in rows if row["phone"]})
    ids = [row["audience_id"] for row in rows if row["audience_id"]]
    if not phones and not ids:
        return {}
    memberships = (await db.execute(text("""
        SELECT 'phone:'||m.phone AS key,g.id,g.name,g.version
        FROM subscription_campaign_members m JOIN subscription_campaign_groups g
          ON g.tenant_id=m.tenant_id AND g.id=m.group_id
        WHERE m.tenant_id=:t AND m.phone=ANY(CAST(:phones AS text[]))
          AND m.removed_at IS NULL AND g.archived_at IS NULL
        UNION
        SELECT 'contact:'||a.id::text,g.id,g.name,g.version
        FROM subscription_audience_group_links l JOIN subscription_campaign_groups g
          ON g.tenant_id=l.tenant_id AND g.id=l.group_id
        JOIN subscription_audience_contacts a ON a.tenant_id=l.tenant_id AND a.id=l.contact_id
        WHERE l.tenant_id=:t AND l.contact_id=ANY(CAST(:ids AS uuid[])) AND a.phone IS NULL
          AND a.removed_at IS NULL AND g.archived_at IS NULL
        ORDER BY name,id
    """), {"t": tenant, "phones": phones, "ids": ids})).mappings().all()
    result = {}
    for item in memberships:
        result.setdefault(item["key"], []).append({"id": item["id"], "name": item["name"], "version": item["version"]})
    return result


def contact_dto(row, groups):
    result = {name: row.get(name) for name in (
        "key", "name", "telegram", "email", "notes", "chatbot_enabled", "audio_transcription_enabled",
        "plan_name", "price_cents", "due_date", "service_username", "password_configured")}
    result["phone_e164"] = "+" + row["phone"] if row["phone"] else None
    result["groups"] = [{"id": group["id"], "name": group["name"]} for group in groups]
    result["group_ids"] = [group["id"] for group in groups]
    result["version"] = fingerprint({"data": result, "source_version": row["source_version"],
        "source_updated": row["source_updated"], "groups": groups})
    return jsonable_encoder(result)


async def project(db, tenant, rows):
    groups = await group_map(db, tenant, rows)
    return [contact_dto(row, groups.get(row["key"], [])) for row in rows]


async def contacts_page(db, tenant, q="", group_id=None, page=1, page_size=30, *, export=False):
    if group_id is not None and group_id != "none":
        await checked_groups(db, tenant, [group_id])
    phone_query = re.sub(r"[^0-9]", "", q) if re.fullmatch(r"[+0-9 ().\-]+", q) else ""
    params = {"t": tenant, "q": q.strip().casefold(), "phone_q": phone_query,
              "group_id": None if group_id == "none" else group_id,
              "ungrouped": group_id == "none",
              "limit": schemas.MAX_CONTACTS + 1 if export else page_size, "offset": 0 if export else (page - 1) * page_size}
    total = (await db.execute(text(CATALOG_SQL + "SELECT count(*) FROM catalog c" + FILTER_SQL), params)).scalar_one()
    if export and total > schemas.MAX_CONTACTS:
        raise fail("AUDIENCE_LIMIT", 422, "Há mais de 100.000 contatos nesta seleção; aplique um filtro para exportar sem truncar.")
    rows = (await db.execute(text(CATALOG_SQL + "SELECT c.* FROM catalog c" + FILTER_SQL +
        " ORDER BY lower(c.name),c.key LIMIT :limit OFFSET :offset"), params)).mappings().all()
    items = await project(db, tenant, rows)
    if export and len(items) != total:
        raise fail("AUDIENCE_CONFLICT")
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size)}


async def contact_rows(db, tenant, keys):
    return (await db.execute(text(CATALOG_SQL + "SELECT * FROM catalog WHERE key=ANY(CAST(:keys AS text[])) ORDER BY key"),
        {"t": tenant, "keys": keys})).mappings().all()


async def contact_detail(db, tenant, key):
    rows = await contact_rows(db, tenant, [key])
    if not rows:
        raise fail("AUDIENCE_NOT_FOUND", 404)
    return (await project(db, tenant, rows))[0]


async def active_count(db, tenant):
    return int((await db.execute(text(CATALOG_SQL + "SELECT count(*) FROM catalog"), {"t": tenant})).scalar_one())


async def groups(db, tenant, *, ids=None, q="", page=1, page_size=30):
    params = {"t": tenant, "ids": ids, "q": q.strip().casefold(), "limit": page_size, "offset": (page - 1) * page_size}
    clause = """ FROM subscription_campaign_groups g WHERE g.tenant_id=:t AND g.archived_at IS NULL
      AND (CAST(:ids AS uuid[]) IS NULL OR g.id=ANY(CAST(:ids AS uuid[])))
      AND (:q='' OR position(:q in lower(g.name))>0) """
    total = (await db.execute(text("SELECT count(*)" + clause), params)).scalar_one()
    rows = (await db.execute(text("""SELECT g.*,
      (SELECT count(*) FROM subscription_campaign_members m WHERE m.tenant_id=g.tenant_id
        AND m.group_id=g.id AND m.removed_at IS NULL) AS whatsapp_count,
      (SELECT count(*) FROM subscription_audience_group_links l JOIN subscription_audience_contacts a
        ON a.tenant_id=l.tenant_id AND a.id=l.contact_id AND a.phone IS NULL AND a.removed_at IS NULL
        WHERE l.tenant_id=g.tenant_id AND l.group_id=g.id) AS telegram_count,
      (SELECT count(*) FROM subscription_campaign_members m JOIN subscription_campaign_permissions p
        ON p.tenant_id=m.tenant_id AND p.phone=m.phone AND p.revoked
        WHERE m.tenant_id=g.tenant_id AND m.group_id=g.id AND m.removed_at IS NULL) AS blocked_count
    """ + clause + " ORDER BY lower(g.name),g.id LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return {"items": [{"id": row["id"], "name": row["name"], "version": row["version"],
        "whatsapp_count": row["whatsapp_count"], "telegram_count": row["telegram_count"],
        "member_count": row["whatsapp_count"] + row["telegram_count"], "blocked_count": row["blocked_count"],
        "archived": False} for row in rows], "total": total, "page": page, "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)}


async def group_detail(db, tenant, identity):
    values = await groups(db, tenant, ids=[identity])
    if not values["items"]:
        raise fail("AUDIENCE_NOT_FOUND", 404)
    rows = (await db.execute(text("""SELECT 'phone:'||phone AS key FROM subscription_campaign_members
        WHERE tenant_id=:t AND group_id=:g AND removed_at IS NULL
        UNION SELECT 'contact:'||a.id::text FROM subscription_audience_group_links l
        JOIN subscription_audience_contacts a ON a.tenant_id=l.tenant_id AND a.id=l.contact_id
        WHERE l.tenant_id=:t AND l.group_id=:g AND a.phone IS NULL AND a.removed_at IS NULL
        ORDER BY key"""), {"t": tenant, "g": identity})).scalars().all()
    return {"group": values["items"][0], "contact_keys": list(rows)}


async def checked_groups(db, tenant, ids):
    if not ids:
        return []
    result = (await groups(db, tenant, ids=ids, page_size=schemas.MAX_GROUPS))["items"]
    if len(result) != len(ids):
        raise fail("AUDIENCE_NOT_FOUND", 404)
    return result


async def invalidate(db, tenant):
    from app.subscription_campaign_runtime import invalidate_audiences
    from app.subscription_schedule_runtime import invalidate_schedule_contacts
    from app.subscription_bulk_runtime import invalidate_bulk_contacts
    await invalidate_audiences(db, tenant)
    await invalidate_schedule_contacts(db, tenant)
    await invalidate_bulk_contacts(db, tenant)


async def operation(db, tenant, actor, payload, kind, work, *, resource=None):
    values = {"payload": payload.model_dump(mode="json"), "resource": resource}
    # Fingerprints include secret input but raw payloads are never retained.
    signature = fingerprint(values)
    old = (await db.execute(text("""SELECT * FROM subscription_audience_operations
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": payload.idempotency_key})).mappings().one_or_none()
    if old:
        if old["actor_account_id"] != actor.account_id:
            raise fail("AUDIENCE_NOT_FOUND", 404)
        if old["kind"] != kind or old["request_fingerprint"] != signature:
            raise fail("AUDIENCE_IDEMPOTENCY_CONFLICT")
        return {"request_id": old["id"], "idempotency_key": payload.idempotency_key, "replayed": True, "result": old["result"]}
    result = jsonable_encoder(await work())
    identity = uuid4()
    await db.execute(text("""INSERT INTO subscription_audience_operations
        (id,tenant_id,actor_account_id,idempotency_key,kind,request_fingerprint,result)
        VALUES(:id,:t,:a,:key,:kind,:fp,CAST(:result AS jsonb))"""),
        {"id": identity, "t": tenant, "a": actor.account_id, "key": payload.idempotency_key,
         "kind": kind, "fp": signature, "result": encode(result)})
    return {"request_id": identity, "idempotency_key": payload.idempotency_key, "replayed": False, "result": result}


async def _own(db, tenant, key):
    clause = "phone=:phone" if key.startswith("phone:") else "id=:id"
    return (await db.execute(text("SELECT * FROM subscription_audience_contacts WHERE tenant_id=:t AND " + clause + " FOR UPDATE"),
        {"t": tenant, "phone": key[6:] if key.startswith("phone:") else None,
         "id": UUID(key[8:]) if key.startswith("contact:") else None})).mappings().one_or_none()


async def _write(db, tenant, value, *, identity=None, preserve_password=None):
    data = value.model_dump(mode="json")
    secret = preserve_password
    if value.clear_password:
        secret = None
    elif value.service_password is not None:
        secret = channels._cipher().encrypt(value.service_password.encode("utf-8")).decode("ascii") if value.service_password else None
    fields = ("name", "telegram", "email", "notes", "chatbot_enabled", "audio_transcription_enabled",
              "plan_name", "price_cents", "service_username")
    params = {field: data[field] for field in fields}
    params.update({"id": identity or uuid4(), "t": tenant, "phone": value.phone_e164[1:] if value.phone_e164 else None,
                   "due_date": value.due_date, "secret": secret})
    if identity is None:
        await db.execute(text("""INSERT INTO subscription_audience_contacts
            (id,tenant_id,phone,telegram,name,email,notes,chatbot_enabled,audio_transcription_enabled,
             plan_name,price_cents,due_date,service_username,service_password_encrypted)
            VALUES(:id,:t,:phone,:telegram,:name,:email,:notes,:chatbot_enabled,:audio_transcription_enabled,
              :plan_name,:price_cents,:due_date,:service_username,:secret)"""), params)
    else:
        await db.execute(text("""UPDATE subscription_audience_contacts SET phone=:phone,telegram=:telegram,name=:name,
            email=:email,notes=:notes,chatbot_enabled=:chatbot_enabled,audio_transcription_enabled=:audio_transcription_enabled,
            plan_name=:plan_name,price_cents=:price_cents,due_date=:due_date,service_username=:service_username,
            service_password_encrypted=:secret,version=version+1,updated_at=clock_timestamp(),removed_at=NULL
            WHERE tenant_id=:t AND id=:id"""), params)
    return params["id"]


async def _tombstone(db, tenant, old, own):
    if own:
        await db.execute(text("""UPDATE subscription_audience_contacts SET removed_at=clock_timestamp(),
            chatbot_enabled=false,audio_transcription_enabled=false,version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": own["id"]})
    else:
        await db.execute(text("""INSERT INTO subscription_audience_contacts(tenant_id,phone,name,removed_at,
            chatbot_enabled,audio_transcription_enabled) VALUES(:t,:phone,:name,clock_timestamp(),false,false)"""),
            {"t": tenant, "phone": old["phone_e164"][1:], "name": old["name"]})


async def _remove_memberships(db, tenant, *, phone=None, identity=None):
    affected = (await db.execute(text("""UPDATE subscription_campaign_members SET removed_at=clock_timestamp(),updated_at=clock_timestamp()
        WHERE tenant_id=:t AND phone=:phone AND removed_at IS NULL RETURNING group_id"""),
        {"t": tenant, "phone": phone})).scalars().all() if phone else []
    if identity:
        affected = [*affected, *(await db.execute(text("""DELETE FROM subscription_audience_group_links
            WHERE tenant_id=:t AND contact_id=:id RETURNING group_id"""), {"t": tenant, "id": identity})).scalars().all()]
    return set(affected)


async def _memberships(db, tenant, identity, value, *, old=None, old_identity=None):
    desired = await checked_groups(db, tenant, value.group_ids)
    existing_groups = set(UUID(item) for item in (old or {}).get("group_ids", []))
    for group in desired:
        if group["id"] not in existing_groups and group["member_count"] >= schemas.MAX_GROUP_MEMBERS:
            raise fail("AUDIENCE_LIMIT", 422, "Cada grupo aceita até 5.000 contatos. A seleção foi preservada sem importação parcial.")
    affected = await _remove_memberships(db, tenant, phone=((old or {}).get("phone_e164") or "")[1:] or None,
                                         identity=old_identity)
    for group in desired:
        if value.phone_e164:
            await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
                VALUES(:t,:g,:phone,:name) ON CONFLICT(tenant_id,group_id,phone)
                DO UPDATE SET name=EXCLUDED.name,removed_at=NULL,updated_at=clock_timestamp()"""),
                {"t": tenant, "g": group["id"], "phone": value.phone_e164[1:], "name": value.name[:100]})
        else:
            await db.execute(text("""INSERT INTO subscription_audience_group_links(tenant_id,group_id,contact_id)
                VALUES(:t,:g,:id) ON CONFLICT DO NOTHING"""), {"t": tenant, "g": group["id"], "id": identity})
        affected.add(group["id"])
    if affected:
        await db.execute(text("""UPDATE subscription_campaign_groups SET version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[]))"""), {"t": tenant, "ids": sorted(affected, key=str)})


async def save_contact(db, tenant, value, *, old_key=None, expected_version=None):
    old = await contact_detail(db, tenant, old_key) if old_key else None
    if old and old["version"] != expected_version:
        raise fail("AUDIENCE_CONFLICT")
    own = await _own(db, tenant, old_key) if old_key else None
    new_key = "phone:" + value.phone_e164[1:] if value.phone_e164 else None
    if new_key and new_key != old_key:
        collisions = await contact_rows(db, tenant, [new_key])
        tombstone = await _own(db, tenant, new_key)
        if collisions or tombstone:
            raise fail("AUDIENCE_DUPLICATE")
    if value.telegram:
        collision = (await db.execute(text("""SELECT id FROM subscription_audience_contacts WHERE tenant_id=:t
            AND telegram=:telegram AND (CAST(:id AS uuid) IS NULL OR id<>CAST(:id AS uuid))"""),
            {"t": tenant, "telegram": value.telegram, "id": own["id"] if own else None})).scalar_one_or_none()
        if collision:
            raise fail("AUDIENCE_DUPLICATE")
    if old is None and await active_count(db, tenant) >= schemas.MAX_CONTACTS:
        raise fail("AUDIENCE_LIMIT", 422)
    preserve_password = own["service_password_encrypted"] if own else None
    target_identity = own["id"] if own else None
    # Keep an old phone tombstone when changing identity, otherwise a legacy
    # customer or a separately imported group could revive the previous number.
    if old_key and old_key.startswith("phone:") and new_key != old_key:
        await _tombstone(db, tenant, old, own)
        if own:
            await db.execute(text("UPDATE subscription_audience_contacts SET telegram=NULL WHERE tenant_id=:t AND id=:id"),
                {"t": tenant, "id": own["id"]})
        target_identity = None
    identity = await _write(db, tenant, value, identity=target_identity, preserve_password=preserve_password)
    await _memberships(db, tenant, identity, value, old=old, old_identity=own["id"] if own else None)
    await invalidate(db, tenant)
    return {"contact": await contact_detail(db, tenant, new_key or "contact:" + str(identity))}


async def archive_contact(db, tenant, key, expected_version):
    old = await contact_detail(db, tenant, key)
    if old["version"] != expected_version:
        raise fail("AUDIENCE_CONFLICT")
    own = await _own(db, tenant, key)
    await _tombstone(db, tenant, old, own)
    affected = await _remove_memberships(db, tenant, phone=old["phone_e164"][1:] if old["phone_e164"] else None,
                                        identity=own["id"] if own else None)
    if affected:
        await db.execute(text("""UPDATE subscription_campaign_groups SET version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[]))"""), {"t": tenant, "ids": sorted(affected, key=str)})
    await invalidate(db, tenant)
    return {"archived": True, "key": key}


async def preserve_orphaned_group_contacts(db, tenant, identity, retained_phones=()):
    """Removing a local group membership must not implicitly delete a contact.

    Earlier extractors stored contacts only in campaign_members. Copy only a
    contact's last active source into the audience overlay before removing that
    source. Existing customer/overlay profiles and tombstones stay untouched.
    """
    await db.execute(text(CATALOG_SQL + """INSERT INTO subscription_audience_contacts(tenant_id,phone,name)
        SELECT :t,c.phone,c.name FROM catalog c WHERE c.priority=2
          AND NOT c.phone=ANY(CAST(:retained AS text[]))
          AND EXISTS(SELECT 1 FROM subscription_campaign_members m
            WHERE m.tenant_id=:t AND m.group_id=:g AND m.phone=c.phone AND m.removed_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
            ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
            WHERE m.tenant_id=:t AND m.group_id<>:g AND m.phone=c.phone AND m.removed_at IS NULL)
        ON CONFLICT(tenant_id,phone) DO NOTHING"""),
        {"t": tenant, "g": identity, "retained": list(retained_phones)})


async def save_group(db, tenant, name, keys, *, identity=None, expected_version=None):
    if identity:
        old = await group_detail(db, tenant, identity)
        if old["group"]["version"] != expected_version:
            raise fail("AUDIENCE_CONFLICT")
    else:
        if (await groups(db, tenant))["total"] >= schemas.MAX_GROUPS:
            raise fail("AUDIENCE_LIMIT", 422)
    rows = await contact_rows(db, tenant, keys) if keys is not None else []
    if keys is not None and len(rows) != len(keys):
        raise fail("AUDIENCE_NOT_FOUND", 404)
    if identity is None:
        identity = uuid4()
        await db.execute(text("INSERT INTO subscription_campaign_groups(id,tenant_id,name) VALUES(:id,:t,:name)"),
            {"id": identity, "t": tenant, "name": name})
    else:
        if keys is not None:
            await preserve_orphaned_group_contacts(db, tenant, identity, [row["phone"] for row in rows if row["phone"]])
        await db.execute(text("""UPDATE subscription_campaign_groups SET name=:name,version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:t AND id=:id"""), {"id": identity, "t": tenant, "name": name})
    if keys is not None:
        phones = [row["phone"] for row in rows if row["phone"]]
        tgids = [row["audience_id"] for row in rows if row["phone"] is None]
        await db.execute(text("""UPDATE subscription_campaign_members SET removed_at=clock_timestamp(),updated_at=clock_timestamp()
            WHERE tenant_id=:t AND group_id=:g AND removed_at IS NULL AND NOT phone=ANY(CAST(:phones AS text[]))"""),
            {"t": tenant, "g": identity, "phones": phones})
        if phones:
            await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
                SELECT :t,:g,x.phone,x.name FROM jsonb_to_recordset(CAST(:rows AS jsonb)) x(phone varchar(15),name varchar(100))
                ON CONFLICT(tenant_id,group_id,phone) DO UPDATE SET name=EXCLUDED.name,removed_at=NULL,updated_at=clock_timestamp()
                WHERE subscription_campaign_members.name<>EXCLUDED.name OR subscription_campaign_members.removed_at IS NOT NULL"""),
                {"t": tenant, "g": identity, "rows": encode([{"phone": r["phone"], "name": r["name"][:100]} for r in rows if r["phone"]])})
        await db.execute(text("""DELETE FROM subscription_audience_group_links WHERE tenant_id=:t AND group_id=:g
            AND NOT contact_id=ANY(CAST(:ids AS uuid[]))"""), {"t": tenant, "g": identity, "ids": tgids})
        if tgids:
            await db.execute(text("""INSERT INTO subscription_audience_group_links(tenant_id,group_id,contact_id)
                SELECT :t,:g,id FROM unnest(CAST(:ids AS uuid[])) id ON CONFLICT DO NOTHING"""),
                {"t": tenant, "g": identity, "ids": tgids})
    await invalidate(db, tenant)
    return await group_detail(db, tenant, identity)


async def archive_group(db, tenant, identity, expected_version):
    old = await group_detail(db, tenant, identity)
    if old["group"]["version"] != expected_version:
        raise fail("AUDIENCE_CONFLICT")
    await preserve_orphaned_group_contacts(db, tenant, identity)
    await db.execute(text("""UPDATE subscription_campaign_groups SET archived_at=clock_timestamp(),
        version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": identity})
    await invalidate(db, tenant)
    return {"archived": True, "id": identity}


async def existing_phones(db, tenant, phones):
    rows = (await db.execute(text("""SELECT phone FROM subscription_audience_contacts
        WHERE tenant_id=:t AND phone=ANY(CAST(:phones AS text[]))
        UNION SELECT substring(whatsapp_e164 FROM 2) FROM customers WHERE tenant_id=:t
          AND active AND deleted_at IS NULL AND substring(whatsapp_e164 FROM 2)=ANY(CAST(:phones AS text[]))
        UNION SELECT m.phone FROM subscription_campaign_members m JOIN subscription_campaign_groups g
          ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
        WHERE m.tenant_id=:t AND m.removed_at IS NULL AND m.phone=ANY(CAST(:phones AS text[]))"""),
        {"t": tenant, "phones": phones})).scalars().all()
    return set(rows)


async def prepare_import(db, tenant, actor, payload):
    parsed = importer.parse(payload.content, payload.default_country)
    await checked_groups(db, tenant, payload.group_ids)
    phones = [contact["phone_e164"][1:] for contact in parsed["contacts"]]
    existing = await existing_phones(db, tenant, phones)
    counts = {**parsed["counts"], "existing": len(existing), "new": len(phones) - len(existing)}
    now = await clock(db)
    timestamp = int(now.timestamp())
    token = None
    if not parsed["fatal"] and phones:
        sealed = {"purpose": PURPOSE, "tenant_id": str(tenant), "actor_account_id": str(actor.account_id),
            "issued_at": timestamp, "expires_at": timestamp + schemas.PREVIEW_SECONDS,
            "group_ids": [str(value) for value in payload.group_ids], "contacts": parsed["contacts"],
            "counts": counts, "source_hash": hashlib.sha256(payload.content.encode("utf-8")).hexdigest()}
        raw = encode(sealed).encode("utf-8")
        if len(raw) > schemas.MAX_PREVIEW_BYTES:
            raise fail("AUDIENCE_LIMIT", 422)
        token = channels._cipher().encrypt(raw).decode("ascii")
        if len(token) > schemas.MAX_TOKEN:
            raise fail("AUDIENCE_LIMIT", 422)
    return {"preview_token": token, "counts": counts, "errors": parsed["errors"],
            "sample": [{**row, "existing": row["phone_e164"][1:] in existing} for row in parsed["sample"]],
            "can_commit": bool(token and not counts["invalid"]), "can_skip_invalid": bool(token and counts["invalid"]),
            "expires_at": datetime.fromtimestamp(timestamp + schemas.PREVIEW_SECONDS, timezone.utc),
            "limits": {"rows": schemas.MAX_IMPORT_ROWS, "contacts": schemas.MAX_CONTACTS, "members_per_group": schemas.MAX_GROUP_MEMBERS}}


def decode_preview(token, tenant, actor, now):
    import json
    try:
        raw = channels._cipher().decrypt(token.encode("ascii"))
        if len(raw) > schemas.MAX_PREVIEW_BYTES:
            raise ValueError()
        value = json.loads(raw)
        if (value.get("purpose") != PURPOSE or value.get("tenant_id") != str(tenant)
                or value.get("actor_account_id") != str(actor.account_id)
                or not isinstance(value.get("issued_at"), int) or not isinstance(value.get("expires_at"), int)
                or value["expires_at"] - value["issued_at"] != schemas.PREVIEW_SECONDS
                or value["issued_at"] > now.timestamp() + 1):
            raise ValueError()
        if not isinstance(value.get("contacts"), list) or not 1 <= len(value["contacts"]) <= schemas.MAX_IMPORT_ROWS:
            raise ValueError()
    except (InvalidToken, ValueError, UnicodeError, TypeError, AttributeError, KeyError):
        raise fail("AUDIENCE_PREVIEW_INVALID", 422) from None
    if now.timestamp() >= value["expires_at"]:
        raise fail("AUDIENCE_PREVIEW_EXPIRED", 410)
    return value


async def commit_import(db, tenant, actor, payload):
    preview = decode_preview(payload.preview_token, tenant, actor, await clock(db))
    if preview["counts"]["invalid"] and not payload.skip_invalid:
        raise fail("AUDIENCE_IMPORT_INVALID", 422)
    contacts = preview["contacts"]
    phones = [row["phone_e164"][1:] for row in contacts]
    existing = await existing_phones(db, tenant, phones)
    new = [row for row in contacts if row["phone_e164"][1:] not in existing]
    if await active_count(db, tenant) + len(new) > schemas.MAX_CONTACTS:
        raise fail("AUDIENCE_LIMIT", 422, "A importação ultrapassaria 100.000 contatos. Nenhuma linha foi importada parcialmente.")
    archived = set((await db.execute(text("""SELECT phone FROM subscription_audience_contacts WHERE tenant_id=:t
        AND phone=ANY(CAST(:phones AS text[])) AND removed_at IS NOT NULL"""), {"t": tenant, "phones": phones})).scalars().all())
    active_phones = [phone for phone in phones if phone not in archived]
    target_groups = await checked_groups(db, tenant, [UUID(value) for value in preview["group_ids"]])
    membership_additions = 0
    for group in target_groups:
        present = (await db.execute(text("""SELECT count(*) FROM subscription_campaign_members WHERE tenant_id=:t
            AND group_id=:g AND removed_at IS NULL AND phone=ANY(CAST(:phones AS text[]))"""),
            {"t": tenant, "g": group["id"], "phones": active_phones})).scalar_one()
        additions = len(active_phones) - present
        if group["member_count"] + additions > schemas.MAX_GROUP_MEMBERS:
            raise fail("AUDIENCE_LIMIT", 422, "A seleção ultrapassaria 5.000 contatos em um grupo. Importe sem esse grupo ou divida a lista.")
        membership_additions += additions
    if new:
        await db.execute(text("""INSERT INTO subscription_audience_contacts(tenant_id,phone,name)
            SELECT :t,x.phone,x.name FROM jsonb_to_recordset(CAST(:rows AS jsonb)) x(phone varchar(15),name varchar(120))
            ON CONFLICT(tenant_id,phone) DO NOTHING"""),
            {"t": tenant, "rows": encode([{"phone": row["phone_e164"][1:], "name": row["name"]} for row in new])})
    # Preserve the current catalog's useful names; importing a file never
    # overwrites an existing profile, preference, service credential or opt-out.
    rows = await contact_rows(db, tenant, ["phone:" + phone for phone in active_phones])
    members = [{"phone": row["phone"], "name": row["name"][:100]} for row in rows]
    for group in target_groups:
        changed = (await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
            SELECT :t,:g,x.phone,x.name FROM jsonb_to_recordset(CAST(:rows AS jsonb)) x(phone varchar(15),name varchar(100))
            ON CONFLICT(tenant_id,group_id,phone) DO UPDATE SET removed_at=NULL,updated_at=clock_timestamp()
            WHERE subscription_campaign_members.removed_at IS NOT NULL RETURNING phone"""),
            {"t": tenant, "g": group["id"], "rows": encode(members)})).scalars().all()
        if changed:
            await db.execute(text("""UPDATE subscription_campaign_groups SET version=version+1,updated_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": group["id"]})
    if membership_additions:
        await invalidate(db, tenant)
    return {"counts": {**preview["counts"], "existing": len(existing), "new": len(new), "added": len(new),
            "unchanged": len(existing) - len(archived), "archived_skipped": len(archived),
            "invalid_skipped": preview["counts"]["invalid"], "memberships_added": membership_additions},
            "group_ids": preview["group_ids"], "source_hash": preview["source_hash"]}


async def preferences(db, tenant, phone):
    """Internal ingress/final-send gate. Unknown contacts keep existing behavior."""
    digits = str(phone or "").removeprefix("+")
    if re.fullmatch(r"[1-9][0-9]{7,14}", digits) is None:
        return {"chatbot_enabled": True, "audio_transcription_enabled": True}
    row = (await db.execute(text("""SELECT chatbot_enabled,audio_transcription_enabled,removed_at
        FROM subscription_audience_contacts WHERE tenant_id=:t AND phone=:phone"""),
        {"t": tenant, "phone": digits})).mappings().one_or_none()
    if not row:
        return {"chatbot_enabled": True, "audio_transcription_enabled": True}
    return {"chatbot_enabled": row["removed_at"] is None and row["chatbot_enabled"],
            "audio_transcription_enabled": row["removed_at"] is None and row["audio_transcription_enabled"]}


async def contact_facts(db, tenant, phone, *, include_password=False):
    """Internal per-recipient facts. Never attach this result to generic webhooks.

    Secret decryption is opt-in for an explicitly configured {password} text
    response to this exact recipient. List/detail/export do not call this helper.
    """
    digits = str(phone or "").removeprefix("+")
    if re.fullmatch(r"[1-9][0-9]{7,14}", digits) is None:
        return {}
    row = (await db.execute(text("""SELECT name,plan_name,price_cents,due_date,service_username,
        service_password_encrypted FROM subscription_audience_contacts
        WHERE tenant_id=:t AND phone=:phone AND removed_at IS NULL"""),
        {"t": tenant, "phone": digits})).mappings().one_or_none()
    if not row:
        return {}
    result = {"name": row["name"], "plan": row["plan_name"] or "", "username": row["service_username"] or "",
        "amount": "" if row["price_cents"] is None else f'{row["price_cents"] / 100:.2f}'.replace(".", ","),
        "expires_at": row["due_date"].strftime("%d/%m/%Y") if row["due_date"] else ""}
    if include_password:
        result["password"] = ""
        if row["service_password_encrypted"]:
            try:
                result["password"] = channels._cipher().decrypt(row["service_password_encrypted"].encode("ascii")).decode("utf-8")
            except (InvalidToken, ValueError, UnicodeError):
                raise fail("AUDIENCE_INVALID", 503, "A credencial salva está indisponível.") from None
    return result
