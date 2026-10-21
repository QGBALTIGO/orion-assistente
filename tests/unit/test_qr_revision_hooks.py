"""Revision fence at QR admission and real turn write boundaries; no live Redis."""
from datetime import datetime
import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import qr_ai, qr_intake_revision as revision
from test_preview_audio import MemoryRedis
from test_qr_multi_flow import flow


class RevisionRedis(MemoryRedis):
    def __init__(self):
        super().__init__();self.current=1;self.seen={"synthetic-message":1};self.evaluations=0;self.deadlines={};self.restored=[]
    async def set(self,key,value,ex=None,nx=False,px=None):
        result=await super().set(key,value,ex=ex,nx=nx)
        if result:
            ttl=px if px is not None else ex*1000 if ex is not None else None
            self.deadlines[key]=qr_ai.monotonic()+ttl/1000 if ttl is not None else None
        return result
    async def eval(self, script, nkeys, *args):
        if script == qr_ai._STATE_SNAPSHOT_SCRIPT:
            assert nkeys == 3
            rows=[]
            for key in args:
                deadline=self.deadlines.get(key)
                ttl=max(0,int((deadline-qr_ai.monotonic())*1000)) if deadline is not None else -1
                rows.extend((self.values.get(key),ttl if key in self.values else -2))
            return rows
        if script == qr_ai._STATE_RESTORE_SCRIPT:
            assert nkeys == 3
            self.restored.append(args[:3])
            for i,key in enumerate(args[:3]):
                present,value,ttl=args[3+i*3:6+i*3]
                if present and (ttl==-1 or ttl>0):
                    await self.set(key,value,px=ttl if ttl>0 else None)
                else:
                    await self.delete(key)
            return 1
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
    if stage == "after_write": assert redis.restored == []


@pytest.mark.asyncio
async def test_superseded_handoff_restores_provisional_state_then_combined_correction_keeps_bot_responding(flow,monkeypatch):
    import redis.asyncio as redis_asyncio
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    client=RevisionRedis();client.values=h.redis.data
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k:client)
    original=client.set;changed=False
    async def pause_then_new_notice(key,value,*args,**kwargs):
        nonlocal changed
        result=await original(key,value,*args,**kwargs)
        if key==h.key and json.loads(value).get("step")=="human" and not changed:
            changed=True;client.current=2;client.seen["correction"]=2
        return result
    monkeypatch.setattr(client,"set",pause_then_new_notice)
    first=await qr_ai.run_locked(h.tenant,"5567999999999","Quero atendente humano",h.namespace,
        "synthetic-message",intake_revision=1)
    assert first == {"answer":None,"step":"superseded","not_committed":True}
    assert h.state()=={} and h.key+":catalog-context" not in client.values
    combined=await qr_ai.run_locked(h.tenant,"5567999999999",
        "Quero atendente humano\nNão, melhor continuar com você",h.namespace,"synthetic-message",
        message_ids=["synthetic-message","correction"],intake_revision=2,require_summary_review=True)
    assert combined["answer"] and "continuo por aqui" in combined["answer"]
    assert combined["step"]!="human" and h.state().get("step")!="human" and not h.calls["writes"]


@pytest.mark.asyncio
async def test_superseded_service_selection_restores_old_selection_and_catalog_context(flow,monkeypatch):
    import redis.asyncio as redis_asyncio
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    before=h.seed("date");before_context=h.redis.data[h.key+":catalog-context"]
    client=RevisionRedis();client.values=h.redis.data
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k:client)
    original=client.set
    async def select_then_new_notice(key,value,*args,**kwargs):
        result=await original(key,value,*args,**kwargs)
        if key==h.key and json.loads(value).get("services")==[str(h.beard)]:
            client.current=2
        return result
    monkeypatch.setattr(client,"set",select_then_new_notice)
    result=await qr_ai.run_locked(h.tenant,"5567999999999","Quero barba",h.namespace,
        "synthetic-message",intake_revision=1)
    assert result["step"]=="superseded" and result["not_committed"]
    assert h.state()==before and client.values[h.key+":catalog-context"]==before_context
    assert not h.calls["writes"]


