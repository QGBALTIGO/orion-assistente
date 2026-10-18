"""Request-local authority for managed QR; absence preserves the legacy pilot.

Only the authenticated ingress binds a scope. Neither a model result nor a
conversation state may supply it. Revalidation is deliberately asynchronous:
an old socket's fence must not survive a provider call or a conversation lock.
"""
from collections.abc import Awaitable, Callable
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.qr_channels import ChannelIdentity


@dataclass(frozen=True)
class ChannelScope:
    identity: ChannelIdentity
    guard: Callable[[], Awaitable[ChannelIdentity]]


_scope: ContextVar[ChannelScope | None] = ContextVar("orion_qr_channel_scope", default=None)


@dataclass
class TransactionHold:
    identity: ChannelIdentity
    session: object
    transaction: object
    task: object
    active: bool = True


_hold: ContextVar[TransactionHold | None] = ContextVar("orion_qr_channel_transaction", default=None)


def _held(hold, identity):
    return (hold.active and hold.identity == identity and hold.task is asyncio.current_task()
            and hold.session.in_transaction() and hold.transaction.is_active
            and hold.session.get_transaction() is hold.transaction)


def _changed():
    return HTTPException(409, "Autorização do canal mudou durante o atendimento")


async def _lock_authority(session, identity):
    from app.qr_channels import enabled
    enabled()
    valid = (await session.execute(text("""SELECT public.qr_channel_transaction_guard(
        CAST(:tenant AS uuid), CAST(:channel AS uuid), CAST(:account AS uuid),
        CAST(:owner AS uuid), CAST(:fence AS bigint))"""),
        {"tenant": identity.tenant_id, "channel": identity.channel_id,
         "account": identity.owner_account_id, "owner": identity.lease_owner,
         "fence": identity.fence})).scalar_one()
    if valid is not True:
        raise _changed()


@asynccontextmanager
async def transaction_guard(session, tenant):
    """Keep managed authority locked in the domain transaction, never across LLM I/O."""
    identity = current()
    if identity is None:
        yield
        return
    inherited = _hold.get()
    if (tenant != identity.tenant_id or not session.in_transaction()
            or inherited is not None and not _held(inherited, identity)):
        raise _changed()
    await _lock_authority(session, identity)
    hold = TransactionHold(identity, session, session.get_transaction(), asyncio.current_task())
    token = _hold.set(hold)
    try:
        yield
        if not _held(hold, current()):
            raise _changed()
        # clock_timestamp is checked again before commit; expiration rolls back
        # the entire domain operation, including writes already executed.
        await _lock_authority(session, identity)
    finally:
        hold.active = False
        _hold.reset(token)


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
    hold = _hold.get()
    if hold is not None:
        if not _held(hold, scope.identity):
            raise _changed()
        # The same transaction owns SHARE locks. Calling the support guard's
        # FOR UPDATE here would deadlock against our own runtime transaction.
        from app.qr_channels import enabled
        enabled()
        return
    fresh = await scope.guard()
    # Includes channel, tenant, owner membership, instance, fence and AI flag.
    # The guard also checks the current company/onboarding operational gates.
    if fresh != scope.identity or not fresh.allowed_sender or not fresh.assistant_enabled:
        raise _changed()
