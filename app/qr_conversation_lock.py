"""Bounded ownership of a QR conversation across slow or cancelled inference.

The Redis token is checked again at domain transaction boundaries. Renewal
does not grant channel permission, and loss never permits a stale task to
restore state over the conversation's next owner.
"""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException
from redis.exceptions import LockError


LOCK_SECONDS = 90
RENEW_SECONDS = 5
# Both bridge clients have a 19-second request deadline. Include lock waiting
# in this budget so the API cannot keep executing business work after that.
PROCESSING_SECONDS = 14
# Owner previews include the existing 25-second ASR call and never send or
# commit business actions. Keep that contract separate from live text ingress.
PREVIEW_SECONDS = 60
CLEANUP_SECONDS = 1


@dataclass
class ConversationLease:
    lock: object
    task: object
    deadline: float
    timeout: object
    active: bool = True
    lost: bool = False
    writes: bool = False


_lease: ContextVar[ConversationLease | None] = ContextVar("qr_conversation_lease", default=None)
_OWNED_SET = """
if redis.call('GET',KEYS[2])~=ARGV[1] then return 0 end
redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3])
return 1
"""


def _unavailable():
    return HTTPException(503, "Não consegui confirmar o resultado do atendimento. Confira a conversa antes de reenviar o pedido.")


def _changed():
    return HTTPException(409, "A posse desta conversa mudou durante o atendimento")


async def owns_lock():
    """Cleanup permission only; an elapsed deadline grants no business write."""
    lease = _lease.get()
    if lease is None:
        return True
    try:
        return await lease.lock.owned() is True
    except Exception:
        return False


async def assert_current():
    lease = _lease.get()
    if lease is None:
        return
    if not lease.active or lease.task is not asyncio.current_task():
        raise _changed()
    if lease.timeout.expired() or monotonic() >= lease.deadline:
        raise _unavailable()
    if lease.lost or not await owns_lock():
        lease.lost = True
        raise _changed()


def mark_write():
    lease = _lease.get()
    if lease is not None:
        lease.writes = True


def write_started():
    lease = _lease.get()
    return bool(lease and lease.writes)


def ownership():
    """Internal Redis fencing material; never returned in an API or a log."""
    lease = _lease.get()
    if lease is None:
        return None
    if lease.lock.local.token is None:
        raise _changed()
    return lease.lock.name, lease.lock.local.token


async def set_cleanup(redis, key, value, *, ex):
    fence = ownership()
    if fence is None:
        return await redis.set(key, value, ex=ex)
    lock_key, token = fence
    if await redis.eval(_OWNED_SET, 2, key, lock_key, token, value, ex) != 1:
        raise _changed()
    return True


async def _renew(lease):
    while True:
        await asyncio.sleep(RENEW_SECONDS)
        try:
            if monotonic() >= lease.deadline or await lease.lock.extend(LOCK_SECONDS, replace_ttl=True) is not True:
                lease.lost = True
                lease.task.cancel()
                return
        except Exception:
            lease.lost = True
            lease.task.cancel()
            return


@asynccontextmanager
async def hold(lock, *, deadline=None, preview=False):
    budget = PREVIEW_SECONDS if preview else PROCESSING_SECONDS
    deadline = min(monotonic() + budget, deadline) if deadline is not None else monotonic() + budget
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _unavailable()
    timeout = asyncio.timeout(remaining)
    lease = None
    try:
        async with timeout:
            if await lock.acquire() is not True:
                raise HTTPException(409, "Há outro atendimento em andamento nesta conversa")
            lease = ConversationLease(lock, asyncio.current_task(), deadline, timeout)
            token = _lease.set(lease)
            heartbeat = asyncio.create_task(_renew(lease))
            failed = False
            try:
                await assert_current()
                yield
                await assert_current()
            except BaseException:
                failed = True
                raise
            finally:
                lease.active = False
                heartbeat.cancel()
                try:
                    async with asyncio.timeout(CLEANUP_SECONDS):
                        try:
                            await heartbeat
                        except asyncio.CancelledError:
                            pass
                        await lock.release()
                except LockError:
                    if not failed:
                        raise _changed() from None
                except Exception:
                    if not failed:
                        raise _unavailable() from None
                finally:
                    _lease.reset(token)
    except TimeoutError:
        if timeout.expired():
            raise _unavailable() from None
        raise
    except asyncio.CancelledError:
        if lease is not None and lease.lost:
            raise _changed() from None
        raise
