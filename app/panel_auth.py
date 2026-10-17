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
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import from_url
from redis.exceptions import RedisError

router = APIRouter(prefix="/auth", tags=["Sessão do painel"])
COOKIE = "__Host-orion_session"
TTL = 12 * 3600
REMEMBER_TTL = 30 * 24 * 3600


def _redis():
    return from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                    decode_responses=True, socket_timeout=3)


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
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) != 43:
        raise HTTPException(401, "Faça login no painel")
    redis = _redis()
    try:
        content = await redis.get(_hash_cookie(token))
    except (RedisError, OSError):
        raise HTTPException(503, "Sessões indisponíveis") from None
    finally:
        await redis.aclose()
    if not content:
        raise HTTPException(401, "Sessão expirada, entre novamente")
    data = json.loads(content)
    if data.get("account_id"):
        from app.account_auth import current_account
        try:
            row = await current_account(data["account_id"], data["auth_version"])
        except (ValueError,KeyError):
            raise HTTPException(401,"Sessão inválida") from None
        # A changed role/password invalidates prior sessions.
        if row["role"] != data["role"]:
            raise HTTPException(401,"Sessão expirada") from None
        # Return only this account's display identity; never change the privilege role.
        data["name"] = row["name"]
        data["email"] = row["email"]
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
    redis = _redis()
    try:
        attempts = int(await redis.get(ip_key) or "0")
        if attempts >= 8:
            raise HTTPException(429, "Muitas tentativas. Aguarde quinze minutos")
        valid_user = secrets.compare_digest(
            payload.username.strip().lower(), os.getenv("PANEL_ADMIN_USERNAME", "admin").lower()
        )
        legacy_admin = valid_user and _password_valid(payload.password)
        account = None
        if not legacy_admin:
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
        await redis.set(_hash_cookie(token),json.dumps(claims),ex=lifespan,nx=True)
        response.set_cookie(COOKIE,token,max_age=lifespan,secure=True,
                            httponly=True,samesite="strict",path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"ok":True,"csrf":csrf,"role":claims["role"],
                "name":account["name"] if account else "Administrador"}
    except (RedisError, OSError):
        raise HTTPException(503, "Login temporariamente indisponível") from None
    finally:
        await redis.aclose()


@router.get("/me")
async def me(data: dict = Depends(panel_actor)):
    return {"authenticated": True, "role": data["role"], "csrf": data["csrf"],
            "account":bool(data.get("account_id")),
            "name":data.get("name","Administrador"),
            "email":data.get("email")}


@router.post("/logout")
async def logout(request: Request, response: Response, _: dict = Depends(panel_actor)):
    redis = _redis()
    try:
        await redis.delete(_hash_cookie(request.cookies[COOKIE]))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível encerrar a sessão") from None
    finally:
        await redis.aclose()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
