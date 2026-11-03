"""Public booking uses configured starts and the full real PostgreSQL engine.

Every tenant/contact is a disposable fixture. No WhatsApp transport is called.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.booking_service import book_appointment, confirm_hold, list_slots
from app.start_policies import router as start_policies_router
from test_public_catalog import public_db, publish, combo, book_body

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def manual_public_db(public_db, monkeypatch):
    public_db["app"].include_router(start_policies_router)

    async def no_external_followup(*args, **kwargs):
        return {}

    monkeypatch.setattr("app.appointment_confirmation.public_followup", no_external_followup)
    return public_db


def start(d, clock, *, day=None, zone="America/Sao_Paulo"):
    return datetime.combine(day or d["day"], time.fromisoformat(clock), tzinfo=ZoneInfo(zone))


def clocks(available):
    return [datetime.fromisoformat(value).strftime("%H:%M") for value in available["slots"]]


async def set_starts(d, times, *, weekday=None, mode="manual"):
    weekday = d["day"].weekday() if weekday is None else weekday
    path = f"/api/v1/tenants/{d['a']}/professionals/{d['pro']}/start-policies"
    current = await d["client"].get(path)
    assert current.status_code == 200, current.text
    policy = next(item for item in current.json()["policies"] if item["weekday"] == weekday)
    saved = await d["client"].put(path + f"/{weekday}", json={
        "mode": mode, "start_times": times, "expected_version": policy["version"],
    })
    assert saved.status_code == 200, saved.text
    return saved.json()


async def quote(d, *, service=None, services=(), addons=(), professional=None, day=None):
    params = [("professional_id", str(professional or d["pro"])),
              ("day", (day or d["day"]).isoformat())]
    params += ([("service_ids", str(value)) for value in services] if services else
               [("service_id", str(service or d["cut"]))])
    params += [("addon_ids", str(value)) for value in addons]
    response = await d["client"].get(d["public"] + "/availability", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def add_lunch(d):
    async with d["migrator"].connect() as db:
        working_hour = (await db.execute(text("""SELECT id FROM working_hours
            WHERE tenant_id=:tenant AND professional_id=:pro AND weekday=:weekday"""),
            {"tenant": d["a"], "pro": d["pro"], "weekday": d["day"].weekday()})).scalar_one()
    response = await d["client"].post(f"/api/v1/tenants/{d['a']}/working_breaks", json={
        "working_hour_id": str(working_hour), "start_time": "12:00", "end_time": "13:00",
    })
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_manual_public_and_internal_slots_match_without_changing_opening_hours(manual_public_db):
    d = manual_public_db
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET duration_min=40 WHERE id=:id"), {"id": d["cut"]})
    await add_lunch(d)
    configured = ["08:00", "08:40", "09:20", "11:40", "13:00", "17:20", "17:40"]
    await set_starts(d, configured)
    await publish(d)

    public = await quote(d)
    assert clocks(public) == ["08:00", "08:40", "09:20", "13:00", "17:20"]
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as db:
        internal = await list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"])
    assert public["slots"] == internal["slots"]
    assert public["duration_min"] == internal["duration_min"] == 40

    other = await quote(d, professional=d["pro2"])
    assert "08:15" in clocks(other) and "08:40" not in clocks(other)
    catalogue = await d["client"].get(d["public"])
    assert catalogue.status_code == 200, catalogue.text
    opening = catalogue.json()["opening_hours"][d["day"].weekday()]["intervals"]
    assert opening == [{"open": "08:00", "close": "18:00"}]
    assert "start_policies" not in catalogue.text

    # Changing the policy is live availability, not a new publication of the site.
    await set_starts(d, configured, mode="automatic")
    restored = await quote(d)
    assert "08:15" in clocks(restored) and "08:40" not in clocks(restored)
    assert (await d["client"].get(d["public"])).json()["opening_hours"][d["day"].weekday()]["intervals"] == opening


@pytest.mark.asyncio
@pytest.mark.parametrize("selection,duration", [
    ("multiple", 60), ("package", 60), ("package_override", 80), ("addons", 60),
])
async def test_manual_public_selections_respect_effective_duration_prep_and_cleanup(manual_public_db, selection, duration):
    d = manual_public_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE services
            SET duration_min=40,prep_min=5,cleanup_min=10 WHERE id=:id"""), {"id": d["cut"]})
    await add_lunch(d)
    await set_starts(d, ["08:00", "08:40", "10:30", "10:50", "11:00",
                         "13:00", "13:05", "16:30", "16:50", "17:00"])
    selection_args = {}
    if selection == "multiple":
        selection_args["services"] = [d["cut"], d["beard"]]
    elif selection in {"package", "package_override"}:
        package, _ = await combo(d, **({"duration_override_min": duration} if selection == "package_override" else {}))
        selection_args["service"] = package["synthetic_service_id"]
    else:
        response = await d["client"].put(d["base"] + f"/services/{d['cut']}/addons", json={
            "expected_version": 0, "items": [{"addon_service_id": str(d["beard"]), "add_duration": True}],
        })
        assert response.status_code == 200, response.text
        selection_args["addons"] = [d["beard"]]
    await publish(d)
    result = await quote(d, **selection_args)
    assert result["duration_min"] == duration
    expected = ["08:40", "10:30", "13:05", "16:30"]
    if duration == 60:
        expected = ["08:40", "10:30", "10:50", "13:05", "16:30", "16:50"]
    assert clocks(result) == expected


