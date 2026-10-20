"""Transaction-bound authority: causal rollback and task/transaction lifetime."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import qr_channel_scope as scope, qr_intake_revision as revision
from app.database import tenant_transaction
from app.qr_channels import ChannelIdentity


class Session:
    def __init__(self, answers=(True, True)):
        self.answers = iter(answers); self.sql = []; self.pending = []; self.saved = []
        self.tx = None

    @asynccontextmanager
    async def begin(self):
        self.tx = SimpleNamespace(is_active=True)
        try:
            yield self.tx
            self.saved.extend(self.pending)
        finally:
            self.pending.clear(); self.tx.is_active = False; self.tx = None

    def get_transaction(self): return self.tx
    def in_transaction(self): return self.tx is not None

    async def execute(self, statement, params=None):
        sql = str(statement); self.sql.append(sql)
        if "qr_channel_transaction_guard" in sql:
            assert params["tenant"] == self.identity.tenant_id
            return SimpleNamespace(scalar_one=lambda: next(self.answers))
        return SimpleNamespace(scalar_one=lambda: None)


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    identity = ChannelIdentity(uuid4(), uuid4(), uuid4(), "baileys", 4, uuid4(), True, True)
    calls = []
    async def guard(): calls.append("support"); return identity
    token = scope.bind(identity, guard)
    try: yield identity, calls
    finally: scope.reset(token)


def session(identity, *answers):
    db = Session(answers or (True, True)); db.identity = identity
    return db


async def write(db, identity):
    async with tenant_transaction(db, identity.tenant_id):
        await revision.check_before_write()
        db.pending.append("domain-write")


@pytest.mark.asyncio
async def test_legacy_transaction_uses_only_existing_sql(monkeypatch):
    monkeypatch.delenv("QR_CHANNELS_ENABLED", raising=False)
    db = Session()
    async with tenant_transaction(db, uuid4()): db.pending.append("legacy")
    assert db.saved == ["legacy"] and len(db.sql) == 1
    assert "set_config" in db.sql[0]


@pytest.mark.asyncio
async def test_live_transaction_skips_support_lock_but_retains_redis_guard(managed, monkeypatch):
    identity, calls = managed; db = session(identity); observed = []
    class Redis:
        async def eval(self, *args): observed.append(args); return 1
    monkeypatch.setattr(revision, "enabled", lambda: True)
    token = revision._turn.set(SimpleNamespace(redis=Redis(), tenant=identity.tenant_id,
        phone="5567999999901", expected=2, message_ids=("synthetic",), write_started=False))
    try: await write(db, identity)
    finally: revision._turn.reset(token)
    assert db.saved == ["domain-write"] and observed and not calls
    assert len([s for s in db.sql if "transaction_guard" in s]) == 2
    await scope.assert_current()
    assert calls == ["support"] and scope._hold.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [(False,), (True, False)])
async def test_revocation_before_write_or_expiry_before_commit_rolls_back(managed, answers):
    identity, _ = managed; db = session(identity, *answers)
    with pytest.raises(HTTPException) as exc: await write(db, identity)
    assert exc.value.status_code == 409 and db.saved == [] and scope._hold.get() is None


@pytest.mark.asyncio
async def test_child_cannot_borrow_parent_transaction_lock(managed):
    identity, calls = managed; db = session(identity)
    async with tenant_transaction(db, identity.tenant_id):
        with pytest.raises(HTTPException): await asyncio.create_task(scope.assert_current())
        await scope.assert_current()
    assert not calls


@pytest.mark.asyncio
async def test_child_context_cannot_keep_expired_hold_after_parent_exit(managed):
    identity, calls = managed; db = session(identity); ready = asyncio.Event()
    async def child():
        await ready.wait()
        await scope.assert_current()
    async with tenant_transaction(db, identity.tenant_id): task = asyncio.create_task(child())
    ready.set()
    with pytest.raises(HTTPException): await task
    assert not calls


@pytest.mark.asyncio
async def test_body_failure_rolls_back_and_releases_token(managed):
    identity, calls = managed; db = session(identity)
    with pytest.raises(RuntimeError):
        async with tenant_transaction(db, identity.tenant_id):
            db.pending.append("never-committed")
            raise RuntimeError("synthetic")
    await scope.assert_current()
    assert not db.saved and calls == ["support"] and scope._hold.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["transaction", "identity", "flag"])
async def test_changed_transaction_identity_or_global_flag_never_commits(managed, monkeypatch, damage):
    identity, _ = managed; db = session(identity); token = None
    try:
        with pytest.raises(HTTPException):
            async with tenant_transaction(db, identity.tenant_id):
                db.pending.append("never-committed")
                if damage == "transaction": db.tx.is_active = False
                elif damage == "identity": token = scope.bind(replace(identity, channel_id=uuid4()), lambda: None)
                else: monkeypatch.setenv("QR_CHANNELS_ENABLED", "0")
                await scope.assert_current()
    finally:
        if token is not None: scope.reset(token)
    assert not db.saved


@pytest.mark.asyncio
async def test_wrong_tenant_rejected_before_channel_sql(managed):
    identity, _ = managed; db = session(identity)
    with pytest.raises(HTTPException):
        async with tenant_transaction(db, uuid4()): pytest.fail("must not enter")
    assert len(db.sql) == 1 and not db.saved
