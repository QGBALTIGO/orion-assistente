"""Bounded high-fanout qualification for real PostgreSQL appointment notices.

Synthetic/isolated only: all bookings and notifications belong to booking_db's
disposable tenant and transport is an in-memory callback. It neither calls
WhatsApp nor estimates the provider's throughput.
"""
import asyncio
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import appointment_notifications as notices
from app.booking_service import book_appointment, confirm_hold
from tests.integration.test_booking import phone,start_at
from tests.integration.test_appointment_notifications_pg import comm_db, jobs, PRO_PHONE

pytestmark=pytest.mark.integration

SECOND_PRO="+5567999999923"
CUSTOMERS=[phone(920+n) for n in range(8)]
TOTAL_BOOKINGS=80


def percentile(samples,q):
    ordered=sorted(samples)
    return round(ordered[min(len(ordered)-1,int((len(ordered)-1)*q))],2)


@pytest.mark.asyncio
async def test_eighty_independent_bookings_generate_correct_due_jobs_and_dispatch_once(comm_db):
    d=comm_db
    # The managed QR pilot is intentionally capped at 10 numbers. Exercise 80
    # valid appointments distributed across time using only 8 consented
    # synthetic customers and 2 professionals (not 80 real recipients).
    all_allowed=[PRO_PHONE,SECOND_PRO,*CUSTOMERS]
    assert len(set(all_allowed))==10
    async with d["migrator"].begin() as s:
        await s.execute(text("""UPDATE professionals SET phone_e164=:p
           WHERE tenant_id=:t AND id=:id"""),{"p":SECOND_PRO,"t":d["a"],"id":d["pro2"]})
        await s.execute(text("""UPDATE qr_channels SET allowed_senders=CAST(:allowed AS jsonb)
           WHERE tenant_id=:t AND id=:id"""),
           {"t":d["a"],"id":d["channel"],"allowed":json.dumps([p[1:] for p in all_allowed])})
        for recipient in CUSTOMERS:
            await s.execute(text("""INSERT INTO contact_consents
                (tenant_id,contact_e164,operational_opt_in,marketing_opt_in)
                VALUES(:t,:p,true,true)
                ON CONFLICT (tenant_id,contact_e164) DO UPDATE SET operational_opt_in=true,
                marketing_opt_in=true"""),{"t":d["a"],"p":recipient})

    appointments=[]
    start=time.perf_counter()
    # Eight distinct slots per week per professional, five weeks within the
    # tenant's 60-day horizon. Existing prep/cleanup buffers remain enforced.
    for week in range(5):
        day=d["day"]+timedelta(weeks=week)
        for pro_index,pro in enumerate((d["pro"],d["pro2"])):
            for hour in range(10,18):
                number=CUSTOMERS[(week*3+hour*2+pro_index)%len(CUSTOMERS)]
                key="capacity-"+str(uuid4())
                async with d["factory"]() as db:
                    held=await book_appointment(db,d["a"],pro,[d["cut"]],
                          number,"Cliente sintético de carga",start_at(day,hour),key)
                    done=await confirm_hold(db,d["a"],held["id"])
                    appointments.append(done)
    booking_seconds=time.perf_counter()-start
    assert len(appointments)==TOTAL_BOOKINGS
    assert len({x["id"] for x in appointments})==TOTAL_BOOKINGS
    assert len({x["protocol"] for x in appointments})==TOTAL_BOOKINGS

    all_rows=await jobs(d)
    assert len(all_rows)==TOTAL_BOOKINGS*2
    assert Counter(x["event"] for x in all_rows)=={
        "professional_new":TOTAL_BOOKINGS,"reminder":TOTAL_BOOKINGS}
    for row in all_rows:
        if row["event"]=="reminder":
            actual=next(x for x in appointments if x["id"]==row["appointment_id"])
            assert row["due_at"]==actual["start_at"]-timedelta(hours=1)
            assert row["expires_at"]==row["due_at"]+timedelta(minutes=2)
            assert row["status"]=="pending"

    # Time travel ONLY due timestamps in disposable PostgreSQL so the test
    # can exercise 80 reminder dispatches now, without waiting five weeks.
    async with d["migrator"].begin() as s:
        await s.execute(text("""UPDATE appointment_notifications
            SET due_at=clock_timestamp()-interval '1 second',
                expires_at=clock_timestamp()+interval '20 minutes'
            WHERE tenant_id=:t AND event='reminder' AND status='pending'"""),
            {"t":d["a"]})

    sends=[]
    async def synthetic_transport(t,channel,number,body,operation):
        sends.append((t,channel,number,operation,body))
        return {"accepted":True,"state":"accepted","provider_id":"synthetic"}
    ctx={"appointment_sender":synthetic_transport}
    limiter=asyncio.Semaphore(12)
    elapsed=[]
    async def dispatch(row):
        async with limiter:
            at=time.perf_counter()
            outcome=await notices.process_notification(ctx,str(d["a"]),str(row["id"]))
            elapsed.append((time.perf_counter()-at)*1000)
            return outcome
    started=time.perf_counter()
    responses=await asyncio.gather(*(dispatch(x) for x in all_rows))
    delivery_seconds=time.perf_counter()-started
    assert Counter(responses)=={"accepted":TOTAL_BOOKINGS*2}
    assert len(sends)==TOTAL_BOOKINGS*2
    assert len({item[3] for item in sends})==TOTAL_BOOKINGS*2
    assert {s[2] for s in sends}.issubset(set(all_allowed))
    assert all(s[0]==d["a"] and s[1]==d["channel"] for s in sends)
    assert sum("NOVO AGENDAMENTO" in s[4] for s in sends)==TOTAL_BOOKINGS
    assert sum("1 hora!" in s[4] for s in sends)==TOTAL_BOOKINGS
    assert len([n for n in await jobs(d) if n["status"]=="accepted"])==TOTAL_BOOKINGS*2

    # Double worker callbacks are harmless even after a message is accepted.
    replay=await asyncio.gather(*(dispatch(n) for n in all_rows[:20]))
    assert replay==["accepted"]*20
    assert len(sends)==TOTAL_BOOKINGS*2
    report={
      "scope":"isolated PostgreSQL + synthetic transport; NOT WhatsApp capacity",
      "appointments_confirmed":len(appointments),
      "notifications_accepted":len(sends),
      "unique_operations":len({s[3] for s in sends}),
      "in_flight_limit":12,
      "booking_seconds":round(booking_seconds,2),
      "dispatch_seconds":round(delivery_seconds,2),
      "dispatch_per_second":round(len(sends)/max(delivery_seconds,.00001),2),
      "dispatch_p50_ms":percentile(elapsed,.50),
      "dispatch_p95_ms":percentile(elapsed,.95),
      "dispatch_p99_ms":percentile(elapsed,.99),
      "external_provider_sends":0,
    }
    directory=Path("test-results/appointment-communications")
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"notification-fanout-80.json").write_text(json.dumps(report,indent=2))
    print("ORION_NOTIFICATION_CAPACITY_REPORT="+json.dumps(report,sort_keys=True))
