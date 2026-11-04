"""Shared owner/workspace guards and immutable media registry for the chatbot."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from functools import partial
from threading import BoundedSemaphore
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.catalog_api import support_session_factory
from app import qr_channels as channels
from app.plan_permissions import plan_state
from app.workspace_policy import SUBSCRIPTION_WORKSPACE
from app.subscription_chatbot_schemas import MAX_RULES, MAX_ASSETS, CONTACT_TAGS
from app.subscription_chatbot_rules import summary


MAX_GRAPH_BYTES = 10 * 1024 * 1024
MAX_TENANT_MEDIA_BYTES = 256 * 1024 * 1024
_media_cleanup = set()
_media_writers = BoundedSemaphore(2)
ERRORS = {
    "CHATBOT_CONFLICT": "O Chatbot mudou. Atualize antes de continuar.",
    "CHATBOT_NOT_FOUND": "Resposta ou recurso não encontrado.",
    "CHATBOT_INVALID_GRAPH": "Revise as referências e os limites das respostas.",
    "CHATBOT_CAPABILITY_UNAVAILABLE": "Este recurso está indisponível no momento.",
    "CHATBOT_PREVIEW_EXPIRED": "A prévia expirou. Gere uma nova antes de aplicar.",
    "CHATBOT_PREVIEW_STALE": "As respostas mudaram. Gere uma nova prévia.",
    "CHATBOT_IMPORT_INVALID": "O arquivo não é uma exportação válida do Chatbot Orion.",
    "CHATBOT_RESTORE_CONFIRMATION": "Digite RESTORE para confirmar o snapshot exato.",
    "CHATBOT_BUSY": "Há uma restauração em andamento. Aguarde a conclusão.",
    "CHATBOT_EFFECT_REVOKED": "Esta resposta não está mais autorizada.",
    "CHATBOT_MEDIA_QUOTA": "O limite de arquivos do Chatbot foi atingido.",
}


def fail(code, status=409, message=None):
    return HTTPException(status, {"code": code, "message": message or ERRORS.get(code, "Operação indisponível."),
                                  **({"refresh": True} if code.endswith(("CONFLICT", "STALE")) else {})})


def encode(value):
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(json.dumps(jsonable_encoder(value), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def workspace(db, tenant, *, write=True):
    state = await plan_state(db, tenant, lock="shared")
    if (state.get("workspace_profile") != SUBSCRIPTION_WORKSPACE
            or write and not state.get("permissions", {}).get("chatbot.manage")):
        raise fail("WORKSPACE_FEATURE_UNAVAILABLE", 403,
                   "O Chatbot está disponível somente para Serviço de assinaturas.")
    return state


@asynccontextmanager
async def owner_transaction(tenant, actor, *, write=True):
    async with support_session_factory()() as db:
        async with db.begin():
            await workspace(db, tenant, write=write)
            await channels._owner(db, tenant, actor)
            status = (await db.execute(text("SELECT status FROM tenants WHERE id=:t FOR UPDATE"),
                                       {"t": tenant})).scalar_one_or_none()
            if status is None or write and status != "active":
                raise fail("CHATBOT_CAPABILITY_UNAVAILABLE", 403)
            yield db


async def revision(db, tenant, *, create=False):
    if create:
        await db.execute(text("INSERT INTO subscription_chatbot_state(tenant_id) VALUES(:t) ON CONFLICT DO NOTHING"), {"t": tenant})
    return (await db.execute(text("SELECT revision FROM subscription_chatbot_state WHERE tenant_id=:t"),
                             {"t": tenant})).scalar_one_or_none() or 1


async def check_revision(db, tenant, expected):
    current = await revision(db, tenant)
    if current != expected:
        raise fail("CHATBOT_CONFLICT")
    return current


async def pending_restore(db, tenant):
    return (await db.execute(text("""SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t
        AND kind='restore' AND status IN ('queued','running') ORDER BY created_at,id LIMIT 1"""),
        {"t": tenant})).mappings().one_or_none()


async def writable(db, tenant):
    if await pending_restore(db, tenant):
        raise fail("CHATBOT_BUSY")


async def bump_revision(db, tenant):
    await revision(db, tenant, create=True)
    current = (await db.execute(text("""UPDATE subscription_chatbot_state SET revision=revision+1,
        updated_at=clock_timestamp() WHERE tenant_id=:t RETURNING revision"""), {"t": tenant})).scalar_one()
    # Only the chatbot epoch changes. ASR, calls, absence and webhooks retain
    # their own device revision and operation evidence.
    await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
        error_code='chatbot_changed',finished_at=clock_timestamp()
        WHERE tenant_id=:t AND type='chatbot_action' AND status IN ('pending','failed')"""), {"t": tenant})
    await db.execute(text("""UPDATE subscription_chatbot_runs SET status='ignored',error_code='chatbot_changed',
        updated_at=clock_timestamp(),finished_at=clock_timestamp()
        WHERE tenant_id=:t AND status IN ('ready','waiting_order','waiting_audio')"""), {"t": tenant})
    await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',error_code='CHATBOT_EFFECT_REVOKED',
        finished_at=clock_timestamp() WHERE tenant_id=:t AND kind='test_response'
          AND status IN ('queued','running') AND expected_revision<:revision"""), {"t": tenant, "revision": current})
    return current


async def next_order(db, tenant):
    await revision(db, tenant, create=True)
    return (await db.execute(text("""UPDATE subscription_chatbot_state SET next_creation_order=next_creation_order+1
        WHERE tenant_id=:t RETURNING next_creation_order-1"""), {"t": tenant})).scalar_one()


async def check_device(db, tenant, device_id, *, actor_id=None, live=False):
    if device_id is None:
        return None
    row = (await db.execute(text("""SELECT * FROM subscription_devices WHERE tenant_id=:t AND id=:d
        AND removed_at IS NULL FOR UPDATE"""), {"t": tenant, "d": device_id})).mappings().one_or_none()
    if not row or actor_id is not None and row["owner_account_id"] != actor_id:
        raise fail("CHATBOT_NOT_FOUND", 404)
    transport = await channels._require_canonical(db, tenant, row["channel_id"])
    if (transport["owner_account_id"] != row["owner_account_id"] or transport["assistant_enabled"]
            or transport["allowed_senders"] or row["status"] in {"removing", "removed"}):
        raise fail("CHATBOT_CONFLICT")
    if live and (not transport["enabled"] or not transport["lease_expires_at"]
                 or transport["lease_expires_at"] <= datetime.now(timezone.utc)):
        raise fail("CHATBOT_CAPABILITY_UNAVAILABLE", 409, "Conecte o dispositivo antes de realizar essa ação.")
    return dict(row), dict(transport)


def asset_descriptor(row):
    names = {"image": "imagem.jpg", "sticker": "figurinha.webp", "video": "video.mp4",
             "ptv": "recado.mp4", "document": "resposta.txt"}
    thumb = ({"mime": row["thumbnail_mime"], "size": row["thumbnail_size"], "sha256": row["thumbnail_sha256"]}
             if row.get("thumbnail_size") else None)
    return {key: row[key] for key in ("id", "kind", "mime", "size", "sha256", "width", "height", "duration_ms")} | {
        "file_name": names[row["kind"]], "thumbnail": thumb}


async def load_graph(db, tenant):
    budget = (await db.execute(text("""SELECT count(*) AS count,
        COALESCE(sum(octet_length(response::text)+octet_length(keyword)+512),0) AS bytes
        FROM subscription_chatbot_rules WHERE tenant_id=:t AND removed_at IS NULL"""), {"t": tenant})).mappings().one()
    templates_budget = (await db.execute(text("""SELECT count(*) AS count,
        COALESCE(sum(octet_length(body)+octet_length(name)+512),0) AS bytes
        FROM subscription_chatbot_templates WHERE tenant_id=:t AND removed_at IS NULL"""), {"t": tenant})).mappings().one()
    if (budget["count"] > MAX_RULES or templates_budget["count"] > MAX_RULES
            or budget["bytes"] + templates_budget["bytes"] > MAX_GRAPH_BYTES):
        raise fail("CHATBOT_INVALID_GRAPH", 413, "O conjunto de respostas ultrapassa o limite de tamanho.")
    rules = (await db.execute(text("SELECT * FROM subscription_chatbot_rules WHERE tenant_id=:t AND removed_at IS NULL"),
                              {"t": tenant})).mappings().all()
    templates = (await db.execute(text("SELECT * FROM subscription_chatbot_templates WHERE tenant_id=:t AND removed_at IS NULL"),
                                  {"t": tenant})).mappings().all()
    assets = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND status='ready'"),
                               {"t": tenant})).mappings().all()
    assets = {str(row["id"]): dict(row) for row in assets}
    templates = {str(row["id"]): dict(row) | {"asset": asset_descriptor(assets[str(row["asset_id"])])
        if row["asset_id"] and str(row["asset_id"]) in assets else None} for row in templates}
    return {str(row["id"]): dict(row) for row in rules}, templates, assets


def check_graph_budget(rules, templates=None):
    templates = templates or {}
    if (len(rules) > MAX_RULES or len(templates) > MAX_RULES
            or len(encode([*rules.values(), *templates.values()]).encode()) > MAX_GRAPH_BYTES):
        raise fail("CHATBOT_INVALID_GRAPH", 422, "A seleção ultrapassa o limite de respostas ou de tamanho do Chatbot.")


def rule_dto(row):
    return {key: row[key] for key in ("id", "creation_order", "revision", "device_id", "enabled", "match_mode",
                                    "keyword", "response", "created_at", "updated_at")}


def rule_summary(row, device_names=None):
    return {key: row[key] for key in ("id", "creation_order", "revision", "device_id", "enabled", "match_mode",
                                    "keyword", "created_at", "updated_at")} | summary(row) | {
        "device_name": "Todos os Dispositivos" if row["device_id"] is None
            else (device_names or {}).get(str(row["device_id"]), "Dispositivo indisponível")}


def job_dto(row):
    return {key: row.get(key) for key in ("id", "kind", "status", "counts", "safety_backup_id", "result", "created_at", "finished_at")} | {
        "source": (row.get("payload") or {}).get("source") if row["kind"] == "restore" else None,
        "error": {"code": row["error_code"], "message": ERRORS.get(row["error_code"], "A operação não foi concluída. Confira os detalhes antes de tentar novamente.")}
                 if row.get("error_code") else None}


async def request_job(db, tenant, actor, kind, key, payload, expected, *, preview=None):
    digest = fingerprint(payload)
    old = (await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND idempotency_key=:key FOR UPDATE"),
                            {"t": tenant, "key": key})).mappings().one_or_none()
    if old:
        if old["kind"] != kind or old["actor_account_id"] != actor.account_id or old["request_fingerprint"] != digest:
            raise fail("CHATBOT_CONFLICT")
        return dict(old), True
    await check_revision(db, tenant, expected)
    row = (await db.execute(text("""INSERT INTO subscription_chatbot_jobs
        (tenant_id,actor_account_id,kind,idempotency_key,request_fingerprint,expected_revision,preview_id,payload)
        VALUES(:t,:a,:kind,:key,:fp,:revision,:preview,CAST(:payload AS jsonb)) RETURNING *"""),
        {"t": tenant, "a": actor.account_id, "kind": kind, "key": key, "fp": digest, "revision": expected,
         "preview": preview, "payload": encode(payload)})).mappings().one()
    return dict(row), False


async def complete_job(db, job_id, result, *, counts=None):
    return (await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='succeeded',
        result=CAST(:result AS jsonb),counts=COALESCE(CAST(:counts AS jsonb),counts),finished_at=clock_timestamp()
        WHERE id=:id RETURNING *"""), {"id": job_id, "result": encode(result),
                                      "counts": encode(counts) if counts is not None else None})).mappings().one()


