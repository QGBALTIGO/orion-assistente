"""Audited, short-lived maintenance access to a customer's own Orion account.

The platform owner keeps their original HttpOnly admin cookie. A second,
Redis-only record scoped to that cookie temporarily derives the customer's
permissions on each request. No customer password, token or browser URL is
generated. Business permissions continue to be checked in PostgreSQL.
"""
import hashlib
import hmac
import json
import os
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import text

from app.account_auth import _storage, current_account, current_account_and_membership
from app.admin_accounts import require_platform_owner
from app.panel_auth import COOKIE, ROUTE_MEMBERSHIP, VerifiedRouteMembership, _request_redis, panel_actor

router = APIRouter(prefix="/auth/maintenance", tags=["Acesso de manutenção"])
admin_router = APIRouter(prefix="/api/v1/admin/accounts", tags=["Administração de clientes"])

MAINTENANCE_SECONDS = 30 * 60
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def maintenance_key(cookie):
    if not cookie or len(cookie) != 43:
        raise HTTPException(401, "Sessão administrativa inválida")
    return "orion:maintenance:" + hashlib.sha256(cookie.encode()).hexdigest()


def maintenance_guard_key(cookie):
    if not cookie or len(cookie) != 43:
        raise HTTPException(401, "Sessão administrativa inválida")
    return "orion:maintenance-guard:" + hashlib.sha256(cookie.encode()).hexdigest()


def _platform_owner(claims):
    if claims.get("role") != "admin" or not claims.get("account_id"):
        return False
    if claims.get("auth_provider") == "legacy_admin":
        return True
    email = str(claims.get("email") or "").strip().lower()
    owner = os.getenv("PANEL_OWNER_EMAIL", "").strip().lower()
    return bool(owner and claims.get("email_verified") is True
                and hmac.compare_digest(email, owner))


class MaintenanceStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)
    confirmation: str = Field(pattern=r"^ACESSAR$")


