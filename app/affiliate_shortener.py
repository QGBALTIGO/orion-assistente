"""Universal links using the customer's own Spoo.me account; no Orion redirect."""
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.catalog_api import Actor, authenticate

router = APIRouter()
BASE = "https://spoo.me/api/v1"
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
ALIAS = re.compile(r"[A-Za-z0-9_-]{3,16}\Z")
KEY = re.compile(r"[A-Za-z0-9_.-]{16,256}\Z")
LINK_ID = re.compile(r"[a-fA-F0-9]{24}\Z")
HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
SHORTENERS = {"spoo.me", "is.gd", "v.gd", "bit.ly", "tinyurl.com", "t.co", "amzn.to", "a.co", "s.shopee.com.br", "shope.ee", "meli.la", "meli.promo", "amzon.promo"}
PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".test", ".invalid", ".onion", ".lan", ".home", ".arpa")
MARKETPLACE_RESTRICTED = {"mercadolivre.com.br", "mercadolibre.com", "mercadolibre.com.ar", "mercadolibre.com.mx", "mercadolivre.com"}
ACTIONS = {"list", "create", "stats", "status", "delete"}

def fail(status, message):
    raise HTTPException(status, message, headers=NO_STORE)

def enabled():
    return os.getenv("ORION_AFFILIATE_SHORTENER_ENABLED", "0") == "1"

async def authorize_owner(actor, tenant_id):
    from app.affiliate_workspace import affiliate_owner
    await affiliate_owner(actor, tenant_id)

def domain_matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)

