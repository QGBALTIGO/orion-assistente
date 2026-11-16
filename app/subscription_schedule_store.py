"""Owner-scoped scheduled-message snapshots, catalog and read projections."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_chatbot_store as shared
from app import subscription_campaign_store as campaigns
from app import subscription_schedule_policy as policy
from app import subscription_schedule_schemas as schemas
from app.customer_names import useful_name

FEATURE = "scheduled_messages.manage"
encode, fingerprint = shared.encode, shared.fingerprint
ERRORS = {
    "SCHEDULE_INVALID": "Confira os campos, os contatos e o horário do agendamento.",
    "SCHEDULE_NOT_FOUND": "Agendamento ou pedido não encontrado nesta empresa.",
    "SCHEDULE_CONFLICT": "O agendamento mudou. Atualize e revise novamente.",
    "SCHEDULE_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
    "SCHEDULE_REVIEW_EXPIRED": "A revisão expirou. Faça uma nova revisão.",
    "SCHEDULE_LIMIT": "A seleção ou o conteúdo ultrapassa os limites. Reduza os destinatários ou o texto.",
    "SCHEDULE_NO_ELIGIBLE": "Nenhum destinatário desta seleção está autorizado.",
    "SCHEDULE_DEVICE_UNAVAILABLE": "O dispositivo precisa estar disponível antes do envio.",
    "SCHEDULE_TEMPLATE_CHANGED": "O template ou seu arquivo mudou. Faça uma nova revisão.",
    "SCHEDULE_CONTACT_CHANGED": "Um contato selecionado mudou ou deixou o catálogo.",
    "SCHEDULE_SOURCE_REMOVED": "O contato deixou suas fontes confirmadas.",
    "SCHEDULE_OPTED_OUT": "A autorização deste contato foi revogada.",
    "SCHEDULE_SELF_CONTACT": "O próprio número verificado do dispositivo não recebe este agendamento.",
    "SCHEDULE_REVOKED": "A autorização deste agendamento não está mais vigente.",
    "SCHEDULE_CONTEXT_CHANGED": "O dispositivo ou a autorização confirmada mudou.",
    "SCHEDULE_CANCELLED": "O trabalho pendente foi cancelado.",
    "SCHEDULE_BUSY": "Esta ocorrência já iniciou envios e não permite esta alteração.",
    "SCHEDULE_PAST_DUE": "A data passou. Uma confirmação explícita é necessária para iniciar.",
    "SCHEDULE_DUE": "O horário chegou. Aguardando admissão pelo dispositivo.",
    "SCHEDULE_RECURRENCE_INVALID": "O horário deste ciclo é inexistente ou ambíguo no fuso confirmado. Edite a ocorrência.",
    "SCHEDULE_EFFECT_EXPIRED": "A tentativa expirou sem confirmação de envio.",
    "SCHEDULE_PREDECESSOR_FAILED": "A sequência parou após uma parte não confirmada.",
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
    except (ValueError, KeyError):
        raise fail("SCHEDULE_INVALID", 422) from None
    return zone


def bounded_json(value, maximum):
    try:
        size = len(json.dumps(jsonable_encoder(value), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        raise fail("SCHEDULE_INVALID", 422) from None
    if size > maximum:
        raise fail("SCHEDULE_LIMIT", 422)
    return size


async def schedule_row(db, tenant, identity, *, lock=False):
    row = (await db.execute(text("SELECT * FROM subscription_schedules WHERE tenant_id=:t AND id=:id" +
                               (" FOR UPDATE" if lock else "")), {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("SCHEDULE_NOT_FOUND", 404)
    return dict(row)


async def run_row(db, tenant, identity, *, lock=False):
    row = (await db.execute(text("SELECT * FROM subscription_schedule_runs WHERE tenant_id=:t AND id=:id" +
                               (" FOR UPDATE" if lock else "")), {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("SCHEDULE_NOT_FOUND", 404)
    return dict(row)


def check_versions(schedule, run, payload, kind):
    parent_ok = schedule["version"] == payload.expected_version
    run_ok = run is None or run["version"] == payload.expected_run_version
    if parent_ok and run_ok:
        return
    final = payload.expected_version <= schedule["version"] and (
        run is None or payload.expected_run_version <= run["version"])
    extra = {}
    if final:
        extra["rejection"] = {"reason": "stale_version", "idempotency_key": str(payload.idempotency_key),
            "kind": kind, "schedule_id": str(schedule["id"]), "run_id": str(run["id"]) if run else None,
            "expected_version": payload.expected_version,
            "expected_run_version": payload.expected_run_version if run else None}
    raise fail("SCHEDULE_CONFLICT", **extra)


async def request_row(db, tenant, actor, key):
    row = (await db.execute(text("""SELECT * FROM subscription_schedule_requests
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": key})).mappings().one_or_none()
    if row and row["actor_account_id"] != actor.account_id:
        raise fail("SCHEDULE_NOT_FOUND", 404)
    return dict(row) if row else None


