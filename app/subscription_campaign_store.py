"""Private tenant/owner boundary and immutable campaign operation records."""
from datetime import timedelta
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_chatbot_store as shared
from app import subscription_campaign_policy as policy
from app import subscription_campaign_schemas as schemas

FEATURE = "campaigns.manage"
encode, fingerprint = shared.encode, shared.fingerprint
ERRORS = {
    "CAMPAIGN_INVALID": "Confira os campos, os telefones e os horários da campanha.",
    "CAMPAIGN_NOT_FOUND": "Registro não encontrado nesta empresa.",
    "CAMPAIGN_CONFLICT": "O registro mudou. Atualize e revise novamente.",
    "CAMPAIGN_IDEMPOTENCY_CONFLICT": "Esta chave já pertence a outro pedido.",
    "CAMPAIGN_REVIEW_EXPIRED": "A prévia expirou. Faça uma nova revisão.",
    "CAMPAIGN_REVOKED": "A autorização desta campanha não está mais vigente.",
    "CAMPAIGN_PERMISSION_CONFLICT": "A autorização de um contato mudou. Atualize o grupo.",
    "CAMPAIGN_LIMIT": "A seleção ou o conteúdo expandido ultrapassa os limites. Reduza o texto ou divida os destinatários.",
    "CAMPAIGN_NO_ELIGIBLE": "Nenhum destinatário pode receber esta campanha.",
    "CAMPAIGN_DEVICE_UNAVAILABLE": "O dispositivo não está disponível neste contexto.",
    "CAMPAIGN_TEMPLATE_CHANGED": "O template mudou ou seu arquivo está indisponível.",
    "CAMPAIGN_BUSY": "Esta campanha já iniciou envios e não pode voltar à edição.",
    "CAMPAIGN_OPTED_OUT": "Autorização de campanhas revogada.",
    "CAMPAIGN_MEMBER_REMOVED": "O contato deixou os grupos de origem da campanha.",
    "CAMPAIGN_PAUSED": "A campanha foi pausada antes de concluir esta sequência.",
    "CAMPAIGN_CANCELLED": "A campanha foi arquivada.",
    "CAMPAIGN_CONTEXT_CHANGED": "O dispositivo ou a autorização confirmada mudou.",
    "CAMPAIGN_EFFECT_EXPIRED": "A janela desta tentativa terminou sem confirmação de envio.",
    "CAMPAIGN_PREDECESSOR_FAILED": "A sequência parou após uma parte anterior não confirmada.",
    "MESSAGE_OUTCOME_UNCERTAIN": "Não foi possível confirmar o resultado; não houve repetição automática.",
}


def fail(code, status=409, **extra):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "A operação não pôde ser concluída."), **extra},
                         headers={"Cache-Control": "no-store"})


def owner_transaction(tenant, actor):
    # write=True enforces active tenant and the feature even for pure GETs.
    # This context takes locks, but creates no records or external operations.
    return shared.owner_transaction(tenant, actor, feature=FEATURE)


