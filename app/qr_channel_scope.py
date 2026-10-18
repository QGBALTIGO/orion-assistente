"""Request-local authority for managed QR; absence preserves the legacy pilot.

Only the authenticated ingress binds a scope. Neither a model result nor a
conversation state may supply it. Revalidation is deliberately asynchronous:
an old socket's fence must not survive a provider call or a conversation lock.
"""
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from uuid import UUID

from fastapi import HTTPException

from app.qr_channels import ChannelIdentity


@dataclass(frozen=True)
class ChannelScope:
    identity: ChannelIdentity
    guard: Callable[[], Awaitable[ChannelIdentity]]


_scope: ContextVar[ChannelScope | None] = ContextVar("orion_qr_channel_scope", default=None)


def current() -> ChannelIdentity | None:
    scope = _scope.get()
    return scope.identity if scope else None


def bind(identity: ChannelIdentity, guard: Callable[[], Awaitable[ChannelIdentity]]):
    if (not isinstance(identity, ChannelIdentity) or not identity.allowed_sender
            or not identity.assistant_enabled or not callable(guard)):
        raise HTTPException(403, "Canal sem autorização para atendimento")
    return _scope.set(ChannelScope(identity, guard))


def reset(token):
    _scope.reset(token)


def tenant_scope(tenant: UUID) -> str:
    """Separate each channel's Redis records, retaining every legacy key."""
    identity = current()
    if identity is None:
        return str(tenant)
    if tenant != identity.tenant_id:
        raise ValueError("Authenticated channel tenant mismatch")
    return f"{tenant}:{identity.channel_id}"


def message_identity(message_id: str | None) -> str | None:
    """Domain idempotency/consent IDs are also isolated, not just Redis keys."""
    identity = current()
    if identity is None or message_id is None:
        return message_id
    return hashlib.sha256(f"{identity.context_namespace}\0{message_id}".encode()).hexdigest()


async def assert_current():
    scope = _scope.get()
    if scope is None:
        return
    fresh = await scope.guard()
    # Includes channel, tenant, owner membership, instance, fence and AI flag.
    # The guard also checks the current company/onboarding operational gates.
    if fresh != scope.identity or not fresh.allowed_sender or not fresh.assistant_enabled:
        raise HTTPException(409, "Autorização do canal mudou durante o atendimento")
