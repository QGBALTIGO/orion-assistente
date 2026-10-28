"""Optional, explicit Google connection and tenant-authorized CSV exports."""
import json
import os
import secrets
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import text

from app.calendar_provider import CalendarProviderError, GoogleCalendar
from app.calendar_sync import OUTSIDE_INITIAL_SYNC, calendar_lock
from app.catalog_api import Actor, authenticate, authorize, ensure_tenant
from app.csv_export import csv_document
from app.database import get_session_factory, tenant_transaction
from app.google_oauth import (
    OAUTH_COOKIE, OAUTH_PATH, OAUTH_TTL, candidate_key, connection_fingerprint,
    digest, google_configured, configuration_checklist, make_authorization_url, oauth_config, oauth_redis,
    pending_candidate, request_initiator, validate_initiator, email_confirmation_required,
    authorization_origin,
)
from app.tenant_access import resolve_tenant_access

router = APIRouter(tags=["Integrações opcionais"])
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class CalendarSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    calendar_id: str = Field(min_length=1, max_length=250)

    @field_validator("calendar_id")
    @classmethod
    def safe_id(cls, value):
        if value != value.strip() or value == "primary" or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Selecione um calendário listado pelo Google")
        return value


def unavailable():
    return HTTPException(503, "A conexão com o Google está temporariamente indisponível. Tente novamente")


def provider_http(error):
    messages = {
        "google_permissions_incomplete": "Autorize os eventos e a lista de calendários para continuar",
        "google_refresh_token_missing": "Autorize novamente o acesso ao Google para permitir a sincronização",
        "google_authorization_expired": "A autorização do Google expirou. Conecte novamente",
        "google_permission_denied": "O Google não autorizou o acesso necessário. Conecte novamente",
        "google_calendar_unavailable": "O calendário não está disponível para esta conta Google",
        "google_calendar_not_writable": "Escolha um calendário que esta conta possa editar",
        "google_configuration_error": "A integração Google precisa ser configurada pela equipe Orion",
        "google_calendar_list_too_large": "A conta excede o limite de 1.000 calendários desta integração",
    }
    return HTTPException(503 if error.retryable or error.code == "google_configuration_error" else 409,
                         messages.get(error.code, "Não foi possível concluir a conexão com o Google. Tente novamente"))


async def connection_row(db, tenant, *, lock=False):
    return (await db.execute(text("""
        SELECT id,tenant_id,calendar_id,refresh_token_enc,status,last_error,updated_at
        FROM google_connections WHERE tenant_id=:tenant
    """ + (" FOR UPDATE" if lock else "")), {"tenant": tenant})).mappings().first()


async def connection_snapshot(tenant, *, require_ready=False):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await ensure_tenant(db, tenant)
            if require_ready:
                from app.onboarding_service import require_operational_ready
                await require_operational_ready(db, tenant, "google")
            return await connection_row(db, tenant)


def still_same_connection(candidate, row):
    if candidate.get("connection_revision") != connection_fingerprint(row):
        raise HTTPException(409, "A conexão foi alterada ou desconectada. Inicie a autorização novamente")


def current_candidate(candidate, row):
    return candidate if candidate and candidate.get("connection_revision") == connection_fingerprint(row) else None


def callback_result(tenant, outcome):
    response = RedirectResponse("/panel?" + urlencode({"aba": "agenda", "google": outcome, "empresa": str(tenant)}),
                                status_code=303, headers=NO_STORE)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH, secure=True, httponly=True, samesite="lax")
    return response