@admin_router.post("/{account_id}/maintenance")
async def start_maintenance(account_id: UUID, payload: MaintenanceStart, request: Request,
                            admin: UUID = Depends(require_platform_owner)):
    """Explicit, logged confirmation, one target per admin browser session."""
    cookie = request.cookies.get(COOKIE, "")
    key = maintenance_key(cookie)
    guard_key = maintenance_guard_key(cookie)
    reason = " ".join(payload.reason.split())
    if len(reason) < 10:
        raise HTTPException(422, "Informe a finalidade da manutenção")
    redis, owned = _request_redis(request)
    created = False
    guard_created = False
    try:
        if await redis.exists(key):
            raise HTTPException(409, "Encerre o atendimento atual antes de acessar outra conta")
        if await redis.exists(guard_key):
            raise HTTPException(409, "Atualize o painel após a manutenção anterior")
        # The guard outlives the 30-minute access for as long as the admin
        # cookie is valid. A client page must never silently gain the original
        # admin's permissions when the temporary record expires.
        parent_ttl = int(await redis.ttl("orion:session:" + hashlib.sha256(cookie.encode()).hexdigest()))
        if parent_ttl <= 0:
            raise HTTPException(401, "Sessão de administrador expirada")
        async with _storage()() as db:
            async with db.begin():
                row = (await db.execute(text("""
                    SELECT a.id,a.name,a.email,a.auth_version,a.registration_pending,
                           COALESCE(c.state,'active') AS state,
                           (SELECT count(*) FROM tenant_memberships m
                            WHERE m.account_id=a.id AND m.active) AS company_count,
                           (SELECT t.name FROM tenant_memberships m
                            JOIN tenants t ON t.id=m.tenant_id
                            WHERE m.account_id=a.id AND m.active ORDER BY t.id LIMIT 1) AS company
                    FROM panel_accounts a LEFT JOIN admin_account_controls c ON c.account_id=a.id
                    WHERE a.id=:target AND a.role='member'
                      AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
                    FOR SHARE OF a
                """), {"target": account_id})).mappings().first()
                if not row:
                    raise HTTPException(404, "Conta de cliente não encontrada")
                if row["auth_version"] != payload.expected_version:
                    raise HTTPException(409, "A conta mudou. Atualize a ficha e tente novamente")
                if row["state"] != "active" or row["registration_pending"]:
                    raise HTTPException(409, "Contas suspensas, arquivadas ou não confirmadas não permitem manutenção")
                if row["company_count"] > 1:
                    raise HTTPException(409, "O vínculo da empresa precisa ser regularizado antes")
                expires = int(time.time()) + MAINTENANCE_SECONDS
                session_data = {
                    "actor_id": str(admin), "account_id": str(account_id),
                    "auth_version": row["auth_version"],
                    "company": row["company"] or "Empresa ainda não cadastrada",
                    "expires_at": expires, "reason": reason,
                }
                await db.execute(text("""
                    INSERT INTO admin_account_audit(actor_id,account_id,action,reason,metadata)
                    VALUES(:actor,:target,'maintenance_start',:reason,
                           CAST(:metadata AS jsonb))
                """), {"actor": admin, "target": account_id, "reason": reason,
                        "metadata": json.dumps({"expires_at": expires})})
                # Set the anti-escalation guard BEFORE enabling the target.
                # If Redis fails between writes, no unguarded maintenance
                # access can become active.
                guard_created = bool(await redis.set(
                    guard_key, "1", ex=max(parent_ttl, MAINTENANCE_SECONDS),
                    nx=True,
                ))
                if not guard_created:
                    raise HTTPException(409, "Atualize o painel após a manutenção anterior")
                created = bool(await redis.set(
                    key, json.dumps(session_data, ensure_ascii=False),
                    ex=MAINTENANCE_SECONDS, nx=True,
                ))
                if not created:
                    raise HTTPException(409, "Já existe um acesso de manutenção neste navegador")
        return {"ok": True, "expires_at": expires,
                "message": "Acesso de manutenção registrado. Nenhuma senha foi compartilhada."}
    except (RedisError, OSError):
        if created or guard_created:
            try:
                await redis.delete(key, guard_key)
            except (RedisError, OSError):
                pass
        raise HTTPException(503, "Acesso temporário indisponível") from None
    except Exception:
        if created or guard_created:
            try:
                await redis.delete(key, guard_key)
            except (RedisError, OSError):
                pass
        raise
    finally:
        if owned:
            await redis.aclose()