async def existing_request(db, tenant, actor, key, kind, payload, *, schedule_id=None, run_id=None):
    row = await request_row(db, tenant, actor, key)
    if row and (row["kind"] != kind or row["request_fingerprint"] != fingerprint(payload)
                or schedule_id is not None and row["schedule_id"] != schedule_id
                or run_id is not None and row["run_id"] != run_id):
        raise fail("SCHEDULE_IDEMPOTENCY_CONFLICT")
    return row


def operation_dto(row, now, *, replayed=False):
    expired = row["status"] == "prepared" and row["expires_at"] <= now
    return {"request_id": row["id"], "idempotency_key": row["idempotency_key"], "kind": row["kind"],
        "status": "expired" if expired else row["status"], "schedule_id": row["schedule_id"],
        "run_id": row["run_id"], "replayed": replayed, "created_at": row["created_at"],
        "expires_at": row["expires_at"], "result": row["result"],
        "preview": row["preview"] if row["status"] == "prepared" and not expired else None}


async def save_request(db, tenant, actor, key, kind, payload, *, schedule_id=None, run_id=None,
                       snapshot=None, preview=None, result=None, identity=None, now=None):
    now = now or await clock(db)
    for value, limit in ((snapshot, schemas.MAX_EXPANDED_BYTES), (preview, schemas.MAX_METADATA_BYTES), (result, 4194304)):
        if value is not None:
            bounded_json(value, limit)
    prepared = snapshot is not None
    row = (await db.execute(text("""INSERT INTO subscription_schedule_requests
        (id,tenant_id,actor_account_id,idempotency_key,kind,request_fingerprint,schedule_id,run_id,
         status,review_version,snapshot,preview,result,created_at,expires_at)
        VALUES(:id,:t,:a,:key,:kind,:fp,:schedule,:run,:status,:review,
         CAST(:snapshot AS jsonb),CAST(:preview AS jsonb),CAST(:result AS jsonb),:now,:expires) RETURNING *"""),
        {"id": identity or uuid4(), "t": tenant, "a": actor.account_id, "key": key, "kind": kind,
         "fp": fingerprint(payload), "schedule": schedule_id, "run": run_id,
         "status": "prepared" if prepared else "succeeded", "review": fingerprint(snapshot) if prepared else None,
         "snapshot": encode(snapshot) if snapshot is not None else None,
         "preview": encode(preview) if preview is not None else None,
         "result": encode(result) if result is not None else None, "now": now,
         "expires": now + timedelta(seconds=schemas.REVIEW_SECONDS) if prepared else None})).mappings().one()
    return dict(row)


async def complete_request(db, tenant, request_id, result, now, *, schedule_id=None, run_id=None):
    bounded_json(result, 4194304)
    row = (await db.execute(text("""UPDATE subscription_schedule_requests
        SET status='succeeded',confirmed_at=:now,result=CAST(:result AS jsonb),
            schedule_id=COALESCE(:schedule,schedule_id),run_id=COALESCE(:run,run_id)
        WHERE tenant_id=:t AND id=:id RETURNING *"""),
        {"t": tenant, "id": request_id, "now": now, "result": encode(result),
         "schedule": schedule_id, "run": run_id})).mappings().one()
    return dict(row)


async def template_snapshot(db, tenant, identity):
    try:
        return await campaigns.template_snapshot(db, tenant, identity)
    except HTTPException as exc:
        if exc.status_code in {404, 409, 422}:
            raise fail("SCHEDULE_TEMPLATE_CHANGED") from None
        raise


CATALOG_SQL = """WITH catalog AS (
 SELECT substring(c.whatsapp_e164 FROM 2) AS phone,c.name,0 AS priority,c.id::text AS ordering,
   NULL::uuid AS group_id,jsonb_build_object('kind','customer','id',c.id,
       'phone',substring(c.whatsapp_e164 FROM 2),'version',c.version) AS origin
 FROM customers c WHERE c.tenant_id=:t AND c.active=true AND c.deleted_at IS NULL
   AND c.whatsapp_e164 ~ '^\\+[1-9][0-9]{7,14}$'
 UNION ALL
 SELECT m.phone,m.name,1,g.id::text,g.id,
   jsonb_build_object('kind','group_member','group_id',g.id,'phone',m.phone,
       'created_at',m.created_at,'updated_at',m.updated_at)
 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
   ON g.tenant_id=m.tenant_id AND g.id=m.group_id
 WHERE m.tenant_id=:t AND m.removed_at IS NULL AND g.archived_at IS NULL
   AND m.phone ~ '^[1-9][0-9]{7,14}$'
) """


