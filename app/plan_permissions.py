"""One local entitlement resolver shared by HTTP, domain transactions and jobs.

Commercial records remain independent of manual grants. No provider HTTP or
account/session mutation belongs in this module.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.plan_catalog import ALL_FEATURES, CODES, PLAN_FEATURES, RECOVERY_FEATURES

LOCK_PREFIX = "orion-plan-v1:"


def _date(value):
    if value is None:
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _future(value, now):
    parsed = _date(value)
    return parsed is not None and parsed > now


def derive_plan(snapshot, now=None):
    """Pure authority decision. Unknown/unverified commercial codes grant nothing."""
    now = now or datetime.now(timezone.utc)
    manual, commercial, trial = (snapshot.get(key) for key in ("manual", "commercial", "trial"))
    used = max(0, int(snapshot.get("active_professionals") or 0))
    manual_code = manual.get("plan_code") if isinstance(manual, dict) else None
    manual_valid = bool(manual_code in CODES and _date(manual.get("started_at")) is not None
        and _date(manual["started_at"]) <= now
        and (manual.get("expires_at") is None or _future(manual["expires_at"], now)))
    manual_view = ({"plan_code": manual_code, "started_at": manual.get("started_at"),
                    "expires_at": manual.get("expires_at"), "active": manual_valid} if manual_code else None)
    code, source, status, until = None, "none", "no_access", None
    platform = snapshot.get("platform_workspace") is True
    if platform:
        source, status = "platform_admin", "active"
    elif manual_valid:
        code, source, status, until = manual_code, "manual", "active", manual.get("expires_at")
    elif isinstance(commercial, dict):
        mapped = next((key for key, value in CODES.items() if value["db_code"] == commercial.get("code")), None)
        paid_valid = (mapped is not None and commercial.get("cakto_bound") is True
            and _date(commercial.get("last_verified_payment_at")) is not None
            and _date(commercial["last_verified_payment_at"]) <= now
            and (commercial.get("status") == "active" or
                 commercial.get("status") == "past_due" and _future(commercial.get("grace_until"), now)))
        if paid_valid:
            code, source, status = mapped, "cakto", "active"
            until = commercial.get("grace_until") if commercial.get("status") == "past_due" else None
        else:
            status = commercial.get("status") if commercial.get("status") in {"past_due", "suspended", "canceled"} else "no_access"
    if source == "none" and isinstance(trial, dict) and trial.get("status") == "active" and _future(trial.get("ends_at"), now):
        if _date(trial.get("started_at")) is not None and _date(trial["started_at"]) <= now:
            code, source, status, until = "essencial", "trial", "active", trial["ends_at"]
    if source == "none" and status == "no_access" and ((manual_view and not manual_valid) or
            isinstance(trial, dict) and trial.get("status") in {"expired", "active"}):
        status = "expired"
    active = source != "none" and snapshot.get("tenant_status") == "active"
    if source != "none" and not active:
        status = "tenant_inactive"
    features = sorted(ALL_FEATURES if platform and active else
                      (PLAN_FEATURES.get(code, frozenset()) if active else frozenset()) | RECOVERY_FEATURES)
    maximum = CODES[code]["max_professionals"] if code else None
    result = {"code": code, "name": "Administração Orion" if platform else CODES[code]["name"] if code else "Sem plano ativo",
        "source": source, "status": status, "active": active, "valid_until": until,
        "version": int(snapshot.get("version") or 0), "platform_workspace": platform,
        "features": features, "permissions": {key: key in features for key in sorted(ALL_FEATURES)},
        "limits": {"max_professionals": maximum, "active_professionals": used,
                   "excess_professionals": max(0, used - maximum) if maximum is not None else 0},
        "manual_assignment": manual_view, "commercial_subscription": commercial, "trial": trial}
    revision_input = {**result, "tenant_id": str(snapshot.get("tenant_id"))}
    result["revision"] = hashlib.sha256(json.dumps(revision_input, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return result


async def lock_plan(db, tenant_id, exclusive=False):
    if not isinstance(tenant_id, UUID):
        raise ValueError("Verified tenant UUID required")
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await db.execute(text(f"SELECT {function}(hashtextextended(:key,0))"), {"key": LOCK_PREFIX + str(tenant_id)})


async def plan_state(db, tenant_id, lock="shared"):
    if lock not in {"shared", "exclusive", None}:
        raise ValueError("Invalid plan lock")
    if lock:
        await lock_plan(db, tenant_id, exclusive=lock == "exclusive")
    snapshot = (await db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"), {"tenant": tenant_id})).scalar_one()
    if snapshot is None:
        raise HTTPException(404, "Empresa não encontrada")
    return derive_plan(snapshot)


@dataclass(frozen=True)
class PlanScope:
    tenant_id: UUID
    features: frozenset[str]
    exclusive: bool = False
    bypass: bool = False


_scope = ContextVar("orion_plan_scope", default=None)


def bind_feature(tenant_id, feature, exclusive=False, bypass=False):
    if not isinstance(tenant_id, UUID) or feature not in ALL_FEATURES:
        raise ValueError("Verified plan context required")
    current = _scope.get()
    if current and current.tenant_id != tenant_id:
        raise HTTPException(403, "Contexto de empresa inválido")
    features = (current.features if current else frozenset()) | {feature}
    return _scope.set(PlanScope(tenant_id, frozenset(features), exclusive or bool(current and current.exclusive),
                               bypass or bool(current and current.bypass)))


def reset_feature(token):
    _scope.reset(token)


def _bypass(tenant_id):
    scope = _scope.get()
    return bool(scope and scope.tenant_id == tenant_id and scope.bypass)


async def require_feature(db, tenant_id, feature, lock="shared"):
    if feature not in ALL_FEATURES:
        raise HTTPException(403, "Funcionalidade não disponível")
    state = await plan_state(db, tenant_id, lock=lock)
    if not _bypass(tenant_id) and not state["permissions"].get(feature, False):
        raise HTTPException(403, {"code": "PLAN_FEATURE_UNAVAILABLE", "feature": feature,
            "message": "Este recurso não está disponível no plano da sua empresa.",
            "plan_code": state["code"], "source": state["source"]})
    return state


async def has_feature(db, tenant_id, feature, lock="shared"):
    state = await plan_state(db, tenant_id, lock=lock)
    return feature in ALL_FEATURES and (_bypass(tenant_id) or state["permissions"].get(feature, False))


async def resolve_plan(tenant_id):
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        async with db.begin():
            return await plan_state(db, tenant_id)


async def require_tenant_feature(tenant_id, feature):
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        async with db.begin():
            return await require_feature(db, tenant_id, feature)


async def tenant_has_feature(tenant_id, feature):
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        async with db.begin():
            return await has_feature(db, tenant_id, feature)


@asynccontextmanager
async def plan_transaction_guard(db, tenant_id):
    scope = _scope.get()
    if scope is None:
        yield
        return
    if scope.tenant_id != tenant_id:
        raise HTTPException(403, "Contexto de empresa inválido")
    if not scope.bypass:
        await lock_plan(db, tenant_id, exclusive=scope.exclusive)
        for feature in sorted(scope.features):
            await require_feature(db, tenant_id, feature, lock=None)
    yield
    if not scope.bypass:
        for feature in sorted(scope.features):
            await require_feature(db, tenant_id, feature, lock=None)
