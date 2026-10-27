"""Real PostgreSQL cross-connection lock ordering, never a production load test.

The public request owns its professional/day locks before pinning the source
contract, using a single transaction. Editors serialize without a lock cycle.
"""
import asyncio
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import public_site_api, professional_commissions
from app.booking_service import acquire_professional_lock, book_appointment, confirm_hold
from test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["book", "confirm"])
@pytest.mark.parametrize("writer,multiple", [("archive", False), ("configuration", False), ("configuration", True)])
async def test_public_booking_does_not_wait_on_editor_blocked_by_its_guard(public_db, monkeypatch, stage, writer, multiple):
    d = public_db; client = d["client"]
    await publish(d)
    if multiple:
        response = await client.get(d["public"] + "/availability", params=[
            ("professional_id", str(d["pro"])), ("day", d["day"].isoformat()),
            ("service_ids", str(d["cut"])), ("service_ids", str(d["wash"]))])
        assert response.status_code == 200, response.text
        quoted = response.json()
    else:
        quoted = await availability(d)
    guard_ready, writer_attempted, writer_has_advisory, release_booking = (asyncio.Event() for _ in range(4))
    original_writer_lock = professional_commissions.acquire_professional_lock
    async def writer_lock(db, tenant, professional):
        if tenant == d["a"] and professional == d["pro"]:
            writer_attempted.set()
        await original_writer_lock(db, tenant, professional)
        if tenant == d["a"] and professional == d["pro"]:
            writer_has_advisory.set()
    monkeypatch.setattr(professional_commissions, "acquire_professional_lock", writer_lock)
    boundary = "book_appointment" if stage == "book" else "confirm_hold"
    original_engine = getattr(public_site_api, boundary)
    async def paused_engine(*args, **kwargs):
        guard_ready.set()  # Published/source FOR SHARE locks are held now.
        await asyncio.wait_for(release_booking.wait(), timeout=5)
        return await original_engine(*args, **kwargs)
    monkeypatch.setattr(public_site_api, boundary, paused_engine)
    tasks = []
    try:
        booking = asyncio.create_task(client.post(d["public"] + "/appointments", json=book_body(quoted)))
        tasks.append(booking)
        await asyncio.wait_for(guard_ready.wait(), timeout=5)
        if writer == "archive":
            edit = asyncio.create_task(client.patch(d["base"] + "/professionals/" + str(d["pro"]) + "/status",
                json={"expected_version": 1, "active": False}))
        else:
            edit = asyncio.create_task(client.put(d["base"] + "/professionals/" + str(d["pro"]) + "/configure",
                json={"expected_version": 1, "name": "Profissional sintético",
                      "service_ids": [str(d["cut"])]}))
        tasks.append(edit)
        await asyncio.wait_for(writer_attempted.wait(), timeout=5)
        # The editor is already attempting its exclusive professional lock;
        # public owns the shared scope before any source row was pinned.
        assert not writer_has_advisory.is_set()
        release_booking.set()
        booked, edited = await asyncio.wait_for(asyncio.gather(booking, edit), timeout=5)
        assert booked.status_code == 201, booked.text
        assert edited.status_code == (409 if writer == "archive" else 200), edited.text
        assert writer_has_advisory.is_set()
        async with d["migrator"].connect() as db:
            rows = (await db.execute(text("SELECT status FROM appointments WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalars().all()
            customers = (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                {"tenant": d["a"], "phone": "+5511987654321"})).scalar_one()
            assert rows == ["confirmed"]
            assert customers == 1
            assert (await db.execute(text("SELECT count(*) FROM public_booking_receipts WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1
        if writer == "configuration":
            # The already committed operation is recoverable even when the
            # editor has since changed the professional's assignments.
            retry = await client.post(d["public"] + "/appointments", json=book_body(quoted))
            assert retry.status_code == 201, retry.text
            replay = await client.post(d["public"] + "/appointments", json=book_body(quoted))
            assert replay.status_code == 201 and replay.json() == retry.json(), replay.text
            async with d["migrator"].connect() as db:
                assert (await db.execute(text("SELECT status FROM appointments WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalars().all() == ["confirmed"]
                assert (await db.execute(text("SELECT count(*) FROM public_booking_receipts WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1
                assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                    {"tenant": d["a"], "phone": "+5511987654321"})).scalar_one() == 1
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_bounded_confirmation_rolls_back_without_consuming_the_hold(public_db):
    d = public_db
    await publish(d)
    quoted = await availability(d)
    tried = asyncio.Event()
    class TrackedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if "pg_try_advisory_xact_lock" in str(statement):
                tried.set()
            return result
    factory = async_sessionmaker(d["runtime"], class_=TrackedSession, expire_on_commit=False)
    async with factory() as session:
        hold = await book_appointment(session, d["a"], d["pro"], [d["cut"]], "+5511987654321",
            "Cliente inteiramente sintético", datetime.fromisoformat(quoted["slots"][0]), "cancel-bounded-" + d["slug"], source="public_site")
    task = None
    try:
        async with d["migrator"].begin() as editor:
            await acquire_professional_lock(editor, d["a"], d["pro"])
            before = (await editor.execute(text("SELECT to_jsonb(a) FROM appointments a WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one()
            async def confirmation():
                async with factory() as session:
                    return await confirm_hold(session, d["a"], hold["id"], customer_phone="+5511987654321",
                                              source="public_site", nonblocking_professional_lock=True)
            task = asyncio.create_task(confirmation())
            await asyncio.wait_for(tried.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            after = (await editor.execute(text("SELECT to_jsonb(a) FROM appointments a WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one()
            assert after == before and after["status"] == "hold"
        async with factory() as session:
            confirmed = await confirm_hold(session, d["a"], hold["id"], customer_phone="+5511987654321", source="public_site")
            assert confirmed["id"] == hold["id"] and confirmed["status"] == "confirmed"
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
