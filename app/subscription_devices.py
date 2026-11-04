"""Owner-only subscription devices, fenced controls and a durable effect broker.

The browser receives no channel credential. Every effect is derived from an
authenticated event and current settings, and is reauthorized immediately before
transport. This module does not enable the legacy assistant or its inbox.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
from uuid import UUID, uuid4
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app import qr_channels as channels
from app.plan_permissions import plan_state
from app.qr_legacy import is_alias
from app.subscription_device_schemas import (CreateDevice, DeviceAction, DeviceEvent,
    DevicePair, DeviceSettings, UpdateSettings, EffectRequest, EffectFinish,
    ControlRequest, ControlFinish)
from app.subscription_device_rules import business_state, effects_for_event, transcription_parts
from app.workspace_policy import SUBSCRIPTION_WORKSPACE


BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/devices"
INTERNAL = "/internal/qr/channels/{channel_id}/device"
router = APIRouter(tags=["Dispositivos de assinaturas"], dependencies=[Depends(channels.enabled)])
TERMINAL = {"succeeded", "failed"}
LIVE_CONTROL = {"pending", "running", "uncertain"}
ERROR_MESSAGES = {
    "DEVICE_CONFLICT": "O dispositivo mudou. Atualize antes de continuar.",
    "DEVICE_NOT_FOUND": "Dispositivo não encontrado.",
    "DEVICE_LIMIT_REACHED": "Esta conta permite um dispositivo ativo.",
    "DEVICE_CAPABILITY_UNAVAILABLE": "Este recurso está indisponível no momento.",
    "DEVICE_BRIDGE_UNAVAILABLE": "Não foi possível consultar a conexão agora.",
    "WORKSPACE_FEATURE_UNAVAILABLE": "Dispositivos estão disponíveis apenas para Serviço de assinaturas.",
}


def fail(code, status=409):
    return HTTPException(status, {"code": code, "message": ERROR_MESSAGES.get(code, "Operação indisponível."),
                                  **({"refresh": True} if code == "DEVICE_CONFLICT" else {})})


def _json(value):
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))


def _budget(name):
    try:
        return max(0, min(1000000000, int(os.getenv(name, "0"))))
    except (ValueError, TypeError):
        return 0


def audio_capable():
    from app.subscription_device_audio import audio_available
    return (audio_available() and _budget("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS") > 0
            and _budget("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS") > 0)


def settings_capabilities():
    return {"manage": True, "transcription": audio_capable(), "transcription_max_minutes": 30,
            "proxy": True, "webhook": True}


async def _workspace(db, tenant_id):
    state = await plan_state(db, tenant_id, lock="shared")
    # No admin/support bypass: the module is scoped to this tenant's persisted
    # niche even when called by an administrator in a maintenance session.
    if (state.get("workspace_profile") != SUBSCRIPTION_WORKSPACE
            or not state.get("permissions", {}).get("devices.manage")):
        raise fail("WORKSPACE_FEATURE_UNAVAILABLE", 403)
    return state


@asynccontextmanager
async def _owner_transaction(tenant_id, actor):
    async with support_session_factory()() as db:
        async with db.begin():
            await _workspace(db, tenant_id)
            await channels._owner(db, tenant_id, actor)
            tenant = (await db.execute(text("SELECT status FROM tenants WHERE id=:t FOR UPDATE"),
                                       {"t": tenant_id})).scalar_one_or_none()
            if tenant != "active":
                raise fail("WORKSPACE_FEATURE_UNAVAILABLE", 403)
            yield db


async def _device(db, tenant_id, device_id, *, actor=None, revision=None, removed=False):
    row = (await db.execute(text("""SELECT * FROM subscription_devices
        WHERE tenant_id=:t AND id=:d AND (:removed OR removed_at IS NULL) FOR UPDATE"""),
        {"t": tenant_id, "d": device_id, "removed": removed})).mappings().one_or_none()
    if not row or actor is not None and row["owner_account_id"] != actor.account_id:
        raise fail("DEVICE_NOT_FOUND", 404)
    if revision is not None and row["revision"] != revision:
        raise fail("DEVICE_CONFLICT")
    transport = await channels._require_canonical(db, tenant_id, row["channel_id"])
    if (transport["owner_account_id"] != row["owner_account_id"]
            or is_alias(tenant_id, transport["id"]) or transport["assistant_enabled"]):
        raise fail("DEVICE_CONFLICT")
    return dict(row), dict(transport)


def public_settings(row):
    settings = json.loads(_json(row["settings"]))
    settings["proxy"]["password_configured"] = bool(row["proxy_secret_encrypted"])
    return settings


def settings_response(row):
    return {"revision": row["revision"], "settings": public_settings(row),
            "capabilities": settings_capabilities(), "applied_revision": row["applied_revision"],
            "restart_required": row["restart_required"]}


async def _metrics(db, row):
    zone = ZoneInfo(row["settings"]["timezone"])
    today = datetime.now(timezone.utc).astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    stats = (await db.execute(text("""SELECT count(*) AS total,
        count(*) FILTER(WHERE occurred_at>=:today) AS today,
        count(*) FILTER(WHERE occurred_at>=:week) AS last_7_days,
        count(*) FILTER(WHERE occurred_at>=:month) AS last_30_days
        FROM subscription_device_events WHERE tenant_id=:t AND device_id=:d
          AND kind IN ('text','audio','outbound')"""),
        {"t": row["tenant_id"], "d": row["id"], "today": today,
         "week": today-timedelta(days=6), "month": today-timedelta(days=29)})).mappings().one()
    return {"available": True, "scope": "orion_events", "tracking_since": row["created_at"],
            "timezone": row["settings"]["timezone"], **dict(stats)}


def _safe_runtime(data):
    data = data if isinstance(data, dict) else {}
    status = data.get("status")
    valid = {"disabled", "stopped", "starting", "connecting", "reconnecting", "qr_ready", "qr_delayed", "qr_expired",
             "pairing", "pair_expired", "pair_failed", "connected", "disconnected", "logged_out", "error", "unavailable"}
    result = {"status": "qr_ready" if status == "qr" else status if status in valid else "disconnected"}
    phone = data.get("phone")
    result["phone"] = phone if isinstance(phone, str) and re.fullmatch(r"[1-9][0-9]{7,14}", phone) else None
    result["phone_verified"] = bool(result["phone"] and data.get("phone_verified") is True)
    if not result["phone_verified"]:
        result["phone"] = None
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    name = profile.get("name")
    avatar = profile.get("avatar_url")
    try:
        url = urlsplit(avatar) if isinstance(avatar, str) and len(avatar) <= 2048 else None
        hostname = (url.hostname or "").lower() if url else ""
        safe_avatar = bool(url and url.scheme == "https" and not url.username and not url.password
            and not url.port and not url.fragment and any(hostname == suffix or hostname.endswith("." + suffix)
                for suffix in ("whatsapp.net", "whatsapp.com", "fbcdn.net")))
    except ValueError:
        safe_avatar = False
    result["profile"] = {"name": name[:100] if isinstance(name, str) else None,
        "avatar_url": avatar if safe_avatar else None,
        "updated_at": None}
    for key in ("connected_at", "last_seen_at"):
        try:
            value = datetime.fromisoformat(str(data.get(key)).replace("Z", "+00:00"))
            result[key] = value.isoformat() if value.tzinfo else None
        except (ValueError, TypeError):
            result[key] = None
    revision = data.get("applied_revision", data.get("settings_revision"))
    if type(revision) is int and revision > 0:
        result["applied_revision"] = revision
    return result


async def device_dto(db, row):
    runtime = _safe_runtime(row["runtime"])
    status = row["status"]
    if status not in {"disabled", "removing", "removed"}:
        recent = row["runtime_at"] and row["runtime_at"] > datetime.now(timezone.utc)-timedelta(seconds=90)
        status = runtime["status"] if recent else "disconnected"
    current = status == "connected"
    idle = status not in {"removing", "removed"}
    pending = (await db.execute(text("""SELECT * FROM subscription_device_operations
        WHERE tenant_id=:t AND device_id=:d AND status IN ('pending','running','uncertain')"""),
        {"t": row["tenant_id"], "d": row["id"]})).mappings().one_or_none()
    if pending:
        idle = False
        status = "removing" if pending["action"] == "remove" else "pairing" if pending["action"] == "pair" else "connecting"
    return {"id": row["id"], "name": row["name"], "revision": row["revision"], "status": status,
        "phone": runtime["phone"], "phone_verified": runtime["phone_verified"], "profile": runtime["profile"],
        "connected_at": runtime["connected_at"], "last_seen_at": runtime["last_seen_at"],
        "metrics": await _metrics(db, row),
        "features": {"transcription": row["settings"]["transcription"]["enabled"],
                     "call_blocking": row["settings"]["calls"]["reject_enabled"]},
        "capabilities": {"connect": idle and not current, "qr": idle and status in {"starting", "connecting", "qr_ready", "qr_delayed", "qr_expired", "pairing"},
            "pair": idle and status == "qr_ready", "restart": idle and status != "disabled",
            "relink": idle and status != "disabled", "remove": idle, "settings": idle, "diagnostics": True},
        "pending_operation": operation_dto(pending) if pending else None,
        "last_error": None}


async def _refresh(tenant_id, device_id, actor):
    async with _owner_transaction(tenant_id, actor) as db:
        row, transport = await _device(db, tenant_id, device_id, actor=actor)
        if row["status"] in {"disabled", "removing"}:
            return
        revision, generation = row["revision"], row["generation"]
    try:
        data = await channels._bridge_control(transport, "device/status", "GET")
    except HTTPException:
        return  # A status GET is never an instruction to reconnect.
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor)
        if row["revision"] != revision or row["generation"] != generation:
            return
        if (str(data.get("device_id")) != str(device_id)
                or str(data.get("generation")) != str(generation)):
            return
        await _store_runtime(db, row, data)


async def _store_runtime(db, row, data):
    safe = _safe_runtime(data)
    applied = safe.get("applied_revision") == row["revision"]
    stored_status = ("qr" if safe["status"].startswith("qr_") else "connecting"
        if safe["status"] in {"starting", "reconnecting", "connecting"} else safe["status"]
        if safe["status"] in {"disabled", "pairing", "connected", "error"} else "disconnected")
    await db.execute(text("""UPDATE subscription_devices SET runtime=CAST(:runtime AS jsonb),
        runtime_at=clock_timestamp(),status=CASE WHEN status IN ('disabled','removing','removed') THEN status ELSE :status END,
        applied_revision=CASE WHEN :applied THEN revision ELSE applied_revision END,
        restart_required=CASE WHEN :applied THEN false ELSE restart_required END
        WHERE tenant_id=:t AND id=:d AND removed_at IS NULL"""),
        {"t": row["tenant_id"], "d": row["id"], "runtime": _json(safe),
         "status": stored_status, "applied": applied})


@router.get(BASE)
async def list_devices(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        ids = (await db.execute(text("SELECT id FROM subscription_devices WHERE tenant_id=:t AND removed_at IS NULL"),
                                {"t": tenant_id})).scalars().all()
    for device_id in ids:
        await _refresh(tenant_id, device_id, actor)
    async with _owner_transaction(tenant_id, actor) as db:
        rows = (await db.execute(text("SELECT * FROM subscription_devices WHERE tenant_id=:t AND removed_at IS NULL ORDER BY created_at"),
                                 {"t": tenant_id})).mappings().all()
        items = [await device_dto(db, row) for row in rows]
        return jsonable_encoder({"items": items, "usage": {"used": len(items), "limit": 1},
                                 "can_create": not items, "capabilities": settings_capabilities()})


@router.post(BASE, status_code=201)
async def create_device(tenant_id: UUID, payload: CreateDevice, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    fingerprint = hashlib.sha256(_json({"name": payload.name}).encode()).hexdigest()
    async with _owner_transaction(tenant_id, actor) as db:
        existing = (await db.execute(text("SELECT * FROM subscription_devices WHERE tenant_id=:t AND idempotency_key=:key"),
                                    {"t": tenant_id, "key": payload.idempotency_key})).mappings().one_or_none()
        if existing:
            if (existing["request_fingerprint"] != fingerprint or existing["owner_account_id"] != actor.account_id
                    or existing["removed_at"] is not None):
                raise fail("DEVICE_CONFLICT")
            response.status_code = 200
            return jsonable_encoder({"device": await device_dto(db, existing)})
        active = (await db.execute(text("SELECT id FROM subscription_devices WHERE tenant_id=:t AND removed_at IS NULL"),
                                  {"t": tenant_id})).scalar_one_or_none()
        if active:
            raise fail("DEVICE_LIMIT_REACHED")
        transport = await channels.provision_owned_connection(db, tenant_id, payload.idempotency_key, actor)
        if (transport["enabled"] or transport["assistant_enabled"] or transport["allowed_senders"]
                or is_alias(tenant_id, transport["id"])):
            raise fail("DEVICE_CONFLICT")
        settings = DeviceSettings(name=payload.name).model_dump(mode="json")
        row = (await db.execute(text("""INSERT INTO subscription_devices
            (tenant_id,channel_id,owner_account_id,name,settings,idempotency_key,request_fingerprint)
            VALUES(:t,:c,:a,:name,CAST(:settings AS jsonb),:key,:fingerprint) RETURNING *"""),
            {"t": tenant_id, "c": transport["id"], "a": actor.account_id, "name": payload.name,
             "settings": _json(settings), "key": payload.idempotency_key, "fingerprint": fingerprint})).mappings().one()
        return jsonable_encoder({"device": await device_dto(db, row)})


@router.get(BASE + "/{device_id}")
async def get_device(tenant_id: UUID, device_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    await _refresh(tenant_id, device_id, actor)
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor)
        return jsonable_encoder({"device": await device_dto(db, row)})


@router.get(BASE + "/{device_id}/settings")
async def get_settings(tenant_id: UUID, device_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor)
        return jsonable_encoder(settings_response(row))


@router.put(BASE + "/{device_id}/settings")
async def update_settings(tenant_id: UUID, device_id: UUID, payload: UpdateSettings, response: Response,
                          actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor, revision=payload.expected_revision)
        pending = (await db.execute(text("SELECT id FROM subscription_device_operations WHERE device_id=:d AND status IN ('pending','running','uncertain')"),
                                   {"d": device_id})).scalar_one_or_none()
        if pending:
            raise fail("DEVICE_CONFLICT")
        settings = payload.settings.model_dump(mode="json")
        if settings["transcription"]["enabled"] and not audio_capable():
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
        secret = row["proxy_secret_encrypted"]
        if payload.settings.proxy.clear_password:
            secret = None
        elif payload.settings.proxy.password is not None:
            secret = channels._cipher().encrypt(payload.settings.proxy.password.encode()).decode()
        proxy_changed = settings["proxy"] != row["settings"]["proxy"] or secret != row["proxy_secret_encrypted"]
        updated = (await db.execute(text("""UPDATE subscription_devices SET name=:name,settings=CAST(:settings AS jsonb),
            proxy_secret_encrypted=:secret,revision=revision+1,updated_at=clock_timestamp(),
            restart_required=restart_required OR :restart,webhook_failures=0
            WHERE tenant_id=:t AND id=:d RETURNING *"""),
            {"t": tenant_id, "d": device_id, "name": settings["name"], "settings": _json(settings),
             "secret": secret, "restart": proxy_changed and row["status"] != "disabled"})).mappings().one()
        # Existing permits become stale. A pending effect is never silently
        # rewritten with newly edited destinations, templates or credentials.
        await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),
            error_code='settings_changed' WHERE device_id=:d AND status IN ('pending','failed')"""), {"d": device_id})
        return jsonable_encoder(settings_response(updated))


