"""Owner-scoped bulk snapshots, explicit row identity and read-only projections."""
from copy import deepcopy
from datetime import timedelta
import json
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_chatbot_store as shared
from app import subscription_campaign_store as campaigns
from app import subscription_schedule_store as catalog
from app import subscription_bulk_policy as policy
from app import subscription_bulk_schemas as schemas
from app.customer_names import useful_name

FEATURE = "bulk_messages.manage"
encode, fingerprint = shared.encode, shared.fingerprint
ERRORS = {
    "BULK_INVALID": "Confira os campos, os telefones e o conteúdo das linhas.",
    "BULK_NOT_FOUND": "Lote, contato ou pedido não encontrado nesta empresa.",
    "BULK_CONFLICT": "Os dados mudaram. Atualize e revise novamente.",
    "BULK_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
    "BULK_REVIEW_EXPIRED": "A revisão expirou. Faça uma nova revisão.",
    "BULK_LIMIT": "O lote ou conteúdo ultrapassa os limites. Reduza as linhas ou o texto.",
    "BULK_NO_ELIGIBLE": "Nenhuma linha deste lote está autorizada para envio.",
    "BULK_DEVICE_UNAVAILABLE": "O dispositivo não está disponível para esta operação.",
    "BULK_TEMPLATE_CHANGED": "O template ou seu arquivo mudou. Faça uma nova revisão.",
    "BULK_CONTACT_CHANGED": "Um contato selecionado mudou ou deixou o catálogo.",
    "BULK_SOURCE_REMOVED": "O contato deixou suas fontes confirmadas.",
    "BULK_OPTED_OUT": "A autorização deste contato foi revogada.",
    "BULK_SELF_CONTACT": "O próprio número verificado do dispositivo não recebe esta mensagem.",
    "BULK_REVOKED": "A autorização deste lote não está mais vigente.",
    "BULK_CONTEXT_CHANGED": "O dispositivo ou a autorização confirmada mudou.",
    "BULK_CANCELLED": "O trabalho pendente foi cancelado.",
    "BULK_BUSY": "A operação anterior ainda está em andamento.",
    "BULK_EFFECT_EXPIRED": "A tentativa expirou sem confirmação de envio.",
    "BULK_PREDECESSOR_FAILED": "A sequência parou após uma parte não confirmada.",
    "MESSAGE_OUTCOME_UNCERTAIN": "Não foi possível confirmar o resultado. Não houve repetição automática.",
}


def fail(code, status=409, **extra):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "A operação não pôde ser concluída."), **extra},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


