"""Narrative parity with the real booking engine/RLS and disposable PostgreSQL.

The composed cases run real qr_ai.turn with the local deterministic semantic
planner; no model HTTP or WhatsApp transport is involved. Engine race/lifecycle
cases test the common authority also used by the site, without parallel agenda.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from redis.asyncio import from_url

from app import qr_ai, qr_customer, qr_profile
from app import qr_assistant_interactions as choices
from app.booking_service import (book_appointment, cancel_appointment, confirm_hold,
    list_slots, read_appointment, reschedule_appointment, appointment_snapshot)
from app.database import tenant_transaction
from app.qr_understanding import build_plan
from test_booking import booking_db, phone, start_at, UTC
from test_qr_context_availability_postgres import Redis

pytestmark = pytest.mark.integration


async def hold(d, *, number=980, key=None, when=None, ids=None, professional=None):
    async with d["factory"]() as db:
        return await book_appointment(db, d["a"], professional or d["pro"], ids or [d["cut"]],
            phone(number), "Cliente sintético", when or start_at(d["day"], 10), key or str(uuid4()))


async def confirm(d, entry, **kw):
    async with d["factory"]() as db:
        return await confirm_hold(db, d["a"], entry["id"], **kw)


async def read(d, entry):
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            value=await read_appointment(db, d["a"], UUID(str(entry["id"])))
            extra=(await db.execute(text("SELECT occupied_from,occupied_until FROM appointments WHERE tenant_id=:t AND id=:id"),
                {"t":d["a"],"id":UUID(str(entry["id"]))})).mappings().one()
            return {**value,**dict(extra),"expected_snapshot":appointment_snapshot(value)}


@pytest_asyncio.fixture(autouse=True)
async def cleanup_own_management_receipts(booking_db):
    # The reused fixture predates management receipts. Remove only this fixture's
    # synthetic tenant receipts before its parent fixture deletes the tenant.
    yield
    async with booking_db["migrator"].begin() as db:
        await db.execute(text("DELETE FROM management_requests WHERE tenant_id IN (:a,:b)"),
            {"a":booking_db["a"],"b":booking_db["b"]})


async def count(d):
    async with d["migrator"].connect() as db:
        return (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t":d["a"]})).scalar_one()


async def turn_setup(d, monkeypatch, redis=None):
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET name=CASE WHEN id=:p THEN 'Bruno' ELSE 'Breno' END WHERE tenant_id=:t"),
            {"p": d["pro"], "t": d["a"]})
    async def no_profile(*_):
        return None
    async def planner(**kwargs):
        return build_plan(message=kwargs["message"], catalog=kwargs["catalog"], professionals=kwargs["professionals"],
            context=kwargs["context"], faqs=kwargs["faqs"], reference_date=kwargs["reference_date"])
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_customer, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_profile, "runtime_profile", no_profile)
    monkeypatch.setattr(qr_ai, "interpret_plan", planner)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "1")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED", "0")
    redis = redis if redis is not None else Redis()
    namespace = "qualification:"+str(uuid4())
    async def turn(message, number=981):
        token = qr_ai._message_identity.set("synthetic-"+str(uuid4()))
        try:
            return await qr_ai.turn(d["a"], phone(number)[1:], message, redis, namespace, preview=False)
        finally:
            qr_ai._message_identity.reset(token)
    return redis, namespace, turn


@pytest.mark.asyncio
async def test_B04_real_turn_combination_matches_persisted_items_total_duration_and_buffers(booking_db, monkeypatch):
    d=booking_db
    redis, namespace, turn=await turn_setup(d, monkeypatch)
    result=await turn(f"Quero agendar corte e barba em {d['day']} às 10:00 com Bruno")
    assert result["step"] == "confirm" and "55,00" in result["answer"]
    state=json.loads(redis.data[f"orion:qr:ai:{d['a']}:{namespace}"])
    actual=await read(d, {"id":state["hold"]})
    assert actual["status"] == "hold" and actual["total_cents"] == 5500
    assert set(x["service_id"] for x in actual["items"]) == {d["cut"],d["beard"]}
    assert actual["end_at"]-actual["start_at"] == timedelta(minutes=60)
    assert actual["occupied_from"] == actual["start_at"]-timedelta(minutes=5)
    assert actual["occupied_until"] == actual["end_at"]+timedelta(minutes=10)
    done=await turn("Pode marcar")
    assert done["step"] == "confirmed" and (await read(d, actual))["status"] == "confirmed"


@pytest.mark.asyncio
async def test_B10_catalog_deactivation_after_proposal_cannot_confirm_original_hold(booking_db, monkeypatch):
    d=booking_db
    redis, namespace, turn=await turn_setup(d, monkeypatch)
    await turn(f"Quero corte em {d['day']} às 10:00 com Bruno")
    state=json.loads(redis.data[f"orion:qr:ai:{d['a']}:{namespace}"])
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET active=false WHERE tenant_id=:t AND id=:id"), {"t":d["a"],"id":d["cut"]})
    result=await turn("Pode marcar")
    assert result["step"] != "confirmed" and (await read(d,{"id":state["hold"]}))["status"] != "confirmed"


@pytest.mark.asyncio
async def test_C02_two_named_professionals_query_real_free_agendas_only(booking_db, monkeypatch):
    d=booking_db
    _,_,turn=await turn_setup(d, monkeypatch)
    await hold(d, ids=[d["platinum"]], when=start_at(d["day"],14), professional=d["pro"])
    result=await turn(f"Quais horários Bruno e Breno tem para corte em {d['day']} às 15:00?")
    assert "Breno" in result["answer"] and not ("Bruno: 15" in result["answer"])
    assert await count(d) == 1, "Read-only comparison must not create another hold"


@pytest.mark.asyncio
async def test_C03_no_preference_uses_available_professional_not_first_catalog_row(booking_db, monkeypatch):
    d=booking_db
    redis,namespace,turn=await turn_setup(d,monkeypatch)
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO schedule_blocks(tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:s,:e)"),
            {"t":d["a"],"p":d["pro"],"s":start_at(d["day"],0),"e":start_at(d["day"]+timedelta(days=1),0)})
    result=await turn(f"Quero corte em {d['day']} às 15:00, tanto faz o profissional")
    assert "Breno" in result["answer"]
    state=json.loads(redis.data[f"orion:qr:ai:{d['a']}:{namespace}"])
    assert not state.get("professional") or state["professional"] == str(d["pro2"])
    assert not state.get("hold") or (await read(d,{"id":state["hold"]}))["professional_id"] == d["pro2"]


@pytest.mark.asyncio
async def test_C06_whole_day_block_is_not_published_as_available_in_real_turn(booking_db,monkeypatch):
    d=booking_db
    _,_,turn=await turn_setup(d,monkeypatch)
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO schedule_blocks(tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:s,:e)"),
            {"t":d["a"],"p":d["pro"],"s":start_at(d["day"],0),"e":start_at(d["day"]+timedelta(days=1),0)})
    result=await turn(f"Tem horário para corte em {d['day']} às 15:00 com Bruno?")
    assert result["step"] != "confirm" and "15h" not in result["answer"] and await count(d)==0


@pytest.mark.asyncio
async def test_C07_professional_break_cannot_be_crossed_by_combo_hold(booking_db):
    d=booking_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time)
            SELECT :t,id,'12:00','13:00' FROM working_hours WHERE tenant_id=:t AND professional_id=:p"""),{"t":d["a"],"p":d["pro"]})
    with pytest.raises(HTTPException) as exc:
        await hold(d,ids=[d["cut"],d["beard"]],when=start_at(d["day"],11,30))
    assert exc.value.status_code==409 and await count(d)==0