def operation_dto(row):
    return {"id": row["id"], "device_id": row["device_id"], "action": row["action"],
            "status": row["status"], "error": ({"code": row["error_code"], "message": "Confira o estado da conexão antes de tentar novamente."}
                                             if row["error_code"] else None)}


async def _reserve_control(db, row, action, key, request_payload=None):
    previous = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE device_id=:d AND idempotency_key=:key"),
                                 {"d": row["id"], "key": key})).mappings().one_or_none()
    if previous:
        if previous["action"] != action:
            raise fail("DEVICE_CONFLICT")
        return dict(previous), False
    pending = (await db.execute(text("SELECT id FROM subscription_device_operations WHERE device_id=:d AND status IN ('pending','running','uncertain')"),
                               {"d": row["id"]})).scalar_one_or_none()
    if pending:
        raise fail("DEVICE_CONFLICT")
    if action in {"restart", "relink", "pair"} and row["status"] == "disabled":
        raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
    operation = (await db.execute(text("""INSERT INTO subscription_device_operations
        (tenant_id,device_id,generation,settings_revision,idempotency_key,action,request_payload)
        VALUES(:t,:d,:g,:r,:key,:action,CAST(:payload AS jsonb)) RETURNING *"""),
        {"t": row["tenant_id"], "d": row["id"], "g": row["generation"], "r": row["revision"],
         "key": key, "action": action, "payload": _json(request_payload or {})})).mappings().one()
    return dict(operation), True


