"""Comprehensive appointment lifecycle / transactional notice qualification.

All appointments, customers, professionals, settings and notices are synthetic
and live only in a disposable PostgreSQL fixture. Send callbacks are replaced
by an in-memory, deterministic receipt — no WhatsApp account or phone is used.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
import pytest_asyncio
from sqlalchemy import text
from fastapi import HTTPException

from app import appointment_notifications as notifications
from app.appointment_lifecycle import LifecycleActor, customer_actor, SYSTEM_ACTOR
from app.booking_service import book_appointment, confirm_hold, cancel_appointment, reschedule_appointment
from app.management_models import transition_appointment
from app.database import tenant_transaction
from tests.integration.test_booking import booking_db, phone, start_at
from tests.integration.test_appointment_notifications_pg import comm_db, booking, jobs, PRO_PHONE

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def clean_extra_management_receipts(comm_db):
    """Remove the operation receipts made by completion tests before tenant teardown.

    The disposable booking fixture deletes its tenants last but does not own
    management_requests, because ordinary booking tests don't write them.
    """
    yield
    async with comm_db["migrator"].begin() as db:
        await db.execute(text("DELETE FROM management_requests WHERE tenant_id=:t"),
                         {"t":comm_db["a"]})


async def single(d,event):
    values=[dict(x) for x in await jobs(d) if x["event"]==event]
    assert len(values)==1,(event,len(values))
    return values[0]


def fake_transport():
    records=[]
    async def send(tenant,channel,number,body,operation):
        records.append((tenant,channel,number,body,operation))
        return {"accepted":True,"state":"accepted","provider_id":"synthetic-"+str(len(records))}
    return {"appointment_sender":send},records


async def mark_due(d,notification_id):
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointment_notifications
            SET due_at=clock_timestamp()-interval '1 second',
                expires_at=clock_timestamp()+interval '3 minutes'
            WHERE tenant_id=:tenant AND id=:id AND status='pending'"""),
            {"tenant":d["a"],"id":notification_id})


