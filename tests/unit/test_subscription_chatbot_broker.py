"""Private broker bounds and media-writer cancellation without real storage.

The broker tests use plain JSON/stream requests. The media tests exercise the
real asynchronous writer lifecycle while substituting only the DB and S3 edges.
"""
import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
import threading
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest
from starlette.datastructures import Headers

from app import subscription_chatbot_broker as broker
from app import subscription_chatbot_media as media
from app import subscription_chatbot_store as store


TENANT = UUID("10000000-0000-4000-8000-000000000001")
DEVICE = UUID("20000000-0000-4000-8000-000000000001")
GENERATION = UUID("30000000-0000-4000-8000-000000000001")
EFFECT = UUID("40000000-0000-4000-8000-000000000001")
SECRET = "SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_REFLECT"
SCOPE_HEADERS = {
    "x-orion-device-id": str(DEVICE),
    "x-orion-device-generation": str(GENERATION),
    "x-orion-device-revision": "3",
    "x-orion-device-effect": str(EFFECT),
    "x-orion-chatbot-revision": "7",
}


def nested(depth):
    value = None
    for _ in range(depth):
        value = [value]
    return value


def test_http_receipt_accepts_json_types_without_evaluating_templates_or_urls():
    value = {"items": [None, True, False, 0, -8, 1.5, "ação 😀"],
             "url": "https://example.com/private", "text": "${expression} {name}"}
    assert broker.checked_result(value) is value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
    {1: SECRET}, {"x" * 2049: SECRET}, (SECRET,), {SECRET}, b"private", "\ud800"])
def test_invalid_http_receipt_shape_is_refused_without_reflecting_content(value):
    with pytest.raises(HTTPException) as error:
        broker.checked_result(value)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "CHATBOT_HTTP_RESULT_INVALID"
    assert SECRET not in str(error.value.detail)


def test_http_receipt_limits_depth_nodes_and_key_length_at_the_boundary():
    assert broker.checked_result(nested(16)) == nested(16)
    assert broker.checked_result([None] * 8191) == [None] * 8191
    assert broker.checked_result({"x" * 2048: None}) == {"x" * 2048: None}
    for value in (nested(17), [None] * 8192):
        with pytest.raises(HTTPException) as error:
            broker.checked_result(value)
        assert error.value.status_code == 422
    cycle = []
    cycle.append(cycle)
    with pytest.raises(HTTPException) as error:
        broker.checked_result(cycle)
    assert error.value.status_code == 422