async def _operation_response(tenant_id, device_id, operation_id, actor):
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor, removed=True)
        operation = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE tenant_id=:t AND device_id=:d AND id=:o"),
                                     {"t": tenant_id, "d": device_id, "o": operation_id})).mappings().one_or_none()
        if not operation:
            raise fail("DEVICE_NOT_FOUND", 404)
        return jsonable_encoder({"operation": operation_dto(operation),
                                 "device": None if row["removed_at"] else await device_dto(db, row)})


async def _reconcile_control_result(tenant_id, device_id, operation_id, actor, data):
    if not isinstance(data, dict):
        return
    async with _owner_transaction(tenant_id, actor) as db:
        row, transport = await _device(db, tenant_id, device_id, actor=actor, removed=True)
        operation = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE tenant_id=:t AND device_id=:d AND id=:o FOR UPDATE"),
                                     {"t": tenant_id, "d": device_id, "o": operation_id})).mappings().one_or_none()
        if not operation or operation["status"] in TERMINAL:
            return
        if (data.get("recorded") is False and str(data.get("operation_id")) == str(operation_id)
                and operation["started_at"] is None):
            # No broker permit was ever granted and the transport ledger has
            # no attempt. This is the only safe "definitely not started" case.
            await db.execute(text("""UPDATE subscription_device_operations SET status='failed',
                error_code='operation_not_started',finished_at=clock_timestamp() WHERE id=:o"""), {"o": operation_id})
            return
        if (str(data.get("operation_id")) != str(operation_id) or str(data.get("device_id")) != str(device_id)
                or str(data.get("generation")) != str(operation["generation"])
                or data.get("settings_revision") != operation["settings_revision"]
                or data.get("action") != operation["action"] or operation["status"] not in {"running", "uncertain"}):
            return
        status = data.get("status")
        if operation["action"] == "pair" and status == "pairing" and re.fullmatch(r"[A-Z0-9-]{4,32}", str(data.get("code", ""))):
            status = "succeeded"
        if status not in {"succeeded", "failed", "uncertain"}:
            return
        error = data.get("error_code")
        error = error if isinstance(error, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", error) else None
        await _finish_control(db, row, transport, operation, status, error)


async def _dispatch_control(transport, row, operation, actor):
    payload = {"operation_id": str(operation["id"]), "device_id": str(row["id"]),
               "generation": str(row["generation"]), "settings_revision": row["revision"],
               **operation["request_payload"]}
    try:
        data = await channels._bridge_control(transport, "device/" + operation["action"], "POST", payload)
    except HTTPException:
        # A timeout does not prove that the provider did nothing. The ledger
        # remains reviewable, and repeating the user's key never repeats I/O.
        async with support_session_factory()() as db:
            async with db.begin():
                await db.execute(text("""UPDATE subscription_device_operations SET status='uncertain',error_code='transport_uncertain'
                    WHERE id=:o AND status IN ('pending','running')"""), {"o": operation["id"]})
        return None
    await _reconcile_control_result(row["tenant_id"], row["id"], operation["id"], actor, data)
    return data


@router.post(BASE + "/{device_id}/actions", status_code=202)
async def device_action(tenant_id: UUID, device_id: UUID, payload: DeviceAction, response: Response,
                        actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, transport = await _device(db, tenant_id, device_id, actor=actor, removed=True)
        existing = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE device_id=:d AND idempotency_key=:key"),
                                    {"d": device_id, "key": payload.idempotency_key})).mappings().one_or_none()
        if existing:
            if existing["action"] != payload.action:
                raise fail("DEVICE_CONFLICT")
            operation, dispatch = dict(existing), False
        else:
            if row["removed_at"] or row["revision"] != payload.expected_revision:
                raise fail("DEVICE_CONFLICT")
            operation, dispatch = await _reserve_control(db, row, payload.action, payload.idempotency_key)
    if dispatch:
        await _dispatch_control(transport, row, operation, actor)
    return await _operation_response(tenant_id, device_id, operation["id"], actor)


