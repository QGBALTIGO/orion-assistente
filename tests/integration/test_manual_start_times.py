"""Manual policy API, booking revalidation and PostgreSQL isolation."""
import asyncio
import json
from datetime import time

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.booking_service import book_appointment, confirm_hold, list_slots, reschedule_appointment
from app.database import tenant_transaction
from app.main import app
from test_booking import booking_db, headers, phone, start_at  # noqa: F401

pytestmark = pytest.mark.integration


def endpoint(data, professional=None, weekday=None):
    path = f"/api/v1/tenants/{data['a']}/professionals/{professional or data['pro']}/start-policies"
    return path if weekday is None else path + f"/{weekday}"


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers())


async def save(http, data, times, *, version=0, mode="manual", professional=None):
    payload = {"mode": mode, "expected_version": version}
    if times is not None:
        payload["start_times"] = times
    return await http.put(endpoint(data, professional, data["day"].weekday()), json=payload)


@pytest.mark.asyncio
async def test_policy_defaults_sorted_save_optimistic_version_and_inactive_preservation(booking_db):
    d = booking_db
    async with client() as http:
        initial = await http.get(endpoint(d))
        assert initial.status_code == 200, initial.text
        assert len(initial.json()["policies"]) == 7
        assert all(p["mode"] == "automatic" and p["version"] == 0 for p in initial.json()["policies"])
        saved = await save(http, d, ["09:20", "08:40"])
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"weekday": d["day"].weekday(), "mode": "manual",
                                "start_times": ["08:40", "09:20"], "version": 1}
        assert (await save(http, d, ["10:00"])).status_code == 409
        assert (await save(http, d, ["08:40", "08:40"], version=1)).status_code == 422
        assert (await save(http, d, [], version=1)).status_code == 422
        automatic = await save(http, d, None, mode="automatic", version=1)
        assert automatic.status_code == 200, automatic.text
        assert automatic.json()["start_times"] == ["08:40", "09:20"]
        async with d["factory"]() as db:
            slots = await list_slots(db, d["a"], d["pro"], [d["beard"]], d["day"])
        assert start_at(d["day"], 8, 45).isoformat() in slots["slots"]
        assert start_at(d["day"], 8, 40).isoformat() not in slots["slots"]
        restored = await save(http, d, None, version=2)
        assert restored.status_code == 200 and restored.json()["version"] == 3


@pytest.mark.asyncio
async def test_manual_membership_booking_combo_and_existing_confirmations(booking_db):
    d = booking_db
    async with client() as http:
        assert (await save(http, d, ["08:00", "08:40", "09:20", "17:40"])).status_code == 200
        async with d["factory"]() as db:
            short = await list_slots(db, d["a"], d["pro"], [d["beard"]], d["day"])
        assert short["slots"] == [start_at(d["day"], h, m).isoformat()
                                  for h, m in ((8, 0), (8, 40), (9, 20), (17, 40))]
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as error:
                await book_appointment(db, d["a"], d["pro"], [d["beard"]], phone(801),
                                       "Pessoa sintética", start_at(d["day"], 8, 45), "manual-unlisted-801")
        assert error.value.status_code == 422
        async with d["factory"]() as db:
            hold = await book_appointment(db, d["a"], d["pro"], [d["cut"], d["beard"]], phone(802),
                                          "Pessoa sintética", start_at(d["day"], 8), "manual-combo-802")
        async with d["factory"]() as db:
            confirmed = await confirm_hold(db, d["a"], hold["id"])
        async with d["factory"]() as db:
            remaining = await list_slots(db, d["a"], d["pro"], [d["cut"], d["beard"]], d["day"])
        assert remaining["slots"] == [start_at(d["day"], 9, 20).isoformat()]
        assert (await save(http, d, ["09:20"], version=1)).status_code == 200
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as error:
                await reschedule_appointment(db, d["a"], hold["id"], start_at(d["day"], 10))
        assert error.value.status_code == 422
        async with d["migrator"].connect() as db:
            current = (await db.execute(text("SELECT status,start_at,end_at FROM appointments WHERE id=:id"),
                                        {"id": hold["id"]})).mappings().one()
        assert current["status"] == "confirmed"
        assert current["start_at"] == confirmed["start_at"] and current["end_at"] == confirmed["end_at"]


