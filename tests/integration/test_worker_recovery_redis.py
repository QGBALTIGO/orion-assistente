"""Actual ARQ retained-failure recovery on an explicitly disposable Redis.

SQL state transitions are modeled here to isolate the queue/result contract.
This does not qualify PostgreSQL pooling, live WhatsApp sockets or provider I/O.
"""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from arq.connections import ArqRedis
from arq.jobs import Job
from arq.worker import Worker
import pytest

from app import worker
from test_qr_intake_revision_redis import redis_case


pytestmark = pytest.mark.integration


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class EventStore:
    def __init__(self):
        self.tenant, self.event, self.channel = uuid4(), uuid4(), uuid4()
        self.status, self.attempts, self.answers = "pending", 0, 0
        self.fail = True

    def session(self):
        store = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, query, params=None):
                sql = str(query)
                if "ORDER BY id LIMIT 100" in sql:
                    return Rows([{"tenant_id": store.tenant, "id": store.event,
                                  "attempts": store.attempts}] if store.status == "pending" else [])
                if "RETURNING id, channel_id" in sql:
                    if store.status != "pending":
                        return Rows()
                    store.status = "processing"
                    store.attempts += 1
                    return Rows([{"id": store.event, "channel_id": store.channel,
                                  "sender_e164": "+5567999999901", "message_text": "synthetic",
                                  "external_id": "synthetic-original-id", "envelope": {}}])
                if "CASE WHEN attempts >= 5" in sql:
                    store.status = "failed" if store.attempts >= 5 else "pending"
                elif "SET status='processed'" in sql:
                    store.status = "processed"
                return Rows()

        return Session()

    async def answer(self, *args):
        self.answers += 1
        if self.fail:
            raise ConnectionError("synthetic transient dependency outage")
        return None


@asynccontextmanager
async def no_domain_transaction(db, tenant):
    yield db


@pytest.mark.asyncio
async def test_failed_arq_recovery_result_does_not_suppress_next_durable_attempt(redis_case, monkeypatch):
    redis, _, _ = redis_case
    store = EventStore()
    queue_name = "orion:qa:arq-recovery:" + str(uuid4())
    arq = ArqRedis(connection_pool=redis.connection_pool, default_queue_name=queue_name)
    job_ids = ["wa:" + str(store.event)]
    monkeypatch.setattr(worker, "support_session_factory", lambda: store.session)
    monkeypatch.setattr(worker, "get_session_factory", lambda: store.session)
    monkeypatch.setattr(worker, "tenant_transaction", no_domain_transaction)
    monkeypatch.setattr(worker, "_event_answer", store.answer)

    async def interaction(*args):
        pass

    monkeypatch.setattr(worker, "interaction", interaction)
    from app import inbox_control
    monkeypatch.setattr(inbox_control, "enabled", lambda: False)

    async def run_one():
        runner = Worker([worker.process_inbound_event], redis_pool=arq, queue_name=queue_name,
                        burst=True, max_jobs=1, max_burst_jobs=1, handle_signals=False,
                        keep_result=3600)
        await runner.async_run()

    try:
        await arq.enqueue_job("process_inbound_event", str(store.tenant), str(store.event),
                              _job_id=job_ids[0])
        await run_one()
        assert store.status == "pending" and store.attempts == 1
        assert (await Job(job_ids[0], redis=arq).result_info()).success is False

        assert await worker.recover_events({"redis": arq}) == 1
        first_recovery = (await redis.zrange(queue_name, 0, -1))[0].decode()
        job_ids.append(first_recovery)
        await run_one()
        assert store.status == "pending" and store.attempts == 2
        assert (await Job(first_recovery, redis=arq).result_info()).success is False
        assert await redis.ttl("arq:result:" + first_recovery) > 3500

        # The real ARQ rejects an ID with a retained result, even a failed one.
        # A fresh durable attempt must still be enqueued immediately.
        await asyncio.gather(*(worker.recover_events({"redis": arq}) for _ in range(12)))
        queued = await redis.zrange(queue_name, 0, -1)
        assert len(queued) == 1, "Retained failed result suppressed durable recovery"
        next_recovery = queued[0].decode()
        job_ids.append(next_recovery)
        assert next_recovery != first_recovery
        assert await redis.ttl("arq:result:" + first_recovery) > 3500

        store.fail = False
        await run_one()
        assert store.status == "processed" and store.attempts == store.answers == 3
        assert await worker.recover_events({"redis": arq}) == 0
        assert not await redis.zrange(queue_name, 0, -1)
    finally:
        for identity in set(job_ids):
            await redis.delete(*(prefix + identity for prefix in
                                 ("arq:job:", "arq:result:", "arq:retry:", "arq:in-progress:")))
        await redis.delete(queue_name, queue_name + ":health-check")
