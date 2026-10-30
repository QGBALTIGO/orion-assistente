"""Orion Home Screen web push pilot.

Isolated to the authenticated, non-impersonating platform administrator.
Subscriptions are encrypted at rest in Redis with a bounded TTL and are never
returned to a user. Only an explicitly tapped test sends a generic payload.
No customer, professional, or appointment data is included.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from app.orion_pwa_icon import icon_png
from app.panel_auth import _redis, panel_actor

router = APIRouter(tags=["Orion PWA"])
STORE_SECONDS = 90 * 86400
MAX_DEVICES = 5
PUSH_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
BASE = Path(__file__).parent / "static"


def pilot_enabled() -> bool:
    return (os.getenv("ORION_ADMIN_PUSH_ENABLED") == "1"
            and bool(os.getenv("ORION_PUSH_VAPID_PRIVATE_KEY"))
            and bool(os.getenv("INTEGRATION_FERNET_KEY")))


def _admin(identity: dict) -> UUID:
    if (identity.get("role") != "admin"
            or identity.get("auth_provider") != "legacy_admin"
            or identity.get("maintenance")
            or not identity.get("account_id")):
        raise HTTPException(403, "Notificações de teste disponíveis somente para o administrador")
    return UUID(str(identity["account_id"]))


def _require_pilot() -> None:
    if not pilot_enabled():
        raise HTTPException(503, "Teste de notificações ainda não configurado")


def _vapid_public() -> str:
    # Private key is a PKCS8 DER P-256 key base64-encoded in Railway.
    try:
        raw = base64.b64decode(os.environ["ORION_PUSH_VAPID_PRIVATE_KEY"], validate=True)
        key = serialization.load_der_private_key(raw, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("invalid_curve")
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(public).decode().rstrip("=")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "Notificações temporariamente indisponíveis") from None


def _app_key(account_id: UUID) -> str:
    return "orion:pwa:admin-push:v1:" + str(account_id)


def _endpoint_hash(endpoint: str) -> str:
    secret = os.environ["INTEGRATION_FERNET_KEY"].encode()
    return hmac.new(secret, endpoint.encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    return Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode())


def _valid_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or len(endpoint) > 1600 or len(endpoint) < 40:
        raise HTTPException(422, "Inscrição de notificações inválida")
    try:
        uri = urlsplit(endpoint)
        hostname = (uri.hostname or "").lower()
        ok = (uri.scheme == "https" and hostname.endswith(".push.apple.com")
              and uri.port in (None, 443) and not uri.username and not uri.password
              and not uri.fragment and uri.path.startswith("/")
              and not any(ord(ch) <= 32 or ch == "\\" for ch in endpoint))
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(422, "Para este piloto, utilize o aplicativo Orion instalado no iPhone")
    return endpoint


def _decode_key(value: str, expected: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}={0,2}", value):
        raise ValueError("invalid_push_key")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != expected:
        raise ValueError("invalid_push_key")
    return raw


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=20, max_length=35)


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=40, max_length=1600)
    keys: PushKeys


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=40, max_length=1600)


def _checked(subscription: Subscription) -> dict:
    endpoint = _valid_endpoint(subscription.endpoint)
    try:
        p256dh = _decode_key(subscription.keys.p256dh, 65)
        _decode_key(subscription.keys.auth, 16)
    except (ValueError, TypeError):
        raise HTTPException(422, "Chaves de notificações inválidas") from None
    if p256dh[0] != 4:
        raise HTTPException(422, "Chaves de notificações inválidas")
    return subscription.model_dump()


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(BASE / "orion-pwa.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public,max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/pwa-sw.js", include_in_schema=False)
async def service_worker():
    # No fetch caching. The worker only displays intentionally visible pushes.
    return FileResponse(BASE / "orion-pwa-sw.js", media_type="text/javascript",
        headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/",
                 "X-Content-Type-Options": "nosniff"})


@router.get("/pwa/icon-{size}.png", include_in_schema=False)
async def icon(size: int):
    try:
        payload = icon_png(size)
    except ValueError:
        raise HTTPException(404, "Ícone indisponível") from None
    return Response(payload, media_type="image/png",
        headers={"Cache-Control": "public,max-age=604800",
                 "X-Content-Type-Options": "nosniff"})


@router.get("/api/pwa/admin/config")
async def config(identity: dict = Depends(panel_actor)):
    account = _admin(identity)
    if not pilot_enabled():
        return {"enabled": False, "devices": 0}
    public = _vapid_public()
    redis = _redis()
    try:
        devices = await redis.hlen(_app_key(account))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível consultar as notificações") from None
    finally:
        await redis.aclose()
    return {"enabled": True, "devices": devices, "vapid_public_key": public}


@router.post("/api/pwa/admin/subscribe", status_code=201)
async def subscribe(subscription: Subscription, identity: dict = Depends(panel_actor)):
    account = _admin(identity)
    _require_pilot()
    checked = _checked(subscription)
    key, digest = _app_key(account), _endpoint_hash(checked["endpoint"])
    encrypted = _fernet().encrypt(json.dumps(checked, separators=(",", ":")).encode()).decode()
    redis = _redis()
    try:
        accepted = await redis.eval("""
          if redis.call('HEXISTS',KEYS[1],ARGV[1])==0
              and redis.call('HLEN',KEYS[1])>=tonumber(ARGV[3]) then return 0 end
          redis.call('HSET',KEYS[1],ARGV[1],ARGV[2])
          redis.call('EXPIRE',KEYS[1],ARGV[4])
          return 1
        """, 1, key, digest, encrypted, MAX_DEVICES, STORE_SECONDS)
        if not accepted:
            raise HTTPException(409, "Limite de dispositivos atingido")
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível ativar as notificações") from None
    finally:
        await redis.aclose()
    return {"subscribed": True}


@router.post("/api/pwa/admin/unsubscribe")
async def unsubscribe(device: Device, identity: dict = Depends(panel_actor)):
    account = _admin(identity)
    _require_pilot()
    endpoint = _valid_endpoint(device.endpoint)
    redis = _redis()
    try:
        await redis.hdel(_app_key(account), _endpoint_hash(endpoint))
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível desativar as notificações") from None
    finally:
        await redis.aclose()
    return {"unsubscribed": True}


@router.post("/api/pwa/admin/test", status_code=202)
async def send_test(device: Device, identity: dict = Depends(panel_actor)):
    account = _admin(identity)
    _require_pilot()
    endpoint = _valid_endpoint(device.endpoint)
    digest, storage = _endpoint_hash(endpoint), _app_key(account)
    redis = _redis()
    try:
        # The exact subscribed device must belong to the authenticated admin.
        saved = await redis.hget(storage, digest)
        if not saved:
            raise HTTPException(409, "Ative as notificações neste dispositivo primeiro")
        limit = await redis.eval("""
          local n=redis.call('INCR',KEYS[1])
          if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end
          return n
        """, 1, "orion:pwa:test-limit:v1:" + str(account), 600)
        if limit > 3:
            raise HTTPException(429, "Limite de testes atingido. Tente mais tarde")
    except (RedisError, OSError):
        raise HTTPException(503, "Envio temporariamente indisponível") from None
    finally:
        await redis.aclose()
    try:
        subscription = json.loads(_fernet().decrypt(saved.encode()))
        if subscription.get("endpoint") != endpoint:
            raise ValueError("subscription_changed")
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise HTTPException(409, "Reative as notificações neste dispositivo") from None

    def dispatch():
        from pywebpush import webpush
        return webpush(
            subscription_info=subscription,
            data=json.dumps({"type": "orion-admin-test"}, separators=(",", ":")),
            vapid_private_key=os.environ["ORION_PUSH_VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": "https://orionagenda.pro"},
            content_encoding="aes128gcm", timeout=8, ttl=120,
        )
    try:
        # Third-party push is done out of the request event loop.
        await asyncio.wait_for(asyncio.to_thread(dispatch), timeout=10)
    except Exception as error:
        # Never include remote push URLs, keys or provider responses in logs.
        status = getattr(error, "status_code", None)
        if status in (404, 410):
            cache = _redis()
            try:
                await cache.hdel(storage, digest)
            except (RedisError, OSError):
                pass
            finally:
                await cache.aclose()
            raise HTTPException(410, "A inscrição expirou. Ative novamente.") from None
        raise HTTPException(503, "O serviço de notificações não confirmou o envio") from None
    # Push gateway acceptance is not evidence of iPhone display/read.
    return {"accepted_by_push_service": True}