async def google_status_data(tenant_id, actor, request):
    access = await resolve_tenant_access(actor, tenant_id, "read")
    configured = google_configured()
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await ensure_tenant(db, tenant_id)
            row = await connection_row(db, tenant_id)
            stats = dict((await db.execute(text("""
                SELECT count(*) FILTER(WHERE status='pending') AS pending,
                       count(*) FILTER(WHERE status='dead' AND last_error IS DISTINCT FROM :outside) AS failed,
                       count(*) FILTER(WHERE status='sent') AS sent,
                       max(updated_at) FILTER(WHERE status='sent') AS last_synced_at
                FROM calendar_outbox WHERE tenant_id=:tenant
            """), {"tenant": tenant_id, "outside": OUTSIDE_INITIAL_SYNC})).mappings().one())
    can_manage = access.role in {"owner", "support", "admin"}
    email_pending = can_manage and await email_confirmation_required(request, actor)
    candidate, cached_name, oauth_available, worker_health = None, None, True, None
    if configured:
        redis = oauth_redis()
        try:
            if can_manage:
                source = request_initiator(request, actor)
                candidate = current_candidate(await pending_candidate(redis, tenant_id, source), row)
            heartbeat = await redis.get("orion:google:worker:health")
            if heartbeat:
                health = json.loads(heartbeat)
                if (isinstance(health, dict)
                        and isinstance(health.get("configured"), bool)
                        and isinstance(health.get("sync_enabled"), bool)):
                    worker_health = health
            if row and row["calendar_id"]:
                raw = await redis.get("google:calendar-meta:" + str(tenant_id))
                meta = json.loads(raw) if raw else {}
                if isinstance(meta, dict) and meta.get("id") == row["calendar_id"]:
                    cached_name = meta.get("name")
        except HTTPException:
            candidate = None
        except (RedisError, OSError, ValueError, TypeError):
            oauth_available = False
        finally:
            await redis.aclose()
    selected = row["calendar_id"] if row and row["calendar_id"] else None
    legacy = selected == "primary"
    status = row["status"] if row else "not_connected"
    connected = bool(row and row["refresh_token_enc"] and status == "active" and not legacy)
    enabled = os.getenv("GOOGLE_LIVE_SYNC") == "1"
    last_error = "google_calendar_selection_required" if legacy else row["last_error"] if row else None
    if not configured:
        sync_status = "not_configured"
    elif candidate:
        sync_status = "choose_calendar"
    elif legacy or status == "error":
        sync_status = "reauthorization_required"
    elif not connected:
        sync_status = "not_connected"
    elif not enabled or worker_health and not worker_health["sync_enabled"]:
        sync_status = "paused"
    elif worker_health and not worker_health["configured"]:
        sync_status = "attention"
    elif stats["failed"] or last_error:
        sync_status = "attention"
    elif stats["pending"]:
        sync_status = "pending"
    elif stats["sent"]:
        sync_status = "up_to_date"
    else:
        sync_status = "idle"
    if not oauth_available and not connected:
        sync_status = "attention"
        last_error = "google_oauth_unavailable"
    if configured and email_pending and not connected:
        sync_status = "email_confirmation_required"
    return {
        "configured": configured, "connected": connected, "status": status,
        "authorization_origin": authorization_origin() if configured else None,
        "sync_enabled": enabled, "sync_status": sync_status,
        "calendar_id": selected, "calendar_name": cached_name,
        "last_synced_at": stats["last_synced_at"], "pending": stats["pending"],
        "failed": stats["failed"], "sent": stats["sent"], "last_error": last_error,
        "direction": "orion_to_google",
        "worker_online": worker_health is not None,
        "worker_ready": bool(worker_health and worker_health["configured"] and worker_health["sync_enabled"]),
        "selection_pending": bool(candidate),
        "calendar_change_locked": bool(selected),
        "email_confirmation_required": bool(email_pending),
        "setup": configuration_checklist() if can_manage else None,
        "capabilities": {
            "can_connect": can_manage and configured and oauth_available and not email_pending,
            "can_disconnect": can_manage and bool(candidate or row and row["refresh_token_enc"]),
            "can_select_calendar": can_manage and configured and bool(candidate),
            "can_retry": can_manage and configured and connected and enabled and bool(stats["pending"] or stats["failed"]),
        },
    }