@pytest.mark.asyncio
async def test_public_combo_reserves_real_duration_without_inventing_the_next_start(manual_public_db):
    d = manual_public_db
    await set_starts(d, ["08:00", "08:40", "09:20", "10:00"])
    package, _ = await combo(d, duration_override_min=60)
    await publish(d)
    offered = await quote(d, service=package["synthetic_service_id"])
    payload = book_body(offered)
    result = await d["client"].post(d["public"] + "/appointments", json=payload)
    assert result.status_code == 201, result.text
    booked = result.json()
    assert datetime.fromisoformat(booked["end_at"]) - datetime.fromisoformat(booked["start_at"]) == timedelta(hours=1)
    remaining = await quote(d, service=package["synthetic_service_id"])
    assert clocks(remaining) == ["09:20", "10:00"]
    assert "09:00" not in clocks(remaining)


@pytest.mark.asyncio
async def test_public_revalidates_an_old_quote_and_keeps_a_confirmed_booking(manual_public_db):
    d = manual_public_db
    await set_starts(d, ["08:00", "08:40", "09:20"])
    await publish(d)
    first = await quote(d)
    first_payload = book_body(first)
    confirmed = await d["client"].post(d["public"] + "/appointments", json=first_payload)
    assert confirmed.status_code == 201, confirmed.text
    old_offer = await quote(d)
    assert "08:40" in clocks(old_offer)
    stale_payload = {**book_body(old_offer, phone="+5511987654399"),
                     "start_at": start(d, "08:40").isoformat()}
    async with d["migrator"].connect() as db:
        before = (await db.execute(text("""SELECT id,start_at,end_at,status FROM appointments
            WHERE tenant_id=:tenant"""), {"tenant": d["a"]})).mappings().all()

    await set_starts(d, ["09:20"])
    rejected = await d["client"].post(d["public"] + "/appointments", json=stale_payload)
    assert rejected.status_code == 422, rejected.text
    assert clocks(await quote(d)) == ["09:20"]
    replay = await d["client"].post(d["public"] + "/appointments", json=first_payload)
    assert replay.status_code == 201 and replay.json() == confirmed.json(), replay.text
    async with d["migrator"].connect() as db:
        after = (await db.execute(text("""SELECT id,start_at,end_at,status FROM appointments
            WHERE tenant_id=:tenant"""), {"tenant": d["a"]})).mappings().all()
        assert after == before
        assert (await db.execute(text("""SELECT count(*) FROM customers
            WHERE tenant_id=:tenant AND whatsapp_e164=:phone"""),
            {"tenant": d["a"], "phone": stale_payload["customer_phone"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_public_manual_starts_respect_blocks_time_off_holds_and_expiry(manual_public_db):
    d = manual_public_db
    configured = ["08:00", "08:40", "09:20", "10:00", "10:40", "11:20", "13:00",
                  "13:40", "14:20", "15:00", "15:40", "16:20", "17:00", "17:40"]
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET duration_min=40 WHERE id=:id"), {"id": d["cut"]})
        for table, begin, end in [("schedule_blocks", "13:00", "14:00"), ("time_off", "15:00", "16:00")]:
            await db.execute(text(f"""INSERT INTO {table}(tenant_id,professional_id,starts_at,ends_at)
                VALUES(:tenant,:pro,:begin,:end)"""),
                {"tenant": d["a"], "pro": d["pro"], "begin": start(d, begin), "end": start(d, end)})
    await add_lunch(d)
    await set_starts(d, configured)
    await publish(d)
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as db:
        held = await book_appointment(db, d["a"], d["pro"], [d["cut"]], "+5511987654381",
            "Cliente sintético de reserva temporária", start(d, "08:40"), "manual-public-hold")
    async with factory() as db:
        booked = await book_appointment(db, d["a"], d["pro"], [d["cut"]], "+5511987654382",
            "Cliente sintético confirmado", start(d, "10:00"), "manual-public-confirmed")
        await confirm_hold(db, d["a"], booked["id"])
    available = await quote(d)
    assert clocks(available) == ["08:00", "09:20", "10:40", "11:20", "14:20", "16:20", "17:00"]
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointments SET hold_expires_at=clock_timestamp()-interval '1 second'
            WHERE tenant_id=:tenant AND id=:id"""), {"tenant": d["a"], "id": held["id"]})
    freed = await quote(d)
    assert clocks(freed) == ["08:00", "08:40", "09:20", "10:40", "11:20", "14:20", "16:20", "17:00"]
    saved = await d["client"].get(f"/api/v1/tenants/{d['a']}/professionals/{d['pro']}/start-policies")
    assert next(p for p in saved.json()["policies"] if p["weekday"] == d["day"].weekday())["start_times"] == configured


@pytest.mark.asyncio
async def test_each_weekday_uses_its_own_local_starts_and_repeats_next_week(manual_public_db):
    d = manual_public_db
    zone = "America/Campo_Grande"
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET timezone=:zone WHERE id=:tenant"), {"zone": zone, "tenant": d["a"]})
        for weekday in range(7):
            await db.execute(text("""INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time)
                VALUES(:tenant,:pro,:weekday,'08:00','18:00')
                ON CONFLICT(tenant_id,professional_id,weekday) DO NOTHING"""),
                {"tenant": d["a"], "pro": d["pro"], "weekday": weekday})
    definitions = {weekday: [f"08:{7 + weekday:02d}", f"09:{21 + weekday:02d}"] for weekday in range(7)}
    for weekday, times in definitions.items():
        await set_starts(d, times, weekday=weekday)
    await publish(d)
    monday = d["day"] + timedelta(days=(7 - d["day"].weekday()) % 7)
    for weekday in range(7):
        day = monday + timedelta(days=weekday)
        result = await quote(d, day=day)
        assert result["timezone"] == zone
        assert result["slots"] == [start(d, value, day=day, zone=zone).isoformat() for value in definitions[weekday]]
    following_week = await quote(d, day=monday + timedelta(days=7))
    assert clocks(following_week) == definitions[0]
    too_far = await d["client"].get(d["public"] + "/availability", params={
        "professional_id": str(d["pro"]), "service_id": str(d["cut"]),
        "day": (monday + timedelta(days=365)).isoformat(),
    })
    assert too_far.status_code == 422, too_far.text
