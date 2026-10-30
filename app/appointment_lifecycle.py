"""Trusted appointment audit metadata and optimistic mutation guards.

These helpers do not open transactions or contact another service. Context is
constructed by authenticated adapters, never deserialized from a request body.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

REQUESTERS = frozenset({"customer", "professional", "business", "system", "unknown"})
SOURCES = frozenset({"panel", "whatsapp_qr", "whatsapp_meta", "public_site", "system", "unknown"})
ACTOR_ROLES = frozenset({"support", "admin", "owner", "editor", "viewer", "customer", "professional", "system", "unknown"})
CAS_SQL = """(CAST(:expected_updated_at AS timestamptz) IS NULL
    OR updated_at=CAST(:expected_updated_at AS timestamptz))"""


@dataclass(frozen=True)
class LifecycleActor:
    role: str = "unknown"
    source: str = "unknown"
    account_id: UUID | None = None
    customer_id: UUID | None = None
    professional_id: UUID | None = None


SYSTEM_ACTOR = LifecycleActor(role="system", source="system")


def customer_can_change(appointment, now):
    """Inclusive deadline. The caller supplies a trusted server/database clock."""
    if now.tzinfo is None or appointment["start_at"].tzinfo is None:
        raise ValueError("aware_clock_required")
    return (appointment["status"] == "confirmed"
            and now <= appointment["start_at"] - timedelta(minutes=60))


async def require_customer_deadline(db, appointment, context):
    if context.role not in {"customer", "professional"} or appointment["status"] == "hold":
        return
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if not customer_can_change(appointment, now):
        raise HTTPException(409, "Seu agendamento está próximo e não pode mais ser alterado por esta página. Para solicitar ajuda, entre em contato com a empresa.")


def panel_actor(actor):
    role = actor.membership_role if actor.role == "member" else actor.role
    return LifecycleActor(role=role or "unknown", source="panel", account_id=actor.account_id)


def customer_actor(customer_id, source):
    return LifecycleActor(role="customer", source=source, customer_id=customer_id)


def resolved_actor(context, source, customer_id, *, verified_customer=False):
    if context is not None:
        return context
    if verified_customer and source in {"whatsapp_qr", "whatsapp_meta"}:
        return customer_actor(customer_id, source)
    return LifecycleActor(source=source)


def metadata(context=None, *, requested_by=None, reason=None, previous=None):
    context = context or LifecycleActor()
    if context.role not in ACTOR_ROLES or context.source not in SOURCES:
        raise ValueError("Unknown trusted appointment actor")
    requester = requested_by or (context.role if context.role in {"customer", "system"} else "unknown")
    if requester not in REQUESTERS:
        raise ValueError("Unknown appointment requester")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 1000 or "\x00" in reason):
        raise HTTPException(422, "Informe um motivo de até 1000 caracteres")
    details = {
        "v": 1, "requested_by": requester, "reason": reason.strip() or None if reason is not None else None,
        "source": context.source,
        "actor": {"role": context.role, "account_id": context.account_id, "customer_id": context.customer_id},
        "previous": {key: previous[key] for key in ("status", "start_at", "end_at", "professional_id")
                     if key in previous} if previous is not None else None,
    }
    return jsonable_encoder(details)


def public_history(row):
    """Old events have no reliable attribution, including the old operator field."""
    raw = row.get("details")
    details = raw if isinstance(raw, dict) and raw.get("v") == 1 else {}
    actor = details.get("actor") if isinstance(details.get("actor"), dict) else {}
    return {
        "id": row["id"], "event": row["event"], "occurred_at": row["occurred_at"],
        "requested_by": details.get("requested_by") if details.get("requested_by") in REQUESTERS else "unknown",
        "reason": details.get("reason") if isinstance(details.get("reason"), str) else None,
        "source": details.get("source") if details.get("source") in SOURCES else "unknown",
        "actor": {"role": actor.get("role") if actor.get("role") in ACTOR_ROLES else "unknown",
                  "account_id": actor.get("account_id"), "customer_id": actor.get("customer_id")},
        "previous": details.get("previous") if isinstance(details.get("previous"), dict) else None,
    }


def check_expected_updated_at(row, expected):
    if expected is None:
        return  # Compatibility for authenticated channel adapters using snapshots.
    if not isinstance(expected, datetime) or expected.tzinfo is None or expected.utcoffset() is None:
        raise HTTPException(422, "Envie a versão do agendamento com fuso explícito")
    if row["updated_at"] != expected:
        raise HTTPException(409, "O agendamento foi alterado. Atualize os detalhes antes de continuar")


def operation_payload(appointment_id, expected_updated_at, context, **fields):
    payload = {"appointment_id": appointment_id, **fields}
    if expected_updated_at is not None:
        payload["expected_updated_at"] = expected_updated_at.astimezone(timezone.utc)
    if context is not None:
        payload["actor"] = metadata(context)["actor"]
        payload["source"] = context.source
    return payload


async def operation_replayed(db, tenant, operation, key, payload):
    """Check an immutable receipt before CAS, without writing failed operations."""
    if key is None:
        return False
    existing = (await db.execute(text("""
        SELECT fingerprint FROM management_requests
        WHERE tenant_id=:tenant AND operation=:operation AND idempotency_key=:key
    """), {"tenant": tenant, "operation": operation, "key": key})).scalar_one_or_none()
    if existing is None:
        return False
    fingerprint = hashlib.sha256(json.dumps(jsonable_encoder(payload), ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if existing != fingerprint:
        raise HTTPException(409, "Esta chave de operação já foi utilizada com outros dados")
    return True


async def record_operation(db, tenant, appointment, operation, key, payload):
    if key is not None:
        # Reuse the tenant-scoped immutable receipt table and conflict contract.
        from app.management_models import request_identity
        await request_identity(db, tenant, operation, key, payload, entity_id=appointment)


def allowed_actions(appointment, *, can_write, now=None):
    if not can_write:
        return []
    now = now or datetime.now(timezone.utc)
    status = appointment["status"]
    if status == "hold":
        if appointment["hold_expires_at"] is None or appointment["hold_expires_at"] <= now:
            return []
        return ["confirm", "cancel"]
    if status != "confirmed":
        return []
    actions = ["cancel", "reschedule"]
    if appointment["start_at"] <= now:
        actions.extend(["complete", "no_show"])
    return actions