def _track_media(task):
    _media_cleanup.add(task)
    def completed(item):
        _media_cleanup.discard(item)
        if not item.cancelled():
            item.exception()  # A cancelled caller cannot consume its result.
    task.add_done_callback(completed)
    return task


@asynccontextmanager
async def media_mutex(tenant, kind, digest):
    """Session lock survives short transaction commits and owns crash recovery.

    Closing the actual connection releases the lock after a process crash; a
    caller can then safely adopt a pending immutable object with IfNoneMatch.
    """
    if not _media_writers.acquire(blocking=False):
        raise fail("CHATBOT_BUSY", 429, "Outro arquivo está sendo preparado. Aguarde antes de tentar novamente.")
    engine, connection = None, None
    key = f"subscription-chatbot-media:{tenant}:{kind}:{digest}"
    locked = False
    confirmed = False
    try:
        # Advisory ownership cannot occupy the transaction pool: a supported
        # pool_size=1/max_overflow=0 must still be able to reauthorize writes.
        # The existing factory has already validated the URL and support role.
        engine = create_async_engine(support_session_factory().kw["bind"].url, poolclass=NullPool)
        connection = await engine.connect()
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        locked = bool((await connection.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                                               {"key": key})).scalar_one())
        confirmed = True
        if not locked:
            raise fail("CHATBOT_BUSY", 409, "Esse arquivo já está sendo preparado. Aguarde.")
        yield
    finally:
        async def release():
            try:
                if connection is None:
                    return
                if not confirmed:
                    # The server may have acquired the lock just before a
                    # transport/cancellation error hid its response.
                    await connection.invalidate()
                elif locked:
                    released = (await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                                                         {"key": key})).scalar_one()
                    if not released:
                        await connection.invalidate()
            except BaseException:
                if connection is not None:
                    await connection.invalidate()
                raise
            finally:
                try:
                    if connection is not None:
                        await connection.close()
                finally:
                    try:
                        if engine is not None:
                            await engine.dispose()
                    finally:
                        _media_writers.release()
        cleanup = _track_media(asyncio.create_task(release()))
        # Returning a connection with a session lock to the shared pool is never
        # permitted, including cancellation at exactly the final DB commit.
        await asyncio.shield(cleanup)