@pytest.mark.asyncio
async def test_provisional_restore_subtracts_elapsed_ttl_and_never_revives_expired_or_touches_other_keys(monkeypatch):
    clock=[100.0];monkeypatch.setattr(qr_ai,"monotonic",lambda:clock[0])
    client=RevisionRedis();key="orion:qr:ai:synthetic-tenant:synthetic-contact"
    fixed=qr_ai._provisional_keys(key)
    for target,ttl in zip(fixed,(2000,1000,500)):
        await client.set(target,"old-"+target,px=ttl)
    unrelated={key+":receipt":"verified",key+":notice":"new-ingress",key+":done":"consumed"}
    client.values.update(unrelated)
    snapshot=await qr_ai._snapshot_provisional_state(client,key)
    for target in fixed:
        await client.set(target,"provisional",ex=999)
    clock[0]=101.2
    await qr_ai._restore_provisional_state(client,key,snapshot)
    assert client.values[fixed[0]]=="old-"+fixed[0]
    assert 0 < (client.deadlines[fixed[0]]-clock[0])*1000 <= 800
    assert fixed[1] not in client.values and fixed[2] not in client.values
    assert all(client.values[target]==value for target,value in unrelated.items())
    assert client.restored==[fixed]


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


@pytest.mark.asyncio
async def test_verified_confirmation_superseded_before_reply_is_reported_without_executing_correction(flow,monkeypatch):
    import redis.asyncio as redis_asyncio
    from app import qr_operation_receipt as receipt
    h=flow;monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","1")
    h.seed("confirm",hold=str(uuid4()),price=3500,duration=40,selected_start=f"{h.tomorrow}T15:00:00-04:00",
        confirmation_summary="synthetic-current",confirmation_message_id="synthetic-old",
        confirmation_expires_at=datetime.now().timestamp()+240)
    client=RevisionRedis();client.values=h.redis.data
    monkeypatch.setattr(redis_asyncio,"from_url",lambda *a,**k:client)
    original=h.confirm
    async def confirmed_then_late(*args,**kwargs):
        result=await original(*args,**kwargs)
        client.current=2
        return result
    monkeypatch.setattr(qr_ai,"confirm_hold",confirmed_then_late)
    lost=await qr_ai.run_locked(h.tenant,"5567999999999","SIM",h.namespace,"synthetic-message",intake_revision=1)
    assert lost == {"answer":None,"step":"superseded_after_write","not_committed":False}
    assert h.key not in client.values and receipt.key(h.tenant,"5567999999999") in client.values
    client.seen["synthetic-correction"]=2
    result=await qr_ai.run_locked(h.tenant,"5567999999999","Pera, é sexta",h.namespace,"synthetic-correction",
        intake_revision=2,require_summary_review=True)
    assert result["step"] == "completed_operation_review" and "SYNTHETIC" not in result["answer"]
    assert json.loads(client.values[receipt.key(h.tenant,"5567999999999")])["protocol"] == "SYNTHETIC"
    assert "ainda não foi aplicada" in result["answer"] and "_verified_operation" not in result
    assert [x[0] for x in h.calls["writes"]] == ["confirm"] and not h.calls["plans"]
    client.current=3;client.seen["synthetic-accepted"]=3
    await qr_ai.run_locked(h.tenant,"5567999999999","Oi",h.namespace,"synthetic-accepted",intake_revision=3)
    assert receipt.key(h.tenant,"5567999999999") not in client.values
    assert [x[0] for x in h.calls["writes"]] == ["confirm"]


@pytest.mark.asyncio
async def test_request_stopped_or_preview_never_becomes_a_verified_operation_receipt(fenced,monkeypatch):
    from app import qr_operation_receipt as receipt
    tenant,client,*_=fenced
    async def stopped(*a,**k):
        return {"answer":"Solicitação encerrada; reservas confirmadas permanecem.","step":"cancelled"}
    monkeypatch.setattr(qr_ai,"turn",stopped)
    result=await qr_ai.run_locked(tenant,"5567999999999","Cancelar","5567999999999","synthetic-message",intake_revision=1)
    assert result["step"] == "cancelled" and receipt.key(tenant,"5567999999999") not in client.values
    async def preview(*a,**k):
        return {"answer":"synthetic-only","step":"confirmed","_verified_operation":True}
    monkeypatch.setattr(qr_ai,"turn",preview)
    result=await qr_ai.run_locked(tenant,"5567999999999","x","preview:synthetic","synthetic-preview",preview=True)
    assert "_verified_operation" not in result and receipt.key(tenant,"5567999999999") not in client.values
