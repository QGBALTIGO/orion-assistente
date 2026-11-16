"""Fifty NEW composed journeys: real turn, booking PostgreSQL/RLS and Redis.

No production state, provider HTTP, ASR or WhatsApp socket is used. Each case
starts a fresh conversation, traverses multiple customer turns, inspects the
actual persisted agenda and ends with a verified terminal state and cleanup.
The deterministic production build_plan is the semantic boundary, not a fake
that returns a requested step or business success.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from redis.asyncio import from_url
from sqlalchemy import text

from app import booking_service, qr_ai, qr_customer, qr_profile, qr_cancellation, whatsapp_dialogue
from app import qr_assistant_interactions as choices
from app.booking_service import book_appointment, confirm_hold, cancel_appointment, reschedule_appointment
from app.business_profile_schemas import BusinessProfile
from app.database import tenant_transaction
from app.groq_ai import GroqServiceError
from app.qr_understanding import build_plan
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration
JOURNEYS = [
  [
    "FJ01",
    "new_customer",
    "Cliente novo: cadastrar nome, reservar, confirmar e cancelar"
  ],
  [
    "FJ02",
    "known_customer",
    "Cliente já identificado: não pedir nome de novo"
  ],
  [
    "FJ03",
    "combo",
    "Combinação de serviços: total, duração e intervalos reais"
  ],
  [
    "FJ04",
    "add_service",
    "Adicionar barba ao rascunho e confirmar só o resumo novo"
  ],
  [
    "FJ05",
    "remove_service",
    "Retirar serviço do rascunho sem manter cobrança anterior"
  ],
  [
    "FJ06",
    "dedupe_service",
    "Sinônimos de corte não duplicam item nem preço"
  ],
  [
    "FJ07",
    "beneficiary",
    "Pedido para filho exige autorização da equipe; titular mantém identidade e faz pedido próprio"
  ],
  [
    "FJ08",
    "change_professional",
    "Trocar profissional antes do consentimento"
  ],
  [
    "FJ09",
    "change_time",
    "Corrigir horário antes do consentimento"
  ],
  [
    "FJ10",
    "change_date",
    "Corrigir data e conferir expediente do novo dia"
  ],
  [
    "FJ11",
    "decline_summary",
    "Recusar resumo; SIM posterior não ressuscita reserva"
  ],
  [
    "FJ12",
    "conditional_consent",
    "Consentimento condicional exige confirmação separada"
  ],
  [
    "FJ13",
    "no_preference",
    "Sem preferência: escolher profissional efetivamente livre"
  ],
  [
    "FJ14",
    "compare_professionals",
    "Comparar duas agendas sem reserva e escolher uma depois"
  ],
  [
    "FJ15",
    "ineligible_professional",
    "Profissional não habilitado: corrigir antes de reservar"
  ],
  [
    "FJ16",
    "homonym_professional",
    "Profissionais homônimos: escolha por ID estável"
  ],
  [
    "FJ17",
    "ambiguous_service",
    "Serviços ambíguos: esclarecer antes do agendamento"
  ],
  [
    "FJ18",
    "large_catalog",
    "Catálogo extenso: navegar sem truncar autoridade"
  ],
  [
    "FJ19",
    "variable_price",
    "Preço a partir de: explicar e manter ressalva no resumo"
  ],
  [
    "FJ20",
    "price_changed",
    "Preço alterado após proposta: impedir confirmação antiga"
  ],
  [
    "FJ21",
    "service_disabled",
    "Serviço desativado: recusar resumo antigo e escolher ativo"
  ],
  [
    "FJ22",
    "professional_disabled",
    "Profissional inativado após proposta: escolher outro"
  ],
  [
    "FJ23",
    "day_blocked",
    "Bloqueio de dia inteiro: buscar outro profissional"
  ],
  [
    "FJ24",
    "working_break",
    "Intervalo de almoço: rejeitar sobreposição e escolher válido"
  ],
  [
    "FJ25",
    "buffer_conflict",
    "Preparação/limpeza: conflito fora do horário visível"
  ],
  [
    "FJ26",
    "closing_duration",
    "Serviço longo no fechamento: recusar e antecipar"
  ],
  [
    "FJ27",
    "bounded_time_window",
    "Janela depois/antes: consultar, escolher e confirmar"
  ],
  [
    "FJ28",
    "invalid_date",
    "Data inválida: esclarecer sem reservar e corrigir"
  ],
  [
    "FJ29",
    "past_time",
    "Horário passado: rejeitar e escolher data futura"
  ],
  [
    "FJ30",
    "expired_hold",
    "Hold vencido: consentimento tardio não vale para novo"
  ],
  [
    "FJ31",
    "stale_summary",
    "Resumo superado por correção: nunca confirmar o horário velho"
  ],
  [
    "FJ32",
    "duplicate_confirmation",
    "Confirmação repetida: uma única transição persistida"
  ],
  [
    "FJ33",
    "human_handoff",
    "Handoff: encerrar automação sem confirmar o rascunho"
  ],
  [
    "FJ34",
    "other_contact",
    "Outro contato não lista nem cancela a reserva"
  ],
  [
    "FJ35",
    "other_tenant",
    "Outro tenant não lê nem cancela o protocolo"
  ],
  [
    "FJ36",
    "parallel_namespace",
    "Conversa paralela não rouba rascunho ou consentimento"
  ],
  [
    "FJ37",
    "two_reservations",
    "Duas reservas próprias: escolher uma sem cancelar ambas"
  ],
  [
    "FJ38",
    "reschedule",
    "Remarcação própria: preservar protocolo e atualizar agenda"
  ],
  [
    "FJ39",
    "reschedule_conflict",
    "Remarcação ocupada: preservar original até alternativa válida"
  ],
  [
    "FJ40",
    "cancel_withdrawal",
    "Desistir do cancelamento e manter reserva"
  ],
  [
    "FJ41",
    "cancel_stale_snapshot",
    "Agenda alterada após resumo de cancelamento: revisar de novo"
  ],
  [
    "FJ42",
    "completed_history",
    "Atendimento concluído: recusar cancelamento e criar novo pedido"
  ],
  [
    "FJ43",
    "no_show_history",
    "Falta anterior: não inventar multa ou bloquear novo pedido"
  ],
  [
    "FJ44",
    "repeat_completed",
    "Repetir último concluído: revalidar serviços e pedir consentimento"
  ],
  [
    "FJ45",
    "factual_interruptions",
    "Interrupções de FAQ: terminar sem repetir todas as escolhas"
  ],
  [
    "FJ46",
    "privacy_credential",
    "Credencial declarada: orientar privacidade e não reter valor"
  ],
  [
    "FJ47",
    "simulated_audio_text",
    "Transcrição de áudio simulada: consultar sem nome de profissional inventado"
  ],
  [
    "FJ48",
    "assistant_identity",
    "Pergunta sobre identidade: informar assistente sem handoff acidental"
  ],
  [
    "FJ49",
    "planner_failure",
    "Falha do planejador: nenhuma confirmação parcial; retomar depois"
  ],
  [
    "FJ50",
    "typed_ack",
    "Escolha estruturada com ACK real: vincular resumo e consumir uma vez"
  ]
]


class Journey:
    def __init__(self, db, redis, profile):
        self.d, self.redis, self.profile = db, redis, profile
        self.tenant, self.pn = db["a"], phone(1901)[1:]
        self.namespace = "complete50:" + str(uuid4())
        self.messages, self.planner_calls = [], []
        self.planner_failure = False
        self.background_ids = set()
        self.plan_outputs = []
        self.slot_reads = []
        self.completed = False

    @property
    def key(self):
        return f"orion:qr:ai:{self.tenant}:{self.namespace}"

    @property
    def day(self):
        return self.d["day"].isoformat()

    def request(self, service="corte", hour="10:00", professional="Bruno", day=None):
        return f"Quero agendar {service} em {day or self.day} às {hour} com {professional}"

    async def state(self):
        return json.loads(await self.redis.get(self.key) or "{}")

    async def rows(self, *, tenant=None, pn=None):
        t, p = tenant or self.tenant, pn or self.pn
        async with self.d["migrator"].connect() as db:
            rows = (await db.execute(text("""SELECT a.id,a.status,a.protocol,a.professional_id,
                a.start_at,a.end_at,a.occupied_from,a.occupied_until,a.total_cents,a.hold_expires_at,
                c.name AS customer_name,c.whatsapp_e164
                FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:t AND c.whatsapp_e164=:pn ORDER BY a.created_at,a.id"""),
                {"t": t, "pn": "+" + p})).mappings().all()
            return [dict(row) for row in rows]

    async def tenant_agenda_snapshot(self):
        # Independent oracle includes every synthetic contact in this tenant,
        # history and calendar outbox, not just the current contact's row.
        snapshot={}
        async with self.d['migrator'].connect() as db:
            for table in ('appointments','appointment_history','calendar_outbox'):
                values=(await db.execute(text(f'SELECT to_jsonb(x) AS value FROM {table} x WHERE tenant_id=:t ORDER BY to_jsonb(x)::text'),
                    {'t':self.tenant})).scalars().all()
                snapshot[table]=values
        return snapshot

    async def say(self, message, *, read_only=False, consent=False, message_id=None, locked=False, **kwargs):
        before = await self.rows()
        before_state = await self.state()
        before_agenda = await self.tenant_agenda_snapshot() if read_only else None
        token = qr_ai._message_identity.set(message_id or "journey-" + str(uuid4()))
        try:
            if locked:
                result = await qr_ai.run_locked(self.tenant, self.pn, message, self.namespace,
                    message_id=qr_ai._message_identity.get(), **kwargs)
            else:
                result = await qr_ai.turn(self.tenant, self.pn, message, self.redis, self.namespace, preview=False)
        finally:
            qr_ai._message_identity.reset(token)
        after = await self.rows()
        self.messages.append({"message": message, "step": result.get("step"),
            "answer":result.get("answer"),"state_before":before_state,"state_after":await self.state(),
            "appointments_before":before,"appointments_after":after})
        if read_only:
            assert after == before, ("Information changed persisted appointments", message, result)
            assert await self.tenant_agenda_snapshot()==before_agenda, ('Information mutated another contact/history/outbox',message,result)
        if not consent:
            old_confirmed = {row["id"] for row in before if row["status"] == "confirmed"}
            new_confirmed = {row["id"] for row in after if row["status"] == "confirmed"}
            assert new_confirmed <= old_confirmed, ("Consent crossed a message boundary", message, result)
        assert result.get("answer") is None or isinstance(result["answer"], str)
        return result

    async def greeting(self, *, known=True):
        if known:
            await qr_customer.remember_name(self.tenant, self.pn, "Alex")
        r = await self.say("Oi", read_only=True)
        assert "R$" not in r["answer"] and (not known or "Alex" in r["answer"])
        return r

    async def info(self):
        r = await self.say("Quanto custa corte?", read_only=True)
        assert "35" in r["answer"]
        r = await self.say("Aceitam Pix?", read_only=True)
        assert "Pix" in r["answer"]

    async def booking(self, service="corte", hour="10:00", professional="Bruno", day=None):
        r = await self.say(self.request(service,hour,professional,day))
        assert r["step"] == "confirm", r
        state = await self.state()
        assert state.get("hold")
        rows = await self.rows()
        row = next(row for row in rows if str(row["id"]) == state["hold"])
        assert row["status"] == "hold"
        return row

    async def accept(self):
        # An informational interruption deliberately requires a fresh summary.
        r = await self.say("Quero ver o resumo de novo")
        assert r["step"] in {"confirm", "reschedule_confirm", "cancel_confirm"}, r
        r = await self.say("Pode marcar", consent=True)
        assert r["step"] == "confirmed", r
        rows = [row for row in await self.rows() if row["status"] == "confirmed" and row["id"] not in self.background_ids]
        assert len(rows) == 1
        return rows[0]

    async def current_hold(self):
        state = await self.state()
        assert state.get('hold'), state
        row = next(row for row in await self.rows() if str(row['id']) == state['hold'])
        assert row['status'] == 'hold'
        return row

    async def verify(self, row, *, services=None, professional=None, hour=None, day=None, total=None):
        async with self.d['migrator'].connect() as db:
            items = (await db.execute(text('SELECT service_id,name_snapshot,price_snapshot_cents,price_mode_snapshot,duration_snapshot_min FROM appointment_items WHERE tenant_id=:t AND appointment_id=:id ORDER BY item_order'),
                {'t':self.tenant,'id':row['id']})).mappings().all()
            registered=(await db.execute(text('SELECT id,name,price_cents,price_mode,duration_min,prep_min,cleanup_min FROM services WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[]))'),
                {'t':self.tenant,'ids':[item['service_id'] for item in items]})).mappings().all()
        registered={item['id']:item for item in registered}
        assert len(registered)==len(items)
        for item in items:
            service=registered[item['service_id']]
            assert item['name_snapshot']==service['name']
            assert item['duration_snapshot_min']==service['duration_min']
            assert item['price_snapshot_cents']==service['price_cents']
            assert item['price_mode_snapshot']==service['price_mode']
        if services is not None:
            assert {item['service_id'] for item in items} == set(services)
            assert len(items) == len(services), 'Synonyms must not duplicate items'
        if professional is not None:
            assert row['professional_id'] == professional
        if hour is not None:
            assert row['start_at'].astimezone(start_at(self.d['day'],10).tzinfo).strftime('%H:%M') == hour
        if day is not None:
            assert row['start_at'].astimezone(start_at(self.d['day'],10).tzinfo).date() == day
        if total is not None:
            assert row['total_cents'] == total == sum(item['price_snapshot_cents'] for item in items)
        assert row['end_at']-row['start_at'] == timedelta(minutes=sum(item['duration_snapshot_min'] for item in items))
        assert row['occupied_from']==row['start_at']-timedelta(minutes=sum(item['prep_min'] for item in registered.values()))
        assert row['occupied_until']==row['end_at']+timedelta(minutes=sum(item['cleanup_min'] for item in registered.values()))

    async def choose(self, result, index=0, *, consent=False):
        presentation=result['presentation']
        def tokens(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='id' and isinstance(item,str) and item.startswith('oa1_'):
                        yield item
                    else:
                        yield from tokens(item)
            elif isinstance(value,list):
                for item in value:
                    yield from tokens(item)
        token=list(tokens(presentation['action']))[index]
        provider='synthetic-provider-'+str(uuid4())
        assert await choices.acknowledge(self.redis,self.tenant,self.pn,self.namespace,
            choices.PresentationAck(phone=self.pn,presentation_id=presentation['id'],provider_id=provider)) == {'acknowledged':True}
        self.last_selection=choices.Selection(id=token,quoted_message_id=provider)
        return await self.say('[Escolha recebida]',locked=True,consent=consent,
            capabilities=choices.Capabilities(assistant_interactions=1),
            selection=self.last_selection)

    async def finish_booking(self, **expected):
        await self.verify(await self.current_hold(),**expected)
        row=await self.accept()
        await self.verify(row,**expected)
        await self.cancel_conversation(row)
        await self.assert_final()

    async def sql(self, statement, **params):
        async with self.d['migrator'].begin() as db:
            await db.execute(text(statement),{'t':self.tenant,**params})

    @staticmethod
    def displayed_times(answer):
        result=[]
        clock=r'\d{1,2}(?:h|:)(?:\d{2})?'
        for line in answer.splitlines():
            if re.fullmatch(clock+r'(?:\s*[•·,]\s*'+clock+r')*',line.strip()):
                for hour,minute in re.findall(r'(\d{1,2})(?:h|:)(\d{2})?',line):
                    result.append(int(hour)*60+int(minute or 0))
        return result

    async def cancel_conversation(self, row=None):
        r = await self.say("Quero cancelar minha reserva")
        assert r["step"] in {"cancel_confirm","cancel_pick"}, r
        if r["step"] == "cancel_pick":
            assert row is not None
            r = await self.say(row["protocol"])
            assert r["step"] == "cancel_confirm", r
        r = await self.say("Pode cancelar", consent=True)
        assert r["step"] == "cancelled", r
        if row:
            assert next(x for x in await self.rows() if x["id"] == row["id"])["status"] == "cancelled"
        return r

    async def background(self, *, hour="10:00", professional=None, service=None,
                         number=1902, status="confirmed"):
        async with self.d["factory"]() as db:
            row = await book_appointment(db, self.tenant, professional or self.d["pro"],
                service or [self.d["cut"]], phone(number), "Contexto sintético",
                start_at(self.d["day"], *map(int,hour.split(":"))), "background-" + str(uuid4()))
            row = await confirm_hold(db,self.tenant,row["id"])
        if status != "confirmed":
            async with self.d["migrator"].begin() as db:
                await db.execute(text("""UPDATE appointments SET status=:s,
                    start_at=start_at-interval '14 days',end_at=end_at-interval '14 days',
                    occupied_from=occupied_from-interval '14 days',occupied_until=occupied_until-interval '14 days'
                    WHERE tenant_id=:t AND id=:id"""),
                    {"s":status,"t":self.tenant,"id":row["id"]})
        self.background_ids.add(row["id"])
        return row

    async def assert_final(self, *, confirmed=0):
        own = [row for row in await self.rows() if row["id"] not in self.background_ids]
        assert sum(row["status"] == "confirmed" for row in own) == confirmed
        assert not any(row["status"] == "hold" and row["hold_expires_at"] > datetime.now(row["hold_expires_at"].tzinfo)
            for row in own), own
        assert len(self.messages) >= 6, "A one-message check is not a complete journey"


@pytest_asyncio.fixture
async def complete_journey(booking_db, monkeypatch, request):
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Real disposable Redis is mandatory")
        pytest.skip("Real disposable Redis not configured")
    redis = from_url(url,decode_responses=True,socket_connect_timeout=2,socket_timeout=3)
    await redis.ping()
    d = booking_db
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='elite' WHERE tenant_id IN (:a,:b)"),
            {"a":d["a"],"b":d["b"]})
        await db.execute(text("UPDATE professionals SET name=CASE WHEN id=:p THEN 'Bruno' ELSE 'Breno' END WHERE tenant_id=:t"),
            {"p":d["pro"],"t":d["a"]})
        # A second registered day makes date corrections cross real schedules.
        await db.execute(text("""INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time)
            SELECT tenant_id,professional_id,:weekday,'08:00','18:00' FROM working_hours
            WHERE tenant_id=:t"""),{"weekday":(d["day"]+timedelta(days=1)).weekday(),"t":d["a"]})
    profile = BusinessProfile(business_name="Barbearia sintética de homologação",
        timezone="America/Campo_Grande",capabilities=["catalog_answers","appointments","rescheduling"],
        faqs=[{"question":"Aceitam Pix?","answer":"Aceitamos Pix."},
              {"question":"Tem estacionamento?","answer":"Temos estacionamento no local."},
              {"question":"Qual é o endereço?","answer":"Rua Sintética, 100."}])
    h = Journey(d,redis,profile)
    identity=request.node.callspec.params['identity']
    async def published(tenant):
        return {"id":None,"revision":1,"profile":profile}
    async def planner(**kw):
        h.planner_calls.append(kw["message"])
        if h.planner_failure:
            raise GroqServiceError(503,"Synthetic planner unavailable")
        plan=build_plan(message=kw["message"],catalog=kw["catalog"],professionals=kw["professionals"],
            context=kw["context"],faqs=kw["faqs"],reference_date=kw["reference_date"])
        h.plan_outputs.append(plan.model_dump(mode='json'))
        return plan
    for module in (qr_ai,qr_customer,qr_cancellation,whatsapp_dialogue):
        monkeypatch.setattr(module,"get_session_factory",lambda:d["factory"])
    monkeypatch.setattr(qr_profile,"runtime_profile",published)
    monkeypatch.setattr(qr_ai,"interpret_plan",planner)
    actual_slots=qr_ai.list_slots
    async def observed_slots(db,tenant,professional,services,day,**kwargs):
        result=await actual_slots(db,tenant,professional,services,day,**kwargs)
        h.slot_reads.append({'tenant':str(tenant),'professional':str(professional),
            'services':[str(value) for value in services],'day':day.isoformat(),'slots':list(result['slots'])})
        return result
    monkeypatch.setattr(qr_ai,'list_slots',observed_slots)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED","1")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED","0")
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED","0")
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    try:
        yield h
    finally:
        # Teardown never talks to a provider. Cancel only this fixture's own
        # active synthetic records before parent fixture removes its tenant.
        async with d["migrator"].connect() as db:
            active = (await db.execute(text("SELECT id FROM appointments WHERE tenant_id=:t AND status IN ('hold','confirmed')"),
                {"t":d["a"]})).scalars().all()
        for appointment_id in active:
            async with d["factory"]() as db:
                await cancel_appointment(db,d["a"],appointment_id)
        async with d["migrator"].begin() as db:
            remaining=(await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id IN (:a,:b) AND status IN ('hold','confirmed')"),{'a':d['a'],'b':d['b']})).scalar_one()
            assert remaining==0,'Fixture cleanup must not leave live synthetic reservations'
            await db.execute(text("DELETE FROM management_requests WHERE tenant_id IN (:a,:b)"),{"a":d["a"],"b":d["b"]})
        keys = []
        for tenant in (d['a'],d['b']):
            keys += [key async for key in redis.scan_iter(match=f"orion:qr:*{tenant}*")]
        if keys:
            await redis.delete(*keys)
        evidence=os.getenv('ORION_COMPLETE_JOURNEY_EVIDENCE')
        if evidence:
            directory=Path(evidence);directory.mkdir(parents=True,exist_ok=True)
            payload={'identity':identity,'completed_assertions':h.completed,'customer_turns':len(h.messages),
                'messages':h.messages,'local_plans':h.plan_outputs,'actual_pg_slot_reads':h.slot_reads,
                'cleanup_method':'explicit_domain_engine_after_conversation' if identity in {'FJ11','FJ33'} else 'conversation_then_fixture_domain_cleanup',
                'fixture_cleanup':{'active_synthetic_records_cancelled':len(active),'active_reservations_remaining':remaining,'redis_keys_removed':len(keys)},
                'real_postgres':True,'real_redis':True,'provider_http':False,'physical_audio':False,'whatsapp_transport':False}
            (directory/(identity+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf8')
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,kind,title",JOURNEYS,ids=[row[0]+"_"+row[1] for row in JOURNEYS])
async def test_complete_customer_journey(complete_journey,monkeypatch,identity,kind,title):
    h = complete_journey
    await h.greeting(known=kind!="new_customer")
    await h.info()
    if kind == "new_customer":
        await h.say("Meu nome é Alex")
        assert await qr_customer.customer_name(h.tenant,h.pn) == "Alex"
    if kind in {"new_customer","known_customer"}:
        await h.booking()
        row = await h.accept()
        await h.cancel_conversation(row)
        await h.assert_final()
    elif kind == "combo":
        await h.booking("corte e barba")
        await h.finish_booking(services=[h.d["cut"],h.d["beard"]],total=5500,hour="10:00")
    elif kind == "add_service":
        await h.booking()
        r=await h.say("Pode pôr barba junto?")
        assert r["step"]=="time",r
        assert (await h.say('10:00'))['step']=='confirm'
        await h.finish_booking(services=[h.d["cut"],h.d["beard"]],total=5500)
    elif kind == "remove_service":
        await h.booking("corte e barba")
        r=await h.say("Tira a barba, deixa só o corte")
        assert r["step"]=="time",r
        assert (await h.say('10:00'))['step']=='confirm'
        await h.finish_booking(services=[h.d["cut"]],total=3500)
    elif kind == "dedupe_service":
        await h.booking("corte e corte de cabelo")
        await h.finish_booking(services=[h.d["cut"]],total=3500)
    elif kind == "beneficiary":
        r=await h.say(f"Sou o pai do Miguel, quero corte em {h.day} às 10:00 com Bruno para ele")
        assert r['step']=='third_party_identity',r
        assert await qr_customer.customer_name(h.tenant,h.pn)=='Alex'
        assert not await h.rows(), 'A third-party request requires separate authorization'
        # The contact explicitly returns to a request for himself; the child's
        # relationship cannot silently rename the verified contact.
        await h.say("Agora o pedido é para mim, Alex")
        await h.booking()
        await h.finish_booking(services=[h.d["cut"]],total=3500)
    elif kind == "change_professional":
        old=await h.booking()
        await h.say(h.request(professional="Breno"))
        assert (await h.current_hold())["id"]!=old["id"]
        await h.finish_booking(professional=h.d["pro2"],hour="10:00")
    elif kind == "change_time":
        await h.booking()
        await h.say(h.request(hour="11:00"))
        await h.finish_booking(hour="11:00")
    elif kind == "change_date":
        await h.booking()
        target=h.d["day"]+timedelta(days=1)
        await h.say(h.request(day=target.isoformat()))
        await h.finish_booking(day=target)
    elif kind == "decline_summary":
        old=await h.booking()
        r=await h.say("Não")
        assert r["step"]=="request_closed",r
        await h.say("Pode marcar")
        assert not any(row["status"]=="confirmed" for row in await h.rows())
        await h.say("Obrigado",read_only=True)
        async with h.d["factory"]() as db:
            await cancel_appointment(db,h.tenant,old["id"])
        await h.assert_final()
    elif kind == "conditional_consent":
        await h.booking()
        r=await h.say("Confirmo se tiver desconto")
        assert r["step"]!="confirmed" and not any(x["status"]=="confirmed" for x in await h.rows())
        await h.say("Sem desconto, quero conferir o resumo")
        await h.finish_booking(total=3500)
    elif kind == "no_preference":
        await h.background()
        r=await h.say(f"Quero agendar corte em {h.day} às 10:00, tanto faz o profissional")
        assert r["step"]=="confirm",r
        await h.finish_booking(professional=h.d["pro2"])
    elif kind == "compare_professionals":
        await h.background()
        before=len(h.slot_reads)
        r=await h.say(f"Quais horários Bruno e Breno têm para corte em {h.day} às 10:00?",read_only=True)
        observed=h.slot_reads[before:]
        by_professional={item['professional']:item for item in observed if item['day']==h.day and item['services']==[str(h.d['cut'])]}
        assert {str(h.d['pro']),str(h.d['pro2'])} <= by_professional.keys(),'Both named agendas must actually be read'
        clock=lambda value:datetime.fromisoformat(value).astimezone(start_at(h.d['day'],10).tzinfo).strftime('%H:%M')
        assert '10:00' not in {clock(value) for value in by_professional[str(h.d['pro'])]['slots']}
        assert '10:00' in {clock(value) for value in by_professional[str(h.d['pro2'])]['slots']}
        assert "Breno" in r["answer"] and 'Bruno' not in r['answer'] and 600 in h.displayed_times(r['answer']),r
        await h.booking(professional="Breno")
        await h.finish_booking(professional=h.d["pro2"])
    elif kind == "ineligible_professional":
        await h.sql("DELETE FROM professional_services WHERE tenant_id=:t AND professional_id=:p AND service_id=:s",
            p=h.d["pro"],s=h.d["beard"])
        r=await h.say(h.request(service="barba"))
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking("barba",professional="Breno")
        await h.finish_booking(services=[h.d["beard"]],professional=h.d["pro2"],total=2000)
    elif kind == "homonym_professional":
        await h.sql("UPDATE professionals SET name='Bruno' WHERE tenant_id=:t")
        r=await h.say(h.request(),locked=True,capabilities=choices.Capabilities(assistant_interactions=1))
        assert r["step"]=="professional" and not await h.rows(),r
        people=(await h.state())['professionals']
        assert len(people)==2 and people[0]['id']!=people[1]['id']
        expected=UUID(people[1]['id'])
        r=await h.choose(r,index=1)
        assert r["step"]=="confirm",r
        assert (await h.current_hold())["professional_id"]==expected
        await h.finish_booking(professional=expected)
    elif kind == "ambiguous_service":
        await h.sql("UPDATE services SET name=CASE WHEN id=:cut THEN 'Corte masculino' ELSE 'Corte infantil' END WHERE tenant_id=:t AND id IN (:cut,:beard)",
            cut=h.d["cut"],beard=h.d["beard"])
        r=await h.say(f"Quero corte em {h.day} às 10:00 com Bruno")
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking("corte masculino")
        await h.finish_booking(services=[h.d["cut"]],total=3500)
    elif kind == "large_catalog":
        ids={}
        for index in range(1,121):
            identity=uuid4();ids[index]=identity
            await h.sql("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:t,:name,20,1000)",
                id=identity,name=f"Serviço {index:03}")
            for pro in (h.d["pro"],h.d["pro2"]):
                await h.sql("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)",p=pro,s=identity)
        r=await h.say("lista",read_only=True)
        assert r.get("_catalog_page"),r
        first_page=r['_catalog_page']
        assert len(first_page['rows'])<=8
        r=await h.say("Pode mostrar mais serviços?",read_only=True)
        assert r.get("_catalog_page") and r['_catalog_page']['page']==first_page['page']+1,r
        assert len(r['_catalog_page']['rows'])<=8
        assert not ({str(row['id']) for row in first_page['rows']} & {str(row['id']) for row in r['_catalog_page']['rows']})
        r=await h.say("buscar Serviço 117",read_only=True)
        assert any(str(item["id"])==str(ids[117]) for item in r["_catalog_page"]["rows"])
        await h.say("1",locked=True)
        assert (await h.state()).get("services")==[str(ids[117])]
        r=await h.say(f"Quero agendar em {h.day} às 10:00 com Bruno")
        assert r["step"]=="confirm",r
        await h.finish_booking(services=[ids[117]],total=1000)
    elif kind == "variable_price":
        await h.sql("UPDATE services SET price_mode='from' WHERE tenant_id=:t AND id=:s",s=h.d["cut"])
        r=await h.say("Quanto custa corte?",read_only=True)
        assert "partir" in r["answer"]
        r=await h.say("Mas por que a partir de?",read_only=True)
        assert "valor inicial" in r["answer"] and "35" in r["answer"],r
        r=await h.say(h.request())
        assert r["step"]=="confirm" and "partir" in r["answer"],r
        await h.finish_booking(total=3500)
    elif kind == "price_changed":
        old=await h.booking()
        await h.sql("UPDATE services SET price_cents=4500 WHERE tenant_id=:t AND id=:s",s=h.d["cut"])
        r=await h.say("Pode marcar")
        assert r["step"]!="confirmed"
        await h.booking()
        assert (await h.current_hold())["id"]!=old["id"]
        await h.finish_booking(total=4500)
    elif kind == "service_disabled":
        await h.booking()
        await h.sql("UPDATE services SET active=false WHERE tenant_id=:t AND id=:s",s=h.d["cut"])
        assert (await h.say("Pode marcar"))["step"]!="confirmed"
        await h.booking("barba")
        await h.finish_booking(services=[h.d["beard"]],total=2000)
    elif kind == "professional_disabled":
        await h.booking()
        await h.sql("UPDATE professionals SET active=false WHERE tenant_id=:t AND id=:p",p=h.d["pro"])
        assert (await h.say("Pode marcar"))["step"]!="confirmed"
        await h.booking(professional="Breno")
        await h.finish_booking(professional=h.d["pro2"])
    elif kind == "day_blocked":
        await h.sql("INSERT INTO schedule_blocks(tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:s,:e)",
            p=h.d["pro"],s=start_at(h.d["day"],0),e=start_at(h.d["day"]+timedelta(days=1),0))
        r=await h.say(h.request())
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking(professional="Breno")
        await h.finish_booking(professional=h.d["pro2"])
    elif kind == "working_break":
        await h.sql("INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time) SELECT :t,id,'12:00','13:00' FROM working_hours WHERE tenant_id=:t AND professional_id=:p",p=h.d["pro"])
        r=await h.say(h.request(hour="11:30"))
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking(hour="13:15")
        await h.finish_booking(hour="13:15")
    elif kind == "buffer_conflict":
        await h.background()
        r=await h.say(h.request(service="barba",hour="10:45"))
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking("barba",hour="11:00")
        await h.finish_booking(hour="11:00",total=2000)
    elif kind == "closing_duration":
        r=await h.say(h.request(service="platinado",hour="17:00"))
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking("platinado",hour="14:00")
        await h.finish_booking(services=[h.d["platinum"]],hour="14:00",total=15000)
    elif kind == "bounded_time_window":
        r=await h.say(f"Tem horário para corte em {h.day} com Bruno depois das 14h e antes das 16h?",read_only=True)
        times=h.displayed_times(r['answer'])
        assert r["step"]!="confirm" and 900 in times and all(840<value<960 for value in times),r
        assert h.slot_reads and any('T15:00:' in value for read in h.slot_reads for value in read['slots'])
        await h.booking(hour="15:00")
        await h.finish_booking(hour="15:00")
    elif kind == "invalid_date":
        r=await h.say("Quero corte em 31/02 às 10:00 com Bruno")
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.say("Vou corrigir a data")
        await h.booking()
        await h.finish_booking(day=h.d["day"])
    elif kind == "past_time":
        yesterday=(datetime.now(start_at(h.d["day"],10).tzinfo)-timedelta(days=1)).date()
        r=await h.say(h.request(day=yesterday.isoformat()))
        assert r["step"]!="confirm" and not await h.rows(),r
        await h.booking()
        await h.finish_booking(day=h.d["day"])
    elif kind == "expired_hold":
        old=await h.booking()
        await h.sql("UPDATE appointments SET hold_expires_at=clock_timestamp()-interval '1 second' WHERE tenant_id=:t AND id=:id",id=old["id"])
        state=await h.state();state["confirmation_expires_at"]=datetime.now().timestamp()-1
        await h.redis.set(h.key,json.dumps(state),ex=300)
        assert (await h.say("Pode marcar"))["step"]!="confirmed"
        await h.booking()
        assert (await h.current_hold())["id"]!=old["id"]
        await h.finish_booking()
    elif kind == "stale_summary":
        r=await h.say(h.request(),locked=True,capabilities=choices.Capabilities(assistant_interactions=1))
        old=r["presentation"]
        record=json.loads(await h.redis.get(h.key+":presentation"))
        token=old['action']['buttons'][0]['id']
        assert token.startswith('oa1_')
        provider="synthetic-old-summary"
        assert await choices.acknowledge(h.redis,h.tenant,h.pn,h.namespace,
            choices.PresentationAck(phone=h.pn,presentation_id=old["id"],provider_id=provider))=={'acknowledged':True}
        await h.say(h.request(hour="11:00"))
        r=await h.say("[Escolha recebida]",locked=True,capabilities=choices.Capabilities(assistant_interactions=1),
            selection=choices.Selection(id=token,quoted_message_id=provider))
        assert r["step"]=='interaction_expired',r
        await h.finish_booking(hour="11:00")
    elif kind == "duplicate_confirmation":
        await h.booking()
        original=await h.current_hold()
        first=await h.say("Pode marcar",locked=True,message_id="same-confirmation",consent=True)
        again=await h.say("Pode marcar",locked=True,message_id="same-confirmation",consent=True)
        assert first["step"]=="confirmed" and again["step"]=="duplicate"
        async with h.d["migrator"].connect() as db:
            count=(await db.execute(text("SELECT count(*) FROM appointment_history WHERE tenant_id=:t AND appointment_id=:id AND event='confirmed'"),
                {"t":h.tenant,"id":original["id"]})).scalar_one()
        assert count==1
        await h.cancel_conversation(next(x for x in await h.rows() if x["status"]=="confirmed"))
        await h.assert_final()
    elif kind == "human_handoff":
        old=await h.booking()
        r=await h.say("Quero falar com um atendente humano")
        assert r["step"]=="human",r
        await h.say("Pode marcar")
        assert not any(x["status"]=="confirmed" for x in await h.rows())
        await h.say("Obrigado",read_only=True)
        async with h.d["factory"]() as db:
            await cancel_appointment(db,h.tenant,old["id"])
        await h.assert_final()
    elif kind in {"other_contact","other_tenant"}:
        await h.booking()
        row=await h.accept()
        old=(h.tenant,h.pn,h.namespace)
        if kind=="other_contact": h.pn=phone(1903)[1:]
        else: h.tenant=h.d["b"]
        h.namespace="foreign:"+str(uuid4())
        result=await h.say("Quais são meus agendamentos?",read_only=True)
        assert row["protocol"] not in result["answer"]
        result=await h.say("Quero cancelar "+row["protocol"],read_only=True)
        assert row["protocol"] not in result["answer"] and result["step"]!="cancelled",result
        h.tenant,h.pn,h.namespace=old
        assert next(x for x in await h.rows() if x["id"]==row["id"])["status"]=="confirmed"
        await h.cancel_conversation(row)
        await h.assert_final()
    elif kind == "parallel_namespace":
        await h.booking()
        original=await h.state();namespace=h.namespace
        h.namespace+=":parallel"
        await h.say("Quanto custa a barba?",read_only=True)
        assert (await h.say("Pode marcar"))["step"]!="confirmed"
        h.namespace=namespace
        assert await h.state()==original
        await h.finish_booking(services=[h.d["cut"]],total=3500)
    elif kind == "two_reservations":
        # A valid opaque protocol may contain a clock-looking substring. Keep
        # the real booking/RLS transaction and make that domain-generated edge
        # deterministic rather than depending on random protocol characters.
        protocol_values=iter(("AG-T4H7LY","AG-3JL6PQ"))
        with monkeypatch.context() as protocol_seed:
            protocol_seed.setattr(booking_service,"protocol",lambda:next(protocol_values))
            a=await h.background(hour="10:00",number=1901)
            b=await h.background(hour="14:00",number=1901)
        assert a["protocol"]=="AG-T4H7LY" and b["protocol"]=="AG-3JL6PQ"
        listing=await h.say("Quais são meus agendamentos?",read_only=True)
        assert a["protocol"] in listing["answer"] and b["protocol"] in listing["answer"]
        await h.cancel_conversation(a)
        rows=await h.rows()
        assert next(x for x in rows if x["id"]==a["id"])["status"]=="cancelled"
        assert next(x for x in rows if x["id"]==b["id"])["status"]=="confirmed"
        await h.cancel_conversation(b)
        await h.assert_final()
    elif kind in {"reschedule","reschedule_conflict"}:
        if kind=="reschedule_conflict": await h.background(hour="14:00")
        await h.booking()
        row=await h.accept()
        assert (await h.say("Quero remarcar minha reserva"))["step"]=="reschedule_day"
        await h.say(h.day)
        r=await h.say("14:00")
        if kind=="reschedule_conflict":
            assert r["step"]!="reschedule_confirm",r
            current=next(x for x in await h.rows() if x["id"]==row["id"])
            assert current["start_at"]==row["start_at"]
            r=await h.say("15:00")
        assert r["step"]=="reschedule_confirm",r
        done=await h.say("Pode remarcar",consent=True)
        assert done["step"]=="rescheduled",done
        moved=next(x for x in await h.rows() if x["id"]==row["id"])
        assert moved["protocol"]==row["protocol"] and moved["start_at"]!=row["start_at"]
        await h.verify(moved,hour="15:00" if kind=="reschedule_conflict" else "14:00")
        await h.cancel_conversation(moved)
        await h.assert_final()
    elif kind == "cancel_withdrawal":
        await h.booking();row=await h.accept()
        assert (await h.say("Quero cancelar minha reserva"))["step"]=="cancel_confirm"
        before=await h.rows()
        assert (await h.say("Não cancela"))["step"]=="cancel_declined"
        # With no live proposal, this is a new cancellation REQUEST, never
        # consent to the withdrawn proposal. Follow its newly displayed summary.
        assert (await h.say("Pode cancelar"))['step']=='cancel_confirm'
        assert await h.rows()==before
        assert (await h.say('Pode cancelar',consent=True))['step']=='cancelled'
        assert next(x for x in await h.rows() if x['id']==row['id'])['status']=='cancelled'
        await h.assert_final()
    elif kind == "cancel_stale_snapshot":
        await h.booking();row=await h.accept()
        assert (await h.say("Quero cancelar minha reserva"))["step"]=="cancel_confirm"
        async with h.d["factory"]() as db:
            await reschedule_appointment(db,h.tenant,row["id"],start_at(h.d["day"],14))
        result=await h.say("Pode cancelar")
        assert result["step"]=="cancel_confirm" and "mudou" in result["answer"],result
        assert next(x for x in await h.rows() if x["id"]==row["id"])["status"]=="confirmed"
        assert (await h.say("Pode cancelar",consent=True))["step"]=="cancelled"
        await h.assert_final()
    elif kind in {"completed_history","no_show_history"}:
        old=await h.background(hour="09:00",number=1901,status="completed" if kind=="completed_history" else "no_show")
        r=await h.say("Quais são meus agendamentos?",read_only=True)
        assert not any(word in r["answer"].casefold() for word in ("multa","banido","proibido"))
        r=await h.say("Quero cancelar "+old["protocol"])
        assert r["step"]!="cancelled"
        await h.booking(hour="14:00")
        await h.finish_booking(hour="14:00")
        assert next(x for x in await h.rows() if x["id"]==old["id"])["status"]==("completed" if kind=="completed_history" else "no_show")
    elif kind == "repeat_completed":
        await h.background(hour="09:00",number=1901,status="completed")
        r=await h.say("Quero o mesmo da última vez")
        assert "Corte" in r["answer"] and not any(x["status"]=="hold" for x in await h.rows()),r
        await h.say("Sim")
        r=await h.say(f"Quero agendar em {h.day} às 14:00 com Bruno")
        assert r["step"]=="confirm",r
        await h.finish_booking(services=[h.d["cut"]],hour="14:00",total=3500)
    elif kind == "factual_interruptions":
        await h.say("Quero agendar corte")
        await h.say("Aceitam Pix?",read_only=True)
        await h.say("Em "+h.day)
        await h.say("Tem estacionamento?",read_only=True)
        await h.say("Com Bruno")
        await h.say("Qual é o endereço?",read_only=True)
        r=await h.say("Às 10:00")
        assert r["step"]=="confirm",r
        await h.finish_booking(services=[h.d["cut"]],professional=h.d["pro"],hour="10:00",day=h.d["day"],total=3500)
    elif kind == "privacy_credential":
        secret="SYNTHETIC_SECRET_NOT_FOR_STORAGE"
        r=await h.say("Minha senha é "+secret,read_only=True)
        assert secret not in r["answer"] and r["step"]=="privacy_guidance",r
        values=[await h.redis.get(key) async for key in h.redis.scan_iter(match=f"orion:qr:*{h.tenant}*") if await h.redis.type(key)=="string"]
        assert all(secret not in (value or "") for value in values)
        await h.booking()
        await h.finish_booking(total=3500)
    elif kind == "simulated_audio_text":
        await h.sql("UPDATE professionals SET name=CASE WHEN id=:p THEN 'Teste IA Orion 06nov' ELSE 'Teste Equipe' END WHERE tenant_id=:t",p=h.d["pro"])
        target=(datetime.now(start_at(h.d['day'],10).tzinfo)+timedelta(days=1)).date()
        transcript="Quero consultar horários de corte masculino amanhã depois das duas da tarde e os profissionais disponíveis. Este é um teste autorizado."
        r=await h.say(transcript,read_only=True)
        assert all(name in r['answer'] for name in ('Teste IA Orion 06nov','Teste Equipe')),r
        assert "não consegui distinguir" not in r["answer"].casefold() and r["step"]!="confirm",r
        plan=h.plan_outputs[-1]
        assert plan['requested_date']==target.isoformat() and plan['requested_time']=='14:00' and plan['time_relation']=='after',plan
        assert not await h.rows()
        await h.booking(hour="15:00",professional="Teste IA Orion 06nov",day=target.isoformat())
        await h.finish_booking(professional=h.d["pro"],hour="15:00",day=target)
    elif kind == "assistant_identity":
        r=await h.say("Você é uma pessoa ou um robô?",read_only=True)
        assert "virtual" in r["answer"].casefold() and r["step"]!="human",r
        await h.booking()
        await h.finish_booking()
    elif kind == "planner_failure":
        h.planner_failure=True
        before=len(h.planner_calls)
        r=await h.say(h.request())
        assert len(h.planner_calls)>before and r.get("degraded") and not await h.rows(),r
        h.planner_failure=False
        await h.say("Quero tentar de novo")
        await h.booking()
        await h.finish_booking()
    elif kind == "typed_ack":
        r=await h.say(h.request(),locked=True,capabilities=choices.Capabilities(assistant_interactions=1))
        assert r["step"]=="confirm" and r.get("presentation"),r
        r=await h.choose(r,consent=True)
        assert r["step"]=="confirmed",r
        assert await h.redis.get(h.key+":presentation") is None
        row=next(x for x in await h.rows() if x["status"]=="confirmed")
        replay=await h.say('[Escolha recebida]',locked=True,read_only=True,
            capabilities=choices.Capabilities(assistant_interactions=1),selection=h.last_selection)
        assert replay['step']=='interaction_expired',replay
        await h.say("Sim",read_only=True)
        await h.cancel_conversation(row)
        await h.assert_final()
    else:
        raise AssertionError("No silent empty journey: "+kind)
    h.completed=True