@router.get(BASE + "/{device_id}/operations/{operation_id}")
async def get_operation(tenant_id: UUID, device_id: UUID, operation_id: UUID, response: Response,
                        actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    # The bridge also persists its control ledger. Reconciliation is a read,
    # never a retry of connect/logout/remove after an uncertain network result.
    result = await _operation_response(tenant_id, device_id, operation_id, actor)
    if result["operation"]["status"] in LIVE_CONTROL:
        async with _owner_transaction(tenant_id, actor) as db:
            row, transport = await _device(db, tenant_id, device_id, actor=actor, removed=True)
        try:
            data = await channels._bridge_control(transport, "device/operations/" + str(operation_id), "GET")
            await _reconcile_control_result(tenant_id, device_id, operation_id, actor, data)
        except HTTPException:
            pass
        result = await _operation_response(tenant_id, device_id, operation_id, actor)
    return result


@router.get(BASE + "/{device_id}/qr")
async def get_qr(tenant_id: UUID, device_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, transport = await _device(db, tenant_id, device_id, actor=actor)
        if row["status"] in {"disabled", "removing"}:
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
    try:
        data = await channels._bridge_control(transport, "device/qr", "GET")
    except HTTPException:
        raise fail("DEVICE_BRIDGE_UNAVAILABLE", 503) from None
    async with _owner_transaction(tenant_id, actor) as db:
        current, _ = await _device(db, tenant_id, device_id, actor=actor, revision=row["revision"])
        if current["generation"] != row["generation"]:
            raise fail("DEVICE_CONFLICT")
    image = data.get("image")
    if image is not None and (not isinstance(image, str) or len(image) > 240000
            or not re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=]+", image)):
        raise fail("DEVICE_BRIDGE_UNAVAILABLE", 503)
    expiry = data.get("expires_in_ms", 0)
    return {"device_id": str(device_id), "revision": row["revision"], "image": image,
            "expires_in_ms": max(0, min(45000, expiry)) if type(expiry) is int else 0}


@router.post(BASE + "/{device_id}/pair")
async def pair_device(tenant_id: UUID, device_id: UUID, payload: DevicePair, response: Response,
                      actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, transport = await _device(db, tenant_id, device_id, actor=actor, revision=payload.expected_revision)
        operation, _ = await _reserve_control(db, row, "pair", uuid4(), {"phone": payload.phone})
    data = await _dispatch_control(transport, row, operation, actor)
    async with _owner_transaction(tenant_id, actor) as db:
        await _device(db, tenant_id, device_id, actor=actor, revision=row["revision"])
    code = data.get("code") if isinstance(data, dict) else None
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9-]{4,32}", code):
        raise fail("DEVICE_BRIDGE_UNAVAILABLE", 503)
    expiry = data.get("expires_in_ms", 0)
    return {"device_id": str(device_id), "revision": row["revision"], "status": "pairing", "code": code,
            "expires_in_ms": max(0, min(90000, expiry)) if type(expiry) is int else 0}


@router.get(BASE + "/{device_id}/diagnostics")
async def device_diagnostics(tenant_id: UUID, device_id: UUID, response: Response,
                             actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with _owner_transaction(tenant_id, actor) as db:
        row, _ = await _device(db, tenant_id, device_id, actor=actor)
        return jsonable_encoder({"device_id": device_id, "revision": row["revision"],
            "applied_revision": row["applied_revision"], "restart_required": row["restart_required"],
            "status": row["status"], "last_seen_at": row["runtime_at"],
            "capabilities": settings_capabilities()})


async def prepare_device_lease(db, channel_id, operation):
    if operation == "receipt":
        return False
    tenant = (await db.execute(text("SELECT tenant_id FROM subscription_devices WHERE channel_id=:c AND removed_at IS NULL"),
                              {"c": channel_id})).scalar_one_or_none()
    if tenant is None:
        return False
    await _workspace(db, tenant)
    # Niche changes acquire the exclusive plan lock. Take its shared counterpart
    # before membership/channel/device rows, exactly as public controls do.
    await channels._lock_membership(db, channel_id)
    await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": tenant})
    return True


async def device_lease_snapshot(db, transport):
    from app import subscription_chatbot_engine as chatbot
    row = (await db.execute(text("SELECT * FROM subscription_devices WHERE channel_id=:c AND removed_at IS NULL FOR UPDATE"),
                           {"c": transport["id"]})).mappings().one_or_none()
    if (not row or transport["assistant_enabled"] or transport["allowed_senders"]
            or is_alias(transport["tenant_id"], transport["id"]) or row["status"] == "removing"):
        raise fail("DEVICE_CONFLICT")
    secret = None
    if row["proxy_secret_encrypted"]:
        try:
            secret = {"password": channels._cipher().decrypt(row["proxy_secret_encrypted"].encode()).decode()}
        except (InvalidToken, UnicodeError):
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE") from None
    return jsonable_encoder({"id": row["id"], "generation": row["generation"], "settings_revision": row["revision"],
        "settings": row["settings"], "proxy_secret": secret, "restart_required": row["restart_required"],
        "chatbot": await chatbot.snapshot(db, row["tenant_id"]),
        "capabilities": {"transcription_available": audio_capable(), "transcription_max_bytes": 25000000,
            "transcription_max_seconds": 1800, "transcription_max_text_chars": 120000,
            "proxy": True, "webhook": True}})


@asynccontextmanager
async def _private_transaction(request, *, live=True, device_id=None, allow_removed=False):
    try:
        channel_id = UUID(str(request.path_params["channel_id"]))
        owner = UUID(request.headers["x-orion-channel-owner"]) if live else None
        fence = int(request.headers["x-orion-channel-fence"]) if live else None
        if live and not 1 <= fence <= 2**63-1:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Canal não autorizado") from None
    async with support_session_factory()() as db:
        async with db.begin():
            tenant = (await db.execute(text("SELECT tenant_id FROM qr_channels WHERE id=:c"), {"c": channel_id})).scalar_one_or_none()
            if tenant is None:
                raise HTTPException(401, "Canal não autorizado")
            await _workspace(db, tenant)
            await channels._lock_membership(db, channel_id)
            await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": tenant})
            transport = await channels._channel(db, channel_id, request.headers.get("x-orion-channel-key"), receipt_only=not live)
            if live:
                channels._current(transport, owner, fence)
            await channels._require_canonical(db, tenant, channel_id)
            row = (await db.execute(text("""SELECT * FROM subscription_devices WHERE tenant_id=:t AND channel_id=:c
                AND (CAST(:d AS uuid) IS NULL OR id=CAST(:d AS uuid)) AND (:removed OR removed_at IS NULL) FOR UPDATE"""),
                {"t": tenant, "c": channel_id, "d": device_id,
                 "removed": allow_removed})).mappings().one_or_none()
            if (not row or transport["assistant_enabled"] or transport["allowed_senders"]
                    or is_alias(tenant, channel_id) or row["owner_account_id"] != transport["owner_account_id"]):
                raise fail("DEVICE_CONFLICT")
            if live and row["status"] in {"disabled", "removing", "removed"}:
                raise fail("DEVICE_CONFLICT")
            yield db, dict(row), dict(transport), owner, fence


def _same_snapshot(row, device_id, generation, revision):
    if row["id"] != device_id or row["generation"] != generation or row["revision"] != revision or row["removed_at"]:
        raise fail("DEVICE_CONFLICT")


@router.post(INTERNAL + "/control/authorize")
async def authorize_control(channel_id: UUID, payload: ControlRequest, request: Request):
    async with _private_transaction(request, live=False, device_id=payload.device_id) as (db, row, transport, _, _):
        _same_snapshot(row, payload.device_id, payload.generation, payload.settings_revision)
        operation = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE tenant_id=:t AND device_id=:d AND id=:o FOR UPDATE"),
                                     {"t": row["tenant_id"], "d": row["id"], "o": payload.operation_id})).mappings().one_or_none()
        if not operation or operation["generation"] != row["generation"] or operation["settings_revision"] != row["revision"]:
            raise fail("DEVICE_CONFLICT")
        if operation["status"] not in {"pending", "running"}:
            return {"authorized": False, "operation_id": str(operation["id"]), "status": operation["status"]}
        first = operation["status"] == "pending"
        if first:
            await db.execute(text("UPDATE subscription_device_operations SET status='running',started_at=clock_timestamp() WHERE id=:o"),
                             {"o": operation["id"]})
        if first and operation["action"] == "remove":
            await db.execute(text("UPDATE subscription_devices SET status='removing' WHERE id=:d"), {"d": row["id"]})
            await db.execute(text("UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),error_code='device_removing' WHERE device_id=:d AND status IN ('pending','failed')"), {"d": row["id"]})
        elif first and operation["action"] == "connect":
            await db.execute(text("UPDATE qr_channels SET enabled=true,revision=revision+1,updated_at=clock_timestamp() WHERE id=:c"),
                             {"c": transport["id"]})
            await db.execute(text("UPDATE subscription_devices SET status='connecting' WHERE id=:d"), {"d": row["id"]})
        return jsonable_encoder({"authorized": True, "operation_id": operation["id"], "action": operation["action"],
            "tenant_id": row["tenant_id"], "channel_id": transport["id"], "device_id": row["id"],
            "generation": row["generation"], "settings_revision": row["revision"],
            "payload": operation["request_payload"], "device": await device_lease_snapshot(db, transport)
                if operation["action"] != "remove" else None})


@router.post(INTERNAL + "/control/finish")
async def finish_control(channel_id: UUID, payload: ControlFinish, request: Request):
    async with _private_transaction(request, live=False, device_id=payload.device_id, allow_removed=True) as (db, row, transport, _, _):
        operation = (await db.execute(text("SELECT * FROM subscription_device_operations WHERE tenant_id=:t AND device_id=:d AND id=:o FOR UPDATE"),
                                     {"t": row["tenant_id"], "d": row["id"], "o": payload.operation_id})).mappings().one_or_none()
        if (not operation or operation["generation"] != payload.generation
                or operation["settings_revision"] != payload.settings_revision):
            raise fail("DEVICE_CONFLICT")
        if operation["status"] in TERMINAL:
            return {"status": operation["status"], "operation_id": str(operation["id"])}
        if operation["status"] not in {"running", "uncertain"}:
            raise fail("DEVICE_CONFLICT")
        await _finish_control(db, row, transport, operation, payload.status, payload.error_code)
        return {"status": payload.status, "operation_id": str(operation["id"])}


async def _finish_control(db, row, transport, operation, status, error):
    await db.execute(text("""UPDATE subscription_device_operations SET status=CAST(:status AS varchar),error_code=:error,
        finished_at=CASE WHEN CAST(:status AS varchar)='uncertain' THEN NULL ELSE clock_timestamp() END WHERE id=:o"""),
        {"o": operation["id"], "status": status, "error": error})
    if status == "succeeded" and operation["action"] == "remove":
        await db.execute(text("""UPDATE qr_channels SET enabled=false,assistant_enabled=false,revision=revision+1,
            fence=fence+1,lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=:c"""), {"c": transport["id"]})
        # Preserve a tombstone and delivery evidence, never transfer historical
        # generation references to the next logical device or auth namespace.
        await db.execute(text("""UPDATE subscription_devices SET status='removed',removed_at=clock_timestamp(),
            settings=CAST(:settings AS jsonb),proxy_secret_encrypted=NULL,runtime='{}'::jsonb,
            revision=revision+1,updated_at=clock_timestamp(),restart_required=false WHERE id=:d"""),
            {"d": row["id"], "settings": _json(DeviceSettings().model_dump(mode="json"))})
    elif status == "succeeded" and operation["action"] in {"connect", "restart", "relink"}:
        await db.execute(text("""UPDATE subscription_devices SET status='connecting',restart_required=false,
            applied_revision=revision,updated_at=clock_timestamp() WHERE id=:d AND removed_at IS NULL"""), {"d": row["id"]})
    elif status == "failed":
        await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE id=:d AND removed_at IS NULL"), {"d": row["id"]})


async def _insert_effect(db, row, event_id, key, effect, *, previous=None):
    effect_id = (await db.execute(text("""INSERT INTO subscription_device_effects
        (tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,previous_effect)
        VALUES(:t,:d,:g,:r,:event,:key,:type,:purpose,CAST(:payload AS jsonb),:previous)
        ON CONFLICT(device_id,event_id,effect_key) DO NOTHING RETURNING id"""),
        {"t": row["tenant_id"], "d": row["id"], "g": row["generation"], "r": row["revision"],
         "event": event_id, "key": key, "type": effect["type"], "purpose": effect["purpose"],
         "payload": _json(effect["payload"]), "previous": previous})).scalar_one_or_none()
    if effect_id is None:
        effect_id = (await db.execute(text("SELECT id FROM subscription_device_effects WHERE device_id=:d AND event_id=:event AND effect_key=:key"),
                                     {"d": row["id"], "event": event_id, "key": key})).scalar_one()
    return effect_id


async def _event_effects(db, device_id, event_id):
    from app.subscription_chatbot_engine import available_effects
    return await available_effects(db, device_id, event_id)


@router.post(INTERNAL + "/event")
async def device_event(channel_id: UUID, payload: DeviceEvent, request: Request):
    async with _private_transaction(request) as (db, row, transport, _, fence):
        _same_snapshot(row, payload.device_id, payload.generation, payload.settings_revision)
        now = datetime.now(timezone.utc)
        if not now-timedelta(minutes=5) <= payload.occurred_at <= now+timedelta(seconds=30):
            return {"effects": [], "duplicate": False, "ignored": "stale_event"}
        data = payload.model_dump(mode="json")
        event_id = (await db.execute(text("""INSERT INTO subscription_device_events
            (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
            VALUES(:t,:d,:g,:provider,:kind,:at,CAST(:payload AS jsonb))
            ON CONFLICT DO NOTHING RETURNING id"""),
            {"t": row["tenant_id"], "d": row["id"], "g": row["generation"], "provider": payload.event_id,
             "kind": payload.kind, "at": payload.occurred_at, "payload": _json(data)})).scalar_one_or_none()
        if event_id is None:
            return {"effects": [], "duplicate": True}
        if payload.kind == "status":
            status_data = payload.state if isinstance(payload.state, dict) else {
                "status": payload.state, "phone": payload.phone, "phone_verified": bool(payload.phone),
                "profile": {"name": payload.name, "avatar_url": payload.avatar_url},
                "settings_revision": payload.settings_revision,
                "last_seen_at": payload.occurred_at.isoformat(),
                **({"connected_at": payload.occurred_at.isoformat()} if payload.state == "connected" else {})}
            await _store_runtime(db, row, status_data)
        rejected_call = None
        device_effects = effects_for_event(row["settings"], data, now=now, audio_available=audio_capable()) if payload.origin == "contact" else []
        for index, effect in enumerate(device_effects):
            effect_id = await _insert_effect(db, row, event_id, str(index), effect,
                previous=rejected_call if effect["purpose"] == "call_reply" else None)
            if effect["type"] == "reject_call":
                rejected_call = effect_id
        if row["settings"]["webhook"]["enabled"] and payload.origin == "contact" and payload.kind in {"text", "audio", "call", "status"}:
            body = {"id": str(event_id), "device_id": str(row["id"]), "type": payload.kind,
                    "occurred_at": payload.occurred_at.isoformat(),
                    "data": {key: data[key] for key in ("phone", "name", "text", "message_id", "audio", "call_id", "call_from") if data.get(key) is not None}}
            if payload.kind == "status":
                body["data"] = _safe_runtime(status_data)
            await _insert_effect(db, row, event_id, "webhook", {"type": "webhook", "purpose": "webhook",
                "payload": {"url": row["settings"]["webhook"]["url"], "event_id": str(event_id), "body": body}})
        from app.subscription_chatbot_engine import record_event
        await record_event(db, row, transport, event_id, data, fence)
        return jsonable_encoder({"effects": await _event_effects(db, row["id"], event_id), "duplicate": False})


@router.get(INTERNAL + "/effects/pending")
async def pending_effects(channel_id: UUID, request: Request):
    from app import subscription_chatbot_engine as chatbot
    async with _private_transaction(request) as (db, row, _, _, _):
        # An interrupted non-idempotent send is never retried automatically.
        # Webhooks carry a stable event id and may be retried up to three times.
        interrupted = (await db.execute(text("""UPDATE subscription_device_effects SET
            status=CASE WHEN type='webhook' AND attempts<3 THEN 'failed' ELSE 'uncertain' END,
            due_at=clock_timestamp(),error_code='interrupted_attempt'
            WHERE device_id=:d AND status='running' AND authorized_at<clock_timestamp()-interval '180 seconds'
            RETURNING *"""), {"d": row["id"]})).mappings().all()
        for effect in interrupted:
            await chatbot.after_effect_finish(db, row, effect, effect["status"])
        await chatbot.reap_unreachable(db, row)
        effects = (await db.execute(text("""SELECT e.* FROM subscription_device_effects e
            LEFT JOIN subscription_device_effects p ON p.tenant_id=e.tenant_id AND p.id=e.previous_effect
            LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.id=e.chatbot_run_id
            LEFT JOIN subscription_device_effects a ON a.tenant_id=e.tenant_id
                AND a.id=CAST(r.input_context->>'transcription_predecessor' AS uuid)
            WHERE e.device_id=:d AND e.generation=:g AND e.settings_revision=:r AND e.due_at<=clock_timestamp()
              AND e.type<>'transcribe' AND e.expires_at>clock_timestamp()
              AND (e.status='pending' OR e.type='webhook' AND e.status='failed' AND e.attempts<3)
              AND (e.type<>'chatbot_action' OR e.previous_effect IS NULL OR p.status='accepted')
              AND (e.type<>'chatbot_action' OR r.input_context->>'transcription_predecessor' IS NULL OR a.status='accepted')
            ORDER BY e.created_at,e.id LIMIT 25"""),
            {"d": row["id"], "g": row["generation"], "r": row["revision"]})).mappings().all()
        reconcile = (await db.execute(text("""SELECT id AS operation_id,type,device_id,generation,settings_revision,chatbot_revision
            FROM subscription_device_effects WHERE device_id=:d AND generation=:g
              AND type<>'webhook' AND status='uncertain'
            ORDER BY created_at,id LIMIT 25"""), {"d": row["id"], "g": row["generation"]})).mappings().all()
        return jsonable_encoder({"effects": [chatbot.descriptor(effect) for effect in effects],
                                 "reconcile": [{key: value for key, value in effect.items()
                                                if key != "chatbot_revision" or value is not None} for effect in reconcile]})


async def _effect(db, row, operation_id):
    effect = (await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND device_id=:d AND id=:o FOR UPDATE"),
                              {"t": row["tenant_id"], "d": row["id"], "o": operation_id})).mappings().one_or_none()
    if not effect:
        raise fail("DEVICE_NOT_FOUND", 404)
    return dict(effect)