async def clock(db):
    return (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def timezone_for(db, tenant):
    zone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant})).scalar_one()
    try:
        if zone in {"Factory", "localtime"}:
            raise ValueError()
        ZoneInfo(zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise fail("BULK_INVALID", 422) from None
    return zone


def bounded_json(value, maximum):
    try:
        size = len(json.dumps(jsonable_encoder(value), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, UnicodeError):
        raise fail("BULK_INVALID", 422) from None
    if size > maximum:
        raise fail("BULK_LIMIT", 422)
    return size


async def batch_row(db, tenant, identity, *, lock=False):
    row = (await db.execute(text("SELECT * FROM subscription_bulk_batches WHERE tenant_id=:t AND id=:id" +
        (" FOR UPDATE" if lock else "")), {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("BULK_NOT_FOUND", 404)
    return dict(row)


async def request_row(db, tenant, actor, key):
    row = (await db.execute(text("""SELECT * FROM subscription_bulk_requests
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": key})).mappings().one_or_none()
    if row and row["actor_account_id"] != actor.account_id:
        raise fail("BULK_NOT_FOUND", 404)
    return dict(row) if row else None


async def existing_request(db, tenant, actor, key, kind, payload, *, bulk_id=None):
    row = await request_row(db, tenant, actor, key)
    if row and (row["kind"] != kind or row["request_fingerprint"] != fingerprint(payload)
                or bulk_id is not None and row["bulk_id"] != bulk_id):
        raise fail("BULK_IDEMPOTENCY_CONFLICT")
    return row


def operation_dto(row, now, *, replayed=False):
    expired = row["status"] == "prepared" and row["expires_at"] <= now
    return {"request_id": row["id"], "idempotency_key": row["idempotency_key"], "kind": row["kind"],
        "status": "expired" if expired else row["status"], "batch_id": row["bulk_id"], "replayed": replayed,
        "created_at": row["created_at"], "expires_at": row["expires_at"], "result": row["result"],
        "preview": row["preview"] if row["status"] == "prepared" and not expired else None}


async def save_request(db, tenant, actor, key, kind, payload, *, bulk_id=None, snapshot=None,
                       preview=None, result=None, identity=None, now=None):
    now = now or await clock(db)
    for value, limit in ((snapshot, schemas.MAX_EXPANDED_BYTES), (preview, schemas.MAX_METADATA_BYTES), (result, 4194304)):
        if value is not None:
            bounded_json(value, limit)
    prepared = snapshot is not None
    row = (await db.execute(text("""INSERT INTO subscription_bulk_requests
        (id,tenant_id,actor_account_id,idempotency_key,kind,request_fingerprint,bulk_id,status,review_version,
          snapshot,preview,result,created_at,expires_at)
        VALUES(:id,:t,:a,:key,:kind,:fp,:bulk,:status,:review,CAST(:snapshot AS jsonb),CAST(:preview AS jsonb),
          CAST(:result AS jsonb),:now,:expires) RETURNING *"""),
        {"id": identity or uuid4(), "t": tenant, "a": actor.account_id, "key": key, "kind": kind,
         "fp": fingerprint(payload), "bulk": bulk_id, "status": "prepared" if prepared else "succeeded",
         "review": fingerprint(snapshot) if prepared else None, "snapshot": encode(snapshot) if prepared else None,
         "preview": encode(preview) if preview is not None else None, "result": encode(result) if result is not None else None,
         "now": now, "expires": now + timedelta(seconds=schemas.REVIEW_SECONDS) if prepared else None})).mappings().one()
    return dict(row)


async def complete_request(db, tenant, request_id, result, now, *, bulk_id):
    bounded_json(result, 4194304)
    row = (await db.execute(text("""UPDATE subscription_bulk_requests SET status='succeeded',confirmed_at=:now,
        result=CAST(:result AS jsonb),bulk_id=:bulk WHERE tenant_id=:t AND id=:id RETURNING *"""),
        {"t": tenant, "id": request_id, "now": now, "result": encode(result), "bulk": bulk_id})).mappings().one()
    return dict(row)


async def template_snapshot(db, tenant, identity):
    try:
        return await campaigns.template_snapshot(db, tenant, identity)
    except HTTPException as exc:
        if exc.status_code in {404, 409, 422}:
            raise fail("BULK_TEMPLATE_CHANGED") from None
        raise


def selection_params(tenant, selection):
    return {"t": tenant, "groups": [UUID(str(v)) for v in selection["group_ids"]],
            "phones": [v.removeprefix("phone:") for v in selection["contact_keys"]],
            "all_contacts": selection["all_contacts"]}


async def selected_contacts(db, tenant, selection):
    params = selection_params(tenant, selection)
    counts = (await db.execute(text(catalog.SELECTION_SQL + """SELECT
        (SELECT count(*) FROM selected) AS total,(SELECT count(*) FROM direct) AS direct,
        (SELECT count(*) FROM members)+(SELECT count(*) FROM direct)-(SELECT count(*) FROM selected) AS duplicates
        """), params)).mappings().one()
    if counts["total"] > schemas.MAX_RECIPIENTS:
        raise fail("BULK_LIMIT", 422)
    if not selection["all_contacts"] and counts["direct"] != len(selection["contact_keys"]):
        raise fail("BULK_CONTACT_CHANGED", 422)
    rows = (await db.execute(text(catalog.SELECTION_SQL + """SELECT ch.phone,
        ARRAY(SELECT n.name FROM catalog n WHERE n.phone=ch.phone ORDER BY n.priority,n.ordering) AS names,
        jsonb_agg(ch.origin ORDER BY ch.priority,ch.ordering) AS origins,
        COALESCE(p.version,0) AS permission_version,COALESCE(p.revoked,false) AS blocked
        FROM chosen ch LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=:t AND p.phone=ch.phone
        GROUP BY ch.phone,p.version,p.revoked ORDER BY ch.phone LIMIT 501"""), params)).mappings().all()
    if len(rows) != counts["total"]:
        raise fail("BULK_CONFLICT")
    contacts = []
    for row in rows:
        if not row["origins"] or len(row["origins"]) > 101:
            raise fail("BULK_LIMIT", 422)
        bounded_json(row["origins"], schemas.MAX_PLAN_BYTES)
        name = next((value for raw in row["names"] if (value := useful_name(raw))), "+" + row["phone"])
        contacts.append({"phone": row["phone"], "name": name, "origins": row["origins"],
                         "permission_version": row["permission_version"], "blocked": row["blocked"]})
    return contacts, int(counts["duplicates"])


def contact_dto(row):
    return {"key": "phone:" + row["phone"], "phone_e164": "+" + row["phone"], "name": row["name"],
        "available": not row["blocked"], "blocked_reason": ERRORS["BULK_OPTED_OUT"] if row["blocked"] else None,
        "version": fingerprint({"origins": row["origins"], "permission_version": row["permission_version"]})}


async def contacts_page(db, tenant, q, page, now, zone):
    # Reuse the exact catalog, ranking and paging. It performs only SELECTs.
    value = await catalog.contacts_page(db, tenant, q, page, now, zone)
    for item in value["items"]:
        if not item["available"]:
            item["blocked_reason"] = ERRORS["BULK_OPTED_OUT"]
    return value


def source_valid_sql(alias="r"):
    if re.fullmatch(r"[a-z][a-z0-9_]*", alias) is None:
        raise ValueError("Internal SQL alias required")
    return (f"(({alias}.source='manual' AND {alias}.manual_consent=true AND {alias}.origins='[]'::jsonb) OR "
            f"({alias}.source='catalog' AND " + catalog.source_valid_sql(alias) + "))")


SOURCE_VALID_SQL = source_valid_sql()


async def sources_valid(db, tenant, origins, phone, *, source="catalog", manual_consent=False):
    if not isinstance(origins, list) or source not in {"manual", "catalog"}:
        return False
    if source == "manual":
        return manual_consent is True and not origins and re.fullmatch(r"[1-9][0-9]{7,14}", phone or "") is not None
    return await catalog.sources_valid(db, tenant, origins, phone)


async def capture_review(db, tenant, actor, definition):
    zone = await timezone_for(db, tenant)
    items = definition["items"]
    catalog_keys = sorted({item["contact_key"] for item in items if item["source"] == "catalog"})
    contacts, _ = await selected_contacts(db, tenant,
        {"group_ids": [], "contact_keys": catalog_keys, "all_contacts": False}) if catalog_keys else ([], 0)
    contact_map = {row["phone"]: row for row in contacts}
    phones = sorted({item["phone"] if item["source"] == "manual" else item["contact_key"][6:] for item in items})
    permissions = (await db.execute(text("""SELECT phone,version,revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=ANY(CAST(:phones AS text[]))"""), {"t": tenant, "phones": phones})).mappings().all()
    permissions = {row["phone"]: row for row in permissions}
    devices, templates = {}, {}
    for item in items:
        key = item["device_id"]
        if key not in devices:
            try:
                device, channel = await shared.check_device(db, tenant, UUID(key), actor_id=actor.account_id)
            except HTTPException:
                raise fail("BULK_DEVICE_UNAVAILABLE") from None
            if device["status"] == "disabled" or not channel["enabled"]:
                raise fail("BULK_DEVICE_UNAVAILABLE")
            runtime = device.get("runtime") or {}
            own_phone = runtime.get("phone") if runtime.get("phone_verified") is True else None
            if not isinstance(own_phone, str) or re.fullmatch(r"[1-9][0-9]{7,14}", own_phone) is None:
                own_phone = None
            devices[key] = jsonable_encoder({**{name: device[name] for name in
                ("id", "name", "generation", "owner_account_id", "channel_id")},
                "settings_revision": device["revision"], "phone_e164": "+" + own_phone if own_phone else None})
        template_id = item.get("template_id")
        if template_id and template_id not in templates:
            templates[template_id] = await template_snapshot(db, tenant, UUID(template_id))
    business = (await db.execute(text("""SELECT t.name,a.email,b.configuration->>'contact_phone' AS contact_phone
        FROM tenants t JOIN panel_accounts a ON a.id=:a LEFT JOIN business_onboarding b ON b.tenant_id=t.id
        WHERE t.id=:t"""), {"t": tenant, "a": actor.account_id})).mappings().one()
    facts = {"my_name": business["name"], "my_email": business["email"], "my_contact_number": business["contact_phone"] or ""}
    rows, expanded = [], 2
    for ordinal, item in enumerate(items, 1):
        phone = item["phone"] if item["source"] == "manual" else item["contact_key"][6:]
        contact = contact_map.get(phone) if item["source"] == "catalog" else None
        if item["source"] == "catalog" and contact is None:
            raise fail("BULK_CONTACT_CHANGED")
        permission = permissions.get(phone, {"version": 0, "revoked": False})
        device, template = devices[item["device_id"]], templates.get(item.get("template_id"))
        name = contact["name"] if contact else "Contato"
        reason = "BULK_OPTED_OUT" if permission["revoked"] else None
        if device["phone_e164"] == "+" + phone:
            reason = "BULK_SELF_CONTACT"
        try:
            plan = policy.frozen_plan(item, template, {**facts, "name": name, "phone": "+" + phone, "phone_number": phone})
        except (ValueError, KeyError, TypeError):
            raise fail("BULK_INVALID", 422) from None
        bounded_json(plan, schemas.MAX_PLAN_BYTES)
        context = {"device": device, "template": template}
        bounded_json(context, schemas.MAX_PLAN_BYTES)
        row = {"row_key": item["row_key"], "ordinal": ordinal, "phone": phone, "name": name,
            "source": item["source"], "origins": contact["origins"] if contact else [],
            "permission_version": permission["version"], "blocked": reason is not None, "blocked_reason": reason,
            "context": context, "plan": plan, "plan_hash": fingerprint(plan)}
        expanded += bounded_json(row, schemas.MAX_EXPANDED_BYTES) + 1024
        if expanded > schemas.MAX_EXPANDED_BYTES:
            raise fail("BULK_LIMIT", 422)
        rows.append(row)
    blocked = sum(row["blocked"] for row in rows)
    counts = {"total": len(rows), "eligible": len(rows) - blocked, "blocked": blocked,
              **policy.duplicate_counts(row["phone"] for row in rows)}
    snapshot = jsonable_encoder({"definition": definition, "timezone": zone, "facts": facts, "rows": rows, "counts": counts})
    bounded_json(snapshot, schemas.MAX_EXPANDED_BYTES)
    return snapshot


def metadata_snapshot(snapshot, actor, request_id, now):
    value = {"timezone": snapshot["timezone"], "interval_seconds": snapshot["definition"]["interval_seconds"],
        "facts": deepcopy(snapshot["facts"]), "counts": deepcopy(snapshot["counts"]), "review_request_id": str(request_id),
        "consent": {"confirmed": True, "actor_account_id": str(actor.account_id),
                    "review_request_id": str(request_id), "confirmed_at": jsonable_encoder(now)}}
    bounded_json(value, schemas.MAX_METADATA_BYTES)
    return value


def message_preview(plan, template):
    parts = []
    for action in plan:
        part = {"type": action["type"], "text": action.get("text") or action.get("caption") or ""}
        for key in ("file_name", "latitude", "longitude"):
            if key in action:
                part[key] = action[key]
        parts.append(part)
    first = plan[0]
    return {"type": first["type"], "template_name": template["name"] if template else None,
        "text": first.get("text"), "caption": first.get("caption"),
        "followup": "\n".join(action.get("text", "") for action in plan[1:]) or None, "parts": parts}


def row_preview(row):
    device, template = row["context"]["device"], row["context"]["template"]
    return {"row_key": row["row_key"], "ordinal": row["ordinal"], "source": row["source"],
        "phone_e164": "+" + row["phone"], "name": row["name"],
        "device": {key: device.get(key) for key in ("id", "name", "phone_e164")},
        "message_type": "template" if template else "text",
        "template": {key: template[key] for key in ("id", "name", "kind")} if template else None,
        "message_preview": message_preview(row["plan"], template), "available": not row["blocked"],
        "blocked_reason": ERRORS.get(row["blocked_reason"]) if row["blocked_reason"] else None,
        "error_code": row["blocked_reason"]}


def review_preview(snapshot, identity, now):
    return {"review_version": fingerprint(snapshot), "expires_at": now + timedelta(seconds=schemas.REVIEW_SECONDS),
        "interval_seconds": snapshot["definition"]["interval_seconds"], "timezone": snapshot["timezone"],
        **snapshot["counts"], "items": [row_preview(row) for row in snapshot["rows"]]}


async def insert_recipients(db, tenant, bulk_id, run_version, snapshot, now):
    rows = []
    for item in snapshot["rows"]:
        device, template = item["context"]["device"], item["context"]["template"]
        rows.append({"id": str(uuid4()), **{key: item[key] for key in
            ("row_key", "ordinal", "phone", "name", "source", "origins", "permission_version", "context", "plan", "plan_hash")},
            "manual_consent": item["source"] == "manual", "device_id": device["id"],
            "generation": device["generation"], "settings_revision": device["settings_revision"],
            "template_id": template["id"] if template else None, "template_revision": template["revision"] if template else None,
            "status": "cancelled" if item["blocked"] else "pending", "finished_at": now if item["blocked"] else None,
            "error_code": item["blocked_reason"]})
    bounded_json(rows, schemas.MAX_EXPANDED_BYTES)
    await db.execute(text("""INSERT INTO subscription_bulk_recipients
        (id,tenant_id,bulk_id,run_version,row_key,ordinal,phone,name,source,origins,manual_consent,permission_version,
          device_id,generation,settings_revision,template_id,template_revision,context,plan,plan_hash,status,finished_at,error_code)
        SELECT x.id,:t,:bulk,:run,x.row_key,x.ordinal,x.phone,x.name,x.source,x.origins,x.manual_consent,x.permission_version,
          x.device_id,x.generation,x.settings_revision,x.template_id,x.template_revision,x.context,x.plan,x.plan_hash,
          x.status,x.finished_at,x.error_code FROM jsonb_to_recordset(CAST(:rows AS jsonb))
          AS x(id uuid,row_key uuid,ordinal integer,phone varchar(15),name varchar(120),source varchar(16),origins jsonb,
          manual_consent boolean,permission_version bigint,device_id uuid,generation uuid,settings_revision bigint,
          template_id uuid,template_revision bigint,context jsonb,plan jsonb,plan_hash char(64),status varchar(16),
          finished_at timestamptz,error_code varchar(80))"""),
        {"t": tenant, "bulk": bulk_id, "run": run_version, "rows": encode(rows)})


def page_response(items, total, page, now, zone):
    return {"items": items, "total": total, "page": page, "page_size": schemas.PAGE_SIZE,
        "pages": max(1, (total + schemas.PAGE_SIZE - 1) // schemas.PAGE_SIZE),
        "timezone": zone, "generated_at": now}


RECIPIENT_STATES_SQL = """WITH states AS (
 SELECT r.*,b.owner_account_id,CASE
   WHEN a.parts=0 THEN r.status
   WHEN a.accepted=a.parts AND a.parts=jsonb_array_length(r.plan) THEN 'sent'
   WHEN a.uncertain THEN 'uncertain'
   WHEN a.running THEN 'running'
   WHEN a.failed AND a.accepted=0 AND a.first_error IN
     ('BULK_CANCELLED','BULK_OPTED_OUT','BULK_SOURCE_REMOVED','BULK_REVOKED',
      'BULK_CONTEXT_CHANGED','BULK_TEMPLATE_CHANGED','BULK_SELF_CONTACT') THEN 'cancelled'
   WHEN a.failed THEN 'failed' ELSE 'pending' END AS public_status,
   CASE WHEN a.uncertain THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE COALESCE(a.first_error,r.error_code) END AS public_error,
   CASE WHEN a.parts>0 AND a.accepted=a.parts AND a.parts=jsonb_array_length(r.plan)
     THEN a.accepted_at ELSE NULL END AS sent_at
 FROM subscription_bulk_recipients r JOIN subscription_bulk_batches b
   ON b.tenant_id=r.tenant_id AND b.id=r.bulk_id AND b.run_version=r.run_version
 LEFT JOIN LATERAL (
   SELECT count(*) AS parts,count(*) FILTER(WHERE e.status='accepted') AS accepted,
     max(e.finished_at) FILTER(WHERE e.status='accepted') AS accepted_at,
     bool_or(e.status='uncertain' OR e.status='running' AND e.authorized_at<CAST(:now AS timestamptz)-interval '180 seconds') AS uncertain,
     bool_or(e.status='running' AND e.authorized_at>=CAST(:now AS timestamptz)-interval '180 seconds') AS running,
     bool_or(e.status IN ('failed','not_sent') OR e.status='pending' AND e.expires_at<=:now) AS failed,
     (array_agg(CASE WHEN e.status='pending' AND e.expires_at<=:now THEN 'BULK_EFFECT_EXPIRED' ELSE e.error_code END
       ORDER BY e.bulk_part_index) FILTER(WHERE (e.status IN ('failed','not_sent') AND e.error_code IS NOT NULL)
         OR e.status='pending' AND e.expires_at<=:now))[1] AS first_error
   FROM subscription_device_effects e WHERE e.tenant_id=r.tenant_id AND e.bulk_recipient_id=r.id
 ) a ON true WHERE r.tenant_id=:t AND b.owner_account_id=:actor
   AND (CAST(:only_bulk AS uuid) IS NULL OR r.bulk_id=CAST(:only_bulk AS uuid))
) """


BATCH_VIEW_SQL = RECIPIENT_STATES_SQL + """, totals AS (
 SELECT bulk_id,count(*) AS n_total,count(*) FILTER(WHERE public_status IN ('pending','running')) AS n_pending,
   count(*) FILTER(WHERE public_status='sent') AS n_sent,count(*) FILTER(WHERE public_status='failed') AS n_failed,
   count(*) FILTER(WHERE public_status='uncertain') AS n_uncertain,
   count(*) FILTER(WHERE public_status='cancelled') AS n_cancelled FROM states GROUP BY bulk_id
), viewed AS (
 SELECT b.*,COALESCE(n_total,0) AS n_total,COALESCE(n_pending,0) AS n_pending,COALESCE(n_sent,0) AS n_sent,
   COALESCE(n_failed,0) AS n_failed,COALESCE(n_uncertain,0) AS n_uncertain,COALESCE(n_cancelled,0) AS n_cancelled,
   CASE WHEN b.status IN ('pending','running') AND n_total>0 AND n_pending=0 THEN
     CASE WHEN n_uncertain>0 THEN 'uncertain' WHEN n_failed>0 THEN 'failed'
       WHEN n_sent>0 THEN 'completed' ELSE 'cancelled' END ELSE b.status END AS effective_status
 FROM subscription_bulk_batches b LEFT JOIN totals t ON t.bulk_id=b.id
 WHERE b.tenant_id=:t AND b.owner_account_id=:actor
   AND (CAST(:only_bulk AS uuid) IS NULL OR b.id=CAST(:only_bulk AS uuid))
) """


def batch_dto(row, now):
    total = int(row.get("n_total", 0))
    counts = {key: int(row.get("n_" + key, 0)) for key in ("pending", "sent", "failed", "uncertain", "cancelled")}
    processed = total - counts["pending"]
    state = row.get("effective_status", row["status"])
    return {"batch_id": row["id"], "version": row["version"], "status": state, "status_label": policy.LABELS[state],
        "interval_seconds": row["interval_seconds"], "timezone": row["snapshot"]["timezone"],
        "created_at": row["created_at"], "confirmed_at": row["confirmed_at"], "finished_at": row["finished_at"],
        "progress": {"total": total, "processed": processed, **counts,
                     "percent": round(processed * 100 / total, 2) if total else 0},
        "actions": ["cancel"] if state in {"pending", "running"} and row.get("cancelled_at") is None else []}


async def batch_item(db, tenant, bulk_id, actor, now):
    row = (await db.execute(text(BATCH_VIEW_SQL + "SELECT * FROM viewed WHERE id=:id"),
        {"t": tenant, "actor": actor.account_id, "only_bulk": bulk_id, "now": now, "id": bulk_id})).mappings().one_or_none()
    if row is None:
        raise fail("BULK_NOT_FOUND", 404)
    return batch_dto(dict(row), now)


async def recipient_items(db, tenant, rows):
    ids = [row["id"] for row in rows]
    effects = (await db.execute(text("""SELECT id,bulk_recipient_id,bulk_part_index,status,error_code,
        payload->'action'->>'type' AS type FROM subscription_device_effects
        WHERE tenant_id=:t AND bulk_recipient_id=ANY(CAST(:ids AS uuid[]))
        ORDER BY bulk_part_index,id"""), {"t": tenant, "ids": ids})).mappings().all() if ids else []
    by_recipient = {}
    for effect in effects:
        by_recipient.setdefault(effect["bulk_recipient_id"], []).append(
            {key: effect[key] for key in ("id", "type", "status", "error_code")})
    items = []
    for row in rows:
        value = row_preview({**row, "blocked": row["public_status"] == "cancelled", "blocked_reason": row["public_error"]})
        value.pop("available")
        value.pop("blocked_reason")
        value.update({"id": row["id"], "batch_id": row["bulk_id"], "status": row["public_status"],
            "status_label": policy.RECIPIENT_LABELS[row["public_status"]], "error_code": row["public_error"],
            "error_message": ERRORS.get(row["public_error"]), "parts": by_recipient.get(row["id"], []),
            "sent_at": row["sent_at"], "created_at": row["created_at"]})
        items.append(value)
    return items


SEARCH_SQL = """ WHERE (:status='' OR public_status=:status) AND (:q='' OR
  position(:q in lower(name||' '||phone||' '||COALESCE(context->'device'->>'name','')||' '||
    COALESCE(context->'template'->>'name','')))>0) """


async def recipient_page(db, batch, now, *, status="", q="", page=1):
    params = {"t": batch["tenant_id"], "actor": batch["owner_account_id"], "only_bulk": batch["id"],
              "now": now, "status": status, "q": q.strip().casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
    total = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT count(*) FROM states" + SEARCH_SQL), params)).scalar_one()
    rows = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT * FROM states" + SEARCH_SQL +
        " ORDER BY ordinal,id LIMIT 30 OFFSET :offset"), params)).mappings().all()
    return page_response(await recipient_items(db, batch["tenant_id"], rows), total, page, now, batch["snapshot"]["timezone"])


async def history_page(db, tenant, actor, now, zone, *, status="", q="", page=1):
    params = {"t": tenant, "actor": actor.account_id, "only_bulk": None, "now": now, "zone": zone,
              "status": status, "q": q.strip().casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
    counts_row = (await db.execute(text(RECIPIENT_STATES_SQL + """SELECT count(*) AS total,
        count(*) FILTER(WHERE sent_at>=CAST(:now AS timestamptz)-interval '30 days') AS last_30_days,
        count(*) FILTER(WHERE (sent_at AT TIME ZONE :zone)::date=
          (CAST(:now AS timestamptz) AT TIME ZONE :zone)::date) AS today
        FROM states WHERE public_status='sent' AND sent_at IS NOT NULL AND sent_at<=:now"""), params)).mappings().one()
    total = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT count(*) FROM states" + SEARCH_SQL), params)).scalar_one()
    rows = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT * FROM states" + SEARCH_SQL +
        " ORDER BY COALESCE(sent_at,created_at) DESC,id DESC LIMIT 30 OFFSET :offset"), params)).mappings().all()
    active_total = (await db.execute(text(BATCH_VIEW_SQL +
        "SELECT count(*) FROM viewed WHERE effective_status IN ('pending','running')"), params)).scalar_one()
    active = (await db.execute(text(BATCH_VIEW_SQL + """SELECT * FROM viewed
        WHERE effective_status IN ('pending','running') ORDER BY created_at DESC,id DESC LIMIT 30"""), params)).mappings().all()
    return page_response(await recipient_items(db, tenant, rows), total, page, now, zone) | {
        "counts": {key: int(counts_row[key]) for key in ("total", "last_30_days", "today")},
        "active_batches": [batch_dto(dict(row), now) for row in active], "active_batches_total": active_total}
