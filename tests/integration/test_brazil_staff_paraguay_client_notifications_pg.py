"""Real PostgreSQL/Redis notification lifecycle with BR staff and PY customer.

No real contact numbers, no WhatsApp sends, and no production data. The
temporary phone pair is synthetic but uses the requested country formats.
comm_db teardown removes every test notice, booking, contact and staff record.
"""
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import appointment_notifications as notices
from app.appointment_lifecycle import LifecycleActor, customer_actor
from app.booking_service import book_appointment, confirm_hold, cancel_appointment, reschedule_appointment
from tests.integration.test_booking import booking_db, start_at
from tests.integration.test_appointment_notifications_pg import comm_db, jobs

pytestmark=pytest.mark.integration

CLIENT_PY="+595992000001"
STAFF_BR="+5521999999999"


@pytest.mark.asyncio
async def test_international_client_booking_notifies_only_brazilian_professional_and_updates_lifecycle(comm_db):
    d=comm_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE professionals SET phone_e164=:phone
            WHERE tenant_id=:t AND id=:id"""),{"t":d["a"],"id":d["pro"],"phone":STAFF_BR})
        await db.execute(text("""UPDATE qr_channels SET allowed_senders=CAST(:allowed AS jsonb)
            WHERE tenant_id=:t AND id=:id"""),
            {"t":d["a"],"id":d["channel"],"allowed":json.dumps([STAFF_BR[1:]])})
    async with d["factory"]() as db:
        held=await book_appointment(db,d["a"],d["pro"],[d["cut"]],CLIENT_PY,
            "Cliente paraguaio sintético",start_at(d["day"],10),"country-test-"+str(uuid4()))
        appointment=await confirm_hold(db,d["a"],held["id"],customer_phone=CLIENT_PY,
                                       source="public_site")
    assert appointment["status"]=="confirmed"
    sent=[]
    async def sender(tenant,channel,phone,body,operation):
        sent.append((tenant,channel,phone,body,operation))
        return {"accepted":True,"state":"accepted","provider_id":"synthetic-confirmation"}
    ctx={"appointment_sender":sender}
    async def check(event,expected_text):
        event_rows=[x for x in await jobs(d) if x["event"]==event]
        assert len(event_rows)==1
        n=event_rows[0]
        assert n["recipient_kind"]=="professional" and n["recipient_id"]==d["pro"]
        assert await notices.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
        assert await notices.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
        assert len([x for x in sent if x[4]==str(n["id"])])==1
        assert sent[-1][0:3]==(d["a"],d["channel"],STAFF_BR)
        assert expected_text in sent[-1][3]
        assert "Cliente paraguaio sintético" in sent[-1][3]
        assert "Corte" in sent[-1][3]
        return n

    new=await check("professional_new","NOVO AGENDAMENTO RECEBIDO!")
    async with d["factory"]() as db:
        moved=await reschedule_appointment(db,d["a"],appointment["id"],start_at(d["day"],14),
                    context=customer_actor(appointment["customer_id"],"public_site"))
    assert moved["start_at"]!=appointment["start_at"]
    rescheduled=await check("professional_rescheduled","AGENDAMENTO REMARCADO!")
    assert "10:00" in sent[-1][3] and "14:00" in sent[-1][3]
    async with d["factory"]() as db:
        cancelled=await cancel_appointment(db,d["a"],appointment["id"],
                    context=customer_actor(appointment["customer_id"],"public_site"))
    assert cancelled["status"]=="cancelled"
    cancelled_notice=await check("professional_cancelled","AGENDAMENTO CANCELADO!")
    assert len(sent)==3
    assert len({row[4] for row in sent})==3
    assert {new["recipient_id"],rescheduled["recipient_id"],cancelled_notice["recipient_id"]}=={d["pro"]}
    assert next(x for x in await jobs(d) if x["event"]=="reminder")["status"]=="cancelled"


@pytest.mark.asyncio
async def test_international_customer_recipients_are_not_silently_approved_for_pilot_broadcasts(comm_db):
    d=comm_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO contact_consents
            (tenant_id,contact_e164,operational_opt_in,marketing_opt_in)
            VALUES (:t,:phone,true,false)"""),{"t":d["a"],"phone":CLIENT_PY})
    async with d["factory"]() as db:
        held=await book_appointment(db,d["a"],d["pro"],[d["cut"]],CLIENT_PY,
            "Cliente internacional sintético",start_at(d["day"],11),
            "customer-notice-"+str(uuid4()))
        appointment=await confirm_hold(db,d["a"],held["id"])
        await cancel_appointment(db,d["a"],appointment["id"],
            context=LifecycleActor(role="owner",source="panel",account_id=d["account"]))
    notices_to_client=[n for n in await jobs(d) if n["event"]=="customer_cancelled"]
    assert len(notices_to_client)==1
    async def forbidden(*args):
        pytest.fail("No WhatsApp send to unapproved international recipient")
    state=await notices.process_notification({"appointment_sender":forbidden},
             str(d["a"]),str(notices_to_client[0]["id"]))
    assert state=="transport_recipient_not_authorized"
    assert next(n for n in await jobs(d)
                if n["id"]==notices_to_client[0]["id"])["status"]=="failed"
