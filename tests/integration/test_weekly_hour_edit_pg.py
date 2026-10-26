"""Edit real weekly shifts and their breaks without compromising tenant isolation."""
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from app.main import app
from test_catalog import setup_catalog, headers


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_weekly_hour_edit_preserves_breaks_and_rejects_overlaps(setup_catalog):
    tenant, other_tenant, _, migrator = setup_catalog
    base = f"/api/v1/tenants/{tenant}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        person = (await c.post(base + "/professionals", headers=headers(), json={"name": "Teste sem dados reais"})).json()["id"]
        another = (await c.post(base + "/professionals", headers=headers(), json={"name": "Segundo teste"})).json()["id"]
        day = {"professional_id": person, "weekday": 0, "start_time": "09:00", "end_time": "18:00"}
        created = await c.post(base + "/working_hours", headers=headers(), json=day)
        assert created.status_code == 201, created.text
        hour_id = created.json()["id"]
        late = await c.post(base + "/working_hours", headers=headers(), json={**day, "weekday": 2, "start_time": "19:00", "end_time": "21:00"})
        assert late.status_code == 201, late.text
        pause = await c.post(base + "/working_breaks", headers=headers(), json={
            "working_hour_id": hour_id, "start_time": "12:00", "end_time": "13:00",
        })
        assert pause.status_code == 201, pause.text
        pause_id = pause.json()["id"]

        edited = await c.put(base + "/working_hours/" + hour_id, headers=headers(), json={
            **day, "start_time": "10:00",
        })
        assert edited.status_code == 200, edited.text

        # A new shift cannot swallow an existing day period.
        overlapping = await c.put(base + "/working_hours/" + hour_id, headers=headers(), json={
            **day, "weekday": 2,
        })
        assert overlapping.status_code == 409 and "Já existe" in overlapping.text

        # A shift cannot silently invalidate an attached lunch break.
        invalid_break = await c.put(base + "/working_hours/" + hour_id, headers=headers(), json={
            **day, "start_time": "13:00",
        })
        assert invalid_break.status_code == 409 and "pausas" in invalid_break.text

        transferred = await c.put(base + "/working_hours/" + hour_id, headers=headers(), json={
            **day, "professional_id": another,
        })
        assert transferred.status_code == 409

        # Change weekday without destroying the linked break. 0=Monday, 2=Wednesday.
        moved = await c.put(base + "/working_hours/" + hour_id, headers=headers(), json={
            **day, "weekday": 1, "start_time": "09:00",
        })
        assert moved.status_code == 200, moved.text
        hours = (await c.get(base + "/working_hours", headers=headers())).json()
        assert next(x for x in hours if x["id"] == hour_id)["weekday"] == 1

        new_pause = await c.put(base + "/working_breaks/" + pause_id, headers=headers(), json={
            "working_hour_id": hour_id, "start_time": "12:30", "end_time": "13:30",
        })
        assert new_pause.status_code == 200, new_pause.text
        outside = await c.put(base + "/working_breaks/" + pause_id, headers=headers(), json={
            "working_hour_id": hour_id, "start_time": "08:00", "end_time": "09:00",
        })
        assert outside.status_code == 422
        assert (await c.post(base + "/working_breaks", headers=headers(), json={
            "working_hour_id": hour_id, "start_time": "13:00", "end_time": "14:00",
        })).status_code == 409

        stored = (await c.get(base + "/working_breaks", headers=headers())).json()
        assert next(x for x in stored if x["id"] == pause_id)["working_hour_id"] == hour_id
        assert next(x for x in stored if x["id"] == pause_id)["start_time"].startswith("12:30")
        assert (await c.get(f"/api/v1/tenants/{other_tenant}/working_hours", headers=headers())).json() == []
        assert (await c.put(f"/api/v1/tenants/{other_tenant}/working_hours/{hour_id}",
                            headers=headers(), json=day)).status_code == 404
        assert (await c.put(base + "/working_hours/" + hour_id, headers=headers("viewer"),
                            json=day)).status_code == 403

    async with migrator.connect() as db:
        row = (await db.execute(text("""
            SELECT professional_id,weekday FROM working_hours
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": tenant, "id": UUID(hour_id)})).one()
        assert row.professional_id == UUID(person) and row.weekday == 1
