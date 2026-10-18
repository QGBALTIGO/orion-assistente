"""Meta customer turns with real planner/formatter and isolated persistence fakes."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app import whatsapp_bot as bot
from app import whatsapp_commands as commands
from app.qr_dialogue import contextual_response


class MemoryRedis:
    def __init__(self):
        self.data={}

    async def get(self,key):
        return self.data.get(key)

    async def set(self,key,value,ex=None):
        self.data[key]=value

    async def delete(self,key):
        self.data.pop(key,None)


class Rows(list):
    def mappings(self):
        return self

    def first(self):
        return self[0] if self else None


@pytest.fixture
def meta(monkeypatch):
    tenant,channel=uuid4(),uuid4()
    phone="+5567999999901"
    catalog=[{"id":uuid4(),"name":"Corte","duration_min":40,"price_cents":3500},
             {"id":uuid4(),"name":"Barba","duration_min":20,"price_cents":2000}]
    pros=[{"id":uuid4(),"name":"Ana"},{"id":uuid4(),"name":"Bia"}]
    now=datetime.now(ZoneInfo("America/Campo_Grande"))
    day=(now+timedelta(days=7)).date()
    records,events={},[]
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def execute(self,sql,params):
            value=str(sql)
            if "FROM subscriptions" in value: return Rows()
            if "FROM whatsapp_channels" in value: return Rows([{"enabled":True,"timezone":"America/Campo_Grande"}])
            if "FROM services" in value: return Rows(catalog)
            if "FROM professionals" in value: return Rows(pros)
            if "human_handoffs" in value: events.append("handoff");return Rows()
            raise AssertionError(value)
    @asynccontextmanager
    async def tx(*_): yield
    async def prefs(_): return None
    async def no_commands(*_,**__): return False,None
    async def decision(body,*,pending,action="booking",semantic_allowed=False):
        return contextual_response(body,awaiting_confirmation=pending,confirmation_action=action)
    async def slots(_db,t,pro,ids,on_day):
        assert t==tenant and pro in [x["id"] for x in pros]
        selected=[x for x in catalog if x["id"] in ids]
        return {"slots":[datetime.combine(on_day,datetime.min.time(),tzinfo=now.tzinfo).replace(hour=h).isoformat() for h in (9,11,14,17)],
                "price_cents":sum(x["price_cents"] for x in selected),"duration_min":sum(x["duration_min"] for x in selected),"timezone":"America/Campo_Grande"}
    async def create(_db,t,pro,ids,sender,name,start,key):
        assert t==tenant and sender==phone
        events.append("hold")
        selected=[x for x in catalog if x["id"] in ids]
        row={"id":uuid4(),"protocol":"AG-ABCDEF","status":"hold","start_at":start,"timezone":"America/Campo_Grande",
             "professional":next(x["name"] for x in pros if x["id"]==pro),"total_cents":sum(x["price_cents"] for x in selected),
             "items":[{"service_id":x["id"],"name_snapshot":x["name"],"duration_snapshot_min":x["duration_min"]} for x in selected],
             "expected_snapshot":"snapshot-"+str(len(events))}
        records[row["id"]]=row
        return row
    async def owned(t,sender,*,item_id=None,**_):
        assert t==tenant and sender==phone
        return records.get(uuid4() if item_id is None else __import__("uuid").UUID(str(item_id)))
    async def confirm(_db,t,item_id,*,customer_phone,expected_snapshot):
        assert t==tenant and customer_phone==phone and expected_snapshot==records[item_id]["expected_snapshot"]
        events.append("confirm")
        records[item_id]["status"]="confirmed"
        return records[item_id]
    monkeypatch.setattr(bot,"get_session_factory",lambda:Session)
    monkeypatch.setattr(bot,"tenant_transaction",tx)
    monkeypatch.setattr(bot,"preferences",prefs)
    monkeypatch.setattr(bot,"try_command",no_commands)
    monkeypatch.setattr(bot,"consent",decision)
    monkeypatch.setattr(bot,"list_slots",slots)
    monkeypatch.setattr(bot,"book_appointment",create)
    monkeypatch.setattr(bot,"owned_booking",owned)
    monkeypatch.setattr(bot,"confirm_hold",confirm)
    redis=MemoryRedis()
    async def turn(body,message_id):
        return await bot.reply(tenant,channel,phone,body,redis,message_id=message_id)
    def state(): return json.loads(redis.data[bot.state_key(str(tenant),str(channel),phone)])
    return {"turn":turn,"state":state,"events":events,"day":day,"catalog":catalog,
            "redis":redis,"records":records,"tenant":tenant,"phone":phone,"channel":channel,"decision":decision}


async def proposal(meta):
    assert "Ana" in await meta["turn"]("Quero agendar Corte e Barba","choose-services")
    assert "dia" in (await meta["turn"]("Ana","choose-person")).lower()
    slots=await meta["turn"](meta["day"].isoformat(),"choose-date")
    assert "09h" in slots and "17h" in slots and "número" not in slots
    assert meta["state"]()["step"]=="time", meta["state"]()
    result=await meta["turn"]("às 11","choose-time")
    assert "*Serviços:* Corte e Barba" in result and "R$ 55,00" in result and "*Horário:* 11h" in result
    assert "SIM" not in result and "NÃO" not in result
    return result


@pytest.mark.asyncio
async def test_meta_natural_flow_new_consent_and_message_retry(meta):
    initial=await proposal(meta)
    assert meta["events"]==["hold"]
    duplicate=await meta["turn"]("sim","choose-time")
    assert duplicate==initial and meta["events"]==["hold"]
    done=await meta["turn"]("pode marcar","consent")
    assert "confirmado" in done and "AG-ABCDEF" not in done and "R$ 55,00" in done
    assert len(meta["records"]) == 1 and next(iter(meta["records"].values()))["protocol"] == "AG-ABCDEF"
    assert next(iter(meta["records"].values()))["status"] == "confirmed"
    assert meta["events"]==["hold","confirm"]
    assert await meta["turn"]("pode marcar","consent")==done
    assert meta["events"]==["hold","confirm"]


@pytest.mark.asyncio
async def test_meta_price_only_and_interruption_require_new_review(meta):
    price=await meta["turn"]("Só quero saber o preço do corte","price")
    assert "R$ 35,00" in price and "Qual dia" not in price and meta["events"]==[]
    await proposal(meta)
    price=await meta["turn"]("Quanto fica a barba?","question")
    assert "R$ 20,00" in price
    renewed=await meta["turn"]("pode confirmar","first-consent")
    assert "Posso confirmar" in renewed and meta["events"]==["hold"]
    done=await meta["turn"]("isso mesmo","new-consent")
    assert "confirmado" in done and meta["events"]==["hold","confirm"]


@pytest.mark.asyncio
async def test_meta_decline_preserves_selection_and_amendment_never_confirms(meta):
    await proposal(meta)
    answer=await meta["turn"]("não","decline")
    assert "ajustar" in answer and len(meta["state"]()["services"])==2
    assert meta["events"]==["hold"]
    renewed=await meta["turn"]("melhor às 14","change")
    assert "14h" in renewed and meta["events"]==["hold","hold"]
    again=await meta["turn"]("pode marcar mas melhor às 17","change-again")
    assert "17h" in again and "confirm" not in meta["events"]


@pytest.mark.asyncio
async def test_meta_missing_message_identity_cannot_write(meta):
    await meta["turn"]("Quero agendar Corte","a")
    await meta["turn"]("Ana","b")
    await meta["turn"](meta["day"].isoformat(),"c")
    text=await meta["turn"]("às 11",None)
    assert "nova mensagem" in text and meta["events"]==[]


@pytest.mark.asyncio
async def test_meta_availability_is_read_only_and_handoff_preserves_context(meta):
    await meta["turn"]("Quero agendar Corte","a")
    await meta["turn"]("Ana","b")
    response=await meta["turn"]("Tem horário " + meta["day"].isoformat() + " às 14?","query")
    assert "14h" in response and meta["events"]==[]
    response=await meta["turn"]("atendente","human")
    assert "equipe" in response and meta["state"]()["services"]
    assert await meta["turn"]("oi","later") is None


@pytest.mark.asyncio
async def test_meta_qualified_consent_never_runs_confirm(meta):
    await proposal(meta)
    response=await meta["turn"]("Pode ser, quanto fica?","question")
    assert "R$ 55,00" in response and meta["events"]==["hold"]
    response=await meta["turn"]("acho que sim","uncertain")
    assert "confirm" not in meta["events"]


@pytest.fixture
def command_flow(monkeypatch,meta):
    tenant,phone,channel=meta["tenant"],meta["phone"],meta["channel"]
    start=datetime.combine(meta["day"],datetime.min.time(),tzinfo=ZoneInfo("America/Campo_Grande")).replace(hour=10)
    record={"id":uuid4(),"protocol":"AG-ABCDEF","professional_id":uuid4(),"professional":"Ana",
        "items":[{"service_id":uuid4(),"name_snapshot":"Barba","duration_snapshot_min":20}],
        "total_cents":2000,"start_at":start,"timezone":"America/Campo_Grande","expected_snapshot":"original-1"}
    revision={"value":1}
    events=[]
    async def owned(t,sender,protocol):
        return record if t==tenant and sender==phone and protocol==record["protocol"] else None
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    async def preview(_db,t,item_id,new_start,*,customer_phone):
        assert t==tenant and customer_phone==phone and item_id==record["id"]
        return {**record,"start_at":new_start,"expected_snapshot":"proposal-"+str(revision["value"])}
    async def reschedule(_db,t,item_id,new_start,*,expected_snapshot,customer_phone):
        assert t==tenant and item_id==record["id"] and customer_phone==phone
        if expected_snapshot!="proposal-"+str(revision["value"]): raise HTTPException(409,"changed")
        events.append("reschedule")
        record["start_at"]=new_start
        return dict(record)
    async def cancel(_db,t,item_id,*,expected_snapshot,customer_phone,future_only=False):
        assert t==tenant and item_id==record["id"] and customer_phone==phone
        assert future_only is True
        if expected_snapshot!=record["expected_snapshot"]: raise HTTPException(409,"changed")
        events.append("cancel")
        return dict(record)
    async def slots(_db,t,pro,ids,on_day):
        return {"slots":[datetime.combine(on_day,datetime.min.time(),tzinfo=start.tzinfo).replace(hour=h).isoformat() for h in (11,14,17)],
                "price_cents":2000,"duration_min":20}
    monkeypatch.setattr(commands,"_owned_booking",owned)
    monkeypatch.setattr(commands,"get_session_factory",lambda:Session)
    monkeypatch.setattr(commands,"consent",meta["decision"])
    monkeypatch.setattr(commands,"preview_reschedule",preview)
    monkeypatch.setattr(commands,"reschedule_appointment",reschedule)
    monkeypatch.setattr(commands,"cancel_appointment",cancel)
    monkeypatch.setattr(commands,"list_slots",slots)
    redis=MemoryRedis()
    async def turn(body,message_id="new-message",*,sender=None):
        current=json.loads(await redis.get("customer-key") or "{}")
        return (await commands.try_command(tenant,channel,sender or phone,body,current,redis,"customer-key",message_id=message_id))[1]
    return {"turn":turn,"record":record,"events":events,"redis":redis,"revision":revision}


@pytest.mark.asyncio
async def test_meta_reschedule_proposal_is_read_only_and_requires_new_consent(command_flow):
    f=command_flow
    await f["turn"]("remarcar AG-ABCDEF","request")
    proposal=await f["turn"](f["record"]["start_at"].strftime("%Y-%m-%d")+" às 14","choose")
    assert "Proposta de remarcação" in proposal and "R$ 20,00" in proposal and f["events"]==[]
    repeat=await f["turn"]("pode confirmar","choose")
    assert "nova resposta" in repeat and f["events"]==[]
    done=await f["turn"]("pode confirmar","consent")
    assert "Remarcação confirmada" in done and f["events"]==["reschedule"]


@pytest.mark.asyncio
async def test_meta_reschedule_catalog_change_does_not_move_booking(command_flow):
    f=command_flow
    await f["turn"]("remarcar AG-ABCDEF","request")
    await f["turn"](f["record"]["start_at"].strftime("%Y-%m-%d")+" às 14","choose")
    f["revision"]["value"]+=1
    answer=await f["turn"]("pode confirmar","consent")
    assert "não foi confirmada" in answer and f["events"]==[] and f["record"]["start_at"].hour==10


@pytest.mark.asyncio
async def test_meta_cancel_full_summary_rejected_on_missing_id_or_other_phone(command_flow):
    f=command_flow
    answer=await f["turn"]("cancelar AG-ABCDEF",None)
    assert "nova mensagem" in answer and f["events"]==[]
    answer=await f["turn"]("cancelar AG-ABCDEF","foreign",sender="+5567999999902")
    assert "não encontrada" in answer
    proposal=await f["turn"]("cancelar AG-ABCDEF","request")
    assert "*Serviço:* Barba" in proposal and "*Horário:* 10h" in proposal and "R$ 20,00" in proposal
    await f["turn"]("pode cancelar","request")
    assert f["events"]==[]
    done=await f["turn"]("pode cancelar","fresh")
    assert "Cancelamento confirmado" in done and f["events"]==["cancel"]


@pytest.mark.asyncio
async def test_meta_cancel_decline_keeps_booking_context_and_question_invalidates_consent(command_flow):
    f=command_flow
    await f["turn"]("cancelar AG-ABCDEF","request")
    await f["turn"]("não","decline")
    state=json.loads(await f["redis"].get("customer-key"))
    assert state["protocol"]=="AG-ABCDEF" and "summary_message_id" not in state and f["events"]==[]
    await f["turn"]("cancelar AG-ABCDEF","new-request")
    await f["turn"]("Tem certeza?","question")
    renewed=await f["turn"]("pode cancelar","consent")
    assert "selecionado para cancelamento" in renewed and f["events"]==[]
    done=await f["turn"]("confirmo o cancelamento","fresh-consent")
    assert "Cancelamento confirmado" in done and f["events"]==["cancel"]


@pytest.mark.asyncio
async def test_meta_semantic_fallback_requires_fresh_context_and_local_guards_win(monkeypatch):
    from app import whatsapp_dialogue as helper
    from app.qr_dialogue import DialogueDecision
    calls=[]
    async def semantic(**kwargs):
        calls.append(kwargs)
        return DialogueDecision("affirm","semantic")
    monkeypatch.setattr(helper,"interpret_contextual_response",semantic)
    assert (await helper.consent("Do jeito que você mostrou, pode seguir",pending=True)).kind=="uncertain"
    assert calls==[]
    assert (await helper.consent("Do jeito que você mostrou, pode seguir",pending=True,semantic_allowed=True)).kind=="affirm"
    assert len(calls)==1
    for body in ("Confirmo se tiver desconto","pode marcar mas troca para amanhã","Pode ser, quanto fica?"):
        assert (await helper.consent(body,pending=True,semantic_allowed=True)).kind!="affirm"
    assert len(calls)==1