@pytest.mark.asyncio
async def test_unlisted_hold_cannot_be_confirmed_after_policy_changes(booking_db):
    d = booking_db
    async with client() as http:
        assert (await save(http, d, ["08:40", "09:20"])).status_code == 200
        async with d["factory"]() as db:
            hold = await book_appointment(db, d["a"], d["pro"], [d["beard"]], phone(803),
                                          "Pessoa sintética", start_at(d["day"], 8, 40), "manual-stale-803")
        assert (await save(http, d, ["09:20"], version=1)).status_code == 200
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as error:
                await confirm_hold(db, d["a"], hold["id"])
        assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_active_policy_guards_schedule_changes_but_inactive_list_does_not(booking_db):
    d = booking_db
    async with d["migrator"].connect() as db:
        hour = (await db.execute(text("SELECT id FROM working_hours WHERE tenant_id=:t AND professional_id=:p"),
                                {"t": d["a"], "p": d["pro"]})).scalar_one()
    async with client() as http:
        assert (await save(http, d, ["08:40", "09:20"])).status_code == 200
        catalog = f"/api/v1/tenants/{d['a']}"
        pause = {"working_hour_id": str(hour), "start_time": "08:30", "end_time": "09:00"}
        blocked = await http.post(catalog + "/working_breaks", json=pause)
        assert blocked.status_code == 409, blocked.text
        changed = await http.put(catalog + f"/working_hours/{hour}", json={
            "professional_id": str(d["pro"]), "weekday": d["day"].weekday(),
            "start_time": "09:00", "end_time": "18:00"})
        assert changed.status_code == 409, changed.text
        assert (await http.delete(catalog + f"/working_hours/{hour}")).status_code == 409
        assert (await save(http, d, None, mode="automatic", version=1)).status_code == 200
        permitted = await http.post(catalog + "/working_breaks", json=pause)
        assert permitted.status_code == 201, permitted.text
        reactivated = await save(http, d, None, version=2)
        assert reactivated.status_code == 422, reactivated.text
        assert "08:40" in reactivated.text
        current = (await http.get(endpoint(d))).json()["policies"][d["day"].weekday()]
        assert current["mode"] == "automatic" and current["version"] == 2
        assert current["start_times"] == ["08:40", "09:20"]
        repaired = await save(http, d, ["09:00", "09:20"], version=2)
        assert repaired.status_code == 200, repaired.text


@pytest.mark.asyncio
async def test_policies_isolate_professionals_days_and_tenants_and_serialize_editors(booking_db):
    d = booking_db
    async with client() as http:
        responses = await asyncio.gather(save(http, d, ["08:40"]), save(http, d, ["09:20"]))
        assert sorted(response.status_code for response in responses) == [200, 409]
        days = (await http.get(endpoint(d))).json()["policies"]
        assert sum(day["mode"] == "manual" for day in days) == 1
        other = await http.get(endpoint(d, d["pro2"]))
        assert all(day["mode"] == "automatic" for day in other.json()["policies"])
        wrong_tenant = endpoint(d).replace(str(d["a"]), str(d["b"]))
        assert (await http.get(wrong_tenant)).status_code == 404
        assert (await http.put(wrong_tenant + f"/{d['day'].weekday()}", json={
            "mode": "manual", "start_times": ["08:40"], "expected_version": 0})).status_code == 404
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["b"]):
            assert (await db.execute(text("SELECT count(*) FROM professional_start_policies"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_database_rejects_invalid_manual_arrays(booking_db):
    d = booking_db
    for values in ([], [time(8, 40), time(8, 40)], [time(8, 40, 1)]):
        with pytest.raises(DBAPIError):
            async with d["migrator"].begin() as db:
                await db.execute(text("""INSERT INTO professional_start_policies
                    (tenant_id,professional_id,weekday,mode,start_times)
                    VALUES(:t,:p,:day,'manual',CAST(:times AS time[]))"""),
                    {"t": d["a"], "p": d["pro"], "day": d["day"].weekday(), "times": values})


@pytest.mark.asyncio
async def test_sql_onboarding_activation_respects_manual_start_capacity(booking_db):
    d = booking_db
    configuration = {"activation_requested": True, "reviewed": True, "niche": "barbearia",
                     "activity": "services", "modality": "online"}
    try:
        async with d["migrator"].begin() as db:
            await db.execute(text("""INSERT INTO business_onboarding
                (tenant_id,is_new,configuration,completed_at)
                VALUES(:t,true,CAST(:configuration AS jsonb),now())"""),
                {"t": d["a"], "configuration": json.dumps(configuration)})
        async with client() as http:
            assert (await save(http, d, ["17:50"])).status_code == 200
            with pytest.raises(DBAPIError, match="Operational catalog is not ready"):
                async with d["factory"]() as db:
                    async with tenant_transaction(db, d["a"]):
                        await db.execute(text("SELECT public.onboarding_activate_tenant(:t)"), {"t": d["a"]})
            assert (await save(http, d, ["08:40"], version=1)).status_code == 200
            async with d["factory"]() as db:
                async with tenant_transaction(db, d["a"]):
                    await db.execute(text("SELECT public.onboarding_activate_tenant(:t)"), {"t": d["a"]})
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:t"), {"t": d["a"]})
