"""Real PG/RLS receiving contracts, with synthetic tenant/contact fixtures only."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import commercial_service as commercial, qr_commerce_store as store
from app.booking_service import book_appointment, confirm_hold, cancel_appointment
from app.business_profile_schemas import BusinessProfile
from app.commercial_schemas import CreateQuote
from test_booking import booking_db, phone, start_at
from test_commercial_catalog_postgres import setup_item, call, SUPPORT, TABLES

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def receiving(booking_db, monkeypatch):
    d = booking_db
    for name in ("COMMERCIAL_CATALOG_ENABLED", "COMMERCIAL_FULFILLMENT_ENABLED", "BUSINESS_PROFILES_ENABLED"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-receiving-key-" + "x" * 40)
    monkeypatch.setattr(store, "get_session_factory", lambda: d["factory"])
    sender = phone(901)
    scope = store.CustomerScope(d["a"], sender[1:], "synthetic-receiving-first")
    profile = BusinessProfile(business_name="Synthetic receiving", capabilities=["product_orders"],
        address="Published synthetic location", fulfillment={"enabled_modes": ["pickup", "delivery"]})
    versions = {}

    async def publish(tenant=None, *, modes=None, capabilities=None):
        target = tenant or d["a"]
        version = uuid4()
        revision = versions.get(target, 0)+1
        values = profile.model_dump(mode="json")
        if modes is not None:
            values["fulfillment"]["enabled_modes"] = modes
        if capabilities is not None:
            values["capabilities"] = capabilities
        async with d["migrator"].begin() as db:
            await db.execute(text("INSERT INTO business_profile_versions(id,tenant_id,revision,profile,actor_role) VALUES (:id,:t,:revision,CAST(:profile AS jsonb),'support')"),
                {"id": version, "t": target, "revision": revision, "profile": json.dumps(values)})
            await db.execute(text("""INSERT INTO business_profile_state(tenant_id,published_version_id,generation)
                VALUES (:t,:id,1) ON CONFLICT(tenant_id) DO UPDATE SET published_version_id=:id,generation=business_profile_state.generation+1"""),
                {"t": target, "id": version})
        versions[target] = revision
        return version

    async def persisted():
        async with d["migrator"].connect() as db:
            stock = (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE tenant_id=:t AND variant_id=:id"),
                {"t": d["a"], "id": variant["id"]})).scalar_one()
            orders = (await db.execute(text("SELECT id,status,revision FROM commercial_orders WHERE tenant_id=:t ORDER BY id"), {"t": d["a"]})).mappings().all()
            events = (await db.execute(text("SELECT count(*) FROM commercial_events WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one()
            appointment_status = (await db.execute(text("SELECT status FROM appointments WHERE id=:id"), {"id": appointment["id"]})).scalar_one()
            return stock, [dict(row) for row in orders], events, appointment_status

    async def quote(selection=None, who=None):
        return await store.create_quote(who or scope, [{"variant_id": variant["id"], "quantity": 1}], fulfillment=selection)

    async def draft(selection=None):
        q = await quote(selection)
        order = await store.create_order(scope, q["id"])
        return q, order

    async def confirm(q, order, message="synthetic-receiving-confirm"):
        actor = store.CustomerScope(d["a"], sender[1:], message)
        return await store.confirm_order(actor, order["id"], revision=order["revision"], token=q["confirmation_token"],
            summary_message_id=scope.message_id)

    try:
        await publish(); await publish(d["b"])
        _, variant = await setup_item(d["factory"], d["a"])
        _, foreign_variant = await setup_item(d["factory"], d["b"])
        async with d["factory"]() as db:
            appointment = await book_appointment(db, d["a"], d["pro"], [d["beard"]], sender,
                "Synthetic receiving customer", start_at(d["day"], 10), "receiving-booking")
            await confirm_hold(db, d["a"], appointment["id"])
        selection = {"mode": "pickup", "pickup_appointment_id": appointment["id"]}
        yield SimpleNamespace(**locals())
    finally:
        async with d["migrator"].begin() as db:
            for table in TABLES:
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})
            for table in ("business_profile_events", "business_profile_state", "business_profile_versions"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})


@pytest.mark.asyncio
async def test_receiving_contract_is_persistent_recoverable_and_does_not_mutate_appointment(receiving):
    h = receiving
    q, order = await h.draft(h.selection)
    assert q["fulfillment_mode"] == "pickup" and q["pickup_appointment_id"] == h.appointment["id"]
    assert q["fulfillment_snapshot"]["pickup_location"] == "Published synthetic location"
    result = await h.confirm(q, order)
    assert result["status"] == "confirmed" and result["payment_status"] == "not_verified"
    recovered = await store.get_order(h.scope, order["id"])
    assert recovered["quote"]["fulfillment_snapshot"] == q["fulfillment_snapshot"]
    assert recovered["quote"]["pickup_appointment_current"]["status"] == "confirmed"
    assert (await h.persisted())[0] == 1 and (await h.persisted())[3] == "confirmed"
    # Known completion is read-only recovery, even after its associated date passes.
    async with h.d["factory"]() as db:
        await cancel_appointment(db, h.d["a"], h.appointment["id"])
    before = await h.persisted()
    assert (await h.confirm(q, order, "synthetic-repeated-confirm"))["status"] == "confirmed"
    assert await h.persisted() == before


@pytest.mark.asyncio
async def test_legacy_none_and_pickup_without_booking_preserve_independent_product_orders(receiving):
    h = receiving
    q = await h.quote()
    assert all(q[name] is None for name in ("fulfillment_mode", "pickup_appointment_id", "fulfillment_snapshot"))
    q2 = await h.quote({"mode": "pickup"}, store.CustomerScope(h.d["a"], h.sender[1:], "synthetic-no-booking"))
    assert q2["pickup_appointment_id"] is None and q2["fulfillment_snapshot"]["pickup_appointment"] is None
    recovered = await store.get_quote(h.scope, q["id"])
    assert recovered["fulfillment_snapshot"] is None


@pytest.mark.asyncio
async def test_two_tenants_and_contacts_cannot_read_or_associate_foreign_booking(receiving):
    h = receiving
    q = await h.quote(h.selection)
    other_phone = store.CustomerScope(h.d["a"], phone(902)[1:], "synthetic-other-phone")
    with pytest.raises(HTTPException) as foreign_contact:
        await h.quote(h.selection, other_phone)
    assert foreign_contact.value.status_code == 404
    with pytest.raises(HTTPException):
        await store.get_quote(other_phone, q["id"])
    assert await store.pickup_appointments(other_phone) == []
    async with h.d["factory"]() as db:
        with pytest.raises(HTTPException) as cross_tenant:
            await commercial.create_quote(db, h.d["b"], CreateQuote(
                items=[{"variant_id": h.foreign_variant["id"], "quantity": 1}], contact_e164=h.sender, fulfillment=h.selection), SUPPORT)
    assert cross_tenant.value.status_code == 404
    assert (await h.persisted())[0] == 0


@pytest.mark.asyncio
async def test_replay_same_message_with_different_receiving_contract_is_rejected(receiving):
    h = receiving
    q = await h.quote(h.selection)
    assert (await h.quote(h.selection))["id"] == q["id"]
    for selection in ({"mode": "pickup"}, {"mode": "delivery"}, None):
        with pytest.raises(HTTPException) as conflict:
            await h.quote(selection)
        assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_delivery_never_confirms_or_reserves_and_has_no_fake_shipping_amount(receiving):
    h = receiving
    q, order = await h.draft({"mode": "delivery"})
    assert q["confirmation_requirements_met"] is False
    assert q["limitations"] == ["delivery_requires_team_review"]
    assert q["fulfillment_snapshot"]["requires_team_review"] is True
    before = await h.persisted()
    with pytest.raises(HTTPException) as error:
        await h.confirm(q, order)
    assert error.value.status_code == 409 and await h.persisted() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "capability", "cancel", "contact"])
async def test_changed_policy_booking_or_contact_prevents_final_mutation(receiving, change):
    h = receiving
    q, order = await h.draft(h.selection)
    if change == "profile":
        await h.publish()
    elif change == "capability":
        await h.publish(capabilities=[])
    elif change == "cancel":
        async with h.d["factory"]() as db:
            await cancel_appointment(db, h.d["a"], h.appointment["id"])
    else:
        async with h.d["migrator"].begin() as db:
            await db.execute(text("UPDATE customers SET whatsapp_e164=:phone WHERE tenant_id=:t AND id=:id"),
                {"t": h.d["a"], "id": h.appointment["customer_id"], "phone": phone(903)})
    before = await h.persisted()
    with pytest.raises(HTTPException) as error:
        await h.confirm(q, order)
    assert error.value.status_code in {404, 409, 423} and await h.persisted() == before


@pytest.mark.asyncio
async def test_product_cancel_does_not_cancel_booking_and_booking_cancel_preserves_order_reference(receiving):
    h = receiving
    q, order = await h.draft(h.selection)
    await h.confirm(q, order)
    async with h.d["factory"]() as db:
        await cancel_appointment(db, h.d["a"], h.appointment["id"])
    recovered = await store.get_order(h.scope, order["id"])
    assert recovered["status"] == "confirmed" and (await h.persisted())[0] == 1
    assert recovered["quote"]["pickup_appointment_id"] == h.appointment["id"]
    assert recovered["quote"]["pickup_appointment_current"]["status"] == "cancelled"
    actor = store.CustomerScope(h.d["a"], h.sender[1:], "synthetic-order-cancel")
    cancelled = await store.cancel_order(actor, order["id"], revision=2, summary_message_id="synthetic-cancel-summary",
        summary_expires_at=commercial.now_utc()+timedelta(minutes=15))
    assert cancelled["status"] == "cancelled" and (await h.persisted())[0] == 0
    assert (await h.persisted())[3] == "cancelled"
    with pytest.raises(DBAPIError):
        async with h.d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM appointments WHERE tenant_id=:t AND id=:id"),
                {"t": h.d["a"], "id": h.appointment["id"]})


@pytest.mark.asyncio
async def test_concurrent_quote_order_and_confirmation_each_mutate_once(receiving):
    h = receiving
    quotes = await asyncio.gather(*(h.quote(h.selection) for _ in range(5)))
    assert len({q["id"] for q in quotes}) == 1
    q = quotes[0]
    orders = await asyncio.gather(*(store.create_order(h.scope, q["id"]) for _ in range(5)))
    assert len({order["id"] for order in orders}) == 1
    results = await asyncio.gather(*(h.confirm(q, orders[0], f"synthetic-parallel-{i}") for i in range(5)))
    assert all(result["status"] == "confirmed" for result in results)
    assert (await h.persisted())[0] == 1


@pytest.mark.asyncio
async def test_db_clock_crossing_after_stock_preflight_rolls_back_all_confirmation_writes(receiving, monkeypatch):
    h = receiving
    async with h.d["migrator"].begin() as db:
        await db.execute(text("""WITH clock AS (SELECT clock_timestamp()+interval '5 seconds' AS starts)
            UPDATE appointments SET start_at=clock.starts,end_at=clock.starts+interval '20 minutes',
                occupied_from=clock.starts,occupied_until=clock.starts+interval '20 minutes',updated_at=clock_timestamp()
            FROM clock WHERE tenant_id=:t AND id=:id"""), {"t": h.d["a"], "id": h.appointment["id"]})
    q, order = await h.draft(h.selection)
    before = await h.persisted()
    crossed = []

    def delayed_factory():
        session = h.d["factory"]()
        execute = session.execute
        async def delayed(statement, parameters=None, *args, **kwargs):
            if str(statement).startswith("UPDATE commercial_orders SET status='confirmed'"):
                future = (await execute(text("SELECT start_at>clock_timestamp() FROM appointments WHERE tenant_id=:t AND id=:id"),
                    {"t": h.d["a"], "id": h.appointment["id"]})).scalar_one()
                assert future is True
                await execute(text("""SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM(start_at-clock_timestamp())))+0.02)
                    FROM appointments WHERE tenant_id=:t AND id=:id"""), {"t": h.d["a"], "id": h.appointment["id"]})
                crossed.append(True)
            return await execute(statement, parameters, *args, **kwargs)
        session.execute = delayed
        return session

    monkeypatch.setattr(store, "get_session_factory", lambda: delayed_factory)
    with pytest.raises(HTTPException) as error:
        await h.confirm(q, order)
    assert error.value.status_code == 409 and crossed == [True]
    assert await h.persisted() == before