async def move_to_past(d,appointment_id):
    # Time travel the booking *only* inside the disposable integration DB;
    # real production appointments are not edited to simulate a due reminder.
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointments SET
            start_at=clock_timestamp()-interval '3 hours',
            end_at=clock_timestamp()-interval '2 hours',
            occupied_from=clock_timestamp()-interval '3 hours 5 minutes',
            occupied_until=clock_timestamp()-interval '1 hour 50 minutes',
            updated_at=clock_timestamp()
            WHERE tenant_id=:tenant AND id=:id AND status='confirmed'"""),
            {"tenant":d["a"],"id":appointment_id})


async def result_on_past(d,appointment_id,kind):
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            payload=SimpleNamespace(
                expected_status="confirmed",expected_updated_at=None,
                coupon_id=None,final_total_cents=None,
                idempotency_key="lifecycle-"+kind+"-"+str(appointment_id))
            return await transition_appointment(db,d["a"],appointment_id,kind,payload,"owner")


@pytest.mark.asyncio
async def test_one_hour_reminder_is_scheduled_exactly_and_fails_closed_before_due(comm_db):
    d=comm_db;appointment=await booking(d)
    job=await single(d,"reminder")
    assert job["recipient_kind"]=="customer"
    assert job["recipient_id"]==appointment["customer_id"]
    assert job["due_at"]==appointment["start_at"]-timedelta(hours=1)
    assert job["expires_at"]==job["due_at"]+timedelta(minutes=2)
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(job["id"]))=="not_due"
    assert (await single(d,"reminder"))["status"]=="pending"
    assert not sent
    await mark_due(d,job["id"])
    assert await notifications.process_notification(ctx,str(d["a"]),str(job["id"]))=="accepted"
    assert await notifications.process_notification(ctx,str(d["a"]),str(job["id"]))=="accepted"
    assert len(sent)==1
    assert sent[0][2]==phone(920)
    assert "1 hora!" in sent[0][3]
    assert "Corte" in sent[0][3]


@pytest.mark.asyncio
async def test_booking_within_one_hour_does_not_create_misleading_late_reminder(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            # Emulate a freshly confirmed, near-start booking in the test DB.
            await db.execute(text("""UPDATE appointments SET
                start_at=clock_timestamp()+interval '45 minutes',
                end_at=clock_timestamp()+interval '85 minutes',
                occupied_from=clock_timestamp()+interval '40 minutes',
                occupied_until=clock_timestamp()+interval '95 minutes',
                updated_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id"""),{"t":d["a"],"id":appointment["id"]})
            await notifications.enqueue_event(db,d["a"],appointment["id"],"confirmed",
                                             {"source":"public_site","requested_by":"customer"})
    rows=[x for x in await jobs(d) if x["event"]=="reminder"]
    assert len(rows)==1 and rows[0]["status"]=="cancelled"


@pytest.mark.asyncio
async def test_customer_cancel_invalidates_all_pending_reminders_and_warns_only_old_professional(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["factory"]() as db:
        outcome=await cancel_appointment(db,d["a"],appointment["id"],
                      context=customer_actor(appointment["customer_id"],"public_site"))
    assert outcome["status"]=="cancelled"
    rows=await jobs(d)
    assert all(r["status"]=="cancelled" for r in rows if r["event"] in {"reminder","professional_new"})
    assert len([r for r in rows if r["event"]=="professional_cancelled"])==1
    assert not [r for r in rows if r["event"]=="customer_cancelled"]
    ctx,sent=fake_transport()
    n=await single(d,"professional_cancelled")
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==PRO_PHONE
    assert "AGENDAMENTO CANCELADO" in sent[0][3]
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert len(sent)==1


@pytest.mark.asyncio
async def test_business_cancel_informs_only_customer_and_never_new_or_old_professional(comm_db):
    d=comm_db;appointment=await booking(d)
    actor=LifecycleActor(role="owner",source="panel",account_id=d["account"])
    async with d["factory"]() as db:
        cancelled=await cancel_appointment(db,d["a"],appointment["id"],context=actor)
    assert cancelled["status"]=="cancelled"
    assert not [n for n in await jobs(d) if n["event"]=="professional_cancelled"]
    notice=await single(d,"customer_cancelled")
    assert notice["recipient_kind"]=="customer"
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==phone(920)
    assert "cancelado pela nossa equipe" in sent[0][3]
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="accepted"
    assert len(sent)==1


@pytest.mark.asyncio
async def test_customer_reschedule_new_one_hour_reminder_only_and_notification_to_correct_pro(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["factory"]() as db:
        moved=await reschedule_appointment(db,d["a"],appointment["id"],start_at(d["day"],14),
                      context=customer_actor(appointment["customer_id"],"public_site"))
    assert moved["protocol"]==appointment["protocol"]
    reminders=[n for n in await jobs(d) if n["event"]=="reminder"]
    assert len(reminders)==2
    assert sorted(n["status"] for n in reminders)==["cancelled","pending"]
    current=next(n for n in reminders if n["status"]=="pending")
    assert current["due_at"]==moved["start_at"]-timedelta(hours=1)
    assert (await single(d,"professional_new"))["status"]=="cancelled"
    n=await single(d,"professional_rescheduled")
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==PRO_PHONE
    assert "10:00" in sent[0][3] and "14:00" in sent[0][3]


@pytest.mark.asyncio
async def test_team_reschedule_notifies_customer_not_professional_and_preserves_protocol(comm_db):
    d=comm_db;appointment=await booking(d)
    actor=LifecycleActor(role="owner",source="panel",account_id=d["account"])
    async with d["factory"]() as db:
        result=await reschedule_appointment(db,d["a"],appointment["id"],start_at(d["day"],14),
                                           context=actor)
    assert result["protocol"]==appointment["protocol"]
    assert not [x for x in await jobs(d) if x["event"]=="professional_rescheduled"]
    notice=await single(d,"customer_rescheduled")
    assert notice["recipient_id"]==appointment["customer_id"]
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==phone(920)
    assert "remarcado pela nossa equipe" in sent[0][3]
    assert "10:00" in sent[0][3] and "14:00" in sent[0][3]


@pytest.mark.asyncio
async def test_due_reminder_cancelled_before_worker_claim_never_sends(comm_db):
    d=comm_db;appointment=await booking(d)
    reminder=await single(d,"reminder")
    await mark_due(d,reminder["id"])
    async with d["factory"]() as db:
        await cancel_appointment(db,d["a"],appointment["id"],
                  context=customer_actor(appointment["customer_id"],"public_site"))
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(reminder["id"]))=="cancelled"
    assert sent==[]


@pytest.mark.asyncio
async def test_no_show_does_not_create_review_or_reengage_customer(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointment_communication_settings SET review=true,
            return_invitation=true WHERE tenant_id=:tenant"""),{"tenant":d["a"]})
    await move_to_past(d,appointment["id"])
    result=await result_on_past(d,appointment["id"],"no_show")
    assert result["status"]=="no_show"
    assert not [n for n in await jobs(d) if n["event"]=="review"]
    assert all(n["status"]=="cancelled" for n in await jobs(d)
               if n["event"] in {"professional_new","reminder"})


