"""Notice-only invalidation cannot borrow or grant assistant authority."""
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import automation_runtime as runtime, qr_ai, qr_channel_scope
from app.qr_channels import ChannelIdentity

PHONE = "+5567999999901"


@pytest.fixture
def notice(monkeypatch):
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    state = SimpleNamespace(identity=ChannelIdentity(uuid4(), uuid4(), uuid4(), "baileys", 7, uuid4(), True, False),
                            calls=[], guards=[True, True], committed=False, rolled_back=False)
    class DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, params):
            sql = str(query)
            state.calls.append((sql, params))
            if "automation_notice_guard" in sql:
                return SimpleNamespace(scalar_one=lambda: state.guards.pop(0))
            return SimpleNamespace()
    @asynccontextmanager
    async def transaction(db, tenant):
        assert tenant == state.identity.tenant_id
        try:
            yield db
        except BaseException:
            state.rolled_back = True
            raise
        else:
            state.committed = True
    monkeypatch.setattr(runtime, "get_session_factory", lambda: DB)
    monkeypatch.setattr(runtime, "tenant_transaction", transaction)
    return state


@pytest.mark.asyncio
async def test_ai_disabled_notice_holds_authority_before_and_after_both_writes(notice):
    await runtime.interaction(notice.identity.tenant_id, PHONE, channel_identity=notice.identity)
    assert notice.committed and not notice.rolled_back
    assert ["guard" if "automation_notice_guard" in sql else "contact" if "INSERT INTO automation_contact_state" in sql else "jobs"
            for sql, _ in notice.calls] == ["guard", "contact", "jobs", "guard"]
    for sql, values in (notice.calls[0], notice.calls[-1]):
        assert values == {"tenant": notice.identity.tenant_id, "channel": notice.identity.channel_id,
                          "account": notice.identity.owner_account_id, "owner": notice.identity.lease_owner,
                          "fence": notice.identity.fence, "sender": PHONE.lstrip("+")}
    assert qr_channel_scope.current() is None  # Never binds permission for bookings.
    with pytest.raises(HTTPException):
        qr_channel_scope.bind(notice.identity, lambda: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("guards,expected_calls", [([False], 1), ([True, False], 4)])
async def test_notice_revocation_or_expiry_prevents_commit(notice, guards, expected_calls):
    notice.guards = guards
    with pytest.raises(HTTPException) as error:
        await runtime.interaction(notice.identity.tenant_id, PHONE, channel_identity=notice.identity)
    assert error.value.status_code == 409
    assert notice.rolled_back and not notice.committed and len(notice.calls) == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("allowed_sender", False), ("tenant_id", uuid4()),
    ("provider", "other"), ("fence", 0), ("fence", True)])
async def test_untrusted_or_mismatched_notice_identity_never_writes(notice, field, value):
    identity = replace(notice.identity, **{field: value})
    with pytest.raises(HTTPException):
        await runtime.interaction(notice.identity.tenant_id, PHONE, channel_identity=identity)
    assert not notice.calls and not notice.committed


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["group@g.us", "123@lid", "+5567999999901suffix"])
async def test_notice_invalid_contact_never_writes(notice, phone):
    with pytest.raises(HTTPException):
        await runtime.interaction(notice.identity.tenant_id, phone, channel_identity=notice.identity)
    assert not notice.calls


@pytest.mark.asyncio
async def test_qr_flag_off_prevents_managed_notice_but_legacy_remains_compatible(notice, monkeypatch):
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "0")
    with pytest.raises(HTTPException):
        await runtime.interaction(notice.identity.tenant_id, PHONE, channel_identity=notice.identity)
    assert not notice.calls
    await runtime.interaction(notice.identity.tenant_id, PHONE)
    assert notice.committed and len(notice.calls) == 2


@pytest.mark.asyncio
async def test_managed_ingress_passes_identity_and_dedupes_only_after_commit(notice, monkeypatch):
    stored = set(); events = []
    class Redis:
        @asynccontextmanager
        async def lock(self, *args, **kwargs): yield
        async def exists(self, key): return key in stored
        async def set(self, key, value, **kwargs): events.append("dedupe"); stored.add(key)
    async def guard(): events.append("authenticate")
    async def record(tenant, phone, **kwargs):
        assert tenant == notice.identity.tenant_id and phone == PHONE
        assert kwargs == {"channel_identity": notice.identity}
        events.append("commit")
    monkeypatch.setattr(runtime, "interaction", record)
    for _ in range(2):
        await qr_ai._record_customer_interaction(notice.identity.tenant_id, PHONE.lstrip("+"), "synthetic-message-1", Redis(),
                                               channel=notice.identity, guard=guard)
    assert events == ["authenticate", "commit", "dedupe", "authenticate"]


@pytest.mark.asyncio
async def test_failed_notice_never_marks_redis_dedupe_or_reports_success(notice, monkeypatch):
    class Redis:
        @asynccontextmanager
        async def lock(self, *args, **kwargs): yield
        async def exists(self, key): return False
        async def set(self, *args, **kwargs): pytest.fail("uncommitted notice was deduped")
    async def guard(): pass
    async def record(*args, **kwargs): raise HTTPException(409, "changed")
    monkeypatch.setattr(runtime, "interaction", record)
    with pytest.raises(HTTPException) as error:
        await qr_ai._record_customer_interaction(notice.identity.tenant_id, PHONE.lstrip("+"), "synthetic-message-2", Redis(),
                                               channel=notice.identity, guard=guard)
    assert error.value.status_code == 409