def _current_effect(row, effect):
    return (row["generation"] == effect["generation"] and row["revision"] == effect["settings_revision"]
            and row["removed_at"] is None and effect["expires_at"] > datetime.now(timezone.utc))


async def _policy_still_allows(db, row, effect):
    if effect["type"] == "chatbot_action":
        from app.subscription_chatbot_engine import policy_allows
        return await policy_allows(db, row, effect)
    event = (await db.execute(text("SELECT payload FROM subscription_device_events WHERE tenant_id=:t AND id=:e"),
                             {"t": row["tenant_id"], "e": effect["event_id"]})).scalar_one()
    if effect["type"] == "webhook":
        return row["settings"]["webhook"]["enabled"] and row["settings"]["webhook"]["url"] == effect["payload"]["url"]
    if effect["purpose"] == "transcription_reply":
        return row["settings"]["transcription"]["enabled"]
    candidates = effects_for_event(row["settings"], event, audio_available=audio_capable())
    return any(item["purpose"] == effect["purpose"] and item["payload"] == effect["payload"] for item in candidates)


async def _reserve_cooldown(db, row, effect):
    if effect["purpose"] not in {"absence", "call_reply"}:
        return True
    phone = effect["payload"]["phone"]
    if phone == (row["settings"].get("test_phone_e164") or "").lstrip("+"):
        return True  # Only the 24h notice suppression is bypassed for testing.
    from app.subscription_sent_messages import transfer_retry_cooldown
    if await transfer_retry_cooldown(db, row, effect):
        return True
    reserved = (await db.execute(text("""INSERT INTO subscription_device_cooldowns(tenant_id,device_id,phone,effect_id)
        VALUES(:t,:d,:phone,:effect) ON CONFLICT(tenant_id,device_id,phone) DO UPDATE
          SET reserved_at=clock_timestamp(),effect_id=EXCLUDED.effect_id
          WHERE subscription_device_cooldowns.reserved_at<=clock_timestamp()-interval '24 hours'
        RETURNING effect_id"""),
        {"t": row["tenant_id"], "d": row["id"], "phone": phone, "effect": effect["id"]})).scalar_one_or_none()
    return reserved is not None