async def effective_maintenance_claims(request: Request, admin_claims: dict, raw: str):
    """Reauthorize BOTH the real admin and the current customer on EVERY request."""
    if not _platform_owner(admin_claims):
        raise HTTPException(403, "Acesso administrativo inválido")
    try:
        record = json.loads(raw)
        target = UUID(record["account_id"])
        actor = UUID(record["actor_id"])
        version = int(record["auth_version"])
        until = int(record["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Sessão de manutenção inválida") from None
    if str(actor) != str(admin_claims["account_id"]) or until <= int(time.time()):
        raise HTTPException(401, "O acesso de manutenção expirou") from None
    tenant = None
    supplied_tenant = request.path_params.get("tenant_id")
    if supplied_tenant is not None:
        try:
            tenant = UUID(str(supplied_tenant))
        except (ValueError, TypeError):
            raise HTTPException(403, "Empresa inválida") from None
    try:
        row = (await current_account_and_membership(str(target), version, tenant)
               if tenant is not None else await current_account(str(target), version))
    except (HTTPException, ValueError):
        raise HTTPException(401, "O cliente não está mais disponível para manutenção") from None
    if row["role"] != "member" or row.get("registration_pending"):
        raise HTTPException(403, "Acesso de manutenção não autorizado")
    if tenant is not None:
        if row["membership_role"] is None:
            raise HTTPException(403, "A empresa não pertence à conta selecionada")
        request.scope[ROUTE_MEMBERSHIP] = VerifiedRouteMembership(
            target, tenant, row["membership_role"],
        )
    result = dict(admin_claims)
    result.update({
        "role": "member", "auth_provider": "password",
        "account_id": str(target), "auth_version": version,
        "name": row["name"], "email": row["email"],
        "email_verified": row.get("verified_at") is not None,
        "maintenance": {
            "active": True, "company": record["company"],
            "expires_at": until,
        },
        "maintenance_actor_id": str(actor),
        "maintenance_reason": str(record["reason"])[:500],
    })
    return result



def end_maintenance_claims(admin_claims: dict, raw: str):
    """Permit admin to end maintenance even after target suspension/revocation."""
    if not _platform_owner(admin_claims):
        raise HTTPException(403, "Administrador inválido")
    try:
        record = json.loads(raw)
        actor = UUID(record["actor_id"])
        target = UUID(record["account_id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Sessão de manutenção inválida") from None
    if str(actor) != str(admin_claims["account_id"]):
        raise HTTPException(403, "Acesso de manutenção não autorizado")
    claims = dict(admin_claims)
    claims.update({
        "account_id": str(target),
        "maintenance_actor_id": str(actor),
        "maintenance_reason": str(record.get("reason") or "Manutenção administrativa")[:500],
        "maintenance": {
            "active": True,
            "company": str(record.get("company") or "Cliente")[:160],
            "expires_at": int(record.get("expires_at") or 0),
        },
    })
    return claims


async def revoke_invalid_maintenance(request: Request, admin_claims: dict, raw: str):
    """An invalid customer must not trap the platform admin inside a dead session."""
    claims = end_maintenance_claims(admin_claims, raw)
    cookie = request.cookies.get(COOKIE, "")
    key = maintenance_key(cookie)
    redis, owned = _request_redis(request)
    try:
        await redis.delete(key)
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível revogar a manutenção") from None
    finally:
        if owned:
            await redis.aclose()
    async with _storage()() as db:
        async with db.begin():
            await db.execute(text("""
                INSERT INTO admin_account_audit(actor_id,account_id,action,reason)
                VALUES(:actor,:target,'maintenance_auto_end',:reason)
            """), {
                "actor": UUID(claims["maintenance_actor_id"]),
                "target": UUID(claims["account_id"]),
                "reason": "Acesso encerrado automaticamente por alteração ou indisponibilidade da conta",
            })


async def audit_maintenance_request(request: Request, claims: dict):
    """Log mutating support requests before dispatch; fail closed without audit."""
    if not claims.get("maintenance") or request.method in SAFE_METHODS:
        return
    if request.url.path == "/auth/maintenance/end":
        return
    async with _storage()() as db:
        async with db.begin():
            await db.execute(text("""
                INSERT INTO admin_account_audit(actor_id,account_id,action,reason,metadata)
                VALUES(:actor,:target,'maintenance_request',:reason,
                       CAST(:metadata AS jsonb))
            """), {
                "actor": UUID(claims["maintenance_actor_id"]),
                "target": UUID(claims["account_id"]),
                "reason": claims["maintenance_reason"],
                "metadata": json.dumps({
                    "method": request.method,
                    "path": request.url.path[:400],
                    "kind": "attempt_before_dispatch",
                }),
            })


@router.post("/end")
async def end_maintenance(request: Request, data: dict = Depends(panel_actor)):
    """Return to original admin identity without logging out or changing passwords."""
    if not data.get("maintenance") or not data.get("maintenance_actor_id"):
        raise HTTPException(409, "Nenhum acesso de manutenção ativo")
    cookie = request.cookies.get(COOKIE, "")
    key = maintenance_key(cookie)
    guard = maintenance_guard_key(cookie)
    redis, owned = _request_redis(request)
    try:
        if not await redis.delete(key):
            raise HTTPException(409, "A sessão de manutenção já terminou")
        await redis.delete(guard)
    except (RedisError, OSError):
        raise HTTPException(503, "Não foi possível encerrar a manutenção") from None
    finally:
        if owned:
            await redis.aclose()
    async with _storage()() as db:
        async with db.begin():
            await db.execute(text("""
                INSERT INTO admin_account_audit(actor_id,account_id,action,reason)
                VALUES(:actor,:target,'maintenance_end',:reason)
            """), {
                "actor": UUID(data["maintenance_actor_id"]),
                "target": UUID(data["account_id"]),
                "reason": str(data["maintenance_reason"])[:500],
            })
    return {"ok": True, "message": "Acesso encerrado. Sua conta de administrador foi restaurada."}
