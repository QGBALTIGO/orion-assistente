"""Revision fence at QR admission and real turn write boundaries; no live Redis."""
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import qr_ai, qr_intake_revision as revision
from test_preview_audio import MemoryRedis
from test_qr_multi_flow import flow


class RevisionRedis(MemoryRedis):
    def __init__(self):
        super().__init__();self.current=1;self.seen={"synthetic-message":1};self.evaluations=0
    async def eval(self, script, nkeys, *args):
        assert script == revision.VALIDATE_SCRIPT and nkeys == 2
        self.evaluations += 1
        expected, ids = args[2], args[3:]
        return int(self.current == expected and all(x in self.seen for x in ids) and max(self.seen[x] for x in ids) == expected)


@pytest.fixture
def fenced(monkeypatch):
    import redis.asyncio as redis_asyncio
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "1")
    redis=RevisionRedis();tenant=uuid4();writes=[];turns=[]
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k: redis)
    return tenant,redis,writes,turns


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,step,done,write_count", [
    ("entry", "superseded", False, 0), ("inference", "superseded", False, 0),
    ("after_write", "superseded_after_write", True, 1), ("current", "confirmed", True, 1)])
async def test_run_locked_acknowledges_zero_write_only_and_consumes_uncertain_written_attempt(fenced, monkeypatch, stage, step, done, write_count):
    tenant,redis,writes,turns=fenced
    if stage == "entry": redis.current=2
    async def turn(*args,**kwargs):
        turns.append(1)
        if stage == "inference": redis.current=2
        await revision.check_before_write()
        writes.append(1)
        if stage == "after_write": redis.current=2
        return {"answer":"synthetic-complete", "step":"confirmed"}
    monkeypatch.setattr(qr_ai,"turn",turn)
    result=await qr_ai.run_locked(tenant,"5567999999999","synthetic","5567999999999",
        "synthetic-message",intake_revision=1)
    assert result["step"] == step and len(writes) == write_count
    assert bool(redis.transactions) == done and redis.closed == 1 and revision._turn.get() is None
    if stage == "entry": assert turns == []
    if stage != "current": assert result["not_committed"] is (not done)


@pytest.mark.asyncio
async def test_real_revision_enabled_admission_requires_revision_but_preview_is_exempt(fenced, monkeypatch):
    tenant,redis,*_=fenced
    async def turn(*args,**kwargs): return {"answer":"synthetic-preview", "step":"preview_complete"}
    monkeypatch.setattr(qr_ai,"turn",turn)
    with pytest.raises(HTTPException) as error:
        await qr_ai.run_locked(tenant,"5567999999999","x","5567999999999","synthetic-message")
    assert error.value.status_code == 422 and not redis.transactions
    result=await qr_ai.run_locked(tenant,"5567999999999","x","preview:synthetic","synthetic-preview",preview=True)
    assert result["step"] == "preview_complete" and redis.evaluations == 0


@pytest.mark.parametrize("value", [0,-1,True,"1",9007199254740992])
def test_ingress_revision_is_a_bounded_strict_integer(value):
    with pytest.raises(ValidationError):
        qr_ai.Incoming(phone="5567999999999",message_id="synthetic",text="Oi",intake_revision=value)


@pytest.mark.parametrize("value", ["true",1,"false",0])
def test_summary_review_signal_is_a_strict_boolean(value):
    with pytest.raises(ValidationError):
        qr_ai.Incoming(phone="5567999999999",message_id="synthetic",text="Oi",require_summary_review=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["book","confirm","reschedule"])
async def test_real_turn_checks_revision_immediately_before_business_write(flow, monkeypatch, operation):
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    redis=RevisionRedis();redis.current=2
    if operation == "book":
        body="Quero agendar corte amanhã às 15:00 com Bruno"
    elif operation == "confirm":
        h.seed("confirm",hold=str(uuid4()),price=3500,duration=40,selected_start=f"{h.tomorrow}T15:00:00-04:00",
            confirmation_summary="synthetic-current",confirmation_expires_at=datetime.now().timestamp()+240)
        body="SIM"
    else:
        h.seed("reschedule_confirm",appointment=str(h.appointment),new_start=f"{h.tomorrow}T15:00:00-04:00",
            confirmation_summary="synthetic-current",confirmation_expires_at=datetime.now().timestamp()+240,
            expected_snapshot="synthetic-snapshot",reschedule_facts={"services":["Corte"],"professional":"Bruno",
                "timezone":str(h.zone),"total_cents":3500,"duration_min":40})
        body="SIM"
    token=revision.bind(h.tenant,"5567999999999",["synthetic-message"],1,redis)
    try:
        with pytest.raises(revision.Superseded) as error:
            await h.turn(body,preview=False)
        assert error.value.not_committed is True and h.calls["writes"] == []
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_hold_created_but_superseded_before_delivery_requires_visible_review_before_any_sim(flow, monkeypatch):
    import redis.asyncio as redis_asyncio
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    client=RevisionRedis();client.values=h.redis.data
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k: client)
    original=h.book
    async def late_book(*args,**kwargs):
        result=await original(*args,**kwargs)
        client.current=2
        return result
    monkeypatch.setattr(qr_ai,"book_appointment",late_book)
    async def message(body, identity, expected, sticky=False):
        client.current=expected;client.seen[identity]=expected
        return await qr_ai.run_locked(h.tenant,"5567999999999",body,h.namespace,identity,
            intake_revision=expected,require_summary_review=sticky)
    hidden=await message("Quero agendar corte amanhã às 15:00 com Bruno","synthetic-message",1)
    assert hidden == {"answer":None,"step":"superseded_after_write","not_committed":False}
    assert h.state()["confirmation_requires_review"] and h.state()["confirmation_message_id"] is None
    blocked=await message("SIM","synthetic-second",2)
    assert blocked["step"] == "confirm" and [x[0] for x in h.calls["writes"]] == ["hold"]
    shown=await message("Quero ver o resumo de novo","synthetic-third",3)
    assert "*Horário:* 15h" in shown["answer"] and h.state()["confirmation_message_id"] == "synthetic-third"
    done=await message("pode marcar","synthetic-fourth",4)
    assert done["step"] == "confirmed" and [x[0] for x in h.calls["writes"]] == ["hold","confirm"]


@pytest.mark.asyncio
async def test_bridge_delivery_failure_signal_blocks_previously_prepared_summary(flow, monkeypatch):
    import redis.asyncio as redis_asyncio
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    h.seed("confirm",hold=str(uuid4()),price=3500,duration=40,selected_start=f"{h.tomorrow}T15:00:00-04:00",
        confirmation_summary="synthetic-valid",confirmation_message_id="synthetic-old",
        confirmation_expires_at=datetime.now().timestamp()+240)
    client=RevisionRedis();client.values=h.redis.data
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k: client)
    result=await qr_ai.run_locked(h.tenant,"5567999999999","SIM",h.namespace,"synthetic-message",
        intake_revision=1,require_summary_review=True)
    assert result["step"] == "confirm" and h.state()["confirmation_requires_review"] and not h.calls["writes"]