@router.post("/api/v1/tenants/{tenant_id}/google/connect")
async def connect_google(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    if await email_confirmation_required(request, actor):
        raise HTTPException(403, "Confirme seu e-mail no Orion antes de conectar o Google Agenda")
    cid, _, redirect, _ = oauth_config()
    if request.url.netloc != urlsplit(redirect).netloc:
        raise HTTPException(503, "Abra o painel no endereço configurado para a conexão Google")
    source = request_initiator(request, actor)
    row = await connection_snapshot(tenant_id)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    intent = {"tenant_id": str(tenant_id), "initiator": source, "nonce_hash": digest(nonce),
              "verifier": verifier, "connection_revision": connection_fingerprint(row)}
    redis = oauth_redis()
    try:
        start_key = "google:start:" + str(tenant_id) + ":" + digest(source["key"])
        if not await redis.set(start_key, "1", ex=10, nx=True):
            raise HTTPException(429, "Aguarde alguns segundos antes de iniciar outra conexão")
        await redis.delete(candidate_key(tenant_id, source))
        if not await redis.set("google:oauth:" + state, json.dumps(intent), ex=OAUTH_TTL, nx=True):
            raise unavailable()
    except (RedisError, OSError):
        raise unavailable() from None
    finally:
        await redis.aclose()
    response = JSONResponse({"authorization_url": make_authorization_url(cid, redirect, state, verifier)},
                            headers=NO_STORE)
    response.set_cookie(OAUTH_COOKIE, nonce, max_age=OAUTH_TTL, path=OAUTH_PATH,
                        secure=True, httponly=True, samesite="lax")
    return response


@router.get("/integrations/google/callback")
async def callback_google(request: Request, state: str = "", code: str | None = None, error: str | None = None):
    # Do not put validation errors containing the authorization code in responses.
    if not 20 <= len(state) <= 150 or code is not None and not 1 <= len(code) <= 4096 or error is not None and len(error) > 200:
        raise HTTPException(400, "Resposta OAuth inválida")
    _, _, redirect, key = oauth_config()
    redis = oauth_redis()
    tenant = None
    try:
        raw = await redis.get("google:oauth:" + state)
        try:
            intent = json.loads(raw) if raw else {}
            tenant = UUID(intent["tenant_id"])
        except (ValueError, TypeError, KeyError):
            raise HTTPException(403, "A autorização expirou. Inicie a conexão novamente") from None
        nonce = request.cookies.get(OAUTH_COOKIE, "")
        if len(nonce) != 43 or not secrets.compare_digest(intent.get("nonce_hash", ""), digest(nonce)):
            raise HTTPException(403, "Esta autorização não pertence a este navegador")
        await validate_initiator(redis, intent, tenant)
        still_same_connection(intent, await connection_snapshot(tenant))
        consumed = await redis.getdel("google:oauth:" + state)
        if not consumed or not secrets.compare_digest(consumed, raw):
            raise HTTPException(403, "Esta autorização já foi utilizada")
        if error is not None or not code:
            return callback_result(tenant, "error")
        refresh = await GoogleCalendar().exchange_code(code, intent["verifier"], redirect)
        # Account removal, membership changes, logout and revoke during the
        # exchange are checked again before saving the temporary encrypted grant.
        await validate_initiator(redis, intent, tenant)
        still_same_connection(intent, await connection_snapshot(tenant))
        candidate = {"tenant_id": str(tenant), "initiator": intent["initiator"],
                     "connection_revision": intent["connection_revision"],
                     "refresh_token_enc": Fernet(key.encode()).encrypt(refresh.encode()).decode()}
        await redis.set(candidate_key(tenant, intent["initiator"]), json.dumps(candidate), ex=OAUTH_TTL)
        return callback_result(tenant, "choose")
    except CalendarProviderError:
        return callback_result(tenant, "error")
    except (RedisError, OSError):
        raise unavailable() from None
    finally:
        await redis.aclose()


async def authorized_candidate(tenant, actor, request, redis):
    await resolve_tenant_access(actor, tenant, "publish")
    source = request_initiator(request, actor)
    candidate = await pending_candidate(redis, tenant, source)
    if candidate is None:
        raise HTTPException(409, "Autorize o Google antes de escolher o calendário")
    await validate_initiator(redis, candidate, tenant)
    row = await connection_snapshot(tenant)
    still_same_connection(candidate, row)
    return source, candidate, row


def decrypt_candidate(candidate, key):
    try:
        return Fernet(key.encode()).decrypt(candidate["refresh_token_enc"].encode()).decode()
    except (InvalidToken, KeyError, ValueError, TypeError, UnicodeError):
        raise HTTPException(409, "A autorização temporária expirou. Conecte o Google novamente") from None


@router.get("/api/v1/tenants/{tenant_id}/google/calendars")
async def google_calendars(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    _, _, _, key = oauth_config()
    redis = oauth_redis()
    try:
        _, candidate, row = await authorized_candidate(tenant_id, actor, request, redis)
        provider = GoogleCalendar()
        token = await provider.refresh_access_token(decrypt_candidate(candidate, key))
        items = await provider.list_calendars(token)
        await validate_initiator(redis, candidate, tenant_id)
        still_same_connection(candidate, await connection_snapshot(tenant_id))
        selected = row["calendar_id"] if row and row["calendar_id"] else None
        for item in items:
            item["selectable"] = not selected or item["id"] == selected or selected == "primary" and item["primary"]
        return {"items": items, "selection_pending": True, "calendar_change_locked": bool(selected),
                "calendar_id": selected}
    except CalendarProviderError as failure:
        raise provider_http(failure) from None
    except (RedisError, OSError):
        raise unavailable() from None
    finally:
        await redis.aclose()


@router.post("/api/v1/tenants/{tenant_id}/google/calendar")
async def select_google_calendar(tenant_id: UUID, payload: CalendarSelection, request: Request,
                                 actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    _, _, _, key = oauth_config()
    await connection_snapshot(tenant_id)
    redis = oauth_redis()
    try:
        source, candidate, prior = await authorized_candidate(tenant_id, actor, request, redis)
        selected = prior["calendar_id"] if prior and prior["calendar_id"] else None
        if selected and selected not in {"primary", payload.calendar_id}:
            raise HTTPException(409, "Este negócio já está vinculado a outro calendário. Reconecte a mesma agenda; a troca entre calendários não está disponível")
        provider = GoogleCalendar()
        token = await provider.refresh_access_token(decrypt_candidate(candidate, key))
        metadata = await provider.get_calendar(token, payload.calendar_id)
        if metadata["id"] != payload.calendar_id:
            raise HTTPException(409, "Escolha o identificador listado pelo Google")
        if selected == "primary":
            # Never reinterpret legacy 'primary' using a different Google user.
            # Only the old grant can prove its own canonical calendar identity.
            if not prior["refresh_token_enc"]:
                raise HTTPException(409, "A conexão antiga precisa de revisão da equipe Orion antes de escolher um calendário")
            try:
                old_refresh = Fernet(key.encode()).decrypt(prior["refresh_token_enc"]).decode()
                old_token = await provider.refresh_access_token(old_refresh)
                old_calendar = await provider.get_calendar(old_token, "primary")
            except (CalendarProviderError, InvalidToken, UnicodeError):
                raise HTTPException(409, "Não foi possível comprovar a agenda da conexão antiga. Solicite a revisão da equipe Orion") from None
            if old_calendar["id"] != metadata["id"]:
                raise HTTPException(409, "Reconecte a agenda usada anteriormente por este negócio")
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant_id):
                # OAuth consent is account setup, not a booking operation.
                # A new business can connect its Google calendar before staff,
                # services and bookable hours are configured. Those separate
                # requirements remain enforced when creating appointments.
                if not await calendar_lock(db, tenant_id):
                    raise HTTPException(409, "Há uma sincronização em andamento. Aguarde alguns segundos e tente novamente")
                current = await connection_row(db, tenant_id, lock=True)
                still_same_connection(candidate, current)
                await validate_initiator(redis, candidate, tenant_id)
                # Consumption precedes the write. A failed commit requires a new
                # explicit OAuth flow, never an implicit use of an old grant.
                raw = await redis.getdel(candidate_key(tenant_id, source))
                if not raw or json.loads(raw) != candidate:
                    raise HTTPException(409, "Esta seleção já foi utilizada. Conecte novamente")
                await db.execute(text("""
                    INSERT INTO google_connections (tenant_id,calendar_id,refresh_token_enc,status)
                    VALUES (:tenant,:calendar,:token,'active')
                    ON CONFLICT (tenant_id) DO UPDATE
                    SET calendar_id=EXCLUDED.calendar_id,refresh_token_enc=EXCLUDED.refresh_token_enc,
                        status='active',last_error=NULL,updated_at=clock_timestamp()
                """), {"tenant": tenant_id, "calendar": metadata["id"],
                        "token": candidate["refresh_token_enc"].encode()})
                # Existing future bookings are included even if they predate the
                # connection. The worker remains the only event writer.
                # Old work never attempted is retained as explicitly excluded,
                # not falsely marked as sent. Known/uncertain deliveries must
                # stay recoverable with their deterministic event identifier.
                if not selected or selected == "primary":
                    await db.execute(text("""
                        UPDATE calendar_outbox o SET status='dead',last_error=:outside,
                            updated_at=clock_timestamp()
                        FROM appointments a
                        WHERE o.tenant_id=:tenant AND a.tenant_id=o.tenant_id
                          AND a.id=o.appointment_id AND a.end_at<now()
                          AND o.external_event_id IS NULL AND o.attempts=0
                          AND o.status IN ('pending','dead')
                    """), {"tenant": tenant_id, "outside": OUTSIDE_INITIAL_SYNC})
                await db.execute(text("""
                    INSERT INTO calendar_outbox (tenant_id,appointment_id,action)
                    SELECT tenant_id,id,'create' FROM appointments
                    WHERE tenant_id=:tenant AND status='confirmed' AND end_at>=now()
                    ON CONFLICT (tenant_id,appointment_id) DO UPDATE
                    SET action='update',status='pending',attempts=0,next_attempt_at=now(),
                        last_error=NULL,updated_at=clock_timestamp()
                """), {"tenant": tenant_id})
                await db.execute(text("""
                    UPDATE calendar_outbox SET status='pending',attempts=0,
                        next_attempt_at=now(),last_error=NULL,updated_at=clock_timestamp()
                    WHERE tenant_id=:tenant AND status IN ('pending','dead')
                      AND last_error IS DISTINCT FROM :outside
                """), {"tenant": tenant_id, "outside": OUTSIDE_INITIAL_SYNC})
        try:
            await redis.set("google:calendar-meta:" + str(tenant_id),
                            json.dumps({"id": metadata["id"], "name": metadata["name"]}), ex=86400)
        except (RedisError, OSError):
            pass  # This optional display cache is not the durable connection.
    except CalendarProviderError as failure:
        raise provider_http(failure) from None
    except (RedisError, OSError):
        raise unavailable() from None
    finally:
        await redis.aclose()
    return await google_status_data(tenant_id, actor, request)


@router.get("/api/v1/tenants/{tenant_id}/google/status")
async def google_status(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await google_status_data(tenant_id, actor, request)


@router.get("/api/v1/tenants/{tenant_id}/google/appointments/{appointment_id}/status")
async def google_appointment_status(tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "read")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            record = (await db.execute(text("""
                SELECT o.status,o.attempts,o.next_attempt_at,o.last_error,o.updated_at
                FROM appointments a LEFT JOIN calendar_outbox o
                  ON o.tenant_id=a.tenant_id AND o.appointment_id=a.id
                WHERE a.tenant_id=:tenant AND a.id=:id
            """), {"tenant": tenant_id, "id": appointment_id})).mappings().first()
            if record is None:
                raise HTTPException(404, "Agendamento não encontrado")
            connection = await connection_row(db, tenant_id)
    raw_status = record["status"]
    excluded = record["last_error"] == OUTSIDE_INITIAL_SYNC
    state = "outside_initial_sync" if excluded else "not_queued" if raw_status is None else "synced" if raw_status == "sent" else \
            "failed" if raw_status == "dead" else "retrying" if record["attempts"] else "pending"
    return {"status": state, "sync_enabled": os.getenv("GOOGLE_LIVE_SYNC") == "1",
            "configured": google_configured(),
            "connection_status": connection["status"] if connection else "not_connected",
            "last_synced_at": record["updated_at"] if raw_status == "sent" else None,
            "attempts": record["attempts"] or 0,
            "next_attempt_at": record["next_attempt_at"] if raw_status == "pending" else None,
            "last_error": None if excluded else record["last_error"], "direction": "orion_to_google"}


@router.post("/api/v1/tenants/{tenant_id}/google/revoke")
async def revoke_google(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant_id):
            await ensure_tenant(db, tenant_id)
            if not await calendar_lock(db, tenant_id):
                raise HTTPException(409, "Há uma sincronização em andamento. Aguarde alguns segundos e tente novamente")
            # A tombstone also invalidates an OAuth intent created before the
            # first connection. Empty ID means no calendar was selected yet.
            await db.execute(text("""
                INSERT INTO google_connections (tenant_id,calendar_id,refresh_token_enc,status,updated_at)
                VALUES (:tenant,'',NULL,'revoked',clock_timestamp())
                ON CONFLICT (tenant_id) DO UPDATE
                SET refresh_token_enc=NULL,status='revoked',last_error=NULL,updated_at=clock_timestamp()
            """), {"tenant": tenant_id})
    return await google_status_data(tenant_id, actor, request)


@router.post("/api/v1/tenants/{tenant_id}/google/retry")
async def retry_google(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    oauth_config()
    if os.getenv("GOOGLE_LIVE_SYNC") != "1":
        raise HTTPException(409, "A sincronização está pausada. A equipe Orion precisa habilitá-la")
    redis = oauth_redis()
    try:
        if not await redis.set("google:retry:" + str(tenant_id), "1", ex=60, nx=True):
            raise HTTPException(429, "Aguarde um minuto antes de repetir a sincronização")
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant_id):
                if not await calendar_lock(db, tenant_id):
                    raise HTTPException(409, "Há uma sincronização em andamento. Aguarde alguns segundos")
                connection = await connection_row(db, tenant_id, lock=True)
                if not connection or connection["status"] != "active" or not connection["refresh_token_enc"] or connection["calendar_id"] == "primary":
                    raise HTTPException(409, "Conecte a agenda Google antes de repetir a sincronização")
                await db.execute(text("""
                    UPDATE calendar_outbox SET status='pending',attempts=0,
                        next_attempt_at=now(),last_error=NULL,updated_at=clock_timestamp()
                    WHERE tenant_id=:tenant AND status IN ('pending','dead')
                      AND last_error IS DISTINCT FROM :outside
                """), {"tenant": tenant_id, "outside": OUTSIDE_INITIAL_SYNC})
                await db.execute(text("""
                    UPDATE google_connections SET last_error=NULL WHERE tenant_id=:tenant
                """), {"tenant": tenant_id})
    except (RedisError, OSError):
        raise unavailable() from None
    finally:
        await redis.aclose()
    return await google_status_data(tenant_id, actor, request)


@router.get("/api/v1/tenants/{tenant_id}/export/appointments.csv")
async def export_appointments(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            rows=(await s.execute(text("""
                SELECT a.protocol,a.start_at,a.end_at,a.status,p.name,c.name,c.whatsapp_e164,
                       a.total_cents
                FROM appointments a
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant ORDER BY a.start_at LIMIT 10000
            """),{"tenant":tenant_id})).all()
    safe_rows=[(protocol,start.isoformat(),end.isoformat(),status,pro,customer,phone,
                f"{cents/100:.2f}".replace(".",","))
               for protocol,start,end,status,pro,customer,phone,cents in rows]
    payload=csv_document(["Protocolo","Início UTC","Fim UTC","Situação",
                          "Profissional","Cliente","Telefone","Total (R$)"],safe_rows)
    return Response(payload,media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":'attachment; filename="agendamentos.csv"',
                             "Cache-Control":"no-store"})