@pytest.mark.asyncio
async def test_C08_buffer_overlap_is_denied_even_when_visible_service_times_do_not_overlap(booking_db):
    d=booking_db
    await hold(d,when=start_at(d["day"],10)) # ends10:40; cleanup through10:50
    with pytest.raises(HTTPException) as exc:
        await hold(d,number=982,when=start_at(d["day"],10,45),ids=[d["beard"]])
    assert exc.value.status_code==409 and await count(d)==1


@pytest.mark.asyncio
async def test_D06_original_hold_deadline_rejects_late_confirmation_without_renewal(booking_db):
    d=booking_db;entry=await hold(d)
    with pytest.raises(HTTPException) as exc:
        await confirm(d,entry,now=entry["hold_expires_at"]+timedelta(milliseconds=1))
    assert exc.value.status_code==409 and (await read(d,entry))["status"]=="cancelled"


@pytest.mark.asyncio
async def test_D07_site_and_whatsapp_sources_compete_for_one_real_slot(booking_db):
    d=booking_db
    async def contender(number,source):
        async with d["factory"]() as db:
            try:
                return await book_appointment(db,d["a"],d["pro"],[d["cut"]],phone(number),
                    "Cliente sintético",start_at(d["day"],10),str(uuid4()),source=source)
            except HTTPException as exc:
                return exc.status_code
    results=await asyncio.gather(contender(983,"public_site"),contender(984,"whatsapp_qr"))
    assert sum(isinstance(x,dict) for x in results)==1 and results.count(409)==1 and await count(d)==1


@pytest.mark.asyncio
async def test_D08_repeated_hold_message_key_returns_same_record_without_duplicate_items(booking_db):
    d=booking_db
    first=await hold(d,key="same-message-key-qualification")
    replay=await hold(d,key="same-message-key-qualification")
    assert first["id"]==replay["id"] and first["hold_expires_at"]==replay["hold_expires_at"]
    assert await count(d)==1 and len((await read(d,first))["items"])==1


