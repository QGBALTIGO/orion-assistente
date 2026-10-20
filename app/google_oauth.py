"""Short-lived, initiator-bound OAuth state. No tokens leave the server."""
import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from redis.asyncio import from_url

from app.calendar_provider import GOOGLE_SCOPES
from app.catalog_api import Actor
from app.panel_auth import COOKIE as SESSION_COOKIE, _hash_cookie
from app.tenant_access import resolve_tenant_access

OAUTH_TTL = 600
OAUTH_COOKIE = "__Secure-orion_google_oauth"
OAUTH_PATH = "/integrations/google/callback"


def _valid_callback(redirect, public_value):
    """One callback/origin contract for runtime and the local checklist."""
    try:
        parsed = urlsplit(redirect)
        public = urlsplit(public_value)
        return bool(parsed.scheme == "https" and parsed.hostname and parsed.netloc
            and not parsed.username and not parsed.password and parsed.path == OAUTH_PATH
            and not parsed.query and not parsed.fragment
            and (not public_value or public.scheme == "https" and public.netloc == parsed.netloc))
    except ValueError:
        return False


def oauth_config():
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "")
    key = os.getenv("INTEGRATION_FERNET_KEY", "")
    public_value = os.getenv("PANEL_PUBLIC_URL", "")
    if not all((cid, secret, redirect, key)) or not _valid_callback(redirect, public_value):
        raise HTTPException(503, "A conexão com o Google ainda precisa ser configurada pela equipe Orion")
    try:
        Fernet(key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "A proteção da integração Google precisa ser configurada pela equipe Orion") from None
    return cid, secret, redirect, key


def google_configured():
    try:
        oauth_config()
    except (HTTPException, ValueError):
        return False
    return True


def configuration_checklist(*, worker=False):
    """Safe local readiness facts. Presence is not authorization or delivery."""
    checks = []
    for name, label in (("GOOGLE_CLIENT_ID", "Identificação do aplicativo Google"),
                        ("GOOGLE_CLIENT_SECRET", "Credencial privada do aplicativo"),
                        ("INTEGRATION_FERNET_KEY", "Proteção das autorizações salvas")):
        ready = bool(os.getenv(name, "").strip())
        if name == "INTEGRATION_FERNET_KEY" and ready:
            try:
                Fernet(os.environ[name].encode())
            except (ValueError, TypeError):
                ready = False
        checks.append({"id": name, "label": label, "ready": ready})
    if not worker:
        valid_redirect = _valid_callback(os.getenv("GOOGLE_REDIRECT_URI", ""), os.getenv("PANEL_PUBLIC_URL", ""))
        checks.append({"id": "GOOGLE_REDIRECT_URI", "label": "Endereço seguro de retorno ao Orion", "ready": valid_redirect})
    configured = all(item["ready"] for item in checks)
    return {"checks": checks, "configured": configured,
            "sync_enabled": os.getenv("GOOGLE_LIVE_SYNC") == "1",
            "external_authorization_verified": False,
            "next_step": "authorize_account" if configured else "prepare_server",
            "direction": "orion_to_google"}


def oauth_redis():
    return from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                    decode_responses=True, socket_timeout=3, socket_connect_timeout=3)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def request_initiator(request: Request, actor: Actor) -> dict:
    authorization = request.headers.get("authorization", "")
    support = os.getenv("SUPPORT_API_TOKEN", "")
    if actor.role == "support" and len(support) >= 32 and \
            secrets.compare_digest(authorization, "Bearer " + support):
        return {"kind": "operator", "key": digest(support), "role": "support"}
    token = request.cookies.get(SESSION_COOKIE, "")
    if len(token) != 43:
        raise HTTPException(401, "Entre no painel para conectar o Google")
    return {"kind": "session", "key": _hash_cookie(token), "role": actor.role,
            "account_id": str(actor.account_id) if actor.account_id else None}


def candidate_key(tenant, initiator) -> str:
    return "google:candidate:" + str(tenant) + ":" + digest(initiator["kind"] + ":" + initiator["key"])


async def validate_initiator(redis, intent, tenant):
    """Recheck session/account and current membership before using a grant."""
    source = intent.get("initiator", {})
    if source.get("kind") == "operator":
        support = os.getenv("SUPPORT_API_TOKEN", "")
        if len(support) < 32 or not secrets.compare_digest(source.get("key", ""), digest(support)):
            raise HTTPException(403, "A autorização do operador mudou. Inicie a conexão novamente")
        actor = Actor("support")
    elif source.get("kind") == "session":
        raw = await redis.get(source.get("key", ""))
        try:
            current = json.loads(raw) if raw else {}
            if not isinstance(current, dict) or current.get("role") != source.get("role") or \
                    current.get("account_id") != source.get("account_id"):
                raise ValueError()
            account_id = UUID(current["account_id"]) if current.get("account_id") else None
            if account_id:
                from app.account_auth import current_account
                account = await current_account(str(account_id), current["auth_version"])
                if account["role"] != current["role"]:
                    raise ValueError()
                if account["verified_at"] is None:
                    raise HTTPException(403, "Confirme seu e-mail no Orion antes de conectar o Google Agenda")
            actor = Actor(current["role"], account_id=account_id)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(403, "Sua sessão mudou. Entre no painel e conecte o Google novamente") from None
    else:
        raise HTTPException(403, "A autorização do Google não é válida")
    await resolve_tenant_access(actor, tenant, "publish")
    return actor


async def email_confirmation_required(request, actor):
    """Preflight the same account requirement enforced again at callback."""
    if actor.account_id is None:
        return False
    from app.panel_auth import panel_actor
    from app.account_auth import current_account
    session = await panel_actor(request)
    if str(session.get("account_id")) != str(actor.account_id) or session.get("role") != actor.role:
        raise HTTPException(403, "Sua sessão mudou. Entre novamente no painel")
    account = await current_account(str(actor.account_id), session["auth_version"])
    return account["verified_at"] is None


async def pending_candidate(redis, tenant, initiator):
    raw = await redis.get(candidate_key(tenant, initiator))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(409, "Inicie a conexão com o Google novamente") from None
    if not isinstance(value, dict) or value.get("tenant_id") != str(tenant) or value.get("initiator") != initiator:
        raise HTTPException(409, "Inicie a conexão com o Google novamente")
    return value


def make_authorization_url(client_id, redirect, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES), "state": state, "access_type": "offline",
        "prompt": "select_account consent", "include_granted_scopes": "true",
        "code_challenge": challenge, "code_challenge_method": "S256",
    })


def connection_fingerprint(row) -> str:
    """Tokens are hashed inside the server; this fingerprint is never returned."""
    if row is None:
        return "absent"
    encrypted = bytes(row["refresh_token_enc"] or b"")
    identity = str(row["id"]) + "\0" + row["calendar_id"] + "\0" + row["updated_at"].isoformat() + "\0"
    return hashlib.sha256(identity.encode() + encrypted).hexdigest()
