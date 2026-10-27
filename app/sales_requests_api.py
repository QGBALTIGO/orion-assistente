"""Receive contact permission and expose requests only to Orion support/admin.

This module never creates accounts, tenants, trials, subscriptions, charges or
outbound messages. No request payload or storage error is logged or echoed.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError, TypeAdapter, UUID4
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_api import Actor, authenticate, support_only, support_session_factory
from app.panel_auth import _redis
from app.sales_requests_schemas import CONSENT_VERSION, Intent, SalesRequest, Status, UpdateSalesRequest, clean_text

router = APIRouter(tags=["Solicitações comerciais"])
BODY_LIMIT = 8192
REDIS_TIMEOUT = 2.0
STORAGE_TIMEOUT = 5.0
PUBLIC_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
UUID4_ADAPTER = TypeAdapter(UUID4)
PUBLIC_COLUMNS = """id,name,email,phone,business_name,segment,intent,status,
    created_at,updated_at,contacted_at,version"""
RATE_SCRIPT = """
for i=1,#KEYS do
    local limit=tonumber(ARGV[(i-1)*2+1])
    local count=tonumber(redis.call('GET',KEYS[i]) or '0')
    if count>=limit then return math.max(1,redis.call('TTL',KEYS[i])) end
end
for i=1,#KEYS do
    local count=redis.call('INCR',KEYS[i])
    if count==1 then redis.call('EXPIRE',KEYS[i],ARGV[(i-1)*2+2]) end