@pytest.mark.asyncio
async def test_D10_confirmation_result_lost_after_commit_replayed_without_second_transition(booking_db):
    d=booking_db;entry=await hold(d)
    await confirm(d,entry,idempotency_key="same-confirmation-message") # result deliberately discarded
    replay=await confirm(d,entry,idempotency_key="same-confirmation-message")
    assert replay["status"]=="confirmed" and await count(d)==1
    async with d["migrator"].connect() as db:
        transitions=(await db.execute(text("SELECT count(*) FROM appointment_history WHERE tenant_id=:t AND appointment_id=:id AND event='confirmed'"),
            {"t":d["a"],"id":entry["id"]})).scalar_one()
    assert transitions==1


@pytest.mark.asyncio
async def test_F02_completed_record_is_not_customer_cancellable(booking_db):
    d=booking_db;entry=await confirm(d,await hold(d))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET status='completed' WHERE tenant_id=:t AND id=:id"),{"t":d["a"],"id":entry["id"]})
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as exc:
            await cancel_appointment(db,d["a"],entry["id"],customer_phone=phone(980))
    assert exc.value.status_code==409 and (await read(d,entry))["status"]=="completed"


@pytest.mark.asyncio
async def test_F03_past_completed_record_is_not_rescheduled_as_new_booking(booking_db):
    d=booking_db;entry=await confirm(d,await hold(d))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET status='completed' WHERE tenant_id=:t AND id=:id"),{"t":d["a"],"id":entry["id"]})
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as exc:
            await reschedule_appointment(db,d["a"],entry["id"],start_at(d["day"],14),customer_phone=phone(980))
    assert exc.value.status_code==409 and await count(d)==1


@pytest.mark.asyncio
async def test_F05_cancelled_hold_cannot_be_reactivated_by_late_yes(booking_db):
    d=booking_db;entry=await hold(d)
    async with d["factory"]() as db:
        await cancel_appointment(db,d["a"],entry["id"])
    with pytest.raises(HTTPException):
        await confirm(d,entry)
    assert (await read(d,entry))["status"]=="cancelled"


@pytest.mark.asyncio
async def test_F07_panel_change_invalidates_old_customer_cancel_snapshot(booking_db):
    d=booking_db;entry=await confirm(d,await hold(d))
    original=await read(d,entry)
    async with d["factory"]() as db:
        await reschedule_appointment(db,d["a"],entry["id"],start_at(d["day"],14))
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as exc:
            await cancel_appointment(db,d["a"],entry["id"],customer_phone=phone(980),expected_snapshot=original["expected_snapshot"])
    actual=await read(d,entry)
    assert exc.value.status_code==409 and actual["status"]=="confirmed" and actual["start_at"]==start_at(d["day"],14)


@pytest.mark.asyncio
async def test_F10_cancelled_slot_reappears_in_shared_site_and_whatsapp_availability(booking_db):
    d=booking_db;entry=await confirm(d,await hold(d))
    async with d["factory"]() as db:
        before=await list_slots(db,d["a"],d["pro"],[d["cut"]],d["day"])
        await cancel_appointment(db,d["a"],entry["id"])
        after=await list_slots(db,d["a"],d["pro"],[d["cut"]],d["day"])
    assert start_at(d["day"],10).isoformat() not in before["slots"]
    assert start_at(d["day"],10).isoformat() in after["slots"]
    assert (await read(d,entry))["status"]=="cancelled"


@pytest.mark.asyncio
async def test_K01_foreign_contact_cannot_use_protocol_or_uuid_to_read_or_mutate(booking_db,monkeypatch):
    d=booking_db;entry=await confirm(d,await hold(d))
    monkeypatch.setattr(qr_customer,"get_session_factory",lambda:d["factory"])
    assert await qr_customer.appointment_services(d["a"],entry["id"],phone(999)[1:]) is None
    assert await qr_customer.appointment_services(d["b"],entry["id"],phone(980)[1:]) is None
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as exc:
            await cancel_appointment(db,d["a"],entry["id"],customer_phone=phone(999))
    assert exc.value.status_code==404 and (await read(d,entry))["status"]=="confirmed"


@pytest.mark.asyncio
async def test_K02_real_turn_memory_names_and_catalog_are_tenant_scoped_for_same_phone(booking_db,monkeypatch):
    d=booking_db
    redis,namespace,turn=await turn_setup(d,monkeypatch)
    await turn(f"Quero corte em {d['day']} às 10:00 com Bruno")
    key=f"orion:qr:ai:{d['a']}:{namespace}"
    original=redis.data[key]
    token=qr_ai._message_identity.set("other-tenant-message")
    try:
        other=await qr_ai.turn(d["b"],phone(981)[1:],"Oi",redis,namespace,preview=False)
    finally:
        qr_ai._message_identity.reset(token)
    assert "Bruno" not in other["answer"] and "35,00" not in other["answer"]
    assert redis.data[key]==original and await count(d)==1


