"""Reception journey over real HTTP, with tenant boundaries and clinical authorization intact."""
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.booking_api import router as booking_router
from app.catalog_api import Actor, authenticate, router as catalog_router
from app.dental_clinic_api import router as clinical_router
from app.dental_reception import router as reception_router
from app.plan_routing import include_plan_router
from tests.integration.test_dental_clinic_postgres import clinic_db, clinic_api_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_reception_operates_without_a_clinical_grant_and_keeps_patient_identity(clinic_api_db):
    d = clinic_api_db
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE dental_clinicians SET enabled=false WHERE tenant_id=:t"), {"t": d["a"]})

    app = FastAPI()
    for router in (booking_router, catalog_router, clinical_router, reception_router):
        include_plan_router(app, router)
    owner = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: owner
    tenant_path = f"/api/v1/tenants/{d['a']}"
    reception_path = tenant_path + "/dental/reception"
    zone = ZoneInfo("America/Campo_Grande")
    day = (datetime.now(zone) + timedelta(days=3)).date()
    first_start = datetime.combine(day, time(9), zone)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.invalid") as client:
        async def call(method, path, expected=200, data=None, **kwargs):
            response = await client.request(method, path, json=data, **kwargs)
            assert response.status_code == expected, response.text
            return response.json()

        procedure = await call("POST", tenant_path + "/services", 201,
                               {"name": "Avaliação sintética", "duration_min": 30, "price_cents": 0})
        professional_payload = {
            "professional": {"name": "Dentista sintético", "service_ids": [procedure["id"]],
                             "idempotency_key": str(uuid4())},
            "profile": {"specialties": ["Clínica geral", "Endodontia"], "cro_number": "987654", "cro_state": "MS",
                        "default_room_id": str(d["room_a"])},
        }
        staff = await call("POST", reception_path + "/professionals", 201, professional_payload)
        assert staff["specialties"] == ["Clínica geral", "Endodontia"]
        assert (await call("POST", reception_path + "/professionals", 201, professional_payload))["id"] == staff["id"]
        different = {**professional_payload, "profile": {**professional_payload["profile"], "cro_number": "13579"}}
        await call("POST", reception_path + "/professionals", 409, different)
        other_staff = await call("POST", reception_path + "/professionals", 201, {
            "professional": {"name": "Dentista sintético dois", "service_ids": [procedure["id"]], "idempotency_key": str(uuid4())},
            "profile": {},
        })
        for person in (staff, other_staff):
            await call("POST", tenant_path + "/working_hours", 201, {
                "professional_id": person["id"], "weekday": day.weekday(), "start_time": "08:00", "end_time": "17:00",
            })
        telephone = f"+5567{str(uuid4().int)[-9:]}"
        def patient_data(name):
            return {"name": name, "birth_date": "2015-03-01", "guardian_name": "Responsável sintético",
                    "guardian_phone": telephone, "request_id": str(uuid4())}
        first_payload = {**patient_data("Paciente sintético primeiro"), "administrative_profile": {
            "email":"synthetic@example.invalid", "record_number":"QA-100", "financial_name":"Pagador sintético",
            "address":{"postal_code":"79000000","city":"Município de teste","state":"MS"},
        }}
        first = await call("POST", reception_path + "/patients", 201, first_payload)
        assert (await call("POST", reception_path + "/patients", 201, first_payload))["id"] == first["id"]
        second = await call("POST", reception_path + "/patients", 201, patient_data("Paciente sintético segundo"))
        assert second["id"] != first["id"]
        found = await call("POST", reception_path + "/patients/search", data={"query":telephone,"limit":10})
        assert {item["id"] for item in found["items"]} == {first["id"], second["id"]}
        updated = await call("PATCH", reception_path + "/patients/" + first["id"], data={
            "name": first["name"], "guardian_name": first["guardian_name"], "guardian_phone": telephone,
            "birth_date": first["birth_date"], "expected_version": first["version"],
        })
        assert updated["administrative_profile"] == first["administrative_profile"]
        await call("GET", tenant_path + "/dental/patients/" + first["id"] + "/records", 403)
        await call("GET", reception_path + "/patients/" + str(d["patient_b"]), 404)

        options = await call("GET", reception_path + "/booking-options")
        assert any(row["id"] == staff["id"] and row["has_hours"] and row["procedure_ids"] == [procedure["id"]]
                   for row in options["professionals"])
        slots = {"professional_id":staff["id"],"procedure_id":procedure["id"],"day":day.isoformat(),
                 "duration_min":45,"room_id":str(d["room_a"])}
        available = await call("GET", reception_path + "/availability", params=slots)
        assert first_start.isoformat() in available["slots"]
        booked_payload = {
            "patient_id":first["id"], "professional_id":staff["id"], "procedure_id":procedure["id"],
            "room_id":str(d["room_a"]), "duration_min":45, "visit_type":"evaluation", "contact":"guardian",
            "start_at":first_start.isoformat(), "request_id":str(uuid4()), "administrative_note":"Observação sintética",
        }
        booked = await call("POST", reception_path + "/appointments", 201, booked_payload)
        assert (await call("POST", reception_path + "/appointments", 201, booked_payload))["id"] == booked["id"]
        second_payload = {**booked_payload,"patient_id":second["id"],"professional_id":other_staff["id"],"request_id":str(uuid4())}
        await call("POST", reception_path + "/appointments", 409, second_payload)
        await call("POST", reception_path + "/appointments", 409, {**second_payload,"professional_id":staff["id"]})
        await call("POST", reception_path + "/appointments", 409, {
            **second_payload, "patient_id":first["id"], "room_id":str(d["room_a2"]),
        })
        second_booking = await call("POST", reception_path + "/appointments", 201, {
            **second_payload, "room_id": str(d["room_a2"]),
        })
        labels = await call("POST", reception_path + "/appointment-labels", data={"ids":[booked["id"],second_booking["id"]]})
        assert {row["patient_name"] for row in labels["items"]} == {first["name"], second["name"]}
        detail = await call("GET", reception_path + "/appointments/" + booked["id"])
        assert detail["administrative_note"] == booked_payload["administrative_note"]
        visits = await call("GET", reception_path + "/patients/" + first["id"] + "/visits")
        assert [visit["id"] for visit in visits["items"]] == [booked["id"]]

        appointment = tenant_path + "/appointments/" + booked["id"]
        record = await call("GET", appointment)
        assert record["reserved_duration_min"] == 45
        assert "administrative_note" not in record
        changed = await call("POST", appointment + "/reschedule", data={
            "start_at":(first_start + timedelta(hours=2)).isoformat(), "expected_updated_at":record["updated_at"],
            "idempotency_key":str(uuid4()),
        })
        assert changed["reserved_duration_min"] == 45
        cancelled = await call("POST", appointment + "/cancel", data={
            "expected_updated_at":changed["updated_at"],"requested_by":"business","idempotency_key":str(uuid4()),
        })
        assert cancelled["status"] == "cancelled"
        assert first_start.isoformat() in (await call("GET", reception_path + "/availability", params=slots))["slots"]
        without_phone = await call("POST", reception_path + "/appointments", 201, {
            **booked_payload, "request_id":str(uuid4()), "contact":"none",
        })
        no_phone = await call("GET", tenant_path + "/appointments/" + without_phone["id"])
        assert no_phone["customer_phone"] is None