@router.post(INTERNAL + "/effects/authorize")
async def authorize_effect(channel_id: UUID, payload: EffectRequest, request: Request):
    async with _private_transaction(request) as (db, row, _, owner, fence):
        if payload.device_id is not None:
            _same_snapshot(row, payload.device_id, payload.generation, payload.settings_revision)
        effect = await _effect(db, row, payload.operation_id)
        if effect["type"] == "chatbot_action":
            from app.subscription_chatbot_engine import authorize_effect as authorize_chatbot
            return await authorize_chatbot(db, row, effect, owner, fence, payload)
        if effect["purpose"] == "diagnostic_test":
            from app.subscription_diagnostics import authorize_diagnostic_effect
            return await authorize_diagnostic_effect(db, row, effect, owner, fence, payload)
        denied = {"authorized": False, "operation_id": str(effect["id"])}
        eligible = effect["status"] == "pending" or (effect["type"] == "webhook" and effect["status"] == "failed" and effect["attempts"] < 3)
        if not eligible or effect["due_at"] > datetime.now(timezone.utc):
            return denied
        from app.subscription_sent_messages import message_authority
        if not await message_authority(db, row, effect):
            return denied
        if not _current_effect(row, effect) or not await _policy_still_allows(db, row, effect):
            await db.execute(text("UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),error_code='policy_changed' WHERE id=:o"), {"o": effect["id"]})
            return denied
        if effect["previous_effect"]:
            previous = (await db.execute(text("SELECT status FROM subscription_device_effects WHERE id=:o AND tenant_id=:t"),
                                        {"o": effect["previous_effect"], "t": row["tenant_id"]})).scalar_one_or_none()
            if previous != "accepted":
                return denied
        if not await _reserve_cooldown(db, row, effect):
            await db.execute(text("UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),error_code='notice_cooldown' WHERE id=:o"), {"o": effect["id"]})
            return denied
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=attempts+1,
            authorized_at=clock_timestamp(),claimed_owner=:owner,claimed_fence=:fence WHERE id=:o"""),
            {"o": effect["id"], "owner": owner, "fence": fence})
        return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": effect["type"],
            "device_id": row["id"], "generation": row["generation"], "settings_revision": row["revision"],
            "payload": effect["payload"]})


@router.post(INTERNAL + "/effects/finish")
async def finish_effect(channel_id: UUID, payload: EffectFinish, request: Request):
    from app.subscription_chatbot_engine import after_effect_finish
    async with _private_transaction(request) as (db, row, _, owner, fence):
        effect = await _effect(db, row, payload.operation_id)
        if payload.device_id is not None and (payload.device_id != row["id"]
                or payload.generation != effect["generation"] or payload.settings_revision != effect["settings_revision"]):
            raise fail("DEVICE_CONFLICT")
        if effect["type"] == "chatbot_action" and payload.chatbot_revision != effect["chatbot_revision"]:
            raise fail("DEVICE_CONFLICT")
        if effect["status"] in {"accepted", "not_sent"}:
            return {"operation_id": str(effect["id"]), "state": effect["status"]}
        receipt = (payload.reconcile and effect["type"] != "webhook" and effect["status"] in {"running", "uncertain"}
                   and effect["attempts"] > 0 and effect["generation"] == row["generation"]
                   and payload.state in {"accepted", "not_sent", "uncertain"})
        if not receipt and (effect["status"] != "running" or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence):
            raise fail("DEVICE_CONFLICT")
        if effect["type"] == "transcribe" and payload.state == "accepted":
            raise fail("DEVICE_CONFLICT")  # Only the authenticated audio handler can complete ASR.
        if effect["type"] == "chatbot_action" and effect["payload"]["action"]["type"] == "http" and payload.state == "accepted":
            raise fail("DEVICE_CONFLICT")  # Its result must pass the canonical result/child-effect handler.
        retry = effect["type"] == "webhook" and payload.state in {"failed", "uncertain"} and effect["attempts"] < 3
        state = "failed" if retry else payload.state
        await db.execute(text("""UPDATE subscription_device_effects SET status=:status,provider_id=:provider,error_code=:error,
            due_at=CASE WHEN :retry THEN clock_timestamp()+(:delay * interval '1 second') ELSE due_at END,
            finished_at=CASE WHEN :retry THEN NULL ELSE clock_timestamp() END WHERE id=:o"""),
            {"o": effect["id"], "status": state, "provider": payload.provider_id, "error": payload.error_code,
             "retry": retry, "delay": 5 * effect["attempts"]})
        if effect["type"] == "webhook":
            if payload.state == "accepted":
                await db.execute(text("UPDATE subscription_devices SET webhook_failures=0 WHERE id=:d"), {"d": row["id"]})
            elif not retry:
                await db.execute(text("UPDATE subscription_devices SET webhook_failures=webhook_failures+1 WHERE id=:d"), {"d": row["id"]})
                if row["settings"]["webhook"]["disable_on_failure"] and _current_effect(row, effect):
                    await db.execute(text("""UPDATE subscription_devices SET
                        settings=jsonb_set(settings,'{webhook,enabled}','false'::jsonb),revision=revision+1,
                        updated_at=clock_timestamp() WHERE id=:d"""), {"d": row["id"]})
        children = await after_effect_finish(db, row, effect, state)
        return jsonable_encoder({"operation_id": effect["id"], "state": state, **({"effects": children} if children else {})})


async def _audio_authorize(request, device_id, generation, revision, operation_id, seconds, *, phase):
    async with _private_transaction(request, device_id=device_id) as (db, row, _, owner, fence):
        _same_snapshot(row, device_id, generation, revision)
        effect = await _effect(db, row, operation_id)
        if (effect["type"] != "transcribe" or effect["status"] != "running" or not _current_effect(row, effect)
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence
                or not await _policy_still_allows(db, row, effect)):
            raise fail("DEVICE_CONFLICT")
        if phase == "after_provider":
            if effect["audio_reserved_seconds"] <= 0:
                raise fail("DEVICE_CONFLICT")
            return
        if phase != "before_provider" or effect["audio_reserved_seconds"] or not audio_capable():
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
        if not math.isfinite(seconds) or not 0 < seconds <= row["settings"]["transcription"]["max_minutes"] * 60:
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
        # Provider minimum is ten seconds per request. This is a technical
        # reservation, never a customer invoice, credit or subscription grant.
        amount = max(10, math.ceil(seconds))
        busy = (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND type='transcribe' AND status='running'
              AND audio_reserved_seconds>0 AND authorized_at>clock_timestamp()-interval '180 seconds'
            LIMIT 1"""), {"t": row["tenant_id"]})).scalar_one_or_none()
        if busy:
            raise HTTPException(429, {"code": "DEVICE_AUDIO_BUSY", "message": "Há uma transcrição em andamento. Aguarde a conclusão."})
        # One global advisory lock makes the sum and tenant reservation atomic.
        # Only this module uses it, and no provider I/O occurs while held.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('subscription-device-asr-quota-v1',0))"))
        day = datetime.now(timezone.utc).date()
        used = (await db.execute(text("""SELECT COALESCE(sum(reserved_seconds),0) AS total,
            COALESCE(sum(reserved_seconds) FILTER(WHERE tenant_id=:t),0) AS tenant
            FROM subscription_device_audio_usage WHERE usage_day=:day"""), {"t": row["tenant_id"], "day": day})).mappings().one()
        if (used["tenant"] + amount > _budget("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS")
                or used["total"] + amount > _budget("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS")):
            raise HTTPException(429, {"code": "DEVICE_AUDIO_QUOTA", "message": "O limite diário de transcrição foi atingido."})
        await db.execute(text("""INSERT INTO subscription_device_audio_usage(tenant_id,usage_day,reserved_seconds,attempts)
            VALUES(:t,:day,:amount,1) ON CONFLICT(tenant_id,usage_day) DO UPDATE SET
              reserved_seconds=subscription_device_audio_usage.reserved_seconds+EXCLUDED.reserved_seconds,
              attempts=subscription_device_audio_usage.attempts+1"""), {"t": row["tenant_id"], "day": day, "amount": amount})
        await db.execute(text("UPDATE subscription_device_effects SET audio_reserved_seconds=:amount WHERE id=:o"),
                         {"amount": amount, "o": operation_id})


