"""Owner-scoped diagnostics with passive reads and explicitly confirmed sends.

Preparing a test only records an intention. Confirmation inserts one immutable
effect; the existing fenced broker is the sole path to message transport.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app import qr_channels as channels
from app import subscription_chatbot_store as store
from app import subscription_devices as devices
from app import subscription_sent_messages as history
from app.subscription_chatbot import bounded_json
from app.subscription_diagnostics_schemas import (PrepareMessage, ConfirmMessage, DiagnosticIntent,
    ProxyResult, ProxyAuthorize, ProxyFinish, ProxyReceipt)

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/diagnostics"
INTERNAL = "/internal/qr/channels/{channel_id}/device/diagnostics"
router = APIRouter(tags=["Diagnósticos de assinaturas"], dependencies=[Depends(channels.enabled)])
TEST_TEXT = "Mensagem de teste do Orion. Envio solicitado pelo proprietário para verificar a conexão do WhatsApp."
PREVIEW_SECONDS = 120
MESSAGE_SECONDS = 120
PROXY_SECONDS = 60
CLAIM_SECONDS = 30
PROXY_FRESH_SECONDS = 90
CONNECTION_LABELS = {
    "disabled": "Desativado", "stopped": "Parado", "starting": "Iniciando", "connecting": "Conectando",
    "reconnecting": "Reconectando", "qr_ready": "QR disponível", "qr_delayed": "Aguardando QR",
    "qr_expired": "QR expirado", "pairing": "Pareando", "pair_expired": "Pareamento expirado",
    "pair_failed": "Pareamento não concluído", "connected": "Conectado", "disconnected": "Desconectado",
    "logged_out": "Sessão encerrada", "error": "Indisponível", "unavailable": "Indisponível", "unknown": "Desconhecido",
}
ERRORS = {
    "DIAGNOSTIC_NOT_FOUND": "Diagnóstico não encontrado.",
    "DIAGNOSTIC_CONTEXT_CHANGED": "O dispositivo ou sua configuração mudou. Prepare um novo teste.",
    "DIAGNOSTIC_KEY_CONFLICT": "Esta chave já pertence a outra intenção de diagnóstico.",
    "DIAGNOSTIC_PREVIEW_EXPIRED": "A confirmação expirou. Prepare um novo teste antes de enviar.",
    "DIAGNOSTIC_CONFIRMATION_INVALID": "Confirme o destinatário e a prévia exatos antes de enviar.",
    "DIAGNOSTIC_DEVICE_UNAVAILABLE": "Conecte o dispositivo e consulte seu estado antes de preparar o teste.",
    "DIAGNOSTIC_SELF_IDENTITY_UNVERIFIED": "O número atual do dispositivo ainda não foi verificado.",
    "DIAGNOSTIC_SELF_IDENTITY_CHANGED": "O número do dispositivo mudou. Prepare um novo teste.",
    "DIAGNOSTIC_BUSY": "Aguarde a execução de diagnóstico que já está em andamento.",
    "DIAGNOSTIC_CONTACT_BUSY": "Aguarde a sequência de mensagens em andamento para este destinatário.",
    "DIAGNOSTIC_REVOKED": "A autorização deste teste deixou de ser válida.",
    "PROXY_NOT_CONFIGURED": "Este dispositivo usa conexão direta; não há proxy configurado para testar.",
    "PROXY_CONFIGURATION_INVALID": "A configuração do proxy está indisponível.",
    "PROXY_DESTINATION_REFUSED": "O endereço do proxy não é permitido.",
    "PROXY_TARGET_UNAVAILABLE": "Não foi possível consultar o destino fixo de verificação.",
    "PROXY_AUTH_FAILED": "O proxy recusou a autenticação.",
    "PROXY_CONNECT_REFUSED": "O proxy recusou a conexão de verificação.",
    "PROXY_TLS_FAILED": "Não foi possível verificar a conexão TLS pelo proxy.",
    "PROXY_TIMEOUT": "O teste de conexão atingiu seu limite de tempo.",
    "PROXY_ABORTED": "O teste foi interrompido.",
    "PROXY_CONFIGURATION_CHANGED": "A configuração mudou durante o teste; o resultado não confirma a configuração atual.",
    "PROXY_AUTHORIZATION_REVOKED": "A autorização do teste foi revogada.",
    "PROXY_PROBE_FAILED": "Não foi possível concluir o teste de conexão do proxy.",
    "PROXY_OUTCOME_UNCERTAIN": "Não foi possível confirmar o resultado do teste de proxy.",
    "PROXY_EXPIRED_BEFORE_START": "O teste expirou antes de receber autorização para iniciar.",
}
PROXY_CODES = frozenset(code for code in ERRORS if code.startswith("PROXY_"))


def fail(code, status=409):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "Diagnóstico indisponível.")})


def stale_device_version(payload, device_id, kind):
    """Only after a locked lookup proves this intention has not been admitted."""
    error = fail("DIAGNOSTIC_CONTEXT_CHANGED")
    error.detail["rejection"] = {"reason": "stale_device_version",
        "idempotency_key": str(payload.idempotency_key), "device_id": str(device_id),
        "kind": kind, "expected_version": payload.expected_version}
    return error


def owner_transaction(tenant, actor):
    # write=True enforces the feature and active tenant; this context itself
    # only SELECTs/locks. GET handlers never invoke a mutating service helper.
    return store.owner_transaction(tenant, actor, feature="diagnostics.manage")


async def clock(db):
    return (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()


def device_version(row):
    return store.fingerprint({key: row[key] for key in ("id", "tenant_id", "channel_id", "owner_account_id", "generation", "revision")})


def configuration_version(row):
    return store.fingerprint({"device": row["id"], "generation": row["generation"], "revision": row["revision"],
        "proxy": row["settings"].get("proxy", {}),
        "secret_digest": hashlib.sha256((row.get("proxy_secret_encrypted") or "").encode()).hexdigest()})


async def scope(db, tenant, identity, actor, *, removed=False):
    row, transport = await devices._device(db, tenant, identity, actor=actor, removed=removed)
    if transport["allowed_senders"]:
        raise fail("DIAGNOSTIC_CONTEXT_CHANGED")
    return row, transport


def scoped_status(data, row):
    if not isinstance(data, dict) or (str(data.get("device_id")) != str(row["id"])
            or str(data.get("generation")) != str(row["generation"])
            or type(data.get("settings_revision")) is not int or data["settings_revision"] != row["revision"]):
        return None
    raw_state = data.get("status")
    state = "qr_ready" if raw_state == "qr" else raw_state if raw_state in CONNECTION_LABELS else "unknown"
    result = devices._safe_runtime(data)
    result.update(status=state, observed_at=datetime.now(timezone.utc))
    return result


async def read_status(transport, device):
    """Bounded read only; never starts a session or persists a status event."""
    try:
        async with asyncio.timeout(6):
            return scoped_status(await channels._bridge_control(transport, "device/status", "GET"), device)
    except (HTTPException, TimeoutError):
        return None


async def read_server():
    """Independent API-to-bridge liveness RTT, not WhatsApp connectivity."""
    value = {"state": "unknown", "latency_ms": None, "observed_at": None, "measurement": "api_to_bridge_health"}
    raw = os.getenv("QR_BRIDGE_URL", "").rstrip("/")
    try:
        parsed = urlsplit(raw)
        valid = (parsed.scheme == "http" and re.fullmatch(r"[a-z0-9-]+\.railway\.internal", parsed.hostname or "")
            and parsed.port == 3000 and not parsed.username and not parsed.password
            and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        return value
    start = time.monotonic()
    try:
        async with asyncio.timeout(5):
            async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", raw + "/health") as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 4096:
                            raise ValueError()
                    data = json.loads(body)
                    if response.status_code != 200 or not isinstance(data, dict) or data.get("status") != "ok" or data.get("bridge") != "linked-device":
                        raise ValueError()
        return value | {"state": "available", "latency_ms": max(0, round((time.monotonic()-start)*1000)),
                        "observed_at": datetime.now(timezone.utc)}
    except (httpx.RequestError, TimeoutError, ValueError):
        return value | {"state": "unavailable", "observed_at": datetime.now(timezone.utc)}


def verified_self(row, transport, live):
    if (row["removed_at"] is not None or row["status"] in {"disabled", "removing", "removed"}
            or not transport["enabled"] or live is None or live.get("status") != "connected"
            or live.get("applied_revision") != row["revision"]):
        raise fail("DIAGNOSTIC_DEVICE_UNAVAILABLE")
    phone = live.get("phone")
    if live.get("phone_verified") is not True or not isinstance(phone, str) or not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
        raise fail("DIAGNOSTIC_SELF_IDENTITY_UNVERIFIED")
    return phone


def applied_state(row, live):
    if live is None or type(live.get("applied_revision")) is not int:
        return "unknown"
    return "yes" if live["applied_revision"] == row["revision"] else "no"


def proxy_view(result):
    code = result.get("error_code")
    state = result.get("state")
    code = code if code in PROXY_CODES else None if state == "reachable" else "PROXY_OUTCOME_UNCERTAIN" if state == "uncertain" else "PROXY_PROBE_FAILED"
    return {"status": {"reachable": "reachable", "not_configured": "unavailable", "failed": "failed", "uncertain": "uncertain"}.get(state, "uncertain"),
        "scope": "configured", "configuration_version": result["configuration_version"],
        "applied_to_session": "yes" if result.get("applied_to_session") is True else "no" if result.get("applied_to_session") is False else "unknown",
        "latency_ms": result.get("latency_ms"), "observed_at": result.get("observed_at"), "error_code": code,
        "reason": ERRORS.get(code) if code else "A conexão TLS com o destino fixo de verificação foi estabelecida pelo proxy configurado."}


async def recent_messages(db, row, now):
    rows = (await db.execute(text("""SELECT e.*,d.name AS device_name,d.removed_at AS device_removed_at,d.runtime AS device_runtime,
      COALESCE(link.root_effect_id,e.id) AS root_effect_id,link.source_effect_id AS retry_of_effect_id,
      ev.kind AS incoming_kind,ev.payload AS incoming_payload,r.origin AS run_origin,
      r.input_kind AS original_input_kind,r.input_text AS run_input_text,
      CASE WHEN e.type='send_text' THEN 'text' ELSE e.payload->'action'->>'type' END AS message_type
      FROM subscription_device_effects e JOIN subscription_devices d ON d.tenant_id=e.tenant_id AND d.id=e.device_id
      LEFT JOIN subscription_sent_message_resends link ON link.tenant_id=e.tenant_id AND link.new_effect_id=e.id
      JOIN subscription_device_effects original ON original.tenant_id=e.tenant_id AND original.id=COALESCE(link.root_effect_id,e.id)
      JOIN subscription_device_events ev ON ev.tenant_id=original.tenant_id AND ev.id=original.event_id
      LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=original.tenant_id AND r.id=original.chatbot_run_id
      WHERE e.tenant_id=:t AND e.device_id=:d AND d.owner_account_id=:a AND """ + history.MESSAGE_SQL + """
      ORDER BY e.created_at DESC,e.id DESC LIMIT 3"""),
      {"t": row["tenant_id"], "d": row["id"], "a": row["owner_account_id"]})).mappings().all()
    result = []
    for item in rows:
        dto = history.item_dto(dict(item) | {"resend_block": "MESSAGE_POLICY_CHANGED"}, now)
        result.append({key: dto[key] for key in ("id", "created_at", "recipient", "sent", "status", "status_label", "outcome")}
            | {"type": dto["sent"]["type"], "truncated": dto["sent"]["truncated"]})
    return {"limit": 3, "items": result}


async def request_row(db, tenant, *, key=None, identity=None, actor=None):
    row = (await db.execute(text("""SELECT r.* FROM subscription_diagnostic_requests r
      JOIN subscription_devices d ON d.tenant_id=r.tenant_id AND d.id=r.device_id
      WHERE r.tenant_id=:t AND (CAST(:key AS uuid) IS NULL OR r.idempotency_key=CAST(:key AS uuid))
        AND (CAST(:id AS uuid) IS NULL OR r.id=CAST(:id AS uuid))
        AND (CAST(:actor AS uuid) IS NULL OR r.actor_account_id=CAST(:actor AS uuid) AND d.owner_account_id=CAST(:actor AS uuid))
      FOR UPDATE OF r"""), {"t": tenant, "key": key, "id": identity, "actor": actor.account_id if actor else None})).mappings().one_or_none()
    return dict(row) if row else None


async def existing_intent(db, tenant, key, actor):
    operation = await request_row(db, tenant, key=key, actor=actor)
    if operation is None and (await db.execute(text("""SELECT EXISTS(SELECT 1 FROM subscription_diagnostic_requests
      WHERE tenant_id=:t AND idempotency_key=:key)"""), {"t": tenant, "key": key})).scalar_one():
        # A key retained by another historical owner cannot cause a uniqueness
        # exception or expose that owner's private preview.
        raise fail("DIAGNOSTIC_KEY_CONFLICT")
    return operation


async def operation_dto(db, operation, now, *, replayed=False, receipt=None):
    state = operation["status"]
    snapshot = operation["snapshot"]
    preview = message = proxy = None
    expires = operation.get("execution_expires_at") or operation["expires_at"]
    finished = operation.get("finished_at")
    if operation["kind"] == "message_test":
        preview = {"confirmation_version": operation["confirmation_version"],
            "recipient": {"mode": snapshot["recipient_mode"], "phone_e164": "+"+snapshot["phone"]},
            "text": snapshot["text"], "expires_at": operation["expires_at"]}
        if operation.get("effect_id"):
            effect = dict((await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND id=:e"),
                {"t": operation["tenant_id"], "e": operation["effect_id"]})).mappings().one())
            status, outcome, _ = history.public_state(effect, now)
            code = "MESSAGE_EXPIRED_BEFORE_SEND" if history.expired_unclaimed(effect, now) else effect.get("error_code")
            safe = code if code in history.FAILURE_MESSAGES or code in ERRORS else "MESSAGE_OUTCOME_UNCERTAIN" if status == "uncertain" else "MESSAGE_FAILED" if status == "failed" else None
            message = {"effect_id": effect["id"], "status": status, "outcome": outcome, "error_code": safe,
                "reason": (history.FAILURE_MESSAGES | ERRORS).get(safe, "Não foi possível confirmar o resultado do envio." if status == "uncertain" else "A mensagem não pôde ser enviada." if status == "failed" else None)}
            state = {"sent": "succeeded", "failed": "failed", "uncertain": "uncertain", "pending": "running" if effect["status"] == "running" else "pending"}[status]
            finished, expires = effect.get("finished_at"), effect["expires_at"]
        elif operation["expires_at"] <= now:
            state = "expired"
    else:
        result = operation.get("result")
        if receipt and receipt.get("recorded") and receipt.get("status") in {"succeeded", "failed", "uncertain"}:
            result, state = receipt["result"], receipt["status"]
            finished = result.get("observed_at")
        if result:
            proxy = proxy_view(result)
        elif expires <= now or state == "running" and operation.get("claim_expires_at") and operation["claim_expires_at"] <= now:
            started = operation.get("started_at") is not None
            state = "uncertain" if started else "failed"
            proxy = proxy_view({"state": "uncertain" if started else "not_configured", "configuration_version": snapshot["configuration_version"],
                "error_code": "PROXY_OUTCOME_UNCERTAIN" if started else "PROXY_EXPIRED_BEFORE_START", "observed_at": None})
    return {"request_id": operation["id"], "idempotency_key": operation["idempotency_key"], "kind": operation["kind"],
        "device_id": operation["device_id"], "status": state, "replayed": replayed, "created_at": operation["created_at"],
        "expires_at": expires, "finished_at": finished, "preview": preview, "message": message, "proxy": proxy}


def same_intent(operation, row, kind, fingerprint):
    if operation["device_id"] != row["id"] or operation["kind"] != kind or not secrets.compare_digest(operation["request_fingerprint"], fingerprint):
        raise fail("DIAGNOSTIC_KEY_CONFLICT")


async def active_execution(db, row, kind, now):
    return bool((await db.execute(text("""SELECT EXISTS(SELECT 1 FROM subscription_diagnostic_requests r
      LEFT JOIN subscription_device_effects e ON e.tenant_id=r.tenant_id AND e.id=r.effect_id
      WHERE r.tenant_id=:t AND r.device_id=:d AND r.kind=:kind AND r.execution_expires_at>:now
        AND ((r.kind='proxy_test' AND r.status IN ('pending','running'))
          OR (r.kind='message_test' AND e.status IN ('pending','running','uncertain'))))"""),
      {"t": row["tenant_id"], "d": row["id"], "kind": kind, "now": now})).scalar_one())


async def contact_busy(db, row, phone, now, *, except_effect=None, before=None):
    return bool((await db.execute(text("""SELECT EXISTS(SELECT 1 FROM subscription_device_effects e
      WHERE e.tenant_id=:t AND e.device_id=:d AND e.type IN ('send_text','chatbot_action','campaign_action')
        AND e.payload->>'phone'=:phone AND e.status IN ('pending','running','uncertain')
        AND e.expires_at>:now AND (CAST(:except AS uuid) IS NULL OR e.id<>CAST(:except AS uuid))
        AND (CAST(:before AS timestamptz) IS NULL OR (e.created_at,e.id)<(CAST(:before AS timestamptz),CAST(:except AS uuid))))"""),
      {"t": row["tenant_id"], "d": row["id"], "phone": phone, "now": now, "except": except_effect, "before": before})).scalar_one())


@router.get(BASE)
async def list_diagnostics(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT * FROM subscription_devices WHERE tenant_id=:t
          AND owner_account_id=:a AND removed_at IS NULL ORDER BY created_at,id"""),
          {"t": tenant_id, "a": actor.account_id})).mappings().all()
        zone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant_id})).scalar_one()
        return jsonable_encoder({"devices": [{"id": row["id"], "name": row["name"], "version": device_version(row)} for row in rows],
            "selected_device_id": None, "generated_at": await clock(db), "timezone": zone})