SELECTION_SQL = CATALOG_SQL + """, direct AS (
 SELECT DISTINCT phone FROM catalog WHERE :all_contacts OR phone=ANY(CAST(:phones AS text[]))
), members AS (
 SELECT * FROM catalog WHERE group_id=ANY(CAST(:groups AS uuid[]))
), selected AS (
 SELECT phone FROM direct UNION SELECT phone FROM members
), chosen AS (
 SELECT c.* FROM catalog c JOIN direct d ON d.phone=c.phone
 UNION ALL SELECT m.* FROM members m WHERE NOT EXISTS(SELECT 1 FROM direct d WHERE d.phone=m.phone)
) """


def selection_params(tenant, selection):
    return {"t": tenant, "groups": [UUID(str(value)) for value in selection["group_ids"]],
            "phones": [key.removeprefix("phone:") for key in selection["contact_keys"]],
            "all_contacts": selection["all_contacts"]}


async def audience_counts(db, tenant, selection):
    params = selection_params(tenant, selection)
    row = (await db.execute(text(SELECTION_SQL + """SELECT
       (SELECT count(*) FROM selected) AS total,(SELECT count(*) FROM direct) AS direct,
       (SELECT count(DISTINCT phone) FROM members) AS from_groups,
       (SELECT count(*) FROM members)+(SELECT count(*) FROM direct)-(SELECT count(*) FROM selected) AS duplicates,
       (SELECT count(*) FROM selected s JOIN subscription_campaign_permissions p
         ON p.tenant_id=:t AND p.phone=s.phone WHERE p.revoked=true) AS blocked"""), params)).mappings().one()
    value = {key: int(row[key]) for key in ("total", "direct", "from_groups", "duplicates", "blocked")}
    if value["total"] > schemas.MAX_RECIPIENTS:
        raise fail("SCHEDULE_LIMIT", 422)
    if not selection["all_contacts"] and value["direct"] != len(selection["contact_keys"]):
        raise fail("SCHEDULE_CONTACT_CHANGED", 422)
    value["eligible"] = value["total"] - value["blocked"]
    return value


async def selected_contacts(db, tenant, selection):
    counts = await audience_counts(db, tenant, selection)
    if not counts["total"]:
        raise fail("SCHEDULE_NO_ELIGIBLE", 422)
    rows = (await db.execute(text(SELECTION_SQL + """SELECT ch.phone,
        ARRAY(SELECT n.name FROM catalog n WHERE n.phone=ch.phone ORDER BY n.priority,n.ordering) AS names,
        jsonb_agg(ch.origin ORDER BY ch.priority,ch.ordering) AS origins,
        COALESCE(p.version,0) AS permission_version,COALESCE(p.revoked,false) AS blocked
        FROM chosen ch LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=:t AND p.phone=ch.phone
        GROUP BY ch.phone,p.version,p.revoked ORDER BY ch.phone LIMIT 10001"""),
        selection_params(tenant, selection))).mappings().all()
    if len(rows) != counts["total"]:
        raise fail("SCHEDULE_CONFLICT")
    contacts = []
    for row in rows:
        if not row["origins"] or len(row["origins"]) > 101:
            raise fail("SCHEDULE_LIMIT", 422)
        bounded_json(row["origins"], 131072)
        name = next((value for raw in row["names"] if (value := useful_name(raw))), "+" + row["phone"])
        contacts.append({"phone": row["phone"], "name": name, "origins": row["origins"],
                         "permission_version": row["permission_version"], "blocked": row["blocked"]})
    return contacts, counts


