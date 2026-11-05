"""Owner-only message-attempt history and explicit, append-only retries.

GETs read PostgreSQL only. POST stores a new frozen operation; the existing
fenced broker remains the only path to the WhatsApp transport.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import secrets
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_chatbot_store as store
from app.subscription_chatbot import bounded_json
from app.subscription_device_rules import business_state, schedule_state
from app.subscription_device_schemas import phone as normalize_phone
from app.subscription_sent_messages_schemas import ResendRequest

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/sent-messages"
router = APIRouter(tags=["Mensagens enviadas de assinaturas"], dependencies=[Depends(channels.enabled)])
PER_PAGE = 30
LIST_TEXT_LIMIT = 2000
DETAIL_TEXT_LIMIT = 120000
MESSAGE_TYPES = {"text", "list", "image", "sticker", "video", "ptv", "audio", "document", "location", "carousel"}
TYPE_LABELS = {"text": "Texto", "list": "Lista", "image": "Imagem", "sticker": "Figurinha", "video": "Vídeo",
               "ptv": "Recado de vídeo", "audio": "Áudio", "document": "Documento", "location": "Localização", "carousel": "Carrossel"}
SAFE_RETRY_CODES = {"TRANSPORT_UNAVAILABLE_BEFORE_SEND", "MESSAGE_PREPARATION_FAILED", "MESSAGE_EXPIRED_BEFORE_SEND"}
TAIL_CODES = {"chatbot_predecessor_failed", "CHATBOT_PREDECESSOR_UNAVAILABLE"}
STATUS_OPTIONS = [{"value": "", "label": "Todos"}, {"value": "failed", "label": "Falharam"},
                  {"value": "failed_resendable", "label": "Falharam e podem ser reenviadas"}]
BLOCK_MESSAGES = {
    "MESSAGE_ALREADY_ACCEPTED": "Esta mensagem já foi aceita pelo transporte; o recebimento não foi confirmado.",
    "MESSAGE_OUTCOME_UNCERTAIN": "O resultado é incerto. Um novo envio poderia duplicar a mensagem.",
    "MESSAGE_IN_PROGRESS": "A tentativa ainda está em andamento.",
    "MESSAGE_ALREADY_RETRIED": "Esta tentativa já tem um reenvio registrado.",
    "MESSAGE_NO_UNSENT_PROOF": "Não há confirmação suficiente de que esta mensagem não foi enviada.",
    "MESSAGE_SCOPE_CHANGED": "O dispositivo ou a configuração mudou depois desta tentativa.",
    "MESSAGE_DEVICE_UNAVAILABLE": "Conecte o dispositivo original antes de reenviar.",
    "MESSAGE_POLICY_CHANGED": "A política atual não permite repetir esta mensagem.",
    "MESSAGE_PREDECESSOR_UNAVAILABLE": "A mensagem anterior da sequência ainda não foi aceita.",
    "MESSAGE_CONTACT_BUSY": "Aguarde a tentativa anterior para este contato.",
    "MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE": "O arquivo original desta mensagem não está disponível para reenvio.",
    "MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE": "Os vínculos originais desta mensagem não estão disponíveis.",
    "MESSAGE_COOLDOWN": "O intervalo de avisos deste contato ainda está em vigor.",
    "MESSAGE_INVALID_RECIPIENT": "O destinatário não permite um novo envio.",
    "MESSAGE_CAMPAIGN_CONTROLLED": "Esta tentativa pertence a uma campanha. Consulte o acompanhamento em Campanhas.",
    "MESSAGE_BULK_CONTROLLED": "Esta tentativa pertence a um lote. Consulte o acompanhamento em Mensagem em Massa.",
    "MESSAGE_SCHEDULE_CONTROLLED": "Esta tentativa pertence a um agendamento. Consulte o acompanhamento em Mensagens Programadas.",
}
FAILURE_MESSAGES = {
    "TRANSPORT_UNAVAILABLE_BEFORE_SEND": "A conexão não estava disponível antes do envio.",
    "MESSAGE_PREPARATION_FAILED": "Não foi possível preparar a mensagem antes do envio.",
    "RECIPIENT_NOT_REGISTERED": "O número não foi confirmado como usuário do WhatsApp.",
    "RECIPIENT_IDENTITY_UNVERIFIED": "A identidade do destinatário não foi confirmada.",
    "MESSAGE_CONTENT_INVALID": "O conteúdo não pôde ser enviado.",
    "MESSAGE_EXPIRED_BEFORE_SEND": "A tentativa expirou antes de receber autorização para envio.",
    "chatbot_predecessor_failed": "A mensagem anterior da sequência não foi enviada.",
    "CHATBOT_PREDECESSOR_UNAVAILABLE": "A mensagem anterior da sequência ficou indisponível.",
}
MESSAGE_SQL = "(e.type='send_text' OR e.type IN ('chatbot_action','campaign_action','scheduled_action','bulk_action') AND e.payload->'action'->>'type'<>'http' AND e.payload->'action'->>'type' IN ('text','list','image','sticker','video','ptv','audio','document','location','carousel'))"


def fail(code, status=409):
    return HTTPException(status, {"code": code, "message": BLOCK_MESSAGES.get(code, "A mensagem não está disponível para esta operação.")})


def owner_transaction(tenant, actor):
    return store.owner_transaction(tenant, actor, feature="sent_messages.manage")


def expired_unclaimed(row, now):
    return (row["status"] == "pending" and row.get("attempts", 0) == 0 and row.get("authorized_at") is None
            and not row.get("provider_id") and row["expires_at"] <= now)


def public_state(row, now):
    state = row["status"]
    if state == "accepted":
        return "sent", "accepted", "Enviada"
    if state == "uncertain" or state == "running" and row["expires_at"] <= now:
        return "uncertain", "uncertain", "Resultado incerto"
    if state in {"failed", "not_sent"} or expired_unclaimed(row, now):
        return "failed", "not_sent" if expired_unclaimed(row, now) else state, "Falhou"
    return "pending", state, "Pendente"


def snapshot_hash(row):
    return store.fingerprint({key: row.get(key) for key in
        ("tenant_id", "device_id", "generation", "settings_revision", "event_id", "type", "purpose", "payload", "chatbot_revision")}
        | {"carousel_assets": (row.get("result") or {}).get("carousel_assets")})


def item_version(row):
    return store.fingerprint({"id": row["id"], "snapshot": snapshot_hash(row), "status": row["status"],
        "attempts": row.get("attempts"), "authorized_at": row.get("authorized_at"), "finished_at": row.get("finished_at"),
        "provider_id": row.get("provider_id"), "error_code": row.get("error_code"), "has_resend": row.get("has_resend", False)})


def _accepted_sql(identity):
    return f"""EXISTS(SELECT 1 FROM subscription_device_effects p
      LEFT JOIN subscription_sent_message_resends pl ON pl.tenant_id=p.tenant_id AND pl.new_effect_id=p.id
      WHERE p.tenant_id=h.tenant_id AND p.id={identity} AND (
        p.status='accepted' OR EXISTS(SELECT 1 FROM subscription_sent_message_resends pr
          JOIN subscription_device_effects pa ON pa.tenant_id=pr.tenant_id AND pa.id=pr.new_effect_id
          WHERE pr.tenant_id=p.tenant_id AND pr.root_effect_id=COALESCE(pl.root_effect_id,p.id) AND pa.status='accepted')))"""


HISTORY_SQL = """
 WITH history AS (
 SELECT e.*, COALESCE(link.root_effect_id,e.id) AS root_effect_id,link.source_effect_id AS retry_of_effect_id,
   d.name AS device_name,d.removed_at AS device_removed_at,d.owner_account_id AS device_owner,
   d.runtime AS device_runtime,
   original.event_id AS original_event_id,original.previous_effect AS logical_previous,
   ev.kind AS incoming_kind,ev.payload AS incoming_payload,
   r.origin AS run_origin,r.input_kind AS original_input_kind,r.input_text AS run_input_text,r.input_context AS run_input_context,
   r.rule_id AS original_rule_id,r.rule_revision AS original_rule_revision,
   r.actor_account_id AS original_actor,r.generation AS original_generation,
   rule.revision AS current_rule_revision,rule.enabled AS current_rule_enabled,
   rule.removed_at AS rule_removed_at,rule.device_id AS rule_device_id,
   CASE WHEN e.type='send_text' THEN 'text' ELSE e.payload->'action'->>'type' END AS message_type,
   EXISTS(SELECT 1 FROM subscription_sent_message_resends child
     WHERE child.tenant_id=e.tenant_id AND child.source_effect_id=e.id) AS has_resend
 FROM subscription_device_effects e
 JOIN subscription_devices d ON d.tenant_id=e.tenant_id AND d.id=e.device_id
 LEFT JOIN subscription_sent_message_resends link ON link.tenant_id=e.tenant_id AND link.new_effect_id=e.id
 JOIN subscription_device_effects original ON original.tenant_id=e.tenant_id AND original.id=COALESCE(link.root_effect_id,e.id)
 JOIN subscription_device_events ev ON ev.tenant_id=original.tenant_id AND ev.id=original.event_id
 LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=original.tenant_id AND r.id=original.chatbot_run_id
 LEFT JOIN subscription_chatbot_rules rule ON rule.tenant_id=r.tenant_id AND rule.id=r.rule_id
 WHERE e.tenant_id=:tenant AND d.owner_account_id=:actor AND """ + MESSAGE_SQL + """
   AND e.created_at<=:snapshot
 ), checked AS (
 SELECT h.*, CASE
 WHEN h.status='accepted' THEN 'MESSAGE_ALREADY_ACCEPTED'
 WHEN h.status='uncertain' OR h.status='running' AND h.expires_at<=:now THEN 'MESSAGE_OUTCOME_UNCERTAIN'
 WHEN h.type='campaign_action' THEN 'MESSAGE_CAMPAIGN_CONTROLLED'
 WHEN h.type='scheduled_action' THEN 'MESSAGE_SCHEDULE_CONTROLLED'
 WHEN h.type='bulk_action' THEN 'MESSAGE_BULK_CONTROLLED'
 WHEN h.has_resend THEN 'MESSAGE_ALREADY_RETRIED'
 WHEN NOT COALESCE(((h.status='not_sent' AND h.provider_id IS NULL AND h.error_code IN
   ('TRANSPORT_UNAVAILABLE_BEFORE_SEND','MESSAGE_PREPARATION_FAILED','MESSAGE_EXPIRED_BEFORE_SEND',
    'chatbot_predecessor_failed','CHATBOT_PREDECESSOR_UNAVAILABLE'))
   OR (h.status='pending' AND h.attempts=0 AND h.authorized_at IS NULL AND h.provider_id IS NULL AND h.expires_at<=:now)),false)
 THEN CASE WHEN h.status IN ('pending','running') THEN 'MESSAGE_IN_PROGRESS' ELSE 'MESSAGE_NO_UNSENT_PROOF' END
 WHEN NOT COALESCE(h.payload->>'phone' ~ '^[1-9][0-9]{7,14}$',false) THEN 'MESSAGE_INVALID_RECIPIENT'
 WHEN NOT COALESCE(h.device_id=CAST(:active_device AS uuid) AND h.generation=CAST(:generation AS uuid)
   AND h.settings_revision=:device_revision AND h.device_removed_at IS NULL,false) THEN 'MESSAGE_SCOPE_CHANGED'
 WHEN NOT :transport_ready THEN 'MESSAGE_DEVICE_UNAVAILABLE'
 WHEN h.type='chatbot_action' AND NOT COALESCE(h.chatbot_revision=:chatbot_revision
   AND h.original_actor=:actor AND h.original_generation=CAST(:generation AS uuid)
   AND h.original_rule_id IS NOT NULL AND h.current_rule_revision=h.original_rule_revision
   AND h.rule_removed_at IS NULL AND (h.current_rule_enabled OR h.run_origin='test_response')
   AND (h.rule_device_id IS NULL OR h.rule_device_id=h.device_id) AND NOT :hours_blocked,false)
 THEN 'MESSAGE_POLICY_CHANGED'
 WHEN h.type='send_text' AND NOT COALESCE((
   h.purpose='transcription_reply' AND :transcription_enabled
   OR h.purpose='absence' AND :absence_enabled AND h.payload->>'text'=:absence_text
   OR h.purpose='call_reply' AND :call_enabled AND h.payload->>'text'=:call_text
      AND NOT (CAST(:call_whitelist AS jsonb) ? (h.payload->>'phone'))),false)
 THEN 'MESSAGE_POLICY_CHANGED'
 WHEN h.logical_previous IS NOT NULL AND NOT """ + _accepted_sql("h.logical_previous") + """
 THEN 'MESSAGE_PREDECESSOR_UNAVAILABLE'
 WHEN h.run_input_context->>'transcription_predecessor' IS NOT NULL AND NOT """ + _accepted_sql("CAST(h.run_input_context->>'transcription_predecessor' AS uuid)") + """
 THEN 'MESSAGE_PREDECESSOR_UNAVAILABLE'
 WHEN h.purpose='transcription_reply' AND NOT EXISTS(SELECT 1 FROM subscription_device_effects a
   WHERE a.tenant_id=h.tenant_id AND a.event_id=h.original_event_id AND a.type='transcribe' AND a.status='accepted')
 THEN 'MESSAGE_PREDECESSOR_UNAVAILABLE'
 WHEN EXISTS(SELECT 1 FROM subscription_device_effects busy WHERE busy.tenant_id=h.tenant_id
   AND busy.device_id=h.device_id AND busy.payload->>'phone'=h.payload->>'phone' AND busy.id<>h.id
   AND busy.expires_at>:now AND busy.status IN ('pending','running','uncertain')
   AND busy.type IN ('send_text','chatbot_action','campaign_action','scheduled_action','bulk_action')) THEN 'MESSAGE_CONTACT_BUSY'
 WHEN h.type='send_text' AND h.purpose IN ('absence','call_reply') AND h.payload->>'phone'<>:test_phone
   AND EXISTS(SELECT 1 FROM subscription_device_cooldowns cd WHERE cd.tenant_id=h.tenant_id
     AND cd.device_id=h.device_id AND cd.phone=h.payload->>'phone' AND cd.reserved_at>:now-interval '24 hours'
     AND cd.effect_id<>h.root_effect_id AND NOT EXISTS(SELECT 1 FROM subscription_sent_message_resends cr
       WHERE cr.tenant_id=cd.tenant_id AND cr.new_effect_id=cd.effect_id AND cr.root_effect_id=h.root_effect_id))
 THEN 'MESSAGE_COOLDOWN'
 WHEN h.message_type IN ('image','sticker','video','ptv','audio','document') AND NOT EXISTS(
   SELECT 1 FROM subscription_chatbot_assets asset WHERE asset.tenant_id=h.tenant_id AND asset.status='ready'
   AND asset.id::text=h.payload->'action'->'asset'->>'id' AND asset.kind=h.message_type
   AND asset.sha256=h.payload->'action'->'asset'->>'sha256'
   AND asset.size::text=h.payload->'action'->'asset'->>'size'
   AND asset.mime=h.payload->'action'->'asset'->>'mime') THEN 'MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE'
 WHEN h.message_type='carousel' AND (jsonb_array_length(h.payload->'action'->'cards')<>3
   OR (SELECT count(*) FROM jsonb_each_text(COALESCE(h.result->'carousel_assets','{}'::jsonb)) item
      JOIN subscription_chatbot_assets asset ON asset.tenant_id=h.tenant_id AND asset.id::text=item.value
      AND asset.status='ready' AND asset.kind='image' WHERE item.key IN ('0','1','2'))<>3)
 THEN 'MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE'
 WHEN h.message_type IN ('list','carousel') AND (SELECT count(*) FROM subscription_chatbot_selections sel
   WHERE sel.tenant_id=h.tenant_id AND sel.outgoing_effect_id=h.id) <
   CASE WHEN h.message_type='carousel' THEN 3 ELSE
     (SELECT count(*) FROM jsonb_array_elements(h.payload->'action'->'sections') section,
        jsonb_array_elements(section->'rows') entry) END THEN 'MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE'
 ELSE NULL END AS resend_block
 FROM history h)