@pytest.mark.asyncio
async def test_L09_panel_reschedule_vs_customer_cancel_one_reviewed_version_wins(booking_db):
    d=booking_db;entry=await confirm(d,await hold(d));review=await read(d,entry)
    async def action(kind):
        async with d["factory"]() as db:
            try:
                options={"expected_updated_at":review["updated_at"]}
                return await (cancel_appointment(db,d["a"],entry["id"],**options) if kind=="cancel"
                    else reschedule_appointment(db,d["a"],entry["id"],start_at(d["day"],14),**options))
            except HTTPException as exc:
                return exc.status_code
    results=await asyncio.gather(action("cancel"),action("move"))
    assert sum(isinstance(x,dict) for x in results)==1 and sum(x in {404,409} for x in results if isinstance(x,int))==1
    assert await count(d)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("private_input,omitted",[
    ("Meu CPF é 000.000.000-00", "000.000.000-00"),
    ("Minha senha é segredo-sintetico", "segredo-sintetico"),
],ids=["document","password"])
async def test_K06_real_redis_does_not_retain_unsolicited_document_or_password_after_booking(booking_db,monkeypatch,private_input,omitted):
    d=booking_db
    url=os.getenv("TEST_REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS")=="1":
            pytest.fail("Disposable Redis is required")
        pytest.skip("Disposable Redis not configured")
    redis=from_url(url,decode_responses=True,socket_connect_timeout=2,socket_timeout=3)
    _,namespace,turn=await turn_setup(d,monkeypatch,redis)
    key=f"orion:qr:ai:{d['a']}:{namespace}"
    try:
        guidance=await turn(private_input)
        assert omitted not in (await redis.get(key) or ""), "Unnecessary personal data entered persistent dialogue"
        assert guidance["step"]=="privacy_guidance" and await count(d)==0
        await turn(f"Quero corte em {d['day']} às 10:00 com Bruno")
        result=await turn("Pode marcar")
        assert result["step"]=="confirmed" and await count(d)==1
        raw=await redis.get(key)
        assert raw and omitted not in raw
        state=json.loads(raw)
        assert state.get("history") and len(state["history"])<=8
        assert not state.get("hold") and not state.get("confirmation_message_id")
    finally:
        await redis.delete(key,key+":catalog-context",key+":presentation")
        await redis.aclose()


@pytest.mark.asyncio
async def test_A06_homonymous_native_professional_choice_reaches_exact_uuid_in_real_booking(booking_db,monkeypatch):
    d=booking_db
    url=os.getenv("TEST_REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS")=="1":
            pytest.fail("Disposable Redis is required")
        pytest.skip("Disposable Redis not configured")
    redis=from_url(url,decode_responses=True,socket_connect_timeout=2,socket_timeout=3)
    _,namespace,turn=await turn_setup(d,monkeypatch,redis)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET name='Bruno' WHERE tenant_id=:t"),{"t":d["a"]})
    key=f"orion:qr:ai:{d['a']}:{namespace}"
    binding=choices.begin_choice()
    try:
        result=await turn(f"Quero corte em {d['day']} às 10:00")
        assert result["step"]=="professional" and await count(d)==0
        presented=await choices.present(redis,d["a"],phone(981)[1:],namespace,result,
            choices.Capabilities(assistant_interactions=1))
        rows=presented["presentation"]["action"]["sections"][0]["rows"]
        assert len(rows)==2 and rows[0]["title"]==rows[1]["title"]=="Bruno"
        assert rows[0]["id"]!=rows[1]["id"] and rows[0]["description"]!=rows[1]["description"]
        state=json.loads(await redis.get(key));selected_id=state["professionals"][1]["id"]
        await choices.acknowledge(redis,d["a"],phone(981)[1:],namespace,
            choices.PresentationAck(phone=phone(981)[1:],presentation_id=presented["presentation"]["id"],provider_id="accepted-homonyms"))
        message=await choices.resolve(redis,d["a"],phone(981)[1:],namespace,
            choices.Selection(id=rows[1]["id"],quoted_message_id="accepted-homonyms"))
        proposal=await turn(message)
        assert proposal["step"]=="confirm" and await count(d)==1
        state=json.loads(await redis.get(key));actual=await read(d,{"id":state["hold"]})
        assert actual["professional_id"]==UUID(str(selected_id))
        assert choices.consume_choice() is None
    finally:
        choices.reset_choice(binding)
        await redis.delete(key,key+":catalog-context",key+":presentation")
        await redis.aclose()
