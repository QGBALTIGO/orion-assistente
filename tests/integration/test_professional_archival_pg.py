"""Archiving a team professional is reversible and must keep operational history.

Integration tests use the normal tenant-aware, real-PostgreSQL fixture.
"""
import os
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from test_management import appointment, customer, management_db

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_archival_preserves_history_and_restores_without_resetting_settings(management_db):
    d = management_db
    c = d["client"]
    person = await customer(d)
    past_id = await appointment(d, person["id"], status="completed")
    path = d["base"] + "/professionals/" + str(d["pro"]) + "/status"

    original = (await c.get(f"/api/v1/tenants/{d['a']}/professionals")).json()
    initial = next(p for p in original if p["id"] == str(d["pro"]))
    assert initial["active"] is True and initial["commission_config_version"] == 1

    archived = await c.patch(path, json={"active": False, "expected_version": 1})
    assert archived.status_code == 200, archived.text
    assert archived.json()["active"] is False
    assert archived.json()["commission_config_version"] == 2
    assert archived.json()["accepts_appointments"] == initial["accepts_appointments"]
    assert archived.json()["commission_percent"] == initial["commission_percent"]
    assert (await c.patch(path, json={"active": False, "expected_version": 1})).status_code == 409
    assert (await c.patch(path, json={"active": False, "expected_version": 2})).status_code == 200

    # This action is a status change, never DELETE: completed bookings still
    # have their original professional and can still be audited.
    async with d["migrator"].connect() as db:
        row = (await db.execute(text("""
            SELECT status, professional_id FROM appointments
            WHERE tenant_id=:tenant AND id=:appointment
        """), {"tenant": d["a"], "appointment": UUID(past_id)})).mappings().one()
        assert row["status"] == "completed" and row["professional_id"] == d["pro"]

    restored = await c.patch(path, json={"active": True, "expected_version": 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["active"] is True
    assert restored.json()["commission_config_version"] == 3
    after = (await c.get(f"/api/v1/tenants/{d['a']}/professionals")).json()
    assert next(p for p in after if p["id"] == str(d["pro"]))["active"] is True


@pytest.mark.asyncio
async def test_archival_requires_rebooking_future_confirmed_appointments(management_db):
    d = management_db
    c = d["client"]
    person = await customer(d)
    appointment_id = await appointment(
        d, person["id"], status="confirmed", start_at=d["now"] + timedelta(days=2),
    )
    path = d["base"] + "/professionals/" + str(d["pro"]) + "/status"
    refused = await c.patch(path, json={"active": False, "expected_version": 1})
    assert refused.status_code == 409 and "Reagende" in refused.text

    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointments SET status='cancelled'
            WHERE tenant_id=:tenant AND id=:appointment
        """), {"tenant": d["a"], "appointment": UUID(appointment_id)})
    allowed = await c.patch(path, json={"active": False, "expected_version": 1})
    assert allowed.status_code == 200 and allowed.json()["active"] is False


@pytest.mark.asyncio
async def test_archival_checks_tenant_and_permissions_and_validates_payload(management_db):
    d = management_db
    c = d["client"]
    path = d["base"] + "/professionals/" + str(d["pro"]) + "/status"
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    assert (await c.patch(path, headers=viewer, json={"active": False, "expected_version": 1})).status_code == 403
    assert (await c.patch(path, json={"active": False})).status_code == 422
    assert (await c.patch(path, json={"active": False, "expected_version": 1,
                                       "tenant_id": str(d["b"])})).status_code == 422
    other = d["base"] + "/professionals/" + str(d["other_pro"]) + "/status"
    assert (await c.patch(other, json={"active": False, "expected_version": 1})).status_code == 404
    assert (await c.patch(path, json={"active": False, "expected_version": 1})).status_code == 200
    remaining = (await c.get(f"/api/v1/tenants/{d['b']}/professionals")).json()
    assert next(p for p in remaining if p["id"] == str(d["other_pro"]))["active"] is True