def test_http_receipt_budget_counts_serialized_utf8_bytes_not_characters():
    value = "á" * ((broker.JSON_BYTES - 2) // 2)
    assert len(store.encode(value).encode()) == broker.JSON_BYTES
    assert broker.checked_result(value) == value
    with pytest.raises(HTTPException) as error:
        broker.checked_result(value + "x")
    assert error.value.status_code == 422


@pytest.mark.parametrize("revision", [1, 2**63 - 1])
def test_headers_bind_all_effect_scope_identifiers_and_positive_bigint_revisions(revision):
    headers = dict(SCOPE_HEADERS, **{"x-orion-device-revision": str(revision),
                                   "x-orion-chatbot-revision": str(revision)})
    scope = broker.header_scope(SimpleNamespace(headers=Headers(headers)))
    assert scope == {"device_id": DEVICE, "generation": GENERATION,
                     "operation_id": EFFECT, "settings_revision": revision,
                     "chatbot_revision": revision}


@pytest.mark.parametrize("missing", list(SCOPE_HEADERS))
def test_missing_effect_scope_header_is_uniformly_unauthorized(missing):
    headers = dict(SCOPE_HEADERS)
    del headers[missing]
    with pytest.raises(HTTPException) as error:
        broker.header_scope(SimpleNamespace(headers=Headers(headers)))
    assert error.value.status_code == 401 and error.value.detail == "Recurso não autorizado"


@pytest.mark.parametrize("header,value", [
    ("x-orion-device-id", SECRET), ("x-orion-device-generation", SECRET),
    ("x-orion-device-effect", SECRET), ("x-orion-device-revision", "0"),
    ("x-orion-device-revision", str(2**63)), ("x-orion-chatbot-revision", "-1"),
    ("x-orion-chatbot-revision", str(2**63)), ("x-orion-chatbot-revision", "1.5"),
])
def test_invalid_effect_scope_is_uniformly_unauthorized(header, value):
    headers = dict(SCOPE_HEADERS, **{header: value})
    with pytest.raises(HTTPException) as error:
        broker.header_scope(SimpleNamespace(headers=Headers(headers)))
    assert error.value.status_code == 401 and error.value.detail == "Recurso não autorizado"
    assert SECRET not in str(error.value.detail)


class StreamingRequest:
    def __init__(self, *chunks, length=None, failure=None):
        self.headers = {} if length is None else {"content-length": length}
        self.chunks, self.failure, self.reads = chunks, failure, 0

    async def stream(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.failure:
            raise self.failure


async def test_binary_stream_preserves_all_bytes_at_the_exact_budget():
    value = b"\x00\xff\x01binary\x00"
    request = StreamingRequest(value[:2], value[2:], length=str(len(value)))
    assert await broker.read_bytes(request, len(value)) == value
    assert request.reads == 2


@pytest.mark.parametrize("length", ["-1", "1.5", "1234567890", "１２", " 4", "11"])
async def test_binary_declared_budget_is_checked_before_reading(length):
    request = StreamingRequest(SECRET.encode(), length=length)
    with pytest.raises(HTTPException) as error:
        await broker.read_bytes(request, 10)
    assert error.value.status_code == 413 and request.reads == 0
    assert SECRET not in str(error.value.detail)


@pytest.mark.parametrize("length", [None, "1", "10"])
async def test_binary_actual_budget_is_enforced_with_missing_or_false_length(length):
    request = StreamingRequest(b"12345", b"678901", b"not read", length=length)
    with pytest.raises(HTTPException) as error:
        await broker.read_bytes(request, 10)
    assert error.value.status_code == 413 and request.reads == 2


async def test_binary_timeout_and_cancellation_do_not_leak_or_retry():
    request = StreamingRequest(b"1", failure=TimeoutError(SECRET))
    with pytest.raises(HTTPException) as error:
        await broker.read_bytes(request, 10)
    assert error.value.status_code == 408 and request.reads == 1
    assert SECRET not in str(error.value.detail)
    request = StreamingRequest(b"1", failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await broker.read_bytes(request, 10)
    assert request.reads == 1


class Mappings:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one(self):
        assert self.value is not None
        return self.value

    def one_or_none(self):
        return self.value


class MediaLedger:
    """Single synthetic registry entry, not a replacement SQL interpreter."""
    def __init__(self):
        self.row = None
        self.events = []
        self.revoked = False
        self.writer_finished = threading.Event()
        self.support_checks = 0
        self.mutex_locked = False
        self.total_count = 0
        self.total_bytes = 0

    @asynccontextmanager
    async def protected(self):
        self.events.append("authorize")
        if self.revoked:
            raise HTTPException(403, "Recurso não autorizado")
        yield self

    @asynccontextmanager
    async def support(self):
        self.support_checks += 1
        self.events.append("support")
        yield self

    @asynccontextmanager
    async def begin(self):
        yield

    @asynccontextmanager
    async def mutex(self, tenant, kind, digest):
        assert tenant == TENANT and kind == "document"
        assert len(digest) == 64 and not self.mutex_locked
        self.mutex_locked = True
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")
            self.mutex_locked = False

    async def execute(self, statement, params):
        assert params["t"] == TENANT
        query = " ".join(str(statement).split()).lower()
        if query.startswith("select *"):
            return Mappings(dict(self.row) if self.row else None)
        if query.startswith("select count"):
            return Mappings({"count": self.total_count, "bytes": self.total_bytes})
        if query.startswith("insert into"):
            assert self.row is None and self.mutex_locked
            self.row = {"id": params["id"], "tenant_id": params["t"],
                        "sha256": params["sha"], "kind": params["kind"],
                        "size": params["size"], "status": "pending"}
            self.events.append("pending")
            return Mappings(None)
        assert query.startswith("update subscription_chatbot_assets") and self.row is not None
        assert params["id"] == self.row["id"] and self.mutex_locked
        for status in ("pending", "ready", "failed"):
            if f"set status='{status}'" in query:
                if status in {"ready", "failed"}:
                    assert self.writer_finished.is_set(), "registry changed before the real writer settled"
                self.row["status"] = status
                self.events.append(status)
                return Mappings(dict(self.row))
        pytest.fail("Unexpected registry operation")


@pytest.fixture
def writer_case(monkeypatch):
    ledger = MediaLedger()
    value = b"Synthetic private document\n"
    prepared = media.PreparedMedia(value, "text/plain; charset=utf-8", len(value), sha256(value).hexdigest())
    monkeypatch.setattr(store, "media_mutex", ledger.mutex)
    monkeypatch.setattr(store, "support_session_factory", lambda: ledger.support)
    return ledger, prepared


async def drain(tasks):
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.parametrize("writer_failure", [False, True])
async def test_cancelled_media_writer_keeps_reservation_and_mutex_until_thread_finishes(writer_case, monkeypatch, writer_failure):
    ledger, prepared = writer_case
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def put(tenant, identity, actual):
        assert tenant == TENANT and identity == ledger.row["id"] and actual is prepared
        calls.append(identity)
        ledger.events.append("writer-start")
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(3), "test did not release the synthetic S3 writer"
            if writer_failure:
                raise RuntimeError(SECRET)
        finally:
            ledger.events.append("writer-finish")
            ledger.writer_finished.set()

    monkeypatch.setattr(media, "put_media", put)
    before = set(store._media_cleanup)
    task = asyncio.create_task(store.persist_prepared(TENANT, "document", prepared, ledger.protected))
    pending = set()
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert ledger.row["status"] == "pending" and ledger.mutex_locked
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pending = set(store._media_cleanup) - before
        assert pending and not all(item.done() for item in pending)
        assert ledger.row["status"] == "pending" and ledger.mutex_locked
        assert not ledger.writer_finished.is_set() and ledger.support_checks == 0
        assert "failed" not in ledger.events and "ready" not in ledger.events and "unlock" not in ledger.events
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await drain({task, *pending, *(set(store._media_cleanup) - before)})
    assert len(calls) == 1 and ledger.row["status"] == "failed"
    assert ledger.support_checks == 1 and not ledger.mutex_locked
    assert ledger.events.index("writer-finish") < ledger.events.index("failed") < ledger.events.index("unlock")
    assert "ready" not in ledger.events and not (set(store._media_cleanup) - before)


async def test_revoked_authorization_after_write_cleans_up_through_independent_support_transaction(writer_case, monkeypatch):
    ledger, prepared = writer_case

    def put(*_args):
        ledger.revoked = True
        ledger.events.append("writer-finish")
        ledger.writer_finished.set()

    monkeypatch.setattr(media, "put_media", put)
    with pytest.raises(HTTPException) as error:
        await store.persist_prepared(TENANT, "document", prepared, ledger.protected)
    assert error.value.status_code == 403
    assert ledger.row["status"] == "failed" and ledger.support_checks == 1
    assert "ready" not in ledger.events and not ledger.mutex_locked
    assert ledger.events.index("writer-finish") < ledger.events.index("support") < ledger.events.index("failed")


@pytest.mark.parametrize("previous_status", ["failed", "pending"])
async def test_recovering_same_immutable_media_reuses_its_reserved_quota_and_identity(writer_case, monkeypatch, previous_status):
    ledger, prepared = writer_case
    identity = UUID("50000000-0000-4000-8000-000000000001")
    ledger.row = {"id": identity, "tenant_id": TENANT, "kind": "document", "sha256": prepared.sha256,
                  "size": prepared.size, "status": previous_status}
    ledger.total_count, ledger.total_bytes = store.MAX_ASSETS, store.MAX_TENANT_MEDIA_BYTES
    calls = []

    def put(tenant, asset_id, actual):
        assert tenant == TENANT and asset_id == identity and actual is prepared
        assert ledger.mutex_locked and ledger.row["status"] == "pending"
        calls.append(asset_id)
        ledger.writer_finished.set()

    monkeypatch.setattr(media, "put_media", put)
    result = await store.persist_prepared(TENANT, "document", prepared, ledger.protected)
    assert result["id"] == identity and result["status"] == "ready" and calls == [identity]
    assert ledger.support_checks == 0 and not ledger.mutex_locked


async def test_new_media_cannot_reuse_capacity_already_reserved_by_other_rows(writer_case, monkeypatch):
    ledger, prepared = writer_case
    ledger.total_count = store.MAX_ASSETS
    monkeypatch.setattr(media, "put_media", lambda *_: pytest.fail("quota rejection must not start storage"))
    with pytest.raises(HTTPException) as error:
        await store.persist_prepared(TENANT, "document", prepared, ledger.protected)
    assert error.value.status_code == 413 and error.value.detail["code"] == "CHATBOT_MEDIA_QUOTA"
    assert ledger.row is None and ledger.support_checks == 0 and not ledger.mutex_locked