end
return 0
"""


def problem(status, message, **headers):
    return HTTPException(status, message, headers={**PUBLIC_HEADERS, **headers})


def checked_origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError):
        return None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        return None
    default = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), port or default


def require_public_origin(request):
    # This first-party POST requires an Origin even when no session is present.
    # Do not trust browser-supplied Forwarded/X-Forwarded-* headers here.
    origin = checked_origin(request.headers.get("origin", ""))
    from app.public_origins import canonical_public_origin, legacy_public_origins
    try:
        allowed = {checked_origin(value) for value in (canonical_public_origin(), *legacy_public_origins())}
    except ValueError:
        raise problem(503, "Solicitações temporariamente indisponíveis") from None
    if origin is None or origin not in allowed:
        raise problem(403, "Origem não autorizada")
    if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
        raise problem(403, "Origem não autorizada")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


async def bounded_payload(request, model, limit=BODY_LIMIT):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise problem(415, "Envie os dados no formato JSON")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit():
            raise problem(400, "Tamanho da solicitação inválido")
        if len(length) > 9 or int(length) > limit:
            raise problem(413, "Solicitação muito extensa")
    content = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                if len(content) + len(chunk) > limit:
                    raise problem(413, "Solicitação muito extensa")
                content.extend(chunk)
    except TimeoutError:
        raise problem(408, "O envio não foi concluído. Tente novamente") from None
    try:
        decoded = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object)
        return model.model_validate(decoded)
    except ValidationError as exc:
        # Never include Pydantic's input or context: they may contain PII.
        errors = [{"loc": [part for part in error["loc"] if part in model.model_fields] or ["body"],
                   "msg": error["msg"]}
                  for error in exc.errors(include_input=False, include_context=False)]
        raise HTTPException(422, errors, headers=PUBLIC_HEADERS) from None
    except (UnicodeError, ValueError, RecursionError):
        raise problem(422, "Solicitação inválida. Confira os campos") from None


async def submission_limit(request, email):
    secret = os.getenv("SUPPORT_API_TOKEN", "")
    if len(secret) < 32:
        raise problem(503, "Solicitações temporariamente indisponíveis")
    peer = request.client.host if request.client else "unknown"
    keys = ["orion:sales-limit:" + kind + ":" + hmac.new(
        secret.encode(), (kind + ":" + value).encode(), hashlib.sha256).hexdigest()
        for kind, value in (("peer", peer), ("email", email))]
    try:
        async with asyncio.timeout(REDIS_TIMEOUT):
            cache = _redis()
            try:
                retry = int(await cache.eval(RATE_SCRIPT, 2, *keys, 20, 600, 5, 3600))
                if retry:
                    raise problem(429, "Muitas tentativas. Aguarde antes de enviar novamente", **{"Retry-After": str(retry)})
            finally:
                await cache.aclose()
    except (RedisError, OSError, TimeoutError, RuntimeError, ValueError):
        raise problem(503, "Solicitações temporariamente indisponíveis") from None


@asynccontextmanager
async def storage():
    try:
        async with asyncio.timeout(STORAGE_TIMEOUT):
            async with support_session_factory()() as db:
                async with db.begin():
                    await db.execute(text("SET LOCAL statement_timeout='2500ms'"))
                    yield db
    except (SQLAlchemyError, OSError, TimeoutError, RuntimeError):
        raise problem(503, "Solicitações temporariamente indisponíveis") from None


@router.post("/api/public/sales-requests", status_code=201)
async def submit_request(request: Request, response: Response):
    require_public_origin(request)
    try:
        identity = UUID4_ADAPTER.validate_python(request.headers.get("idempotency-key", ""))
    except ValidationError:
        raise problem(422, "Identificador de envio inválido") from None
    payload = await bounded_payload(request, SalesRequest)
    if payload.website:
        raise problem(422, "Não foi possível receber esta solicitação")
    await submission_limit(request, payload.email)
    values = payload.model_dump(exclude={"website"})
    values["consent_version"] = CONSENT_VERSION
    fingerprint = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    async with storage() as db:
        new_id = (await db.execute(text("""INSERT INTO sales_requests
            (idempotency_key,payload_hash,name,email,phone,business_name,segment,intent,consent,consent_version)
            VALUES(:key,:fingerprint,:name,:email,:phone,:business_name,:segment,:intent,:consent,:consent_version)
            ON CONFLICT(idempotency_key) DO NOTHING RETURNING id"""),
            {**values, "key": identity, "fingerprint": fingerprint})).scalar_one_or_none()
        if new_id is None:
            existing = (await db.execute(text("SELECT id,payload_hash FROM sales_requests WHERE idempotency_key=:key"),
                                        {"key": identity})).mappings().one()
            if not hmac.compare_digest(existing["payload_hash"], fingerprint):
                raise problem(409, "Este identificador já foi usado. Revise e envie uma nova solicitação")
            new_id = existing["id"]
    response.headers.update(PUBLIC_HEADERS)
    return {"ok": True, "request_id": str(new_id)}


@router.get("/api/v1/admin/sales-requests")
async def list_requests(response: Response, status: Status | Literal["all"] = Query(default="all"),
        intent: Intent | Literal["all"] = Query(default="all"), q: str = Query(default="", max_length=120),
        limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100_000),
        actor: Actor = Depends(authenticate)):
    support_only(actor)
    try:
        query = clean_text(q)
    except ValueError:
        raise problem(422, "Busca inválida") from None
    clauses, params = [], {"limit": limit, "offset": offset}
    if status != "all":
        clauses.append("status=:status")
        params["status"] = status
    if intent != "all":
        clauses.append("intent=:intent")
        params["intent"] = intent
    if query:
        params["query"] = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(name ILIKE :query OR email ILIKE :query OR business_name ILIKE :query OR phone ILIKE :query)")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    async with storage() as db:
        # One MVCC snapshot keeps total, global counters and this page coherent.
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        total = (await db.execute(text("SELECT count(*) FROM sales_requests" + where), params)).scalar_one()
        items = (await db.execute(text("SELECT " + PUBLIC_COLUMNS + " FROM sales_requests" + where +
            " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"), params)).mappings().all()
        grouped = (await db.execute(text("SELECT status,count(*) AS count FROM sales_requests GROUP BY status"))).mappings().all()
    counts = {"all": 0, "new": 0, "contacted": 0, "closed": 0}
    for row in grouped:
        counts[row["status"]] = row["count"]
        counts["all"] += row["count"]
    response.headers.update(PUBLIC_HEADERS)
    return jsonable_encoder({"items": [dict(row) for row in items], "total": total,
        "limit": limit, "offset": offset, "counts": counts, "access": {"can_update": True}})


@router.patch("/api/v1/admin/sales-requests/{request_id}")
async def update_request(request_id: UUID, request: Request, response: Response,
                         actor: Actor = Depends(authenticate)):
    support_only(actor)
    payload = await bounded_payload(request, UpdateSalesRequest, limit=1024)
    async with storage() as db:
        current = (await db.execute(text("SELECT status,version FROM sales_requests WHERE id=:id FOR UPDATE"),
                                   {"id": request_id})).mappings().one_or_none()
        if current is None:
            raise problem(404, "Solicitação não encontrada")
        if current["version"] != payload.expected_version:
            raise problem(409, "A solicitação mudou. Atualize a lista antes de continuar")
        if current["status"] != payload.status:
            await db.execute(text("""UPDATE sales_requests SET status=:status,version=version+1,updated_at=now(),
                contacted_at=CASE WHEN :record_contact THEN coalesce(contacted_at,now()) ELSE contacted_at END,
                updated_by_role=:role,updated_by_account_id=:account WHERE id=:id"""),
                {"id": request_id, "status": payload.status, "record_contact": payload.status == "contacted",
                 "role": actor.role, "account": actor.account_id})
        item = (await db.execute(text("SELECT " + PUBLIC_COLUMNS + " FROM sales_requests WHERE id=:id"),
                                {"id": request_id})).mappings().one()
    response.headers.update(PUBLIC_HEADERS)
    return jsonable_encoder(dict(item))
