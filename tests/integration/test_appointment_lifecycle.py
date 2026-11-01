"""Real PG/RLS agenda transitions, audit identity, CAS and bounded pages."""
import asyncio
from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import booking_api, management_api
from app.booking_service import cancel_appointment, read_appointment
from app.catalog_api import Actor, authenticate, router as catalog_router
from app.database import tenant_transaction
from test_management import management_db, customer, appointment

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def agenda_db(management_db, monkeypatch):
    d = management_db
    app = FastAPI()
    app.include_router(booking_api.router)
    app.include_router(management_api.router)
    app.include_router(catalog_router)
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    monkeypatch.setattr(booking_api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(management_api, "get_session_factory", lambda: factory)
    zone = ZoneInfo("America/Sao_Paulo")
    day = (d["now"].astimezone(zone) + timedelta(days=7)).date()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time)
            VALUES (:tenant,:pro,:weekday,'08:00','18:00')
        """), {"tenant": d["a"], "pro": d["pro"], "weekday": day.weekday()})
        await db.execute(text("""
            INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES (:tenant,:pro,:service)
        """), {"tenant": d["a"], "pro": d["pro"], "service": d["service"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                headers=d["client"].headers) as client:
        try:
            yield {**d, "client": client, "app": app, "factory": factory, "day": day, "zone": zone,
                   "agenda": f"/api/v1/tenants/{d['a']}", "other_agenda": f"/api/v1/tenants/{d['b']}"}
        finally:
            async with d["migrator"].begin() as db:
                await db.execute(text("DELETE FROM usage_counters WHERE tenant_id=:tenant"), {"tenant": d["a"]})


async def hold(d, hour=10, phone="+5511912345678"):
    response = await d["client"].post(d["agenda"] + "/appointments/hold", json={
        "professional_id": str(d["pro"]), "service_ids": [str(d["service"])],
        "customer_name": "Ana sintética", "customer_phone": phone,
        "start_at": datetime.combine(d["day"], time(hour), d["zone"]).isoformat(), "idempotency_key": str(uuid4()),
    })
    assert response.status_code == 201, response.text
    return response.json()


def operation(row, **extra):
    return {"expected_updated_at": row["updated_at"], "idempotency_key": str(uuid4()), **extra}


async def confirm(d, row):
    response = await d["client"].post(d["agenda"] + f"/appointments/{row['id']}/confirm", json=operation(row))
    assert response.status_code == 200, response.text
    return response.json()


async def detail(d, identity):
    response = await d["client"].get(d["agenda"] + f"/appointments/{identity}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("requester", ["customer", "professional", "business"])
async def test_cancellation_persists_requester_separate_from_authenticated_actor(agenda_db, requester):
    d = agenda_db
    d["app"].dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["account"], "owner")
    booking = await confirm(d, await hold(d))
    payload = operation(booking, requested_by=requester, reason="  Pediu revisão do horário.  ")
    result = await d["client"].post(d["agenda"] + f"/appointments/{booking['id']}/cancel", json=payload)
    assert result.status_code == 200, result.text
    current = await detail(d, booking["id"])
    cancellation = current["cancellation"]
    assert current["status"] == "cancelled" and current["allowed_actions"] == []
    assert cancellation["requested_by"] == requester and cancellation["reason"] == "Pediu revisão do horário."
    assert cancellation["source"] == "panel"
    assert cancellation["actor"] == {"role": "owner", "account_id": str(d["account"]), "customer_id": None}
    assert current["history"][0]["event"] == "cancelled"
    assert current["history"][0]["previous"]["status"] == "confirmed"
    assert current["customer_phone"] == "+5511912345678" and current["customer_display_name"] == "Ana sintética"
    assert current["professional_name"] == "Profissional de teste" and current["timezone"] == "America/Sao_Paulo"
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT action FROM calendar_outbox WHERE appointment_id=:id"),
                                {"id": UUID(booking["id"])})).scalar_one() == "delete"
    denied = await d["client"].get(d["other_agenda"] + f"/appointments/{booking['id']}")
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_cancel_and_reschedule_accept_only_the_reviewed_version(agenda_db):
    d = agenda_db
    booking = await confirm(d, await hold(d))
    cancel_payload = operation(booking, requested_by="customer", reason="Conflito sintético")
    move_payload = operation(booking, start_at=datetime.combine(d["day"], time(11), d["zone"]).isoformat())
    cancel_result, move_result = await asyncio.gather(
        d["client"].post(d["agenda"] + f"/appointments/{booking['id']}/cancel", json=cancel_payload),
        d["client"].post(d["agenda"] + f"/appointments/{booking['id']}/reschedule", json=move_payload),
    )
    assert sorted([cancel_result.status_code, move_result.status_code]) == [200, 409]
    current = await detail(d, booking["id"])
    winning = "cancelled" if cancel_result.status_code == 200 else "rescheduled"
    assert [row["event"] for row in current["history"]] == [winning, "confirmed", "hold_created"]
    assert datetime.fromisoformat(current["updated_at"]) > datetime.fromisoformat(booking["updated_at"])
    assert current["status"] == ("cancelled" if winning == "cancelled" else "confirmed")
    if winning == "rescheduled":
        assert datetime.fromisoformat(current["start_at"]) == datetime.fromisoformat(move_payload["start_at"])
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("""
            SELECT count(*) FROM management_requests WHERE tenant_id=:tenant AND operation IN ('agenda_cancel','agenda_reschedule')
        """), {"tenant": d["a"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_confirm_cancel_retries_are_idempotent_and_key_mismatch_does_not_rewrite_audit(agenda_db):
    d = agenda_db
    temporary = await hold(d)
    confirm_payload = operation(temporary)
    replies = await asyncio.gather(*(d["client"].post(d["agenda"] + f"/appointments/{temporary['id']}/confirm",
                                                     json=confirm_payload) for _ in range(5)))
    assert all(reply.status_code == 200 for reply in replies), [reply.text for reply in replies]
    assert len({reply.json()["updated_at"] for reply in replies}) == 1
    confirmed = replies[0].json()
    cancel_payload = operation(confirmed, requested_by="professional", reason="Indisponibilidade")
    replies = await asyncio.gather(*(d["client"].post(d["agenda"] + f"/appointments/{temporary['id']}/cancel",
                                                     json=cancel_payload) for _ in range(5)))
    assert all(reply.status_code == 200 for reply in replies)
    mismatch = await d["client"].post(d["agenda"] + f"/appointments/{temporary['id']}/cancel",
                                      json={**cancel_payload, "reason": "Outro motivo"})
    assert mismatch.status_code == 409
    current = await detail(d, temporary["id"])
    assert [row["event"] for row in current["history"]] == ["cancelled", "confirmed", "hold_created"]
    assert current["cancellation"]["reason"] == "Indisponibilidade"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["complete", "no-show"])
async def test_completed_and_no_show_require_current_version_and_emit_one_transactional_history(agenda_db, target):
    d = agenda_db
    person = await customer(d)
    identity = await appointment(d, person["id"])
    before = await detail(d, identity)
    # A concurrent writer has changed the reviewed version; neither money nor
    # points nor a receipt may be recorded by a stale completion attempt.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET updated_at=updated_at+interval '1 microsecond' WHERE id=:id"), {"id": UUID(identity)})
    stale = await d["client"].post(d["base"] + f"/appointments/{identity}/{target}", json=operation(before))
    assert stale.status_code == 409
    current = await detail(d, identity)
    assert current["status"] == "confirmed" and current["history"] == []
    payload = operation(current)
    results = await asyncio.gather(*(d["client"].post(d["base"] + f"/appointments/{identity}/{target}", json=payload) for _ in range(4)))
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    final = await detail(d, identity)
    expected = "completed" if target == "complete" else "no_show"
    assert final["status"] == expected and final["allowed_actions"] == []
    assert final["commission_cents"] == (2500 if target == "complete" else None)
    assert final["original_total_cents"] == (10000 if target == "complete" else None)
    assert [row["event"] for row in final["history"]] == [expected]
    assert final["history"][0]["actor"]["role"] == "support"
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT action,status FROM calendar_outbox WHERE appointment_id=:id"),
                                {"id": UUID(identity)})).one() == ("update", "pending")
        assert (await db.execute(text("SELECT count(*) FROM financial_entries WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM management_requests WHERE entity_id=:id"), {"id": UUID(identity)})).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["complete", "no-show"])
async def test_future_result_and_readonly_accounts_cannot_change_appointments(agenda_db, target):
    d = agenda_db
    booking = await confirm(d, await hold(d))
    assert "complete" not in booking["allowed_actions"] and "no_show" not in booking["allowed_actions"]
    response = await d["client"].post(d["base"] + f"/appointments/{booking['id']}/{target}", json=operation(booking))
    assert response.status_code == 409
    d["app"].dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["account"], "viewer")
    # Booking authorization uses the verified scoped membership. Management
    # authorization re-reads it, so revoke write access in the actual DB too.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET role='viewer' WHERE tenant_id=:tenant AND account_id=:account"),
                         {"tenant": d["a"], "account": d["account"]})
    readonly = await detail(d, booking["id"])
    assert readonly["allowed_actions"] == []
    response = await d["client"].post(d["agenda"] + f"/appointments/{booking['id']}/cancel", json=operation(booking, requested_by="business"))
    assert response.status_code == 403
    response = await d["client"].post(d["base"] + f"/appointments/{booking['id']}/{target}", json=operation(booking))
    assert response.status_code == 403
    assert (await detail(d, booking["id"]))["updated_at"] == booking["updated_at"]


@pytest.mark.asyncio
async def test_reschedule_availability_ignores_only_the_matching_tenant_booking(agenda_db):
    d = agenda_db
    booking = await confirm(d, await hold(d))
    occupied = await confirm(d, await hold(d, 11, phone="+5511912345679"))
    parameters = [("day", str(d["day"])), ("professional_id", str(d["pro"])), ("service_ids", str(d["service"]))]
    ordinary = await d["client"].get(d["agenda"] + "/availability", params=parameters)
    for_move = await d["client"].get(d["agenda"] + "/availability", params=[*parameters, ("appointment_id", booking["id"])])
    assert ordinary.status_code == for_move.status_code == 200
    original = datetime.fromisoformat(booking["start_at"]).astimezone(d["zone"]).isoformat()
    blocked = datetime.fromisoformat(occupied["start_at"]).astimezone(d["zone"]).isoformat()
    assert original not in ordinary.json()["slots"] and original in for_move.json()["slots"]
    assert blocked not in for_move.json()["slots"]
    cross = await d["client"].get(d["other_agenda"] + "/availability", params=[*parameters, ("appointment_id", booking["id"])])
    assert cross.status_code == 404
    wrong = await d["client"].get(d["agenda"] + "/availability", params={
        "day": str(d["day"]), "professional_id": str(d["other_pro"]), "service_ids": str(d["service"]), "appointment_id": booking["id"]})
    assert wrong.status_code == 409


@pytest.mark.asyncio
async def test_expired_hold_reports_system_cancellation_and_never_records_success_receipt(agenda_db):
    d = agenda_db
    temporary = await hold(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET hold_expires_at=now()-interval '1 minute' WHERE id=:id"), {"id": UUID(temporary["id"])})
    payload = operation(await detail(d, temporary["id"]))
    response = await d["client"].post(d["agenda"] + f"/appointments/{temporary['id']}/confirm", json=payload)
    assert response.status_code == 409
    current = await detail(d, temporary["id"])
    assert current["status"] == "cancelled" and current["cancellation"]["requested_by"] == "system"
    assert current["cancellation"]["event"] == "hold_expired"
    assert current["cancellation"]["source"] == "system" and current["history"][0]["event"] == "hold_expired"
    retry = await d["client"].post(d["agenda"] + f"/appointments/{temporary['id']}/confirm", json=payload)
    assert retry.status_code == 409
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM management_requests WHERE entity_id=:id"), {"id": UUID(temporary["id"])})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE appointment_id=:id"), {"id": UUID(temporary["id"])})).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["whatsapp_qr", "whatsapp_meta"])
async def test_verified_whatsapp_customer_is_recorded_without_panel_impersonation(agenda_db, source):
    d = agenda_db
    booking = await confirm(d, await hold(d))
    async with d["factory"]() as db:
        result = await cancel_appointment(db, d["a"], UUID(booking["id"]), customer_phone=booking["customer_phone"],
                                          future_only=True, source=source)
    assert result["cancellation"]["requested_by"] == "customer"
    assert result["cancellation"]["source"] == source
    assert result["cancellation"]["actor"] == {"role": "customer", "account_id": None, "customer_id": booking["customer_id"]}
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["b"]):
            assert (await db.execute(text("SELECT id FROM appointment_history WHERE appointment_id=:id"), {"id": UUID(booking["id"])})).all() == []


@pytest.mark.asyncio
async def test_legacy_calls_remain_valid_and_spoofed_metadata_is_rejected_before_writes(agenda_db):
    d = agenda_db
    temporary = await hold(d)
    url = d["agenda"] + f"/appointments/{temporary['id']}"
    forged = await d["client"].post(url + "/cancel", json={**operation(temporary), "requested_by": "customer", "source": "whatsapp_qr"})
    assert forged.status_code == 422
    assert (await detail(d, temporary["id"]))["status"] == "hold"
    response = await d["client"].post(url + "/confirm")
    assert response.status_code == 200 and response.json()["status"] == "confirmed"
    assert (await d["client"].post(url + "/cancel")).status_code == 200
    current = await detail(d, temporary["id"])
    assert current["cancellation"]["requested_by"] == "unknown" and current["cancellation"]["actor"]["role"] == "support"


@pytest.mark.asyncio
async def test_filtered_pages_include_real_total_beyond200_and_keep_legacy_attribution_unknown(agenda_db):
    d = agenda_db
    person = await customer(d, name="Ana Paginação", phone="(11) 91234-5678")
    begin = datetime.combine(d["day"], time(8), d["zone"]).astimezone(timezone.utc)
    async with d["migrator"].begin() as db:
        rows = (await db.execute(text("""
            INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
                start_at,end_at,occupied_from,occupied_until,status,total_cents)
            SELECT gen_random_uuid(),:tenant,:customer,:pro,'AG-AAAA'||substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',((n-1)/32)::int+1,1)||substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789',(n-1)%32+1,1),'page-'||n::text,repeat('a',64),
                CAST(:begin AS timestamptz)+n*interval '1 minute',CAST(:begin AS timestamptz)+(n+30)*interval '1 minute',
                CAST(:begin AS timestamptz)+n*interval '1 minute',CAST(:begin AS timestamptz)+(n+30)*interval '1 minute',
                'cancelled',10000 FROM generate_series(1,220) n RETURNING id,protocol
        """), {"tenant": d["a"], "customer": UUID(person["id"]), "pro": d["pro"], "begin": begin})).mappings().all()
        await db.execute(text("""
            INSERT INTO appointment_history(tenant_id,appointment_id,event,operator,details)
            SELECT tenant_id,id,'cancelled','support',CASE WHEN right(protocol,1)::int%2=0
                THEN jsonb_build_object('v',1,'requested_by','customer','source','panel') ELSE '{}'::jsonb END
            FROM appointments WHERE tenant_id=:tenant
        """), {"tenant": d["a"]})
    params = {"day": str(d["day"]), "paged": "true", "q": "Ana Paginação", "limit": 50}
    first = await d["client"].get(d["agenda"] + "/appointments", params=params)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 220 and len(first.json()["items"]) == 50 and first.json()["has_more"] is True
    last = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "offset": 200})).json()
    assert last["total"] == 220 and len(last["items"]) == 20 and last["has_more"] is False
    masked = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "q": "(11) 91234-5678"})).json()
    assert masked["total"] == 220
    unknown = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "cancellation_requested_by": "unknown"})).json()
    known = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "cancellation_requested_by": "customer"})).json()
    assert unknown["total"] == known["total"] == 110
    old = await detail(d, unknown["items"][0]["id"])
    assert old["cancellation"]["requested_by"] == "unknown" and old["history"][0]["actor"]["role"] == "unknown"
    literal = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "q": "%"})).json()
    assert literal["total"] == 0
    other_pro = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "professional_id": str(d["other_pro"])})).json()
    assert other_pro["total"] == 0
    protocol = (await d["client"].get(d["agenda"] + "/appointments", params={**params, "q": rows[0]["protocol"]})).json()
    assert protocol["total"] == 1
    other = (await d["client"].get(d["other_agenda"] + "/appointments", params=params)).json()
    assert other["total"] == 0
    legacy = await d["client"].get(d["agenda"] + "/appointments", params={"day": str(d["day"])})
    assert isinstance(legacy.json(), list) and len(legacy.json()) == 200