@router.post(INTERNAL + "/audio")
async def device_audio(channel_id: UUID, request: Request):
    try:
        device_id = UUID(request.headers["x-orion-device-id"])
        generation = UUID(request.headers["x-orion-device-generation"])
        revision = int(request.headers["x-orion-device-revision"])
        operation_id = UUID(request.headers["x-orion-device-effect"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Áudio não autorizado") from None
    async with _private_transaction(request, device_id=device_id) as (db, row, _, owner, fence):
        _same_snapshot(row, device_id, generation, revision)
        effect = await _effect(db, row, operation_id)
        if (effect["type"] != "transcribe" or effect["status"] != "running" or effect["audio_reserved_seconds"]
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence
                or not _current_effect(row, effect) or not await _policy_still_allows(db, row, effect)):
            raise fail("DEVICE_CONFLICT")
        minutes = row["settings"]["transcription"]["max_minutes"]
    from app.subscription_device_audio import transcribe_audio
    async def authorize(seconds, *, phase):
        await _audio_authorize(request, device_id, generation, revision, operation_id, seconds, phase=phase)
    result = await transcribe_audio(request, max_minutes=minutes, authorize=authorize)
    async with _private_transaction(request, device_id=device_id) as (db, row, _, owner, fence):
        _same_snapshot(row, device_id, generation, revision)
        effect = await _effect(db, row, operation_id)
        if (effect["status"] != "running" or not _current_effect(row, effect)
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence
                or not await _policy_still_allows(db, row, effect)):
            raise fail("DEVICE_CONFLICT")
        event = (await db.execute(text("SELECT payload FROM subscription_device_events WHERE tenant_id=:t AND id=:e"),
                                 {"t": row["tenant_id"], "e": effect["event_id"]})).scalar_one()
        previous = None
        for index, part in enumerate(transcription_parts(row["settings"], event, result["text"])):
            previous = await _insert_effect(db, row, effect["event_id"], f"transcript:{index}",
                {"type": "send_text", "purpose": "transcription_reply", "payload": {"phone": event["phone"], "text": part}},
                previous=previous)
        duration = result.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= minutes * 60:
            raise fail("DEVICE_CAPABILITY_UNAVAILABLE")
        await db.execute(text("""UPDATE subscription_device_effects SET status='accepted',finished_at=clock_timestamp(),
            audio_duration_seconds=:duration,audio_completed_at=clock_timestamp() WHERE id=:o"""),
            {"o": operation_id, "duration": duration})
        # Matching uses only the authenticated ASR result. The formatted
        # transcription keeps its own independent enable switch and ledger.
        from app.subscription_chatbot_engine import complete_transcript
        await complete_transcript(db, row, effect["event_id"], result["text"], predecessor=previous)
        return jsonable_encoder({**result, "truncated": False, "effects": await _event_effects(db, row["id"], effect["event_id"])})
