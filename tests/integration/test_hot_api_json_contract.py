"""Whole HTTP bytes/headers against legacy encoding, using real PG writes and RLS."""
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import httpx
import pytest
from sqlalchemy import text

from app import booking_api, booking_service, catalog_api, commercial_api, commercial_service
from app.catalog_api import Actor
from app.database import tenant_transaction
from app.main import app
from test_booking import booking_db, headers, phone, start_at
from test_commercial_catalog_postgres import commerce, setup_item

pytestmark = pytest.mark.integration


def legacy_response(response, payload, status=200):
    # Before this optimization the endpoint encoded its value, then FastAPI
    # encoded it again. Keep that independent reference, including raw headers.
    expected = JSONResponse(jsonable_encoder(jsonable_encoder(payload)), status_code=status)
    assert response.status_code == status
    assert response.content == expected.body
    assert sorted(response.headers.raw) == sorted(expected.raw_headers)


@pytest.mark.asyncio
async def test_real_booking_and_catalog_preserve_entire_wire_contract(booking_db, monkeypatch):
    d, actor = booking_db, Actor("support")
    tenant, factory = d["a"], d["factory"]
    monkeypatch.setattr(booking_api, "get_session_factory", lambda: factory)
    monkeypatch.setattr(catalog_api, "get_session_factory", lambda: factory)
    base = f"/api/v1/tenants/{tenant}"

    async def expected_appointment(identifier, *, proposal=False, history=False):
        async with factory() as db:
            async with tenant_transaction(db, tenant):
                value = (await booking_service._hold_proposal(db, tenant, identifier) if proposal else
                         await booking_service.read_appointment(db, tenant, identifier, include_history=history))
        return booking_api.appointment_response(value, actor, tenant)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers()) as client:
        async with factory() as db:
            async with tenant_transaction(db, tenant):
                catalog = (await db.execute(text("""SELECT * FROM services WHERE tenant_id=:t
                    AND catalog_kind <> 'variant' ORDER BY sort_order,name,id LIMIT 200"""), {"t": tenant})).mappings().all()
        legacy_response(await client.get(base + "/services"), catalog)
        async with factory() as db:
            available = await booking_service.list_slots(db, tenant, d["pro"], [d["cut"]], d["day"])
        legacy_response(await client.get(base + "/availability", params={"professional_id": str(d["pro"]),
            "service_ids": str(d["cut"]), "day": str(d["day"])}), available)

        request = {"professional_id": str(d["pro"]), "service_ids": [str(d["cut"])],
                   "customer_phone": phone(880), "customer_name": "Pessoa sintética — ação e revisão",
                   "start_at": start_at(d["day"], 10).isoformat(), "idempotency_key": uuid4().hex}
        held = await client.post(base + "/appointments/hold", json=request)
        assert held.status_code == 201, held.text
        identifier = UUID(held.json()["id"])
        legacy_response(held, await expected_appointment(identifier, proposal=True), 201)
        legacy_response(await client.post(base + "/appointments/hold", json=request),
                        await expected_appointment(identifier, proposal=True), 201)

        confirmed = await client.post(base + f"/appointments/{identifier}/confirm",
                                      json={"idempotency_key": uuid4().hex})
        legacy_response(confirmed, await expected_appointment(identifier))
        changed = await client.post(base + f"/appointments/{identifier}/reschedule",
            json={"start_at": start_at(d["day"], 14).isoformat(), "idempotency_key": uuid4().hex})
        legacy_response(changed, await expected_appointment(identifier))
        cancelled = await client.post(base + f"/appointments/{identifier}/cancel", json={
            "requested_by": "business", "reason": "Motivo sintético de revisão", "idempotency_key": uuid4().hex})
        expected = await expected_appointment(identifier)
        legacy_response(cancelled, expected)
        assert expected["status"] == "cancelled" and expected["cancellation"]["requested_by"] == "business"
        legacy_response(await client.get(base + f"/appointments/{identifier}"),
                        await expected_appointment(identifier, history=True))
        legacy_response(await client.get(base + "/appointments", params={"day": str(d["day"])}), [expected])
        legacy_response(await client.get(base + "/appointments", params={"day": str(d["day"]), "paged": "true", "limit": 20}),
                        {"items": [expected], "total": 1, "limit": 20, "offset": 0, "has_more": False})
        legacy_response(await client.get(base + "/appointments", params={"day": str(d["day"]), "paged": "true", "offset": 1}),
                        {"items": [], "total": 1, "limit": 50, "offset": 1, "has_more": False})


@pytest.mark.asyncio
async def test_real_product_catalog_keeps_native_uuid_decimal_time_and_unknown_stock(commerce, monkeypatch):
    tenant, other, factory, _ = commerce
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    monkeypatch.setattr(commercial_api, "get_session_factory", lambda: factory)
    await setup_item(factory, tenant, price=1250, tracked=True)
    await setup_item(factory, tenant, kind="on_request", price=None, tracked=False)
    await setup_item(factory, other, price=9900, tracked=True)
    async with factory() as db:
        expected = await commercial_service.catalog(db, tenant)
    assert len(expected) == 2 and any(p["variants"][0]["quantity_on_hand"] is None for p in expected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers()) as client:
        response = await client.get(f"/api/v1/tenants/{tenant}/commerce/products")
        legacy_response(response, expected)
        assert all(p["tenant_id"] == str(tenant) for p in response.json())