def destination(value):
    """Do not fetch destinations; preserve their bytes after validation."""
    if not isinstance(value, str) or not 8 <= len(value) <= 4096 or value != value.strip():
        fail(422, "Informe um endereço HTTP ou HTTPS válido de até 4.096 caracteres.")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        fail(422, "O endereço contém caracteres inválidos.")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").encode("idna").decode("ascii").lower()
        port = url.port
    except (ValueError, UnicodeError):
        fail(422, "O endereço é inválido.")
    if url.scheme not in {"http", "https"} or url.username is not None or url.password is not None or port not in {None, 80, 443} or not HOST.fullmatch(host):
        fail(422, "Use um endereço público HTTP ou HTTPS, sem credenciais ou portas alternativas.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        fail(422, "Endereços IP não são permitidos.")
    if host.endswith(PRIVATE_SUFFIXES) or domain_matches(host, SHORTENERS):
        fail(422, "Use o endereço público original; links internos e cadeias de encurtadores não são aceitos.")
    if domain_matches(host, MARKETPLACE_RESTRICTED):
        fail(422, "Para Mercado Livre, utilize o link oficial do marketplace.")
    return value

def api_key(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        fail(422, "Informe uma chave de API válida da sua conta Spoo.me.")
    return value

def valid_id(value):
    if not isinstance(value, str) or not LINK_ID.fullmatch(value):
        fail(422, "Identificador de link inválido.")
    return value

def alias_value(value):
    if value in ("", None):
        return None
    if not isinstance(value, str) or not ALIAS.fullmatch(value):
        fail(422, "O endereço personalizado deve ter de 3 a 16 letras, números, hífens ou sublinhados.")
    return value

def integer(value):
    return value if type(value) is int and value >= 0 else None

def safe_link(alias, supplied=None):
    if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
        return None
    expected = "https://spoo.me/" + alias
    return expected if supplied in (None, expected) else None

def normalize_row(row):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not LINK_ID.fullmatch(row["id"]):
        return None
    alias = row.get("alias")
    if row.get("domain") not in (None, "", "spoo.me"):
        return None
    short_url = safe_link(alias, row.get("short_url"))
    long_url = row.get("long_url")
    if not short_url or not isinstance(long_url, str) or len(long_url) > 4096:
        return None
    return {"id": row["id"], "alias": alias, "short_url": short_url,
            "long_url": long_url, "status": row.get("status") if row.get("status") in ("ACTIVE", "INACTIVE") else None,
            "total_clicks": integer(row.get("total_clicks"))}

LIMIT_SCRIPT = """
local tenant = tonumber(redis.call('GET', KEYS[1]) or '0')
local global = tonumber(redis.call('GET', KEYS[2]) or '0')
if tenant >= 12 or global >= 50 then return 0 end
if redis.call('INCR', KEYS[1]) == 1 then redis.call('EXPIRE', KEYS[1], 60) end
if redis.call('INCR', KEYS[2]) == 1 then redis.call('EXPIRE', KEYS[2], 60) end
return 1
"""

async def rate_limit(tenant_id):
    url = os.getenv("REDIS_URL")
    if not url:
        fail(503, "A proteção de uso do encurtador está indisponível.")
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        allowed = await client.eval(LIMIT_SCRIPT, 2, "orion:affiliate:shortener:tenant:" + tenant_id.hex,
                                    "orion:affiliate:shortener:global")
    except Exception:
        fail(503, "A proteção de uso do encurtador está indisponível.")
    finally:
        await client.aclose()
    if not allowed:
        fail(429, "Limite temporário de uso atingido. Aguarde um minuto.")

async def provider(method, path, key, *, payload=None, params=None, expected=200):
    """Fixed upstream; no redirects or mutation retries; sanitized errors."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=4), follow_redirects=False,
                                     trust_env=False) as client:
            async with client.stream(method, BASE + path,
                                     headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
                                     json=payload, params=params) as response:
                status = response.status_code
                if status != expected:
                    if status in (401, 403):
                        fail(status, "A chave Spoo.me não tem acesso. Confira a chave e suas permissões.")
                    if status == 409:
                        fail(409, "Esse endereço personalizado já está em uso. Escolha outro.")
                    if status == 429:
                        fail(429, "O limite do Spoo.me foi atingido. Tente novamente mais tarde.")
                    if status in (400, 422):
                        fail(422, "O Spoo.me recusou os dados ou o destino. Confira o endereço e as regras.")
                    if status == 404:
                        fail(404, "O link não foi encontrado na sua conta Spoo.me.")
                    fail(502, "O serviço Spoo.me não confirmou a operação.")
                if not response.headers.get("content-type", "").lower().startswith("application/json"):
                    fail(502, "O Spoo.me retornou uma resposta inesperada.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 512 * 1024:
                        fail(502, "A resposta do Spoo.me excedeu o tamanho permitido.")
                result = json.loads(data)
    except (httpx.HTTPError, ValueError, UnicodeError):
        fail(502, "Não foi possível confirmar a operação no Spoo.me. Atualize o histórico antes de repetir uma criação.")
    if not isinstance(result, dict):
        fail(502, "O Spoo.me retornou uma resposta inesperada.")
    return result

async def execute(action, body, key):
    if action == "create":
        original = destination(body.get("url"))
        alias = alias_value(body.get("alias"))
        if body.get("accepted") is not True:
            fail(422, "Confirme que você tem autorização para divulgar o destino e respeita os termos.")
        payload = {"long_url": original, "private_stats": True}
        if alias:
            payload["alias"] = alias
        data = await provider("POST", "/shorten", key, payload=payload, expected=201)
        link = safe_link(data.get("alias"), data.get("short_url"))
        if (not link or data.get("short_url") != link or data.get("long_url") != original or not data.get("owner_id")
                or (alias is not None and data.get("alias") != alias)):
            fail(502, "O provedor não confirmou o destino, a conta ou o endereço. Consulte seu histórico antes de repetir.")
        return {"short_url": link, "alias": data["alias"], "long_url": original}
    if action == "list":
        page = body.get("page", 1)
        search = body.get("search", "")
        if type(page) is not int or not 1 <= page <= 10000 or not isinstance(search, str) or len(search) > 100:
            fail(422, "Página ou busca inválida.")
        params = {"page": page, "pageSize": 20, "sortBy": "created_at", "sortOrder": "desc"}
        if search.strip():
            params["filter"] = json.dumps({"search": search.strip()}, ensure_ascii=False)
        data = await provider("GET", "/urls", key, params=params)
        rows = data.get("items")
        if (not isinstance(rows, list) or len(rows) > 20 or data.get("page") != page
                or type(data.get("hasNext")) is not bool or integer(data.get("total")) is None):
            fail(502, "O histórico do Spoo.me retornou uma resposta inesperada.")
        normalized = [normalize_row(row) for row in rows]
        return {"items": [row for row in normalized if row is not None], "page": page,
                "has_next": data["hasNext"], "total": data["total"], "omitted": normalized.count(None)}
    link_id = valid_id(body.get("id"))
    path = "/urls/" + link_id
    if action == "stats":
        data = await provider("GET", "/stats/links/" + link_id, key, params={"group_by": "time", "metrics": "clicks,unique_clicks"})
        summary = data.get("summary")
        if data.get("url_id") != link_id or not isinstance(summary, dict):
            fail(502, "As estatísticas do Spoo.me retornaram uma resposta inesperada.")
        total, unique = integer(summary.get("total_clicks")), integer(summary.get("unique_clicks"))
        if total is None or unique is None:
            fail(502, "O Spoo.me não confirmou os números de cliques.")
        return {"total_clicks": total, "unique_clicks": unique, "period": "7d"}
    if action == "status":
        status = body.get("status")
        if status not in ("ACTIVE", "INACTIVE"):
            fail(422, "Estado do link inválido.")
        data = await provider("PATCH", path, key, payload={"status": status})
        if data.get("id") != link_id or data.get("status") != status:
            fail(502, "A alteração do link não foi confirmada pelo Spoo.me.")
        return {"id": link_id, "status": status}
    if action == "delete":
        data = await provider("DELETE", path, key)
        if data.get("id") != link_id:
            fail(502, "A exclusão não foi confirmada pelo Spoo.me.")
        return {"id": link_id, "deleted": True}
    fail(404, "Operação não encontrada.")

@router.get("/afiliados/{tenant_id}/api/links/status", include_in_schema=False)
async def shortener_status(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await authorize_owner(actor, tenant_id)
    return JSONResponse({"enabled": enabled(), "provider": "spoo.me"}, headers=NO_STORE)

@router.post("/afiliados/{tenant_id}/api/links/{action}", include_in_schema=False)
async def shortener_action(tenant_id: UUID, action: str, request: Request, actor: Actor = Depends(authenticate)):
    await authorize_owner(actor, tenant_id)
    if not enabled():
        fail(503, "O encurtador ainda não foi liberado pela Orion.")
    if action not in ACTIONS:
        fail(404, "Operação não encontrada.")
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
        fail(415, "Envie uma solicitação JSON.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 12 * 1024:
            fail(413, "A solicitação ultrapassou o tamanho permitido.")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        fail(422, "Solicitação inválida.")
    if not isinstance(body, dict):
        fail(422, "Solicitação inválida.")
    key = api_key(body.get("api_key"))
    await rate_limit(tenant_id)
    return JSONResponse(await execute(action, body, key), headers=NO_STORE)