"""


async def _context(db, tenant, actor):
    now, zone = (await db.execute(text("SELECT clock_timestamp(),timezone FROM tenants WHERE id=:t"), {"t": tenant})).one()
    now = now.astimezone(timezone.utc)
    rows = (await db.execute(text("""SELECT id,name,generation,revision,status,removed_at,channel_id,settings
      FROM subscription_devices WHERE tenant_id=:t AND owner_account_id=:a ORDER BY created_at,id"""),
      {"t": tenant, "a": actor.account_id})).mappings().all()
    active = next((dict(row) for row in rows if row["removed_at"] is None), None)
    ready = False
    if active:
        try:
            transport = await channels._require_canonical(db, tenant, active["channel_id"])
            ready = bool(transport["owner_account_id"] == actor.account_id and transport["enabled"]
                and not transport["assistant_enabled"] and not transport["allowed_senders"]
                and transport["lease_expires_at"] and transport["lease_expires_at"] > now and active["status"] == "connected")
        except HTTPException:
            ready = False
    settings = active["settings"] if active else {}
    state = business_state(settings, now) if settings else "open"
    calls = settings.get("calls", {})
    call_on = bool(calls.get("reject_enabled") and calls.get("reply_enabled") and
        (not calls.get("schedule_enabled") or schedule_state(calls, now.astimezone(ZoneInfo(settings["timezone"]))) != "open"))
    hours = settings.get("business_hours", {})
    params = {"tenant": tenant, "actor": actor.account_id, "now": now, "snapshot": now,
        "active_device": active["id"] if active else None, "generation": active["generation"] if active else None,
        "device_revision": active["revision"] if active else 0, "transport_ready": ready,
        "chatbot_revision": await store.revision(db, tenant),
        "hours_blocked": bool(hours.get("block_replies_outside_hours") and state != "open"),
        "transcription_enabled": bool(settings.get("transcription", {}).get("enabled")),
        "absence_enabled": state != "open", "absence_text": hours.get("break_message" if state == "break" else "outside_message", ""),
        "call_enabled": call_on, "call_text": calls.get("reply_message", ""),
        "call_whitelist": store.encode(calls.get("whitelist", [])),
        "test_phone": (settings.get("test_phone_e164") or "").lstrip("+")}
    return {"now": now, "timezone": zone, "params": params, "active": active,
            "devices": [{"id": row["id"], "name": row["name"], "removed": row["removed_at"] is not None} for row in rows]}


async def _row(db, ctx, identity):
    row = (await db.execute(text(HISTORY_SQL + "SELECT * FROM checked WHERE id=:identity"),
        ctx["params"] | {"identity": identity})).mappings().one_or_none()
    if row is None:
        raise fail("MESSAGE_NOT_FOUND", 404)
    return dict(row)


def _bounded(value, maximum):
    if not isinstance(value, str):
        return None, False
    value = value.replace("\x00", "")
    return value[:maximum], len(value) > maximum


def item_dto(row, now, *, detail=False):
    limit = DETAIL_TEXT_LIMIT if detail else LIST_TEXT_LIMIT
    event = row.get("incoming_payload") or {}
    action = row["payload"].get("action") or {}
    kind = row.get("message_type") or ("text" if row["type"] == "send_text" else action.get("type", "text"))
    incoming, cut_in = _bounded(event.get("text"), limit)
    if row.get("incoming_kind") == "audio" and row.get("run_input_text"):
        incoming, cut_in = _bounded(row["run_input_text"], limit)
    outgoing = row["payload"].get("text") if row["type"] == "send_text" else action.get("text", action.get("body"))
    if kind == "list":
        outgoing = "\n".join(filter(None, [action.get("title"), action.get("description"), action.get("button_text"), action.get("footer"),
            *[str(s.get("title", "")) for s in action.get("sections", [])],
            *[" — ".join(filter(None, [r.get("title"), r.get("description")])) for s in action.get("sections", []) for r in s.get("rows", [])]]))
    if kind == "carousel":
        outgoing = "\n".join(filter(None, [action.get("body"), *[" — ".join(filter(None, [c.get("title"), c.get("body")])) for c in action.get("cards", [])]]))
    if kind == "location":
        outgoing = f"{action.get('latitude')}, {action.get('longitude')}"
    outgoing, cut_out = _bounded(outgoing, limit)
    caption, cut_caption = _bounded(action.get("caption"), limit)
    media = action.get("asset") if isinstance(action.get("asset"), dict) else None
    media = {key: media.get(key) for key in ("id", "kind", "mime", "size", "file_name")} if media else None
    if media and action.get("file_name"):
        media["file_name"] = action["file_name"]
    status, outcome, label = public_state(row, now)
    code = "MESSAGE_EXPIRED_BEFORE_SEND" if expired_unclaimed(row, now) else row.get("error_code")
    failure = None
    if status in {"failed", "uncertain"}:
        failure = {"code": code if code in FAILURE_MESSAGES else "MESSAGE_OUTCOME_UNCERTAIN" if status == "uncertain" else "MESSAGE_FAILED",
            "message": "Não foi possível confirmar o resultado do envio." if status == "uncertain" else FAILURE_MESSAGES.get(code, "A mensagem não pôde ser enviada.")}
    origin = {"transcription_reply": ("transcription", "Transcrição"), "absence": ("absence", "Fora do horário"),
              "call_reply": ("call_reply", "Resposta a ligação"),
              "campaign_action": ("campaign", "Campanha"),
              "scheduled_action": ("scheduled", "Mensagem programada"),
              "bulk_action": ("bulk", "Mensagem em massa"),
              "diagnostic_test": ("diagnostic_test", "Teste de diagnóstico")}.get(row["purpose"],
              {"test_response": ("test_response", "Teste de resposta"), "owner_command": ("owner_command", "Comando do aparelho")}.get(row.get("run_origin"), ("chatbot", "Chatbot")))
    blocked = {"campaign_action": "MESSAGE_CAMPAIGN_CONTROLLED", "scheduled_action": "MESSAGE_SCHEDULE_CONTROLLED", "bulk_action": "MESSAGE_BULK_CONTROLLED"}.get(row["type"], row.get("resend_block"))
    eligible = blocked is None and status == "failed"
    runtime = row.get("device_runtime") or {}
    device_phone = runtime.get("phone") if runtime.get("phone_verified") is True else None
    display_name = event.get("name") if event.get("origin", "contact") == "contact" and row.get("run_origin") != "owner_command" else None
    return {"id": row["id"], "root_effect_id": row.get("root_effect_id", row["id"]),
        "retry_of_effect_id": row.get("retry_of_effect_id"), "version": item_version(row),
        "origin": {"code": origin[0], "label": origin[1], "is_resend": row.get("retry_of_effect_id") is not None},
        "device": {"id": row["device_id"], "name": row["device_name"], "removed": row.get("device_removed_at") is not None,
                   "phone_e164": "+" + device_phone if isinstance(device_phone, str) and re.fullmatch(r"[1-9][0-9]{7,14}", device_phone) else None},
        "recipient": {"phone_e164": "+" + row["payload"].get("phone", ""), "display_name": _bounded(display_name, 100)[0]},
        "received": {"type": "selection" if row.get("original_input_kind") == "selection" else row.get("incoming_kind", "text"), "text": incoming, "truncated": cut_in},
        "sent": {"type": kind, "type_label": TYPE_LABELS.get(kind, "Mensagem"), "text": outgoing,
                 "caption": caption, "media": media, "truncated": cut_out or cut_caption},
        "status": status, "status_label": label, "outcome": outcome, "failure": failure,
        "created_at": row["created_at"], "finished_at": row.get("finished_at"),
        "actions": [{"type": "resend", "label": "Reenviar"}] if eligible else [],
        "resend": {"eligible": eligible, "blocked_reason": BLOCK_MESSAGES.get(blocked, "Reenvio indisponível.") if not eligible else None}}


async def transcription_metrics(db, tenant, actor, now, zone):
    from app.subscription_devices import _budget
    local = now.astimezone(ZoneInfo(zone))
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = (await db.execute(text("""SELECT
      COALESCE(sum(e.audio_duration_seconds),0) AS total,
      count(*) FILTER(WHERE e.audio_duration_seconds IS NULL) AS unknown_total,
      COALESCE(sum(e.audio_duration_seconds) FILTER(WHERE COALESCE(e.audio_completed_at,e.finished_at,e.created_at)>=:month),0) AS month,
      count(*) FILTER(WHERE e.audio_duration_seconds IS NULL AND COALESCE(e.finished_at,e.created_at)>=:month) AS unknown_month
      FROM subscription_device_effects e JOIN subscription_devices d ON d.tenant_id=e.tenant_id AND d.id=e.device_id
      WHERE e.tenant_id=:t AND d.owner_account_id=:a AND e.type='transcribe' AND e.status='accepted'"""),
      {"t": tenant, "a": actor.account_id, "month": month})).mappings().one()
    reserved = (await db.execute(text("SELECT reserved_seconds FROM subscription_device_audio_usage WHERE tenant_id=:t AND usage_day=:day"),
        {"t": tenant, "day": now.date()})).scalar_one_or_none() or 0
    limit = _budget("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS")
    return {"total": {"known_seconds": float(row["total"]), "unknown_completions": row["unknown_total"], "complete": not row["unknown_total"]},
        "month": {"known_seconds": float(row["month"]), "unknown_completions": row["unknown_month"], "complete": not row["unknown_month"]},
        "daily": {"limit_seconds": limit, "reserved_seconds": reserved, "remaining_seconds": max(0, limit-reserved),
                  "reset_at": now.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1),
                  "label": "Saldo diário de transcrição desta conta (UTC)"}}


def _literal_query(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get(BASE)
async def list_messages(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
    page: int = Query(default=1, ge=1, le=10000), status: str = "", device_id: UUID | None = None,
    contact: str = Query(default="", max_length=40), q: str = Query(default="", max_length=255),
    snapshot: datetime | None = None):
    response.headers["Cache-Control"] = "no-store"
    if status not in {"", "failed", "failed_resendable"} or "\x00" in q:
        raise HTTPException(422, "Filtro inválido")
    try:
        contact = normalize_phone(contact) if contact else ""
    except ValueError:
        raise HTTPException(422, "Contato inválido") from None
    async with owner_transaction(tenant_id, actor) as db:
        ctx = await _context(db, tenant_id, actor)
        if snapshot is not None:
            if snapshot.tzinfo is None or snapshot > ctx["now"]:
                raise HTTPException(422, "Data de paginação inválida")
            ctx["params"]["snapshot"] = snapshot.astimezone(timezone.utc)
        params = ctx["params"] | {"device": device_id, "contact": contact, "query": _literal_query(q.strip()),
            "offset": (page-1)*PER_PAGE, "limit": PER_PAGE}
        conditions = ["(CAST(:device AS uuid) IS NULL OR device_id=CAST(:device AS uuid))",
                      "(:contact='' OR payload->>'phone'=:contact)"]
        if q.strip():
            conditions.append("""concat_ws(' ',payload->>'phone',
              CASE WHEN COALESCE(incoming_payload->>'origin','contact')='contact' AND run_origin IS DISTINCT FROM 'owner_command'
                THEN incoming_payload->>'name' ELSE '' END,incoming_payload->>'text',
              CASE WHEN incoming_kind='audio' THEN run_input_text ELSE '' END,
              payload->>'text',payload->'action'->>'text',payload->'action'->>'body',
              payload->'action'->>'caption',COALESCE(payload->'action'->>'file_name',payload->'action'->'asset'->>'file_name'),payload->'action'->>'description',
              payload->'action'->>'title',payload->'action'->>'button_text',payload->'action'->>'footer',
              (SELECT string_agg(concat_ws(' ',section->>'title',entry->>'title',entry->>'description'),' ')
                FROM jsonb_array_elements(COALESCE(payload->'action'->'sections','[]'::jsonb)) section
                LEFT JOIN LATERAL jsonb_array_elements(COALESCE(section->'rows','[]'::jsonb)) entry ON true),
              (SELECT string_agg(concat_ws(' ',card->>'title',card->>'body',card->>'button_text'),' ')
                FROM jsonb_array_elements(COALESCE(payload->'action'->'cards','[]'::jsonb)) card),
              message_type,
              CASE purpose WHEN 'chatbot_reply' THEN 'Chatbot' WHEN 'transcription_reply' THEN 'Transcrição'
                WHEN 'absence' THEN 'Fora do horário' WHEN 'call_reply' THEN 'Resposta a ligação'
                WHEN 'diagnostic_test' THEN 'Teste de diagnóstico' WHEN 'campaign_action' THEN 'Campanha'
                WHEN 'scheduled_action' THEN 'Mensagem programada' WHEN 'bulk_action' THEN 'Mensagem em massa' ELSE '' END,
              CASE run_origin WHEN 'test_response' THEN 'Teste de resposta' WHEN 'owner_command' THEN 'Comando do aparelho' ELSE '' END,
              CASE message_type WHEN 'text' THEN 'texto' WHEN 'list' THEN 'lista' WHEN 'image' THEN 'imagem'
                WHEN 'sticker' THEN 'figurinha' WHEN 'video' THEN 'vídeo' WHEN 'ptv' THEN 'recado de vídeo'
                WHEN 'audio' THEN 'áudio' WHEN 'document' THEN 'documento' WHEN 'location' THEN 'localização'
                WHEN 'carousel' THEN 'carrossel' ELSE '' END) ILIKE :query ESCAPE '\\'""")
        if status == "failed":
            conditions.append("(status IN ('failed','not_sent','uncertain') OR status IN ('running','pending') AND expires_at<=:now)")
        elif status == "failed_resendable":
            conditions.append("resend_block IS NULL")
        where = " AND ".join(conditions)
        filtered = (await db.execute(text(HISTORY_SQL+"SELECT count(*) FROM checked WHERE "+where), params)).scalar_one()
        rows = (await db.execute(text(HISTORY_SQL+"SELECT * FROM checked WHERE "+where+
            " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"), params)).mappings().all()
        today = ctx["now"].astimezone(ZoneInfo(ctx["timezone"])).replace(hour=0, minute=0, second=0, microsecond=0)
        counts = dict((await db.execute(text("""SELECT count(*) AS total,count(*) FILTER(WHERE e.created_at>=:today) AS today,
          count(*) FILTER(WHERE e.created_at>=:month) AS last_30_days FROM subscription_device_effects e
          JOIN subscription_devices d ON d.tenant_id=e.tenant_id AND d.id=e.device_id
          WHERE e.tenant_id=:tenant AND d.owner_account_id=:actor AND e.created_at<=:snapshot AND """+MESSAGE_SQL),
          params | {"today": today, "month": today-timedelta(days=29)})).mappings().one())
        return jsonable_encoder({"items": [item_dto(dict(row), ctx["now"]) for row in rows],
            "page": page, "per_page": PER_PAGE, "filtered_total": filtered, "total_pages": max(1, math.ceil(filtered/PER_PAGE)),
            "timezone": ctx["timezone"], "snapshot": params["snapshot"], "generated_at": ctx["now"],
            "counts": counts | {"scope": "message_attempts"}, "devices": ctx["devices"],
            "transcription": await transcription_metrics(db, tenant_id, actor, ctx["now"], ctx["timezone"]),
            "status_options": STATUS_OPTIONS, "capabilities": {"resend": True}})


def operation_dto(request, effect, now, *, replayed):
    status, outcome, _ = public_state(effect, now)
    return {"request_id": request["id"], "original_effect_id": request["source_effect_id"],
        "effect_id": request["new_effect_id"], "root_effect_id": request["root_effect_id"],
        "replayed": replayed, "status": status, "outcome": outcome}


async def _request(db, tenant, actor, key):
    row = (await db.execute(text("""SELECT r.* FROM subscription_sent_message_resends r
      JOIN subscription_device_effects e ON e.tenant_id=r.tenant_id AND e.id=r.new_effect_id
      JOIN subscription_devices d ON d.tenant_id=e.tenant_id AND d.id=e.device_id
      WHERE r.tenant_id=:t AND r.idempotency_key=:key AND r.actor_account_id=:a AND d.owner_account_id=:a"""),
      {"t": tenant, "a": actor.account_id, "key": key})).mappings().one_or_none()
    return dict(row) if row else None


@router.get(BASE + "/resend-requests/{idempotency_key}")
async def recover_resend(tenant_id: UUID, idempotency_key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        saved = await _request(db, tenant_id, actor, idempotency_key)
        if saved is None:
            raise fail("MESSAGE_NOT_FOUND", 404)
        ctx = await _context(db, tenant_id, actor)
        effect = await _row(db, ctx, saved["new_effect_id"])
        return jsonable_encoder(operation_dto(saved, effect, ctx["now"], replayed=True))


@router.get(BASE + "/{effect_id}")
async def message_detail(tenant_id: UUID, effect_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        ctx = await _context(db, tenant_id, actor)
        return jsonable_encoder({"item": item_dto(await _row(db, ctx, effect_id), ctx["now"], detail=True),
            "timezone": ctx["timezone"], "generated_at": ctx["now"]})


async def accepted_representative(db, tenant, identity):
    if identity is None:
        return None
    return (await db.execute(text("""SELECT candidate.id FROM subscription_device_effects original
      LEFT JOIN subscription_sent_message_resends link ON link.tenant_id=original.tenant_id AND link.new_effect_id=original.id
      JOIN subscription_device_effects candidate ON candidate.tenant_id=original.tenant_id
      AND (candidate.id=original.id OR EXISTS(SELECT 1 FROM subscription_sent_message_resends retry
        WHERE retry.tenant_id=original.tenant_id AND retry.root_effect_id=COALESCE(link.root_effect_id,original.id)
          AND retry.new_effect_id=candidate.id))
      WHERE original.tenant_id=:t AND original.id=:id AND candidate.status='accepted'
      ORDER BY candidate.finished_at DESC NULLS LAST,candidate.id LIMIT 1"""),
      {"t": tenant, "id": identity})).scalar_one_or_none()


async def _asset_snapshot(db, tenant, descriptor, kind):
    if not isinstance(descriptor, dict):
        raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
    try:
        identity = UUID(str(descriptor["id"]))
    except (KeyError, ValueError, TypeError):
        raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE") from None
    row = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND id=:id AND status='ready'"),
        {"t": tenant, "id": identity})).mappings().one_or_none()
    if not row or row["kind"] != kind:
        raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
    factual = jsonable_encoder(store.asset_descriptor(dict(row)))
    if any(descriptor.get(key) != factual[key] for key in ("id", "kind", "mime", "size", "sha256", "thumbnail")):
        raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
    return factual


async def frozen_payload(db, source, *, clone_tokens):
    """Copy resolved content and its authority bindings, never compile or fetch."""
    payload = deepcopy(source["payload"])
    if source["type"] == "send_text":
        if not isinstance(payload.get("text"), str) or not payload["text"].strip() or len(payload["text"].encode("utf-16-le"))//2 > 32000:
            raise fail("MESSAGE_CONTENT_INVALID")
        return payload, None, []
    action = payload.get("action")
    if not isinstance(action, dict) or action.get("type") not in MESSAGE_TYPES:
        raise fail("MESSAGE_CONTENT_INVALID")
    kind, tenant = action["type"], source["tenant_id"]
    if kind in {"image", "sticker", "video", "ptv", "audio", "document"}:
        action["asset"] = await _asset_snapshot(db, tenant, action.get("asset"), kind)
    result = None
    if kind == "carousel":
        cards = action.get("cards")
        assets = (source.get("result") or {}).get("carousel_assets")
        if not isinstance(cards, list) or len(cards) != 3 or not isinstance(assets, dict) or set(assets) != {"0", "1", "2"}:
            raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
        for index, card in enumerate(cards):
            row = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND id::text=:id AND status='ready' AND kind='image'"),
                {"t": tenant, "id": str(assets[str(index)])})).mappings().one_or_none()
            if not row or not isinstance(card, dict):
                raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
            factual = jsonable_encoder(store.asset_descriptor(dict(row)))
            if card.get("asset") is not None:
                await _asset_snapshot(db, tenant, card["asset"], "image")
                if str(card["asset"].get("id")) != str(row["id"]):
                    raise fail("MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE")
            card.pop("image_url", None)
            card["asset"] = factual
        result = {"carousel_assets": deepcopy(assets)}
    selections = []
    if kind in {"list", "carousel"}:
        saved = (await db.execute(text("SELECT token_hash,action FROM subscription_chatbot_selections WHERE tenant_id=:t AND outgoing_effect_id=:id"),
            {"t": tenant, "id": source["id"]})).mappings().all()
        bindings = {row["token_hash"]: row["action"] for row in saved}
        entries = [(row, "id", "row") for section in action.get("sections", []) for row in section.get("rows", [])] if kind == "list" else [(card, "selection_id", "carousel") for card in action["cards"]]
        if not entries or len(entries) != len(bindings):
            raise fail("MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE")
        seen = set()
        for index, (entry, field, expected_kind) in enumerate(entries):
            token = entry.get(field)
            if not isinstance(token, str) or not token.startswith("ocb:"):
                raise fail("MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE")
            digest = hashlib.sha256(token.encode()).hexdigest()
            selected = bindings.get(digest)
            if digest in seen or not isinstance(selected, dict) or selected.get("kind") != expected_kind:
                raise fail("MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE")
            if kind == "list" and not isinstance(selected.get("definition"), dict) or kind == "carousel" and selected.get("card_index") != index:
                raise fail("MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE")
            seen.add(digest)
            if clone_tokens:
                token = "ocb:" + secrets.token_urlsafe(24)
                entry[field] = token
            selections.append((hashlib.sha256(token.encode()).hexdigest(), deepcopy(selected)))
    return payload, result, selections


async def _create_retry(db, tenant, actor, source, request, ctx):
    from app import subscription_devices as devices
    device, transport = await devices._device(db, tenant, source["device_id"], actor=actor)
    payload, result, selections = await frozen_payload(db, source, clone_tokens=True)
    previous = await accepted_representative(db, tenant, source["logical_previous"])
    if source["logical_previous"] is not None and previous is None:
        raise fail("MESSAGE_PREDECESSOR_UNAVAILABLE")
    # Expiration with no authorization is proof, not permission to rewrite
    # the original record. The existing broker independently refuses it.
    request_id, effect_id = uuid4(), uuid4()
    event_id, run_id = source["event_id"], None
    if source["type"] == "chatbot_action":
        event_id, run_id = uuid4(), uuid4()
        await db.execute(text("""INSERT INTO subscription_device_events
          (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
          VALUES(:id,:t,:d,:g,:provider,'resend',clock_timestamp(),CAST(:body AS jsonb))"""),
          {"id": event_id, "t": tenant, "d": source["device_id"], "g": source["generation"], "provider": "resend:"+str(request_id),
           "body": store.encode({"source_effect_id": source["id"], "root_effect_id": source["root_effect_id"]})})
        ordering = (await db.execute(text("""SELECT GREATEST(COALESCE(max(ingress_order),0)+1,
          floor(extract(epoch FROM clock_timestamp())*1000000)::bigint) FROM subscription_chatbot_runs
          WHERE tenant_id=:t AND device_id=:d"""), {"t": tenant, "d": source["device_id"]})).scalar_one()
        input_context = deepcopy(source.get("run_input_context") or {})
        transcription = input_context.get("transcription_predecessor")
        if transcription:
            representative = await accepted_representative(db, tenant, UUID(transcription))
            if representative is None:
                raise fail("MESSAGE_PREDECESSOR_UNAVAILABLE")
            input_context["transcription_predecessor"] = str(representative)
        input_context["resend_of_effect_id"] = str(source["id"])
        await db.execute(text("""INSERT INTO subscription_chatbot_runs
          (id,tenant_id,device_id,generation,source_event_id,chatbot_revision,settings_revision,actor_account_id,
           ingress_order,input_fence,phone,origin,input_kind,input_text,input_context,rule_id,rule_revision,plan,status)
          VALUES(:id,:t,:d,:g,:event,:chatrev,:devrev,:actor,:ordering,:fence,:phone,:origin,:kind,:incoming,
            CAST(:context AS jsonb),:rule,:rule_revision,CAST(:plan AS jsonb),'planned')"""),
          {"id": run_id, "t": tenant, "d": source["device_id"], "g": source["generation"], "event": event_id,
           "chatrev": source["chatbot_revision"], "devrev": source["settings_revision"], "actor": actor.account_id,
           "ordering": ordering, "fence": transport["fence"], "phone": payload["phone"], "origin": source["run_origin"],
           "kind": source["original_input_kind"], "incoming": source["run_input_text"], "context": store.encode(input_context),
           "rule": source["original_rule_id"], "rule_revision": source["original_rule_revision"],
           "plan": store.encode([{"action": payload["action"], "delay_seconds": payload["delay_seconds"]}])})
    effect = dict((await db.execute(text("""INSERT INTO subscription_device_effects
      (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,
       previous_effect,chatbot_revision,chatbot_run_id,result,due_at)
      VALUES(:id,:t,:d,:g,:r,:event,:key,:type,:purpose,CAST(:payload AS jsonb),:previous,:chatrev,:run,CAST(:result AS jsonb),
        clock_timestamp()+(CAST(:delay AS double precision)*interval '1 second')) RETURNING *"""),
      {"id": effect_id, "t": tenant, "d": source["device_id"], "g": source["generation"], "r": source["settings_revision"],
       "event": event_id, "key": "resend:"+str(request_id), "type": source["type"], "purpose": source["purpose"],
       "payload": store.encode(payload), "previous": previous, "chatrev": source["chatbot_revision"], "run": run_id,
       "result": store.encode(result) if result is not None else None, "delay": payload.get("delay_seconds", 0)})).mappings().one())
    for token_hash, action in selections:
        await db.execute(text("""INSERT INTO subscription_chatbot_selections
          (tenant_id,device_id,generation,chatbot_revision,run_id,outgoing_effect_id,phone,token_hash,action)
          VALUES(:t,:d,:g,:revision,:run,:effect,:phone,:token,CAST(:action AS jsonb))"""),
          {"t": tenant, "d": source["device_id"], "g": source["generation"], "revision": source["chatbot_revision"],
           "run": run_id, "effect": effect_id, "phone": payload["phone"], "token": token_hash, "action": store.encode(action)})
    saved = dict((await db.execute(text("""INSERT INTO subscription_sent_message_resends
      (id,tenant_id,source_effect_id,root_effect_id,new_effect_id,actor_account_id,idempotency_key,request_fingerprint,source_snapshot_hash)
      VALUES(:id,:t,:source,:root,:effect,:actor,:key,:fp,:snapshot) RETURNING *"""),
      {"id": request_id, "t": tenant, "source": source["id"], "root": source["root_effect_id"], "effect": effect_id,
       "actor": actor.account_id, "key": request.idempotency_key,
       "fp": store.fingerprint({"effect_id": source["id"], "expected_version": request.expected_version}),
       "snapshot": snapshot_hash(source)})).mappings().one())
    return saved, effect


@router.post(BASE + "/{effect_id}/resend", status_code=202)
async def resend_message(tenant_id: UUID, effect_id: UUID, request: Request, response: Response,
                         actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor):
        pass
    payload = await bounded_json(request, ResendRequest, limit=4096)
    async with owner_transaction(tenant_id, actor) as db:
        saved = await _request(db, tenant_id, actor, payload.idempotency_key)
        ctx = await _context(db, tenant_id, actor)
        if saved:
            if saved["request_fingerprint"] != store.fingerprint({"effect_id": effect_id, "expected_version": payload.expected_version}):
                raise fail("MESSAGE_IDEMPOTENCY_CONFLICT")
            effect = await _row(db, ctx, saved["new_effect_id"])
            return jsonable_encoder(operation_dto(saved, effect, ctx["now"], replayed=True))
        source = await _row(db, ctx, effect_id)
        if source["has_resend"]:
            raise fail("MESSAGE_ALREADY_RETRIED")
        if item_version(source) != payload.expected_version:
            raise fail("MESSAGE_VERSION_CONFLICT")
        if source["resend_block"] is not None:
            raise fail(source["resend_block"])
        saved, effect = await _create_retry(db, tenant_id, actor, source, payload, ctx)
        return jsonable_encoder(operation_dto(saved, effect, ctx["now"], replayed=False))


def proven_unsent(row, now):
    return bool(expired_unclaimed(row, now) or row["status"] == "not_sent" and not row.get("provider_id")
                and row.get("error_code") in SAFE_RETRY_CODES | TAIL_CODES)


async def message_authority(db, device, effect):
    """Read-only dispatch fence shared by ordinary messages and retries.

    Admission uses the tenant lock. Later input may be recorded while a retry
    runs, but no later message can overtake that explicit attempt. An expired
    uncertain attempt never blocks unrelated future input forever.
    """
    if effect["type"] not in {"send_text", "chatbot_action", "campaign_action", "scheduled_action", "bulk_action"}:
        return True
    tenant = device["tenant_id"]
    phone = effect["payload"].get("phone")
    older = (await db.execute(text("""SELECT e.id FROM subscription_device_effects e
      LEFT JOIN subscription_sent_message_resends retry ON e.tenant_id=retry.tenant_id AND e.id=retry.new_effect_id
      WHERE e.tenant_id=:t AND e.device_id=:d AND e.payload->>'phone'=:phone AND e.id<>:id
        AND (retry.new_effect_id IS NOT NULL OR e.type IN ('campaign_action','scheduled_action','bulk_action'))
        AND (e.type NOT IN ('campaign_action','scheduled_action','bulk_action') OR e.event_id<>:event)
        AND e.created_at<=:created AND e.status IN ('pending','running','uncertain')
        AND e.expires_at>clock_timestamp() LIMIT 1"""),
      {"t": tenant, "d": device["id"], "phone": phone, "id": effect["id"], "event": effect["event_id"], "created": effect["created_at"]})).scalar_one_or_none()
    if older:
        return False
    retry = (await db.execute(text("SELECT * FROM subscription_sent_message_resends WHERE tenant_id=:t AND new_effect_id=:id"),
        {"t": tenant, "id": effect["id"]})).mappings().one_or_none()
    if retry is None:
        return True
    source = (await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
        {"t": tenant, "id": retry["source_effect_id"]})).mappings().one_or_none()
    root = (await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
        {"t": tenant, "id": retry["root_effect_id"]})).mappings().one_or_none()
    now = datetime.now(timezone.utc)
    if (not source or not root or retry["actor_account_id"] != device["owner_account_id"]
            or not proven_unsent(source, now) or snapshot_hash(source) != retry["source_snapshot_hash"]
            or source["device_id"] != device["id"] or source["generation"] != device["generation"]
            or source["settings_revision"] != device["revision"] or effect["generation"] != device["generation"]
            or effect["settings_revision"] != device["revision"]):
        return False
    if root["previous_effect"] and await accepted_representative(db, tenant, root["previous_effect"]) is None:
        return False
    if effect["purpose"] == "transcription_reply":
        valid = (await db.execute(text("""SELECT id FROM subscription_device_effects
          WHERE tenant_id=:t AND event_id=:event AND type='transcribe' AND status='accepted' LIMIT 1"""),
          {"t": tenant, "event": root["event_id"]})).scalar_one_or_none()
        if not valid:
            return False
    try:
        # Verify current metadata and the copied token bindings. No URL, S3,
        # decoder, provider or rule compiler is contacted here.
        await frozen_payload(db, dict(effect), clone_tokens=False)
    except (HTTPException, KeyError, TypeError, ValueError):
        return False
    return True


async def transfer_retry_cooldown(db, device, effect):
    """Transfer only this notice's still-live reservation, not another notice."""
    retry = (await db.execute(text("SELECT root_effect_id FROM subscription_sent_message_resends WHERE tenant_id=:t AND new_effect_id=:id"),
        {"t": device["tenant_id"], "id": effect["id"]})).scalar_one_or_none()
    if retry is None:
        return False
    changed = (await db.execute(text("""UPDATE subscription_device_cooldowns cd SET effect_id=:id
      WHERE cd.tenant_id=:t AND cd.device_id=:d AND cd.phone=:phone
        AND cd.reserved_at>clock_timestamp()-interval '24 hours'
        AND (cd.effect_id=:root OR EXISTS(SELECT 1 FROM subscription_sent_message_resends previous
          WHERE previous.tenant_id=cd.tenant_id AND previous.new_effect_id=cd.effect_id AND previous.root_effect_id=:root))
      RETURNING cd.effect_id"""), {"id": effect["id"], "t": device["tenant_id"], "d": device["id"],
         "phone": effect["payload"]["phone"], "root": retry})).scalar_one_or_none()
    return changed is not None