async def persist_prepared(tenant, kind, prepared, protected_transaction):
    """Reserve durable quota, write private bytes without locks, reauthorize.

    The factory is the caller's owner or effect-specific transaction guard.
    A failed/revoked upload never becomes a usable registry entry.
    """
    # Perform an authorization check before taking a pool slot or a global
    # advisory lock. The protected factory is rechecked at both DB writes.
    async with protected_transaction():
        pass
    cancelled = asyncio.Event()
    async def operation():
        async with media_mutex(tenant, kind, prepared.sha256):
            if cancelled.is_set():
                raise asyncio.CancelledError()
            return await _persist_prepared_locked(tenant, kind, prepared, protected_transaction, cancelled)
    task = _track_media(asyncio.create_task(operation()))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        raise


async def _persist_prepared_locked(tenant, kind, prepared, protected_transaction, cancelled):
    async with protected_transaction() as db:
        old = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND kind=:kind AND sha256=:sha FOR UPDATE"),
                               {"t": tenant, "kind": kind, "sha": prepared.sha256})).mappings().one_or_none()
        if old and old["status"] == "ready":
            return dict(old)
        # A pending row is recoverable only while owning media_mutex. No
        # previous writer or compensation can still own this same key.
        totals = (await db.execute(text("""SELECT count(*) AS count,COALESCE(sum(size+COALESCE(thumbnail_size,0)),0) AS bytes
            FROM subscription_chatbot_assets WHERE tenant_id=:t"""), {"t": tenant})).mappings().one()
        # A failed/revoked registry write may still have immutable private S3
        # bytes. Retain that reservation; retrying its same id/hash adds none.
        additional = 0 if old else 1
        additional_bytes = 0 if old else prepared.size + len(prepared.thumbnail or b"")
        if totals["count"] + additional > MAX_ASSETS or totals["bytes"] + additional_bytes > MAX_TENANT_MEDIA_BYTES:
            raise fail("CHATBOT_MEDIA_QUOTA", 413)
        asset_id = old["id"] if old else uuid4()
        values = {"id": asset_id, "t": tenant, "kind": kind, "mime": prepared.mime, "size": prepared.size,
                  "sha": prepared.sha256, "width": prepared.width, "height": prepared.height, "duration": prepared.duration_ms,
                  "thumb_size": len(prepared.thumbnail) if prepared.thumbnail else None,
                  "thumb_mime": prepared.thumbnail_mime, "thumb_sha": prepared.thumbnail_sha256}
        if old:
            await db.execute(text("UPDATE subscription_chatbot_assets SET status='pending',updated_at=clock_timestamp() WHERE id=:id AND tenant_id=:t"), values)
        else:
            await db.execute(text("""INSERT INTO subscription_chatbot_assets
                (id,tenant_id,kind,mime,size,sha256,width,height,duration_ms,thumbnail_size,thumbnail_mime,thumbnail_sha256)
                VALUES(:id,:t,:kind,:mime,:size,:sha,:width,:height,:duration,:thumb_size,:thumb_mime,:thumb_sha)"""), values)
    from app.subscription_chatbot_media import put_media
    # Use an executor Future rather than a separately cancellable Task. Even
    # event-loop shutdown must wait for its S3 compensation before unlocking.
    upload = asyncio.get_running_loop().run_in_executor(None, partial(put_media, tenant, asset_id, prepared))
    try:
        while True:
            try:
                await asyncio.shield(upload)
                break
            except asyncio.CancelledError:
                cancelled.set()
                if upload.cancelled():
                    raise
        if cancelled.is_set():
            raise asyncio.CancelledError()
        async with protected_transaction() as db:
            row = (await db.execute(text("""UPDATE subscription_chatbot_assets SET status='ready',updated_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id AND status='pending' RETURNING *"""), {"t": tenant, "id": asset_id})).mappings().one_or_none()
            if not row:
                raise fail("CHATBOT_CONFLICT")
            return dict(row)
    except BaseException:
        if upload.done() and not upload.cancelled():
            async with support_session_factory()() as db:
                async with db.begin():
                    await db.execute(text("""UPDATE subscription_chatbot_assets SET status='failed',updated_at=clock_timestamp()
                        WHERE tenant_id=:t AND id=:id AND status='pending'"""), {"t": tenant, "id": asset_id})
        raise