@pytest.mark.asyncio
async def test_manual_completion_schedules_review_after_configured_delay_and_once(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointment_communication_settings
            SET review=true,review_delay_min=15,return_invitation=false
            WHERE tenant_id=:tenant"""),{"tenant":d["a"]})
    await move_to_past(d,appointment["id"])
    done=await result_on_past(d,appointment["id"],"completed")
    assert done["status"]=="completed"
    notice=await single(d,"review")
    assert notice["recipient_kind"]=="customer"
    assert notice["recipient_id"]==appointment["customer_id"]
    assert timedelta(minutes=14)<notice["due_at"]-notice["created_at"]<timedelta(minutes=16)
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="not_due"
    assert sent==[]
    await mark_due(d,notice["id"])
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==phone(920)
    assert "avaliar sua experiência" in sent[0][3].lower()
    assert "Deixe sua avaliação" in sent[0][3]
    assert "/a#" in sent[0][3]
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="accepted"
    assert len(sent)==1
    assert all(n["status"]=="cancelled" for n in await jobs(d)
               if n["event"] in {"professional_new","reminder"})


@pytest.mark.asyncio
async def test_review_without_marketing_consent_never_sends_and_records_reason(comm_db):
    d=comm_db;appointment=await booking(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointment_communication_settings SET review=true WHERE tenant_id=:t"),{"t":d["a"]})
        await db.execute(text("UPDATE contact_consents SET marketing_opt_in=false WHERE tenant_id=:t"),{"t":d["a"]})
    await move_to_past(d,appointment["id"])
    done=await result_on_past(d,appointment["id"],"completed")
    assert done["status"]=="completed"
    notice=await single(d,"review")
    await mark_due(d,notice["id"])
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(notice["id"]))=="review_not_authorized"
    assert not sent
    assert (await single(d,"review"))["last_error"]=="review_not_authorized"


@pytest.mark.asyncio
async def test_professional_change_requires_new_booking_and_old_notice_never_targets_new_staff(comm_db):
    """The booking API has no reassignment mutation: test the supported cancel+book flow."""
    d=comm_db;appointment=await booking(d)
    changed_phone="+5567999999933"
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET phone_e164=:phone WHERE tenant_id=:t AND id=:id"),
                         {"phone":changed_phone,"t":d["a"],"id":d["pro2"]})
        await db.execute(text("""UPDATE qr_channels SET allowed_senders=CAST(:senders AS jsonb)
           WHERE tenant_id=:t AND id=:c"""),
            {"senders":json.dumps([PRO_PHONE[1:],changed_phone[1:],phone(920)[1:]]),
             "t":d["a"],"c":d["channel"]})
    async with d["factory"]() as db:
        await cancel_appointment(db,d["a"],appointment["id"],
                    context=customer_actor(appointment["customer_id"],"public_site"))
    async with d["factory"]() as db:
        hold=await book_appointment(db,d["a"],d["pro2"],[d["cut"]],
                   phone(920),"Cliente sintético",start_at(d["day"],10),"switch-pro-"+str(uuid4()))
        replacement=await confirm_hold(db,d["a"],hold["id"])
    assert replacement["professional_id"]==d["pro2"]
    assert replacement["protocol"]!=appointment["protocol"]
    notices=[n for n in await jobs(d) if n["event"]=="professional_new"]
    assert len(notices)==2
    old=next(n for n in notices if n["recipient_id"]==d["pro"])
    new=next(n for n in notices if n["recipient_id"]==d["pro2"])
    assert old["status"]=="cancelled" and new["status"]=="pending"
    n=await single(d,"professional_cancelled")
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert await notifications.process_notification(ctx,str(d["a"]),str(new["id"]))=="accepted"
    assert [x[2] for x in sent]==[PRO_PHONE,changed_phone]
    assert "AGENDAMENTO CANCELADO!" in sent[0][3]
    assert "NOVO AGENDAMENTO RECEBIDO!" in sent[1][3]
    assert not [n for n in await jobs(d) if n["event"]=="professional_reassigned"]


@pytest.mark.asyncio
async def test_notification_receipt_accepted_is_not_a_read_receipt(comm_db):
    d=comm_db;await booking(d)
    n=await single(d,"professional_new")
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert (await single(d,"professional_new"))["status"]=="accepted"
    readings=[]
    async def inspect(t,c,operation):
        readings.append(operation)
        return {"operation_id":operation,"accepted":True,"delivery":"delivered"}
    assert await notifications.reconcile_notification({"appointment_inspector":inspect},
                      str(d["a"]),str(n["id"]))=="delivered"
    assert (await single(d,"professional_new"))["status"]=="delivered"
    assert len(sent)==1
    async def read(t,c,operation):
        return {"operation_id":operation,"accepted":True,"delivery":"read"}
    assert await notifications.reconcile_notification({"appointment_inspector":read},
                 str(d["a"]),str(n["id"]))=="read"
    assert (await single(d,"professional_new"))["status"]=="read"
    assert len(sent)==1


@pytest.mark.asyncio
async def test_staff_proposal_rejected_by_customer_notifies_professional_and_preserves_original_slot(comm_db):
    from app.appointment_changes import propose_if_required
    from app.booking_api import RescheduleRequest
    d=comm_db;original=await booking(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointment_communication_settings
            SET reschedule_acceptance=true WHERE tenant_id=:tenant"""),{"tenant":d["a"]})
    payload=RescheduleRequest(start_at=start_at(d["day"],14),
                              expected_updated_at=original["updated_at"],
                              idempotency_key="decline-proposal-"+str(uuid4()))
    operator=LifecycleActor(role="owner",source="panel",account_id=d["account"])
    async with d["factory"]() as db:
        proposal=await propose_if_required(db,d["a"],original["id"],payload,operator)
    proposal_id=proposal["pending_change"]["id"]
    assert proposal["change_status"]=="pending_customer_acceptance"
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            declined=(await db.execute(text("""UPDATE appointment_change_proposals
                SET status='declined' WHERE tenant_id=:t AND appointment_id=:id
                    AND id=:proposal AND status='pending' RETURNING id"""),
                {"t":d["a"],"id":original["id"],"proposal":proposal_id})).scalar_one()
            await notifications.enqueue_event(db,d["a"],original["id"],
                 "reschedule_declined",{"source":"public_site","requested_by":"customer",
                    "previous":{"proposal_id":str(declined)}})
    n=await single(d,"professional_proposal_declined")
    assert n["recipient_kind"]=="professional" and n["recipient_id"]==d["pro"]
    ctx,sent=fake_transport()
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert len(sent)==1 and sent[0][2]==PRO_PHONE
    assert "PROPOSTA DE REMARCAÇÃO RECUSADA!" in sent[0][3]
    assert await notifications.process_notification(ctx,str(d["a"]),str(n["id"]))=="accepted"
    assert len(sent)==1
    # An old proposal must never be delivered after the customer rejects it.
    pending=await single(d,"customer_proposal")
    assert await notifications.process_notification(ctx,str(d["a"]),str(pending["id"]))=="proposal_changed"
    assert len(sent)==1
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            row=(await db.execute(text(
                "SELECT status,start_at FROM appointments WHERE tenant_id=:t AND id=:id"),
                {"t":d["a"],"id":original["id"]})).one()
    assert row.status=="confirmed" and row.start_at==original["start_at"]
