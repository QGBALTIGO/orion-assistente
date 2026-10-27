"""Painel web: senha administrativa + sessão opaca no Redis, sem token técnico no navegador.

Somente homologação para operador do Orion. O token SUPPORT_API_TOKEN continua
restrito a integrações servidor e clientes internos autorizados.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import from_url
from redis.exceptions import RedisError

router = APIRouter(prefix="/auth", tags=["Sessão do painel"])
COOKIE = "__Host-orion_session"
TTL = 12 * 3600
REMEMBER_TTL = 30 * 24 * 3600


@dataclass(frozen=True)
class VerifiedRouteMembership:
    account_id: UUID
    tenant_id: UUID
    role: str | None


ROUTE_MEMBERSHIP = "orion.verified_route_membership"


def _redis():
    return from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                    decode_responses=True, socket_timeout=3, max_connections=100)


async def start_session_store(application):
    """One connection pool per application worker; session claims are never cached."""
    if getattr(application.state, "panel_session_redis", None) is not None:
        raise RuntimeError("Session store already started")
    application.state.panel_session_redis = _redis()


async def close_session_store(application):
    redis = getattr(application.state, "panel_session_redis", None)
    if redis is not None:
        del application.state.panel_session_redis
        await redis.aclose()


def _request_redis(request):
    state = getattr(request.scope.get("app"), "state", None)
    redis = getattr(state, "panel_session_redis", None)
    # ASGI callers without a lifespan still own and close their short-lived client.
    return (redis, False) if redis is not None else (_redis(), True)


def _hash_cookie(token: str) -> str:
    return "orion:session:" + hashlib.sha256(token.encode()).hexdigest()


def _password_valid(password: str) -> bool:
    encoded = os.getenv("PANEL_ADMIN_PASSWORD_HASH", "")
    try:
        algo, iterations, salt, wanted = encoded.split("$")
        if algo != "pbkdf2_sha256" or not 200_000 <= int(iterations) <= 800_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.urlsafe_b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(wanted))
    except (ValueError, TypeError):
        return False


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.scheme not in ("https", "http") or parsed.netloc != host:
            raise HTTPException(403, "Origem não autorizada")


async def panel_actor(request: Request) -> dict:
    """Cookie aleatório armazenado somente como hash; CSRF em toda mutação."""
    # Only this invocation can issue the request-local proof. Redis claims,
    # prior calls and another request's scope never provide membership authority.
    request.scope.pop(ROUTE_MEMBERSHIP, None)
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) != 43:
        raise HTTPException(401, "Faça login no painel")
    redis, owned = _request_redis(request)
    try:
        content = await redis.get(_hash_cookie(token))
    except (RedisError, OSError):
        raise HTTPException(503, "Sessões indisponíveis") from None
    finally:
        if owned:
            await redis.aclose()
    if not content:
        raise HTTPException(401, "Sessão expirada, entre novamente")
    data = json.loads(content)
    # Display metadata is derived from the current database identity, never
    # trusted from old Redis claims and never used as invitation authority.
    data["email_verified"] = False
    if data.get("auth_provider") == "legacy_admin":
        from app.legacy_admin_identity import current_identity
        if data.get("role") != "admin" or not data.get("account_id"):
            raise HTTPException(401, "Sessão inválida")
        try:
            row = await current_identity(data["account_id"], data["auth_version"])
        except (ValueError, KeyError):
            raise HTTPException(401, "Sessão inválida") from None
        data["name"] = row["name"]
        data["email"] = None
    elif data.get("auth_provider") not in (None, "password"):
        raise HTTPException(401, "Sessão inválida")
    elif data.get("account_id"):
        from app.account_auth import current_account, current_account_and_membership
        tenant = None
        if data.get("role") == "member" and os.getenv("BUSINESS_PROFILES_ENABLED", "0") == "1":
            try:
                tenant = UUID(str(request.path_params.get("tenant_id")))
            except (ValueError, TypeError):
                pass
        try:
            row = (await current_account_and_membership(data["account_id"], data["auth_version"], tenant)
                   if tenant is not None else
                   await current_account(data["account_id"], data["auth_version"]))
        except (ValueError,KeyError):
            raise HTTPException(401,"Sessão inválida") from None
        # A changed role/password invalidates prior sessions.
        if row["role"] != data["role"]:
            raise HTTPException(401,"Sessão expirada") from None
        if tenant is not None:
            request.scope[ROUTE_MEMBERSHIP] = VerifiedRouteMembership(
                UUID(data["account_id"]), tenant, row["membership_role"])
        # Return only this account's display identity; never change the privilege role.
        data["name"] = row["name"]
        data["email"] = row["email"]
        data["email_verified"] = row.get("verified_at") is not None
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        _same_origin(request)
        value = request.headers.get("x-orion-csrf", "")
        if not value or not secrets.compare_digest(value, data["csrf"]):
            raise HTTPException(403, "Confirmação de segurança ausente")
    return data


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


@router.post("/login")
async def login(payload: Login, request: Request, response: Response):
    _same_origin(request)
    if not os.getenv("PANEL_ADMIN_PASSWORD_HASH"):
        raise HTTPException(503, "Senha do painel não configurada")
    # Único administrador de homologação, sem múltiplos usuários e sem dados reais.
    client_ip = request.client.host if request.client else "unknown"
    ip_key = "orion:login:fail:" + hashlib.sha256(client_ip.encode()).hexdigest()
    redis, owned = _request_redis(request)
    try:
        attempts = int(await redis.get(ip_key) or "0")
        if attempts >= 8:
            raise HTTPException(429, "Muitas tentativas. Aguarde quinze minutos")
        valid_user = secrets.compare_digest(
            payload.username.strip().lower().encode("utf-8"),
            os.getenv("PANEL_ADMIN_USERNAME", "admin").strip().lower().encode("utf-8")
        )
        legacy_admin = valid_user and _password_valid(payload.password)
        account = None
        if legacy_admin:
            from app.legacy_admin_identity import login_identity
            account = await login_identity()
        else:
            # Registered accounts receive their true role, NEVER a blanket support role.
            from app.account_auth import authenticate_account
            account = await authenticate_account(payload.username,payload.password)
        if not (legacy_admin or account):
            pipe = redis.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 900)
            await pipe.execute()
            raise HTTPException(401, "E-mail ou senha incorretos, ou conta ainda não verificada")
        await redis.delete(ip_key)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        lifespan = REMEMBER_TTL if payload.remember else TTL
        claims={"role": account["role"] if account else "support","csrf":csrf}
        if account:
            claims.update({"account_id":account["id"],"auth_version":account["version"]})
            if account.get("auth_provider") == "legacy_admin":
                claims["auth_provider"] = "legacy_admin"
        await redis.set(_hash_cookie(token),json.dumps(claims),ex=lifespan,nx=True)
        response.set_cookie(COOKIE,token,max_age=lifespan,secure=True,
                            httponly=True,samesite="strict",path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"ok":True,"csrf":csrf,"role":claims["role"],
                "name":account["name"] if account else "Administrador"}
    except (RedisError, OSError):
        raise HTTPException(503, "Login temporariamente indisponível") from None
    finally:
        if owned:
            await redis.aclose()


@router.get("/me")
async def me(data: dict = Depends(panel_actor)):
    return {"authenticated": True, "role": data["role"], "csrf": data["csrf"],
            "account":bool(data.get("account_id")),
            "name":data.get("name","Administrador"),
            "email":data.get("email"),"email_verified":data["email_verified"]}


@router.post("/logout")
async def logout(request: Request, response: Response, _: dict = Depends(panel_actor)):
    redis, owned = _request_redis(request)
    try:
        await redis.delete(_hash_cookie(request.cookies[COOKIE]))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível encerrar a sessão") from None
    finally:
        if owned:
            await redis.aclose()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