@router.get(BASE + "/requests/{idempotency_key}")
async def get_request(tenant_id: UUID, idempotency_key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        operation = await request_row(db, tenant_id, key=idempotency_key, actor=actor)
        if not operation:
            raise fail("DIAGNOSTIC_NOT_FOUND", 404)
        _, transport = await scope(db, tenant_id, operation["device_id"], actor, removed=True)
    receipt = await read_proxy_receipt(transport, operation) if operation["kind"] == "proxy_test" and not operation.get("result") else None
    async with owner_transaction(tenant_id, actor) as db:
        operation = await request_row(db, tenant_id, key=idempotency_key, actor=actor)
        if not operation:
            raise fail("DIAGNOSTIC_NOT_FOUND", 404)
        await scope(db, tenant_id, operation["device_id"], actor, removed=True)
        return jsonable_encoder(await operation_dto(db, operation, await clock(db), replayed=True, receipt=receipt))


@router.get(BASE + "/{device_id}")
async def diagnostics_report(tenant_id: UUID, device_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        original, transport = await scope(db, tenant_id, device_id, actor)
    live, server = await asyncio.gather(read_status(transport, original), read_server())
    async with owner_transaction(tenant_id, actor) as db:
        row, transport = await scope(db, tenant_id, device_id, actor)
        if device_version(row) != device_version(original):
            live = None
        now = await clock(db)
        runtime = live or devices._safe_runtime(row["runtime"])
        observed = live.get("observed_at") if live else row["runtime_at"]
        state = runtime["status"] if live or observed else "unknown"
        if live is None and (not isinstance(row["runtime"], dict)
                or row["runtime"].get("status") not in set(CONNECTION_LABELS) | {"qr"}):
            state = "unknown"
        if row["status"] == "disabled":
            state = "disabled"
        connection = {"state": state, "label": CONNECTION_LABELS.get(state, "Desconhecido"), "source": "bridge" if live else "stored",
            "observed_at": observed, "freshness": "current" if live else "stale" if observed else "unknown",
            "phone_e164": "+"+runtime["phone"] if runtime.get("phone_verified") and runtime.get("phone") else None}
        configured = row["settings"].get("proxy", {}).get("enabled") is True
        parts = urlsplit(row["settings"].get("proxy", {}).get("url") or "")
        version = configuration_version(row)
        previous = (await db.execute(text("""SELECT id,result,snapshot FROM subscription_diagnostic_requests
          WHERE tenant_id=:t AND device_id=:d AND kind='proxy_test' AND result IS NOT NULL
          ORDER BY created_at DESC,id DESC LIMIT 1"""), {"t": tenant_id, "d": device_id})).mappings().one_or_none()
        last = None
        if previous:
            observed_test = previous["result"].get("observed_at")
            try:
                observed_test = datetime.fromisoformat(str(observed_test).replace("Z", "+00:00"))
                fresh = bool(observed_test.tzinfo and now-timedelta(seconds=PROXY_FRESH_SECONDS) <= observed_test <= now)
            except (ValueError, TypeError):
                fresh = False
            last = proxy_view(previous["result"]) | {"request_id": previous["id"],
                "freshness": "current" if previous["snapshot"]["configuration_version"] == version and fresh else "stale"}
        can_prepare, own, blocked = True, None, None
        try:
            own = verified_self(row, transport, live)
        except HTTPException as error:
            can_prepare, blocked = False, error.detail["message"]
        idle = row["status"] not in {"removing", "removed"}
        return jsonable_encoder({"device": {"id": row["id"], "name": row["name"], "version": device_version(row)},
            "generated_at": now, "timezone": row["settings"]["timezone"], "connection": connection, "server": server,
            "recent_messages": await recent_messages(db, row, now),
            "proxy": {"mode": "configured" if configured else "direct", "host": parts.hostname if configured else None,
                "port": (parts.port or 80) if configured else None, "configuration_version": version,
                "applied_to_session": applied_state(row, live), "can_test": configured and idle,
                "blocked_reason": None if configured and idle else ERRORS["PROXY_NOT_CONFIGURED"] if not configured else ERRORS["DIAGNOSTIC_CONTEXT_CHANGED"], "last_test": last},
            "test_message": {"can_prepare": can_prepare, "default_recipient_e164": "+"+own if own else None,
                "text": TEST_TEXT, "requires_confirmation": True, "blocked_reason": blocked}})


@router.post(BASE + "/{device_id}/test-messages/prepare", status_code=201)
async def prepare_message(tenant_id: UUID, device_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        original, transport = await scope(db, tenant_id, device_id, actor)
    payload = await bounded_json(request, PrepareMessage, limit=4096)
    fingerprint = store.fingerprint({"kind": "message_test", "device_id": device_id, "expected_version": payload.expected_version, "phone": payload.phone})
    async with owner_transaction(tenant_id, actor) as db:
        row, transport = await scope(db, tenant_id, device_id, actor)
        existing = await existing_intent(db, tenant_id, payload.idempotency_key, actor)
        if existing:
            same_intent(existing, row, "message_test", fingerprint)
            return jsonable_encoder(await operation_dto(db, existing, await clock(db), replayed=True))
        if device_version(row) != payload.expected_version:
            raise stale_device_version(payload, device_id, "message_test")
        original = row
    live = await read_status(transport, original)
    async with owner_transaction(tenant_id, actor) as db:
        row, transport = await scope(db, tenant_id, device_id, actor)
        existing = await existing_intent(db, tenant_id, payload.idempotency_key, actor)
        if existing:
            same_intent(existing, row, "message_test", fingerprint)
            return jsonable_encoder(await operation_dto(db, existing, await clock(db), replayed=True))
        if device_version(row) != payload.expected_version:
            raise stale_device_version(payload, device_id, "message_test")
        own = verified_self(row, transport, live)
        now, identity = await clock(db), uuid4()
        snapshot = {"phone": payload.phone or own, "recipient_mode": "self" if payload.phone in (None, own) else "explicit",
            "self_phone": own, "text": TEST_TEXT, "device_version": payload.expected_version}
        expires = now + timedelta(seconds=PREVIEW_SECONDS)
        confirmation = store.fingerprint({"id": identity, "tenant": tenant_id, "actor": actor.account_id, "key": payload.idempotency_key,
            "generation": row["generation"], "revision": row["revision"], "snapshot": snapshot, "expires_at": expires})
        await db.execute(text("""INSERT INTO subscription_diagnostic_requests
          (id,tenant_id,device_id,generation,settings_revision,actor_account_id,kind,idempotency_key,request_fingerprint,
           snapshot,confirmation_version,status,created_at,expires_at)
          VALUES(:id,:t,:d,:g,:r,:a,'message_test',:key,:fingerprint,CAST(:snapshot AS jsonb),:confirmation,'prepared',:now,:expires)"""),
          {"id": identity, "t": tenant_id, "d": device_id, "g": row["generation"], "r": row["revision"], "a": actor.account_id,
           "key": payload.idempotency_key, "fingerprint": fingerprint, "snapshot": store.encode(snapshot), "confirmation": confirmation, "now": now, "expires": expires})
        operation = await request_row(db, tenant_id, identity=identity, actor=actor)
        return jsonable_encoder(await operation_dto(db, operation, now))


def confirm_context(operation, row, payload, now):
    if operation["idempotency_key"] != payload.idempotency_key or operation["kind"] != "message_test" or operation["device_id"] != row["id"]:
        raise fail("DIAGNOSTIC_KEY_CONFLICT")
    if not secrets.compare_digest(operation["confirmation_version"], payload.confirmation_version):
        raise fail("DIAGNOSTIC_CONFIRMATION_INVALID")
    if operation.get("effect_id"):
        return
    if operation["expires_at"] <= now:
        raise fail("DIAGNOSTIC_PREVIEW_EXPIRED", 410)
    if operation["generation"] != row["generation"] or operation["settings_revision"] != row["revision"] or row["removed_at"]:
        raise fail("DIAGNOSTIC_CONTEXT_CHANGED")


@router.post(BASE + "/{device_id}/test-messages/{request_id}/confirm", status_code=202)
async def confirm_message(tenant_id: UUID, device_id: UUID, request_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        original, transport = await scope(db, tenant_id, device_id, actor, removed=True)
    payload = await bounded_json(request, ConfirmMessage, limit=4096)
    async with owner_transaction(tenant_id, actor) as db:
        original, transport = await scope(db, tenant_id, device_id, actor, removed=True)
        operation = await request_row(db, tenant_id, identity=request_id, actor=actor)
        if not operation:
            raise fail("DIAGNOSTIC_NOT_FOUND", 404)
        now = await clock(db)
        confirm_context(operation, original, payload, now)
        if operation.get("effect_id"):
            return jsonable_encoder(await operation_dto(db, operation, now, replayed=True))
    live = await read_status(transport, original)
    async with owner_transaction(tenant_id, actor) as db:
        row, transport = await scope(db, tenant_id, device_id, actor, removed=True)
        operation = await request_row(db, tenant_id, identity=request_id, actor=actor)
        if not operation:
            raise fail("DIAGNOSTIC_NOT_FOUND", 404)
        now = await clock(db)
        confirm_context(operation, row, payload, now)
        if operation.get("effect_id"):
            return jsonable_encoder(await operation_dto(db, operation, now, replayed=True))
        own = verified_self(row, transport, live)
        if own != operation["snapshot"]["self_phone"]:
            raise fail("DIAGNOSTIC_SELF_IDENTITY_CHANGED")
        if await active_execution(db, row, "message_test", now):
            raise fail("DIAGNOSTIC_BUSY")
        if await contact_busy(db, row, operation["snapshot"]["phone"], now):
            raise fail("DIAGNOSTIC_CONTACT_BUSY")
        event, effect = uuid4(), uuid4()
        expires = now + timedelta(seconds=MESSAGE_SECONDS)
        await db.execute(text("""INSERT INTO subscription_device_events
          (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
          VALUES(:id,:t,:d,:g,:delivery,'diagnostic',:now,CAST(:payload AS jsonb))"""),
          {"id": event, "t": tenant_id, "d": device_id, "g": row["generation"], "delivery": "diagnostic:"+str(request_id),
           "now": now, "payload": store.encode({"origin": "diagnostic", "diagnostic_request_id": request_id})})
        await db.execute(text("""INSERT INTO subscription_device_effects
          (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,created_at,due_at,expires_at)
          VALUES(:id,:t,:d,:g,:r,:event,'diagnostic_test','send_text','diagnostic_test',CAST(:payload AS jsonb),:now,:now,:expires)"""),
          {"id": effect, "t": tenant_id, "d": device_id, "g": row["generation"], "r": row["revision"], "event": event,
           "payload": store.encode({"phone": operation["snapshot"]["phone"], "text": operation["snapshot"]["text"]}), "now": now, "expires": expires})
        await db.execute(text("""UPDATE subscription_diagnostic_requests SET status='pending',effect_id=:effect,
          confirmed_at=:now,execution_expires_at=:expires WHERE tenant_id=:t AND id=:id"""),
          {"effect": effect, "now": now, "expires": expires, "t": tenant_id, "id": request_id})
        operation = await request_row(db, tenant_id, identity=request_id, actor=actor)
        return jsonable_encoder(await operation_dto(db, operation, now))


async def read_proxy_receipt(transport, operation):
    try:
        async with asyncio.timeout(6):
            data = await channels._bridge_control(transport, f"device/diagnostics/operations/{operation['id']}", "GET")
        if (not isinstance(data, dict) or data.get("recorded") is not True or str(data.get("request_id")) != str(operation["id"])
                or str(data.get("device_id")) != str(operation["device_id"])
                or str(data.get("generation")) != str(operation["generation"])
                or data.get("settings_revision") != operation["settings_revision"]
                or data.get("status") not in {"succeeded", "failed", "uncertain"}):
            return None
        result = ProxyResult.model_validate(data["result"])
        if (operation.get("claim_fence", 0) < 1 or operation.get("started_at") is None
                or not valid_proxy_result(operation, data["status"], result, datetime.now(timezone.utc))):
            return None
        return {"recorded": True, "status": data["status"], "result": result.model_dump(mode="json")}
    except (HTTPException, TimeoutError, ValueError, KeyError):
        return None


@router.post(BASE + "/{device_id}/proxy-tests", status_code=202)
async def test_proxy(tenant_id: UUID, device_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        await scope(db, tenant_id, device_id, actor)
    payload = await bounded_json(request, DiagnosticIntent, limit=4096)
    fingerprint = store.fingerprint({"kind": "proxy_test", "device_id": device_id, "expected_version": payload.expected_version})
    async with owner_transaction(tenant_id, actor) as db:
        row, transport = await scope(db, tenant_id, device_id, actor)
        existing = await existing_intent(db, tenant_id, payload.idempotency_key, actor)
        if existing:
            same_intent(existing, row, "proxy_test", fingerprint)
            return jsonable_encoder(await operation_dto(db, existing, await clock(db), replayed=True))
        if device_version(row) != payload.expected_version:
            raise stale_device_version(payload, device_id, "proxy_test")
        if row["status"] in {"removing", "removed"}:
            raise fail("DIAGNOSTIC_CONTEXT_CHANGED")
        if not row["settings"].get("proxy", {}).get("enabled"):
            raise fail("PROXY_NOT_CONFIGURED")
        now = await clock(db)
        if await active_execution(db, row, "proxy_test", now):
            raise fail("DIAGNOSTIC_BUSY")
        identity, expires = uuid4(), now + timedelta(seconds=PROXY_SECONDS)
        await db.execute(text("""INSERT INTO subscription_diagnostic_requests
          (id,tenant_id,device_id,generation,settings_revision,actor_account_id,kind,idempotency_key,request_fingerprint,
           snapshot,status,created_at,expires_at,execution_expires_at)
          VALUES(:id,:t,:d,:g,:r,:a,'proxy_test',:key,:fingerprint,CAST(:snapshot AS jsonb),'pending',:now,:expires,:expires)"""),
          {"id": identity, "t": tenant_id, "d": device_id, "g": row["generation"], "r": row["revision"], "a": actor.account_id,
           "key": payload.idempotency_key, "fingerprint": fingerprint,
           "snapshot": store.encode({"configuration_version": configuration_version(row), "device_version": device_version(row)}), "now": now, "expires": expires})
    # No SQL locks are held while the bridge claims its independent operation.
    # Response loss never causes an automatic second dispatch.
    try:
        await channels._bridge_control(transport, "device/diagnostics/proxy", "POST", jsonable_encoder({
            "request_id": identity, "device_id": device_id, "generation": row["generation"], "settings_revision": row["revision"]}))
    except HTTPException:
        pass
    async with owner_transaction(tenant_id, actor) as db:
        operation = await request_row(db, tenant_id, identity=identity, actor=actor)
        if not operation:
            raise fail("DIAGNOSTIC_NOT_FOUND", 404)
        await scope(db, tenant_id, device_id, actor, removed=True)
        return jsonable_encoder(await operation_dto(db, operation, await clock(db)))


async def diagnostic_authority(db, row, effect, now):
    """Trusted request linkage, independent of user events and reply policies."""
    if effect["type"] != "send_text" or effect["purpose"] != "diagnostic_test":
        return None
    await store.workspace(db, row["tenant_id"], feature="diagnostics.manage")
    operation = (await db.execute(text("""SELECT * FROM subscription_diagnostic_requests
      WHERE tenant_id=:t AND device_id=:d AND effect_id=:e AND kind='message_test'"""),
      {"t": row["tenant_id"], "d": row["id"], "e": effect["id"]})).mappings().one_or_none()
    if not operation:
        return None
    frozen = operation["snapshot"]
    runtime = devices._safe_runtime(row["runtime"])
    if (operation["status"] != "pending" or operation["confirmed_at"] is None
            or operation["actor_account_id"] != row["owner_account_id"]
            or operation["generation"] != row["generation"] or operation["settings_revision"] != row["revision"]
            or effect["generation"] != row["generation"] or effect["settings_revision"] != row["revision"]
            or row["removed_at"] is not None or row["status"] in {"disabled", "removing", "removed"}
            or operation["execution_expires_at"] is None or operation["execution_expires_at"] <= now
            or effect["expires_at"] <= now or effect["expires_at"] != operation["execution_expires_at"]
            or effect["payload"] != {"phone": frozen.get("phone"), "text": frozen.get("text")}
            or frozen.get("text") != TEST_TEXT or frozen.get("recipient_mode") not in {"self", "explicit"}
            or frozen.get("self_phone") != runtime.get("phone") or not runtime.get("phone_verified")
            or frozen.get("recipient_mode") == "self" and frozen.get("phone") != frozen.get("self_phone")):
        return None
    if not await channels._owner_authorized(db, row["tenant_id"], Actor("member", account_id=operation["actor_account_id"])):
        return None
    if await contact_busy(db, row, frozen["phone"], now, except_effect=effect["id"], before=effect["created_at"]):
        return None
    return dict(operation)


async def authorize_diagnostic_effect(db, row, effect, owner, fence, payload):
    """A running recheck is exclusive to this confirmed purpose and claim."""
    denied = {"authorized": False, "operation_id": str(effect["id"])}
    if payload.recheck:
        if (effect["status"] != "running" or effect["claimed_owner"] != owner
                or effect["claimed_fence"] != fence or effect["attempts"] != 1):
            return denied
    elif effect["status"] != "pending" or effect["attempts"] != 0 or effect["authorized_at"] is not None:
        return denied
    now = await clock(db)
    if effect["due_at"] > now:
        return denied
    operation = await diagnostic_authority(db, row, effect, now)
    if operation is None:
        if not payload.recheck:
            await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
              error_code='DIAGNOSTIC_REVOKED',finished_at=:now WHERE tenant_id=:t AND id=:e AND status='pending'"""),
              {"now": now, "t": row["tenant_id"], "e": effect["id"]})
        return denied
    if not payload.recheck:
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=1,
          authorized_at=:now,claimed_owner=:owner,claimed_fence=:fence WHERE tenant_id=:t AND id=:e"""),
          {"now": now, "owner": owner, "fence": fence, "t": row["tenant_id"], "e": effect["id"]})
    frozen = operation["snapshot"]
    return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": "send_text", "purpose": "diagnostic_test",
        "diagnostic_request_id": operation["id"], "confirmed": True, "recipient_mode": frozen["recipient_mode"],
        "self_phone": frozen["self_phone"], "device_id": row["id"], "generation": row["generation"],
        "settings_revision": row["revision"], "payload": effect["payload"]})


def same_proxy_request(row, operation, payload):
    if (operation is None or operation["kind"] != "proxy_test" or operation["device_id"] != payload.device_id
            or operation["id"] != payload.request_id or operation["tenant_id"] != row["tenant_id"]
            or operation["device_id"] != row["id"] or operation["generation"] != payload.generation
            or operation["settings_revision"] != payload.settings_revision
            or operation["actor_account_id"] != row["owner_account_id"]):
        raise fail("DIAGNOSTIC_REVOKED")


async def proxy_authority(db, row, operation, payload, now):
    """Network authority has no inactive-tenant or historical-receipt mode."""
    await store.workspace(db, row["tenant_id"], feature="diagnostics.manage")
    same_proxy_request(row, operation, payload)
    if not await channels._owner_authorized(db, row["tenant_id"], Actor("member", account_id=operation["actor_account_id"])):
        raise fail("DIAGNOSTIC_REVOKED", 403)
    status = (await db.execute(text("SELECT status FROM tenants WHERE id=:t"), {"t": row["tenant_id"]})).scalar_one_or_none()
    if status != "active":
        raise fail("DIAGNOSTIC_REVOKED", 403)
    if (row["removed_at"] is not None or row["status"] in {"removing", "removed"}
            or row["generation"] != operation["generation"] or row["revision"] != operation["settings_revision"]
            or operation["snapshot"]["configuration_version"] != configuration_version(row)
            or operation["execution_expires_at"] <= now):
        raise fail("DIAGNOSTIC_CONTEXT_CHANGED")


@asynccontextmanager
async def proxy_receipt_transaction(request, request_id):
    """Only these two authenticated callbacks may preserve a paused receipt.

This boundary never returns proxy configuration, secrets or a network grant.
It cannot be used by an authorize/recheck or public endpoint. The sole tenant
state exception is paused; owner, niche, canonical channel and immutable intent
identity remain mandatory, including for historical device tombstones.
"""
    try:
        channel_id = UUID(str(request.path_params["channel_id"]))
        prefix = INTERNAL.format(channel_id=channel_id) + "/proxy/"
        if request.method != "POST" or request.url.path not in {prefix+"finish", prefix+"receipt"}:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Canal não autorizado") from None
    async with support_session_factory()() as db:
        async with db.begin():
            tenant = (await db.execute(text("SELECT tenant_id FROM qr_channels WHERE id=:c"), {"c": channel_id})).scalar_one_or_none()
            if tenant is None:
                raise HTTPException(401, "Canal não autorizado")
            # write=False retains the persisted niche and shared plan lock.
            # Active tenants must still have both capabilities; paused tenants
            # have those two bits masked by the plan resolver by definition.
            state = await store.workspace(db, tenant, write=False, feature="diagnostics.manage")
            await channels._lock_membership(db, channel_id)
            tenant_row = (await db.execute(text("SELECT id,status,whatsapp_channel_id FROM tenants WHERE id=:t FOR UPDATE"),
                {"t": tenant})).mappings().one_or_none()
            status = tenant_row["status"] if tenant_row is not None else None
            if (status not in {"active", "paused"} or status == "active"
                    and not all(state.get("permissions", {}).get(key) for key in ("devices.manage", "diagnostics.manage"))):
                raise devices.fail("WORKSPACE_FEATURE_UNAVAILABLE", 403)
            transport = await channels._channel(db, channel_id, request.headers.get("x-orion-channel-key"), receipt_only=True)
            # The generic canonical guard also requires an active tenant. This
            # receipt-only boundary has already checked the exact status policy;
            # reuse its identity selector without granting execution or repairing
            # an absent/broken pointer. Alias channels remain forbidden below.
            candidates = (await db.execute(text("SELECT id,tenant_id,enabled FROM qr_channels WHERE tenant_id=:tenant ORDER BY id"),
                {"tenant": tenant})).mappings().all()
            canonical, canonical_state = channels._canonical_candidate(candidates, tenant_row["whatsapp_channel_id"])
            if canonical_state != "ready" or canonical is None or canonical["id"] != channel_id:
                raise HTTPException(409, "Conexão única indisponível; atualize o estado ou solicite revisão")
            operation = await request_row(db, tenant, identity=request_id)
            if operation is None or operation["kind"] != "proxy_test":
                raise fail("DIAGNOSTIC_NOT_FOUND", 404)
            row = (await db.execute(text("""SELECT * FROM subscription_devices
              WHERE tenant_id=:t AND id=:d AND channel_id=:c FOR UPDATE"""),
              {"t": tenant, "d": operation["device_id"], "c": channel_id})).mappings().one_or_none()
            if (row is None or operation["actor_account_id"] != row["owner_account_id"]
                    or row["owner_account_id"] != transport["owner_account_id"]
                    or transport["assistant_enabled"] or transport["allowed_senders"]
                    or devices.is_alias(tenant, channel_id)):
                raise fail("DIAGNOSTIC_REVOKED", 403)
            yield db, dict(row), operation


def valid_proxy_result(operation, status, result, now):
    """Accept a bounded receipt, never exception text or unscoped evidence."""
    if (result.configuration_version != operation["snapshot"]["configuration_version"]
            or result.error_code is not None and result.error_code not in PROXY_CODES
            or result.observed_at < (operation.get("started_at") or operation["created_at"])-timedelta(seconds=5)
            or result.observed_at > now+timedelta(seconds=5)
            or result.observed_at > (operation.get("claim_expires_at") or operation["expires_at"])+timedelta(seconds=5)):
        return False
    if status == "succeeded":
        return result.state == "reachable" and result.error_code is None and result.latency_ms is not None
    return ((status == "failed" and result.state in {"failed", "not_configured"}
             or status == "uncertain" and result.state == "uncertain") and result.error_code in PROXY_CODES)


@router.post(INTERNAL + "/proxy/authorize")
async def authorize_proxy(channel_id: UUID, payload: ProxyAuthorize, request: Request):
    async with devices._private_transaction(request, live=False, device_id=payload.device_id) as (db, row, transport, _, _):
        operation = await request_row(db, row["tenant_id"], identity=payload.request_id)
        now = await clock(db)
        await proxy_authority(db, row, operation, payload, now)
        denied = {"authorized": False, "request_id": str(operation["id"]), "status": operation["status"]}
        if payload.recheck:
            if (operation["status"] != "running" or operation["claimed_owner"] != payload.owner
                    or operation["claim_token"] != payload.claim_token or operation["claim_fence"] != payload.claim_fence
                    or operation["claim_expires_at"] is None or operation["claim_expires_at"] <= now):
                return denied
        elif operation["status"] != "pending" or operation["claim_fence"] != 0:
            return denied
        proxy = row["settings"].get("proxy", {})
        secret = None
        if row.get("proxy_secret_encrypted"):
            try:
                secret = {"password": channels._cipher().decrypt(row["proxy_secret_encrypted"].encode()).decode()}
            except (InvalidToken, UnicodeError):
                raise fail("PROXY_CONFIGURATION_INVALID") from None
        if not payload.recheck:
            expires = min(operation["execution_expires_at"], now+timedelta(seconds=CLAIM_SECONDS))
            operation = dict((await db.execute(text("""UPDATE subscription_diagnostic_requests SET status='running',
              started_at=:now,claimed_owner=:owner,claim_token=:token,claim_fence=claim_fence+1,claim_expires_at=:expires
              WHERE tenant_id=:t AND id=:id RETURNING *"""),
              {"now": now, "owner": payload.owner, "token": payload.claim_token, "expires": expires,
               "t": row["tenant_id"], "id": operation["id"]})).mappings().one())
        remaining = max(0, min(CLAIM_SECONDS*1000, int((operation["claim_expires_at"]-now).total_seconds()*1000)))
        if remaining < 1:
            return denied
        return jsonable_encoder({"authorized": True, "kind": "proxy_test", "request_id": operation["id"],
            "tenant_id": row["tenant_id"], "channel_id": channel_id, "device_id": row["id"], "generation": row["generation"],
            "settings_revision": row["revision"], "configuration_version": operation["snapshot"]["configuration_version"],
            "owner": payload.owner, "claim_token": payload.claim_token, "claim_fence": operation["claim_fence"],
            "expires_at": operation["claim_expires_at"], "expires_in_ms": remaining,
            "proxy": {key: proxy.get(key) for key in ("enabled", "url", "username")}, "proxy_secret": secret})


@router.post(INTERNAL + "/proxy/finish")
async def finish_proxy(channel_id: UUID, payload: ProxyFinish, request: Request):
    async with proxy_receipt_transaction(request, payload.request_id) as (db, row, operation):
        now = await clock(db)
        same_proxy_request(row, operation, payload)
        if (operation["claimed_owner"] != payload.owner or operation["claim_token"] != payload.claim_token
                or operation["claim_fence"] != payload.claim_fence or operation["started_at"] is None):
            raise fail("DIAGNOSTIC_REVOKED")
        if operation["status"] in {"succeeded", "failed", "uncertain"}:
            return {"request_id": str(operation["id"]), "status": operation["status"]}
        if operation["status"] != "running" or not valid_proxy_result(operation, payload.status, payload.result, now):
            raise fail("DIAGNOSTIC_REVOKED")
        state, result = payload.status, payload.result.model_dump(mode="json")
        if (row["removed_at"] is not None or row["generation"] != operation["generation"]
                or row["revision"] != operation["settings_revision"]
                or operation["snapshot"]["configuration_version"] != configuration_version(row)):
            state = "failed"
            result.update(state="failed", error_code="PROXY_CONFIGURATION_CHANGED", applied_to_session=False)
        await db.execute(text("""UPDATE subscription_diagnostic_requests SET status=:status,result=CAST(:result AS jsonb),
          finished_at=:now WHERE tenant_id=:t AND id=:id"""),
          {"status": state, "result": store.encode(result), "now": now, "t": row["tenant_id"], "id": operation["id"]})
        return {"request_id": str(operation["id"]), "status": state}


@router.post(INTERNAL + "/proxy/receipt")
async def proxy_receipt(channel_id: UUID, payload: ProxyReceipt, request: Request):
    # Although the private transport uses POST, this endpoint grants reads only
    # and cannot change a request, claim, connection or effect.
    async with proxy_receipt_transaction(request, payload.request_id) as (_, row, operation):
        return jsonable_encoder({"read_only": True, "kind": "proxy_test", "request_id": operation["id"],
            "channel_id": channel_id, "tenant_id": row["tenant_id"], "device_id": operation["device_id"],
            "generation": operation["generation"], "settings_revision": operation["settings_revision"]})