async def clock(db):
    return (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def campaign_row(db, tenant, identity, *, lock=False):
    row = (await db.execute(text("SELECT * FROM subscription_campaigns WHERE tenant_id=:t AND id=:id" +
        (" FOR UPDATE" if lock else "")), {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("CAMPAIGN_NOT_FOUND", 404)
    return dict(row)


async def group_row(db, tenant, identity, *, lock=False):
    row = (await db.execute(text("SELECT * FROM subscription_campaign_groups WHERE tenant_id=:t AND id=:id" +
        (" FOR UPDATE" if lock else "")), {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("CAMPAIGN_NOT_FOUND", 404)
    return dict(row)


def check_version(row, payload, kind, *, campaign_id=None, group_id=None):
    if row["version"] == payload.expected_version:
        return
    extra = {}
    # Caller holds the tenant lock and has proved this key absent. A future
    # version can become valid later; only a strictly older version is final.
    if payload.expected_version < row["version"]:
        extra["rejection"] = {"reason": "stale_version", "idempotency_key": str(payload.idempotency_key),
            "kind": kind, "campaign_id": str(campaign_id) if campaign_id else None,
            "group_id": str(group_id) if group_id else None, "expected_version": payload.expected_version}
    raise fail("CAMPAIGN_CONFLICT", **extra)


async def request_row(db, tenant, actor, key):
    row = (await db.execute(text("""SELECT * FROM subscription_campaign_requests
        WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": tenant, "key": key})).mappings().one_or_none()
    if row and row["actor_account_id"] != actor.account_id:
        raise fail("CAMPAIGN_NOT_FOUND", 404)
    return dict(row) if row else None


def operation_dto(row, now, *, replayed=False):
    expired = row["status"] == "prepared" and row["expires_at"] <= now
    return {"request_id": row["id"], "idempotency_key": row["idempotency_key"], "kind": row["kind"],
        "status": "expired" if expired else row["status"], "campaign_id": row["campaign_id"], "group_id": row["group_id"],
        "result": row["result"], "preview": row["preview"] if row["status"] == "prepared" and not expired else None,
        "replayed": replayed, "created_at": row["created_at"], "expires_at": row["expires_at"]}


async def existing_request(db, tenant, actor, key, kind, payload, *, campaign_id=None, group_id=None):
    existing = await request_row(db, tenant, actor, key)
    if existing and (existing["kind"] != kind or campaign_id is not None and existing["campaign_id"] != campaign_id
            or group_id is not None and existing["group_id"] != group_id or existing["request_fingerprint"] != fingerprint(payload)):
        raise fail("CAMPAIGN_IDEMPOTENCY_CONFLICT")
    return existing


async def save_request(db, tenant, actor, key, kind, payload, *, campaign_id=None, group_id=None,
                       result=None, snapshot=None, preview=None, identity=None, now=None):
    now = now or await clock(db)
    for value, maximum in ((result, 4194304), (snapshot, 67108864), (preview, 1048576)):
        if value is not None:
            bounded_json(value, maximum)
    row = (await db.execute(text("""INSERT INTO subscription_campaign_requests
        (id,tenant_id,actor_account_id,idempotency_key,kind,request_fingerprint,campaign_id,group_id,
         status,review_version,snapshot,preview,result,created_at,expires_at)
        VALUES(:id,:t,:a,:key,:kind,:fp,:campaign,:group,:status,:review,
          CAST(:snapshot AS jsonb),CAST(:preview AS jsonb),CAST(:result AS jsonb),:now,:expiry) RETURNING *"""),
        {"id": identity or uuid4(), "t": tenant, "a": actor.account_id, "key": key, "kind": kind,
         "fp": fingerprint(payload), "campaign": campaign_id, "group": group_id,
         "status": "prepared" if kind == "review" else "succeeded",
         "review": fingerprint(snapshot) if snapshot is not None else None,
         "snapshot": encode(snapshot) if snapshot is not None else None,
         "preview": encode(preview) if preview is not None else None,
         "result": encode(result) if result is not None else None, "now": now,
         "expiry": now + timedelta(seconds=120) if kind == "review" else None})).mappings().one()
    return dict(row)


async def group_summaries(db, tenant, ids=None, *, include_archived=False):
    rows = (await db.execute(text("""SELECT g.*,
        (SELECT count(*) FROM subscription_campaign_members m WHERE m.tenant_id=g.tenant_id
          AND m.group_id=g.id AND m.removed_at IS NULL) AS member_count,
        (SELECT count(*) FROM subscription_campaign_members m JOIN subscription_campaign_permissions p
          ON p.tenant_id=m.tenant_id AND p.phone=m.phone WHERE m.tenant_id=g.tenant_id AND m.group_id=g.id
          AND m.removed_at IS NULL AND p.revoked) AS blocked_count
        FROM subscription_campaign_groups g WHERE g.tenant_id=:t
          AND (:archived OR g.archived_at IS NULL) AND (CAST(:ids AS uuid[]) IS NULL OR g.id=ANY(CAST(:ids AS uuid[])))
        ORDER BY lower(g.name),g.id"""), {"t": tenant, "archived": include_archived, "ids": ids})).mappings().all()
    return [{"id": row["id"], "name": row["name"], "version": row["version"], "member_count": row["member_count"],
             "blocked_count": row["blocked_count"], "archived": row["archived_at"] is not None} for row in rows]


async def group_detail(db, tenant, identity):
    await group_row(db, tenant, identity)
    group = (await group_summaries(db, tenant, [identity], include_archived=True))[0]
    rows = (await db.execute(text("""SELECT m.phone,m.name,COALESCE(p.version,0) AS permission_version,
        COALESCE(p.revoked,false) AS blocked FROM subscription_campaign_members m
        LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=m.tenant_id AND p.phone=m.phone
        WHERE m.tenant_id=:t AND m.group_id=:id AND m.removed_at IS NULL ORDER BY m.phone"""),
        {"t": tenant, "id": identity})).mappings().all()
    return {"group": group, "members": [{"name": item["name"], "phone": "+" + item["phone"],
        "blocked": item["blocked"], "permission_version": item["permission_version"],
        "blocked_reason": ERRORS["CAMPAIGN_OPTED_OUT"] if item["blocked"] else None} for item in rows]}


async def device_options(db, tenant, actor):
    rows = (await db.execute(text("""SELECT d.id,d.name,d.status,d.revision,d.generation,d.owner_account_id,
        q.enabled,q.assistant_enabled,q.allowed_senders,q.owner_account_id AS channel_owner,t.whatsapp_channel_id,d.channel_id
        FROM subscription_devices d JOIN qr_channels q ON q.tenant_id=d.tenant_id AND q.id=d.channel_id
        JOIN tenants t ON t.id=d.tenant_id WHERE d.tenant_id=:t AND d.owner_account_id=:a AND d.removed_at IS NULL
        ORDER BY d.created_at,d.id"""), {"t": tenant, "a": actor.account_id})).mappings().all()
    output = []
    canonical_id = None
    if rows:
        from app.qr_channels import _canonical_candidate
        from app.qr_legacy import is_alias
        channels = (await db.execute(text("SELECT id,tenant_id,enabled FROM qr_channels WHERE tenant_id=:t"),
            {"t": tenant})).mappings().all()
        candidate, state = _canonical_candidate(channels, rows[0]["whatsapp_channel_id"])
        if candidate is not None and state == "ready" and not is_alias(tenant, candidate["id"]):
            canonical_id = candidate["id"]
    for row in rows:
        ready = (row["status"] not in {"disabled", "removing", "removed"} and row["enabled"]
            and not row["assistant_enabled"] and not row["allowed_senders"] and canonical_id == row["channel_id"]
            and row["owner_account_id"] == row["channel_owner"])
        output.append({"id": row["id"], "name": row["name"], "status": row["status"], "available": bool(ready),
                       "blocked_reason": None if ready else ERRORS["CAMPAIGN_DEVICE_UNAVAILABLE"]})
    return output


async def template_snapshot(db, tenant, identity):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_templates
        WHERE tenant_id=:t AND id=:id AND removed_at IS NULL"""), {"t": tenant, "id": identity})).mappings().one_or_none()
    if not row or row["kind"] not in schemas.KINDS:
        raise fail("CAMPAIGN_TEMPLATE_CHANGED")
    result = {key: row[key] for key in ("id", "revision", "name", "kind", "body", "followup_body", "location", "file_name")}
    result["asset"] = None
    if row["asset_id"]:
        asset = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND id=:id AND status='ready'"),
            {"t": tenant, "id": row["asset_id"]})).mappings().one_or_none()
        if asset is None:
            raise fail("CAMPAIGN_TEMPLATE_CHANGED")
        result["asset"] = shared.asset_descriptor(asset)
    if row["kind"] not in {"text", "location"} and not result["asset"]:
        raise fail("CAMPAIGN_TEMPLATE_CHANGED")
    return jsonable_encoder(result)


async def template_options(db, tenant):
    from app.subscription_templates import capabilities
    supported = capabilities()["types"]
    rows = (await db.execute(text("""SELECT t.id,t.name,t.kind,t.revision,a.status AS asset_status
        FROM subscription_chatbot_templates t LEFT JOIN subscription_chatbot_assets a ON a.tenant_id=t.tenant_id AND a.id=t.asset_id
        WHERE t.tenant_id=:t AND t.removed_at IS NULL ORDER BY t.created_at,t.id"""), {"t": tenant})).mappings().all()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "revision": r["revision"],
        "available": bool(supported.get(r["kind"]) and (r["kind"] in {"text", "location"} or r["asset_status"] == "ready")),
        "blocked_reason": None if supported.get(r["kind"]) and (r["kind"] in {"text", "location"} or r["asset_status"] == "ready")
            else ERRORS["CAMPAIGN_TEMPLATE_CHANGED"]} for r in rows if r["kind"] in schemas.KINDS]


async def validate_references(db, tenant, actor, definition, *, for_review=False):
    groups = await group_summaries(db, tenant, [UUID(i) for i in definition["group_ids"]])
    if len(groups) != len(definition["group_ids"]):
        raise fail("CAMPAIGN_NOT_FOUND", 404)
    devices = []
    for identity in definition["device_ids"]:
        row, transport = await shared.check_device(db, tenant, UUID(identity), actor_id=actor.account_id)
        if for_review and (row["status"] == "disabled" or not transport["enabled"]):
            raise fail("CAMPAIGN_DEVICE_UNAVAILABLE")
        devices.append({"id": str(row["id"]), "name": row["name"], "generation": str(row["generation"]),
            "settings_revision": row["revision"], "channel_id": str(row["channel_id"]), "owner_account_id": str(row["owner_account_id"])})
    template = await template_snapshot(db, tenant, UUID(definition["template_id"])) if definition["template_id"] else None
    return jsonable_encoder(groups), devices, template


async def capture_review(db, campaign, actor):
    definition = campaign["definition"]
    groups, devices, template = await validate_references(db, campaign["tenant_id"], actor, definition, for_review=True)
    params = {"t": campaign["tenant_id"], "groups": [UUID(i) for i in definition["group_ids"]]}
    budget = (await db.execute(text("""SELECT count(DISTINCT m.phone) AS contacts,count(*) AS links
        FROM subscription_campaign_members m JOIN subscription_campaign_groups g
          ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
        WHERE m.tenant_id=:t AND m.group_id=ANY(CAST(:groups AS uuid[])) AND m.removed_at IS NULL"""), params)).mappings().one()
    if budget["contacts"] > schemas.MAX_RECIPIENTS:
        raise fail("CAMPAIGN_LIMIT", 422)
    # At most 10k output rows and 100 source ids per phone. Do not materialize
    # the possible 500k group memberships while holding the tenant lock.
    rows = (await db.execute(text("""SELECT m.phone,(array_agg(m.name ORDER BY m.group_id))[1] AS name,
        array_agg(m.group_id ORDER BY m.group_id) AS groups,COALESCE(p.version,0) AS permission_version,
        COALESCE(p.revoked,false) AS blocked FROM subscription_campaign_members m
        JOIN subscription_campaign_groups g ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
        LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=m.tenant_id AND p.phone=m.phone
        WHERE m.tenant_id=:t AND m.group_id=ANY(CAST(:groups AS uuid[])) AND m.removed_at IS NULL
        GROUP BY m.phone,p.version,p.revoked ORDER BY m.phone LIMIT 10001"""), params)).mappings().all()
    if len(rows) > schemas.MAX_RECIPIENTS:
        raise fail("CAMPAIGN_LIMIT", 422)
    ordered = [{**dict(item), "groups": [str(identity) for identity in item["groups"]]} for item in rows]
    business = (await db.execute(text("""SELECT t.name,a.email,b.configuration->>'contact_phone' AS contact_phone
        FROM tenants t JOIN panel_accounts a ON a.id=:a LEFT JOIN business_onboarding b ON b.tenant_id=t.id
        WHERE t.id=:t"""), {"t": campaign["tenant_id"], "a": actor.account_id})).mappings().one()
    facts = {"my_name": business["name"], "my_email": business["email"], "my_contact_number": business["contact_phone"] or ""}
    expanded_bytes = 2
    for contact in ordered:
        try:
            plan = policy.frozen_plan(definition, template, contact_facts(contact, facts))
        except (ValueError, KeyError, TypeError):
            raise fail("CAMPAIGN_INVALID", 422) from None
        bounded_json(plan, 131072)
        expanded_bytes = expanded_budget(expanded_bytes, contact, plan)
        contact["plan_hash"] = fingerprint(plan)
    return {"campaign_id": str(campaign["id"]), "campaign_version": campaign["version"],
        "actor_account_id": str(actor.account_id), "definition": definition,
        "groups": groups, "devices": devices, "template": template, "facts": facts,
        "contacts": ordered, "duplicates": budget["links"] - len(ordered)}


def contact_facts(contact, business):
    return {**business, "name": contact["name"], "phone": "+" + contact["phone"], "phone_number": contact["phone"]}


def message_preview(snapshot):
    definition, template = snapshot["definition"], snapshot["template"]
    if definition["message_type"] == "text":
        return {"type": "text", "text": definition["text"], "caption": None, "followup": None,
                "template_name": None, "parts": [{"type": "text", "text": definition["text"]}]}
    kind, body, followup = template["kind"], template["body"], template["followup_body"]
    parts = [{"type": kind, "text": body if kind == "text" else body if kind not in {"audio", "ptv", "sticker"} else ""}]
    if kind == "audio" and body:
        parts.append({"type": "text", "text": body})
    if followup:
        parts.append({"type": "text", "text": followup})
    return {"type": kind, "text": body if kind == "text" else None, "caption": body if kind != "text" else None,
            "followup": followup or None, "template_name": template["name"], "parts": parts}


def bounded_json(value, maximum):
    # PostgreSQL jsonb::text includes a space after each separator. Budget that
    # representation before INSERT, rather than the smaller wire encoding.
    if len(json.dumps(jsonable_encoder(value), ensure_ascii=False, allow_nan=False).encode("utf-8")) > maximum:
        raise fail("CAMPAIGN_LIMIT", 422)
    return value


def expanded_budget(current, contact, plan):
    # Include all per-contact facts/source ids, full personalized content, and
    # an upper bound for UUIDs, scope, timestamps and SQL envelope separators.
    # This runs incrementally before offering a preview or retaining a batch.
    facts = {key: value for key, value in contact.items() if key != "plan_hash"}
    cost = len(json.dumps(jsonable_encoder(facts), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    cost += len(json.dumps(jsonable_encoder(plan), ensure_ascii=False, allow_nan=False).encode("utf-8")) + 768
    total = current + cost
    if total > schemas.MAX_EXPANDED_BYTES:
        raise fail("CAMPAIGN_LIMIT", 422)
    return total


RECIPIENT_STATES_SQL = """WITH states AS (
 SELECT r.*,CASE
   WHEN a.parts=0 THEN CASE WHEN r.status='running' THEN 'pending' ELSE r.status END
   WHEN a.accepted=a.parts AND a.parts=jsonb_array_length(r.plan) THEN 'sent'
   WHEN a.uncertain THEN 'uncertain'
   WHEN a.running THEN 'pending'
   WHEN a.failed AND a.accepted=0 AND a.first_error IN
       ('CAMPAIGN_PAUSED','CAMPAIGN_CANCELLED','CAMPAIGN_OPTED_OUT','CAMPAIGN_MEMBER_REMOVED',
        'CAMPAIGN_REVOKED','CAMPAIGN_CONTEXT_CHANGED','CAMPAIGN_TEMPLATE_CHANGED') THEN 'cancelled'
   WHEN a.failed THEN 'failed'
   ELSE 'pending' END AS public_status,
   CASE WHEN a.uncertain THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE COALESCE(a.first_error,r.error_code) END AS public_error,
   ARRAY_TO_STRING(ARRAY(SELECT COALESCE(NULLIF(p->>'text',''),NULLIF(p->>'caption',''),p->>'file_name',p->>'type')
       FROM jsonb_array_elements(r.plan) p), E'\\n') AS message
 FROM subscription_campaign_recipients r LEFT JOIN LATERAL (
   SELECT count(*) AS parts,count(*) FILTER(WHERE e.status='accepted') AS accepted,
     bool_or(e.status='uncertain' OR e.status='running' AND e.authorized_at<:now-interval '180 seconds') AS uncertain,
     bool_or(e.status='running' AND e.authorized_at>=:now-interval '180 seconds') AS running,
     bool_or(e.status IN ('failed','not_sent') OR e.status='pending' AND e.expires_at<=:now) AS failed,
     (array_agg(CASE WHEN e.status='pending' AND e.expires_at<=:now THEN 'CAMPAIGN_EFFECT_EXPIRED' ELSE e.error_code END
         ORDER BY e.campaign_part_index) FILTER(WHERE (e.status IN ('failed','not_sent') AND e.error_code IS NOT NULL)
             OR e.status='pending' AND e.expires_at<=:now))[1] AS first_error
   FROM subscription_device_effects e WHERE e.tenant_id=r.tenant_id AND e.campaign_recipient_id=r.id
 ) a ON true WHERE r.tenant_id=:t AND r.campaign_id=:campaign AND r.run_version=:run
) """


async def campaign_progress(db, campaign, now):
    if campaign["run_version"] is None:
        return policy.progress([])
    rows = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT public_status,count(*) AS n FROM states GROUP BY public_status"),
        {"t": campaign["tenant_id"], "campaign": campaign["id"], "run": campaign["run_version"], "now": now})).mappings().all()
    counts = {key: 0 for key in policy.RECIPIENT_LABELS}
    counts.update({row["public_status"]: row["n"] for row in rows})
    total = sum(counts.values())
    processed = total - counts["pending"]
    return {"total": total, "processed": processed, **counts, "percent": round(processed * 100 / total, 2) if total else 0}


async def campaign_dto(db, campaign, now):
    definition = campaign["definition"]
    snapshot = campaign.get("snapshot") or {}
    devices = snapshot.get("devices")
    if devices is None:
        devices = (await db.execute(text("""SELECT id,name FROM subscription_devices
            WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id"""),
            {"t": campaign["tenant_id"], "ids": [UUID(v) for v in definition["device_ids"]]})).mappings().all()
    template = snapshot.get("template")
    if not template and campaign["template_id"]:
        template = (await db.execute(text("SELECT name FROM subscription_chatbot_templates WHERE tenant_id=:t AND id=:id"),
            {"t": campaign["tenant_id"], "id": campaign["template_id"]})).mappings().one_or_none()
    status = campaign["status"]
    actions = {"draft": ["edit", "review", "archive"], "scheduled": ["pause", "archive"],
        "running": ["pause", "archive"], "paused": ["resume", "archive"], "completed": ["archive"], "cancelled": []}[status].copy()
    if status in {"scheduled", "paused"} and campaign["first_admitted_at"] is None:
        actions.insert(1, "edit")
    return {**definition, "id": campaign["id"], "version": campaign["version"], "status": status,
        "status_label": policy.CAMPAIGN_LABELS[status], "archived": campaign["archived_at"] is not None,
        "devices": [{"id": item["id"], "name": item["name"]} for item in devices],
        "template_name": template["name"] if template else None, "start_at": campaign["start_at"],
        "created_at": campaign["created_at"], "updated_at": campaign["updated_at"], "actions": actions,
        "progress": await campaign_progress(db, campaign, now)}


def page_response(items, total, page, now, zone):
    return {"items": items, "total": total, "page": page, "page_size": schemas.PAGE_SIZE,
            "pages": max(1, (total + schemas.PAGE_SIZE - 1) // schemas.PAGE_SIZE), "generated_at": now, "timezone": zone}


async def recipient_page(db, campaign, now, *, status="", q="", page=1):
    params = {"t": campaign["tenant_id"], "campaign": campaign["id"], "run": campaign["run_version"],
              "now": now, "status": status, "q": q.casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
    predicate = """ WHERE (:status='' OR public_status=:status) AND (:q='' OR
        position(:q in lower(name))>0 OR position(:q in phone)>0 OR position(:q in lower(message))>0)"""
    total = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT count(*) FROM states" + predicate), params)).scalar_one()
    rows = (await db.execute(text(RECIPIENT_STATES_SQL + "SELECT * FROM states" + predicate +
        " ORDER BY ordinal,id LIMIT 30 OFFSET :offset"), params)).mappings().all()
    devices = {str(item["id"]): {"id": item["id"], "name": item["name"]}
               for item in (campaign.get("snapshot") or {}).get("devices", [])}
    output = []
    for row in rows:
        effects = (await db.execute(text("""SELECT id,campaign_part_index,status,error_code,finished_at
            FROM subscription_device_effects WHERE tenant_id=:t AND campaign_recipient_id=:id ORDER BY campaign_part_index"""),
            {"t": campaign["tenant_id"], "id": row["id"]})).mappings().all()
        parts = [{"index": item["campaign_part_index"], "effect_id": item["id"],
                  "type": row["plan"][item["campaign_part_index"]]["type"], "status": item["status"],
                  "error_code": item["error_code"], "finished_at": item["finished_at"]} for item in effects]
        error = None if row["public_status"] == "sent" else row["public_error"]
        output.append({"id": row["id"], "name": row["name"], "phone": "+" + row["phone"],
            "status": row["public_status"], "status_label": policy.RECIPIENT_LABELS[row["public_status"]],
            "message": "\n".join(policy.action_text(action) for action in row["plan"]),
            "device": devices.get(str(row["device_id"])) if row["admitted_at"] else None,
            "created_at": row["created_at"], "finished_at": row["finished_at"], "error_code": error,
            "reason": ERRORS.get(error, "A sequência não foi confirmada pelo transporte.") if error else None, "parts": parts})
    return page_response(output, total, page, now, campaign["definition"]["timezone"])