async def contacts_page(db, tenant, q, page, now, zone):
    params = {"t": tenant, "q": q.strip().casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
    matching = """, matching AS (
       SELECT DISTINCT phone FROM catalog WHERE :q='' OR position(:q in lower(name))>0 OR position(:q in phone)>0
    ) """
    total = (await db.execute(text(CATALOG_SQL + matching + "SELECT count(*) FROM matching"), params)).scalar_one()
    rows = (await db.execute(text(CATALOG_SQL + matching + """SELECT c.phone,
        array_agg(c.name ORDER BY c.priority,c.ordering) AS names,
        jsonb_agg(c.origin ORDER BY c.priority,c.ordering) AS origins,
        COALESCE(p.version,0) AS permission_version,COALESCE(p.revoked,false) AS blocked
        FROM catalog c JOIN matching m ON m.phone=c.phone
        LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=:t AND p.phone=c.phone
        GROUP BY c.phone,p.version,p.revoked ORDER BY c.phone LIMIT 30 OFFSET :offset"""), params)).mappings().all()
    items = []
    for row in rows:
        name = next((value for raw in row["names"] if (value := useful_name(raw))), "+" + row["phone"])
        items.append({"key": "phone:" + row["phone"], "phone_e164": "+" + row["phone"], "name": name,
            "available": not row["blocked"], "blocked_reason": ERRORS["SCHEDULE_OPTED_OUT"] if row["blocked"] else None,
            "version": fingerprint({"origins": row["origins"], "permission_version": row["permission_version"]})})
    return page_response(items, total, page, now, zone)


def source_valid_sql(alias="r"):
    if re.fullmatch(r"[a-z][a-z0-9_]*", alias) is None:
        raise ValueError("Internal SQL alias required")
    return f"""EXISTS(
        SELECT 1 FROM jsonb_to_recordset({alias}.origins) o(
          kind text,id uuid,group_id uuid,phone text,version bigint,created_at timestamptz,updated_at timestamptz)
        LEFT JOIN customers c ON o.kind='customer' AND c.tenant_id={alias}.tenant_id AND c.id=o.id
        LEFT JOIN subscription_campaign_members m ON o.kind='group_member' AND m.tenant_id={alias}.tenant_id
          AND m.group_id=o.group_id AND m.phone=o.phone
        LEFT JOIN subscription_campaign_groups g ON g.tenant_id=m.tenant_id AND g.id=m.group_id
        WHERE o.phone={alias}.phone AND (
          o.kind='customer' AND c.active=true AND c.deleted_at IS NULL
            AND c.version=o.version AND c.whatsapp_e164='+'||o.phone
          OR o.kind='group_member' AND m.removed_at IS NULL AND g.archived_at IS NULL
            AND m.created_at=o.created_at AND m.updated_at=o.updated_at))"""


SOURCE_VALID_SQL = source_valid_sql()


async def sources_valid(db, tenant, origins, phone):
    if not isinstance(origins, list) or not origins or len(origins) > 101:
        return False
    try:
        bounded_json(origins, 131072)
        value = (await db.execute(text("SELECT " + SOURCE_VALID_SQL + """ FROM (
            SELECT CAST(:t AS uuid) AS tenant_id,CAST(:origins AS jsonb) AS origins,
              CAST(:phone AS text) AS phone) r"""),
            {"t": tenant, "phone": phone, "origins": encode(origins)})).scalar_one()
        return bool(value)
    except (TypeError, ValueError, HTTPException):
        return False


async def capture_review(db, tenant, actor, definition, *, schedule=None, run=None):
    now = await clock(db)
    zone = await timezone_for(db, tenant)
    value = {**definition, "timezone": zone}
    try:
        if date.fromisoformat(value["start_date"]) < now.astimezone(ZoneInfo(zone)).date():
            raise ValueError()
        due = policy.start_at(value)
    except (ValueError, OverflowError):
        raise fail("SCHEDULE_INVALID", 422) from None
    groups = await campaigns.group_summaries(db, tenant, [UUID(v) for v in value["group_ids"]])
    if len(groups) != len(value["group_ids"]):
        raise fail("SCHEDULE_NOT_FOUND", 404)
    try:
        device, channel = await shared.check_device(db, tenant, UUID(value["device_id"]), actor_id=actor.account_id)
    except HTTPException:
        raise fail("SCHEDULE_DEVICE_UNAVAILABLE") from None
    if device["status"] == "disabled" or not channel["enabled"]:
        raise fail("SCHEDULE_DEVICE_UNAVAILABLE")
    frozen_device = {key: device[key] for key in ("id", "name", "generation", "owner_account_id", "channel_id")}
    frozen_device["settings_revision"] = device["revision"]
    template = await template_snapshot(db, tenant, UUID(value["template_id"])) if value["template_id"] else None
    contacts, counts = await selected_contacts(db, tenant, value)
    runtime = device.get("runtime") or {}
    own_phone = runtime.get("phone") if runtime.get("phone_verified") is True else None
    if isinstance(own_phone, str) and re.fullmatch(r"[1-9][0-9]{7,14}", own_phone):
        for contact in contacts:
            if contact["phone"] == own_phone:
                if not contact["blocked"]:
                    counts["blocked"] += 1
                    counts["eligible"] -= 1
                contact["blocked"] = True
                contact["blocked_reason"] = "SCHEDULE_SELF_CONTACT"
    business = (await db.execute(text("""SELECT t.name,a.email,b.configuration->>'contact_phone' AS contact_phone
        FROM tenants t JOIN panel_accounts a ON a.id=:a LEFT JOIN business_onboarding b ON b.tenant_id=t.id
        WHERE t.id=:t"""), {"t": tenant, "a": actor.account_id})).mappings().one()
    facts = {"my_name": business["name"], "my_email": business["email"], "my_contact_number": business["contact_phone"] or ""}
    expanded = 2
    for contact in contacts:
        try:
            plan = policy.frozen_plan(value, template, {**facts, "name": contact["name"],
                "phone": "+" + contact["phone"], "phone_number": contact["phone"]})
        except (ValueError, KeyError, TypeError):
            raise fail("SCHEDULE_INVALID", 422) from None
        bounded_json(plan, schemas.MAX_PLAN_BYTES)
        contact["plan"], contact["plan_hash"] = plan, fingerprint(plan)
        expanded += bounded_json(contact, schemas.MAX_EXPANDED_BYTES) + 1024
        if expanded > schemas.MAX_EXPANDED_BYTES:
            raise fail("SCHEDULE_LIMIT", 422)
    try:
        end = policy.estimate_completion(due, counts["total"], value["interval_seconds"])
    except ValueError:
        raise fail("SCHEDULE_INVALID", 422) from None
    snapshot = jsonable_encoder({"definition": value, "device": frozen_device, "template": template,
        "facts": facts, "groups": groups, "contacts": contacts, "counts": counts,
        "schedule_id": schedule["id"] if schedule else None, "schedule_version": schedule["version"] if schedule else None,
        "run_id": run["id"] if run else None, "expected_run_version": run["version"] if run else None,
        "due_at": due, "estimated_end_at": end})
    bounded_json({key: item for key, item in snapshot.items() if key != "contacts"}, schemas.MAX_METADATA_BYTES)
    bounded_json(snapshot, schemas.MAX_EXPANDED_BYTES)
    return snapshot


def metadata_snapshot(snapshot, actor, request_id, now):
    frozen = {key: deepcopy(snapshot[key]) for key in ("definition", "device", "template", "facts", "groups")}
    frozen["review_request_id"] = str(request_id)
    frozen["consent"] = {"confirmed": True, "actor_account_id": str(actor.account_id),
                        "review_request_id": str(request_id), "confirmed_at": jsonable_encoder(now)}
    bounded_json(frozen, schemas.MAX_METADATA_BYTES)
    return frozen


def message_preview(snapshot):
    return campaigns.message_preview(snapshot)


def review_preview(snapshot, identity, now):
    return {"review_version": fingerprint(snapshot), "expires_at": now + timedelta(seconds=schemas.REVIEW_SECONDS),
        "title": snapshot["definition"]["title"], "definition": snapshot["definition"],
        **snapshot["counts"], "device": snapshot["device"], "groups": snapshot["groups"],
        "message_preview": message_preview(snapshot),
        "schedule": {"start_at": snapshot["due_at"], "estimated_end_at": snapshot["estimated_end_at"],
            "timezone": snapshot["definition"]["timezone"],
            "repeat_every_30_days": snapshot["definition"]["repeat_every_30_days"]}}


async def insert_recipients(db, tenant, schedule_id, run_id, run_version, snapshot, now):
    rows = []
    for index, contact in enumerate(snapshot["contacts"]):
        rows.append({"id": str(uuid4()), "ordinal": index + 1, **{key: contact[key] for key in
            ("phone", "name", "origins", "permission_version", "plan", "plan_hash")},
            "device_id": snapshot["device"]["id"], "generation": snapshot["device"]["generation"],
            "settings_revision": snapshot["device"]["settings_revision"],
            "status": "cancelled" if contact["blocked"] else "pending",
            "finished_at": now if contact["blocked"] else None,
            "error_code": contact.get("blocked_reason", "SCHEDULE_OPTED_OUT") if contact["blocked"] else None})
    bounded_json(rows, schemas.MAX_EXPANDED_BYTES)
    await db.execute(text("""INSERT INTO subscription_schedule_recipients
        (id,tenant_id,schedule_id,run_id,run_version,ordinal,phone,name,origins,permission_version,
         device_id,generation,settings_revision,plan,plan_hash,status,finished_at,error_code)
        SELECT x.id,:t,:schedule,:run,:version,x.ordinal,x.phone,x.name,x.origins,x.permission_version,
          x.device_id,x.generation,x.settings_revision,x.plan,x.plan_hash,x.status,x.finished_at,x.error_code
        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(id uuid,ordinal integer,phone varchar(15),
          name varchar(120),origins jsonb,permission_version bigint,device_id uuid,generation uuid,
          settings_revision bigint,plan jsonb,plan_hash char(64),status varchar(16),
          finished_at timestamptz,error_code varchar(80))"""),
        {"t": tenant, "schedule": schedule_id, "run": run_id, "version": run_version, "rows": encode(rows)})


def page_response(items, total, page, now, zone):
    return {"items": items, "total": total, "page": page, "page_size": schemas.PAGE_SIZE,
            "pages": max(1, (total + schemas.PAGE_SIZE - 1) // schemas.PAGE_SIZE),
            "timezone": zone, "generated_at": now}


RECIPIENT_STATES_SQL = """WITH states AS (
 SELECT r.*,CASE
   WHEN a.parts=0 THEN CASE WHEN r.status='running' THEN 'pending' ELSE r.status END
   WHEN a.accepted=a.parts AND a.parts=jsonb_array_length(r.plan) THEN 'sent'
   WHEN a.uncertain THEN 'uncertain'
   WHEN a.running THEN 'pending'
   WHEN a.failed AND a.accepted=0 AND a.first_error IN
     ('SCHEDULE_CANCELLED','SCHEDULE_OPTED_OUT','SCHEDULE_SOURCE_REMOVED','SCHEDULE_REVOKED',
      'SCHEDULE_CONTEXT_CHANGED','SCHEDULE_TEMPLATE_CHANGED','SCHEDULE_SELF_CONTACT') THEN 'cancelled'
   WHEN a.failed THEN 'failed' ELSE 'pending' END AS public_status,
   CASE WHEN a.uncertain THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE COALESCE(a.first_error,r.error_code) END AS public_error,
   COALESCE(a.running,false) AS processing
 FROM subscription_schedule_recipients r
 JOIN subscription_schedule_runs cr ON cr.tenant_id=r.tenant_id AND cr.id=r.run_id AND cr.run_version=r.run_version
 LEFT JOIN LATERAL (
   SELECT count(*) AS parts,count(*) FILTER(WHERE e.status='accepted') AS accepted,
     bool_or(e.status='uncertain' OR e.status='running' AND e.authorized_at<CAST(:now AS timestamptz)-interval '180 seconds') AS uncertain,
     bool_or(e.status='running' AND e.authorized_at>=CAST(:now AS timestamptz)-interval '180 seconds') AS running,
     bool_or(e.status IN ('failed','not_sent') OR e.status='pending' AND e.expires_at<=:now) AS failed,
     (array_agg(CASE WHEN e.status='pending' AND e.expires_at<=:now THEN 'SCHEDULE_EFFECT_EXPIRED' ELSE e.error_code END
       ORDER BY e.schedule_part_index) FILTER(WHERE (e.status IN ('failed','not_sent') AND e.error_code IS NOT NULL)
         OR e.status='pending' AND e.expires_at<=:now))[1] AS first_error
   FROM subscription_device_effects e WHERE e.tenant_id=r.tenant_id AND e.schedule_recipient_id=r.id
 ) a ON true WHERE r.tenant_id=:t AND (CAST(:only_run AS uuid) IS NULL OR r.run_id=CAST(:only_run AS uuid))
) """


RUN_VIEW_SQL = RECIPIENT_STATES_SQL + """, totals AS (
 SELECT run_id,count(*) AS n_total,count(*) FILTER(WHERE public_status='pending') AS n_pending,
   count(*) FILTER(WHERE public_status='sent') AS n_sent,count(*) FILTER(WHERE public_status='failed') AS n_failed,
   count(*) FILTER(WHERE public_status='uncertain') AS n_uncertain,
   count(*) FILTER(WHERE public_status='cancelled') AS n_cancelled,bool_or(processing) AS effect_running
 FROM states GROUP BY run_id
), base AS (
 SELECT r.*,s.version AS parent_version,s.status AS series_status,s.title AS parent_title,
   s.repeat_every_30_days,s.owner_account_id,
   COALESCE(n_total,0) AS n_total,COALESCE(n_pending,0) AS n_pending,COALESCE(n_sent,0) AS n_sent,
   COALESCE(n_failed,0) AS n_failed,COALESCE(n_uncertain,0) AS n_uncertain,COALESCE(n_cancelled,0) AS n_cancelled,
   COALESCE(effect_running,false) AS effect_running,d.status AS device_status,
   COALESCE(d.status='connected' AND d.removed_at IS NULL AND q.enabled AND NOT q.assistant_enabled
     AND jsonb_array_length(q.allowed_senders)=0 AND q.owner_account_id=s.owner_account_id
     AND q.lease_expires_at>:now AND d.generation=CAST(r.snapshot->'device'->>'generation' AS uuid)
     AND d.revision=CAST(r.snapshot->'device'->>'settings_revision' AS bigint),false) AS device_available,
   CASE WHEN r.status='pending' AND r.first_admitted_at IS NULL
       AND (r.due_at IS NULL OR r.due_at>:now) THEN 'pending'
     WHEN r.status IN ('pending','running') AND n_total>0 AND n_pending=0 THEN
     CASE WHEN n_uncertain>0 THEN 'uncertain' WHEN n_failed>0 THEN 'failed'
       WHEN n_sent>0 THEN 'completed' ELSE 'cancelled' END ELSE r.status END AS effective_status
 FROM subscription_schedule_runs r JOIN subscription_schedules s ON s.tenant_id=r.tenant_id AND s.id=r.schedule_id
 LEFT JOIN totals t ON t.run_id=r.id
 LEFT JOIN subscription_devices d ON d.tenant_id=r.tenant_id AND d.id=CAST(r.snapshot->'device'->>'id' AS uuid)
 LEFT JOIN qr_channels q ON q.tenant_id=d.tenant_id AND q.id=d.channel_id
 WHERE r.tenant_id=:t AND s.owner_account_id=:actor
   AND (CAST(:only_run AS uuid) IS NULL OR r.id=CAST(:only_run AS uuid))
), bucketed AS (
 SELECT *,CASE WHEN effective_status IN ('completed','failed','uncertain','cancelled') THEN 'history'
   WHEN effective_status='pending' AND first_admitted_at IS NULL AND manual_released_at IS NULL
     AND local_date<(CAST(:now AS timestamptz) AT TIME ZONE timezone)::date AND due_at IS NOT NULL THEN 'past_due'
   ELSE 'active' END AS bucket
 FROM base
), viewed AS (
 SELECT *,CASE WHEN bucket<>'active' THEN NULL
   WHEN effective_status='running' THEN CASE WHEN effect_running THEN 'processing' ELSE 'queued' END
   WHEN due_at IS NULL OR NOT device_available OR error_code IS NOT NULL THEN 'attention'
   WHEN due_at>:now THEN 'upcoming' ELSE 'attention' END AS active_filter
 FROM bucketed
) """


def item_dto(row, now):
    definition = row["snapshot"]["definition"]
    frozen_device = row["snapshot"]["device"]
    total = int(row.get("n_total", 0))
    counts = {key: int(row.get("n_" + key, 0)) for key in ("pending", "sent", "failed", "uncertain", "cancelled")}
    processed = total - counts["pending"]
    progress = {"total": total, "processed": processed, **counts,
                "percent": round(processed * 100 / total, 2) if total else 0}
    state = row.get("effective_status", row["status"])
    projection = policy.run_projection({**row, "status": state}, now, row.get("device_available", False))
    actions = []
    if row["series_status"] == "active":
        if state == "pending" and row["first_admitted_at"] is None:
            actions.append("edit")
        if state in {"pending", "running"}:
            actions.append("cancel_run")
        actions.append("cancel_series")
    try:
        end = policy.estimate_completion(row["due_at"], total, definition["interval_seconds"])
    except ValueError:
        end = None
    return {"schedule_id": row["schedule_id"], "run_id": row["id"],
        "title": definition["title"], "version": row["parent_version"], "run_version": row["version"],
        "occurrence_index": row["occurrence_index"], "series_status": row["series_status"],
        "status": state, **projection,
        "attention_message": ERRORS.get(projection["attention_reason"]) if projection["attention_reason"] else None,
        "device": {"id": frozen_device["id"], "name": frozen_device["name"], "status": row.get("device_status") or "removed"},
        "scheduled_at": row["due_at"], "local_date": row["local_date"], "local_time": definition["start_time"],
        "timezone": row["timezone"], "interval_seconds": definition["interval_seconds"],
        "repeat_every_30_days": definition["repeat_every_30_days"], "estimated_end_at": end,
        "progress": progress, "actions": actions, "created_at": row["created_at"]}


async def run_item(db, tenant, run_id, actor, now):
    row = (await db.execute(text(RUN_VIEW_SQL + "SELECT * FROM viewed WHERE id=:id"),
        {"t": tenant, "only_run": run_id, "actor": actor.account_id, "now": now, "id": run_id})).mappings().one_or_none()
    if row is None:
        raise fail("SCHEDULE_NOT_FOUND", 404)
    return item_dto(dict(row), now)


async def list_runs(db, tenant, actor, now, zone, *, tab="active", status="", q="", page=1, schedule_id=None):
    params = {"t": tenant, "only_run": None, "actor": actor.account_id, "now": now,
              "schedule": schedule_id, "q": q.strip().casefold(), "tab": tab, "status": status,
              "offset": (page - 1) * schemas.PAGE_SIZE}
    search = """ WHERE (CAST(:schedule AS uuid) IS NULL OR schedule_id=CAST(:schedule AS uuid))
       AND (:q='' OR position(:q in lower(snapshot->'definition'->>'title'))>0)"""
    counts_row = (await db.execute(text(RUN_VIEW_SQL + """SELECT count(*) AS "all",
       count(*) FILTER(WHERE bucket='active') AS active,count(*) FILTER(WHERE bucket='past_due') AS past_due,
       count(*) FILTER(WHERE bucket='history') AS history,
       count(*) FILTER(WHERE active_filter='attention') AS attention,
       count(*) FILTER(WHERE active_filter='queued') AS queued,
       count(*) FILTER(WHERE active_filter='processing') AS processing,
       count(*) FILTER(WHERE active_filter='upcoming') AS upcoming FROM viewed""" + search), params)).mappings().one()
    counts = {key: int(counts_row[key]) for key in ("active", "past_due", "history", "all", "attention", "queued", "processing", "upcoming")}
    filters = search + " AND (:tab='all' OR bucket=:tab) AND (:status='' OR active_filter=:status)"
    total = (await db.execute(text(RUN_VIEW_SQL + "SELECT count(*) FROM viewed" + filters), params)).scalar_one()
    rows = (await db.execute(text(RUN_VIEW_SQL + "SELECT * FROM viewed" + filters +
        " ORDER BY due_at DESC NULLS FIRST,created_at DESC,id DESC LIMIT 30 OFFSET :offset"), params)).mappings().all()
    return page_response([item_dto(dict(row), now) for row in rows], total, page, now, zone) | {"counts": counts}


async def recipient_page(db, run, now, *, status="", q="", page=1):
    params = {"t": run["tenant_id"], "only_run": run["id"], "now": now, "status": status,
              "q": q.strip().casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
    clause = " WHERE (:status='' OR public_status=:status) AND (:q='' OR position(:q in lower(name||' '||phone))>0)"
    total = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT count(*) FROM states" + clause), params)).scalar_one()
    rows = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT * FROM states" + clause +
                                  " ORDER BY ordinal,id LIMIT 30 OFFSET :offset"), params)).mappings().all()
    ids = [row["id"] for row in rows]
    effects = (await db.execute(text("""SELECT id,schedule_recipient_id,schedule_part_index,status,error_code,
        payload->'action'->>'type' AS type FROM subscription_device_effects
        WHERE tenant_id=:t AND schedule_recipient_id=ANY(CAST(:ids AS uuid[]))
        ORDER BY schedule_part_index,id"""), {"t": run["tenant_id"], "ids": ids})).mappings().all() if ids else []
    by_recipient = {}
    for effect in effects:
        by_recipient.setdefault(effect["schedule_recipient_id"], []).append(
            {key: effect[key] for key in ("id", "type", "status", "error_code")})
    items = [{"id": row["id"], "ordinal": row["ordinal"], "name": row["name"], "phone": "+" + row["phone"],
        "status": row["public_status"], "status_label": policy.RECIPIENT_LABELS[row["public_status"]],
        "error_code": row["public_error"], "error_message": ERRORS.get(row["public_error"]),
        "parts": by_recipient.get(row["id"], [])} for row in rows]
    return page_response(items, total, page, now, run["timezone"])


async def pending_targets(db, tenant, actor, now, *, ids=None):
    rows = (await db.execute(text("""SELECT r.id,r.schedule_id,r.version AS run_version,s.version,
          r.run_version AS snapshot_version
        FROM subscription_schedule_runs r JOIN subscription_schedules s
          ON s.tenant_id=r.tenant_id AND s.id=r.schedule_id
        WHERE r.tenant_id=:t AND s.owner_account_id=:a AND s.status='active'
          AND (CAST(:ids AS uuid[]) IS NULL OR r.id=ANY(CAST(:ids AS uuid[])))
          AND r.status='pending' AND r.first_admitted_at IS NULL AND r.manual_released_at IS NULL
          AND r.due_at IS NOT NULL AND r.due_at<=:now
          AND EXISTS(SELECT 1 FROM subscription_schedule_recipients p WHERE p.tenant_id=r.tenant_id
            AND p.run_id=r.id AND p.run_version=r.run_version AND p.status='pending' AND p.admitted_at IS NULL)
        ORDER BY r.due_at,r.id LIMIT 101 FOR UPDATE OF r"""),
        {"t": tenant, "a": actor.account_id, "now": now, "ids": ids})).mappings().all()
    if len(rows) > schemas.MAX_PENDING_RUNS:
        raise fail("SCHEDULE_LIMIT", 422)
    return [jsonable_encoder(dict(row)) for row in rows]
