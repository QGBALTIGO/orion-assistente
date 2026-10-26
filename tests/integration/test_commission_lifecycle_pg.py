"""Real PostgreSQL tenant-scoped commission lifecycle (no network or real clients)."""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_management import management_db, customer, appointment

pytestmark = pytest.mark.integration


def config(d, second_id, *, mode="per_service", rule_cents=1000, version=1):
    return {
        "name": "Profissional de teste",
        "active": True,
        "specialty": "Barbeiro",
        "color": "#31ad52",
        "image_url": None,
        "commission_mode": mode,
        "commission_percent": 25,
        "accepts_appointments": True,
        "service_ids": [str(d["service"]), str(second_id)],
        "commission_rules": [
            {"service_id": str(d["service"]), "commission_kind": "percent",
             "commission_percent": 10},
            {"service_id": str(second_id), "commission_kind": "fixed",
             "commission_fixed_cents": rule_cents},
        ],
        "expected_version": version,
    }


@pytest.mark.asyncio
async def test_per_service_configuration_to_completion_dre_settlement_and_immutability(management_db):
    d = management_db
    c = d["client"]
    second = uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO services(id,tenant_id,name,duration_min,price_cents)
            VALUES (:id,:tenant,'Barba sintética',20,2000)
        """), {"id": second, "tenant": d["a"]})
    target = d["base"] + "/professionals/" + str(d["pro"]) + "/configure"
    first = await c.put(target, json=config(d,second))
    assert first.status_code == 200, first.text
    assert first.json()["commission_mode"] == "per_service"
    assert first.json()["commission_config_version"] == 2
    assert (await c.put(target, json=config(d,second))).status_code == 409

    rules = await c.get(d["base"] + "/professionals/commission-rules")
    assert rules.status_code == 200, rules.text
    assert {x["service_id"] for x in rules.json()["items"]} == {
        str(d["service"]), str(second)}
    # Even a privileged support test session is tenant-scoped by route.
    other = await c.get(d["other_base"] + "/professionals/commission-rules")
    assert other.status_code == 200 and other.json()["items"] == []

    person = await customer(d)
    visit = await appointment(d, person["id"], amount=5500)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE appointment_items SET price_snapshot_cents=3500
            WHERE tenant_id=:tenant AND appointment_id=:appointment
        """), {"tenant": d["a"], "appointment": UUID(visit)})
        await db.execute(text("""
            INSERT INTO appointment_items
              (tenant_id,appointment_id,service_id,name_snapshot,
               duration_snapshot_min,price_snapshot_cents,
               prep_snapshot_min,cleanup_snapshot_min,item_order)
            VALUES (:tenant,:appointment,:service,'Barba sintética',20,2000,0,0,1)
        """), {"tenant": d["a"], "appointment": UUID(visit), "service": second})
    complete = await c.post(d["base"] + "/appointments/" + visit + "/complete",
                            json={"idempotency_key": str(uuid4())})
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "completed"
    assert complete.json()["commission_mode_snapshot"] == "per_service"
    assert complete.json()["commission_cents"] == 1350
    items = complete.json()["items"]
    assert [x["commission_cents"] for x in items] == [350, 1000]
    assert [x["commission_kind_snapshot"] for x in items] == ["percent","fixed"]

    report = await c.get(d["base"] + "/finance/commissions", params=d["period"])
    assert report.status_code == 200, report.text
    row = next(x for x in report.json()["items"] if x["id"] == str(d["pro"]))
    assert row["earned_cents"] == 1350 and row["balance_cents"] == 1350
    assert row["commission_mode"] == "per_service"
    dre = await c.get(d["base"] + "/finance/dre", params=d["period"])
    assert dre.status_code == 200, dre.text
    assert dre.json()["summary"]["service_revenue_cents"] == 5500
    assert dre.json()["summary"]["commissions_cents"] == 1350
    assert dre.json()["summary"]["net_result_cents"] == 4150

    preview = await c.get(d["base"] + "/finance/settlements/preview",
                          params={"professional_id": str(d["pro"])})
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["payable_cents"] == 1350
    payment = await c.post(d["base"] + "/finance/settlements",
                           json={"professional_id": str(d["pro"]),
                                 "expected_snapshot": preview.json()["snapshot_hash"],
                                 "payment_method": "pix",
                                 "idempotency_key": str(uuid4())})
    assert payment.status_code == 201, payment.text
    after = await c.get(d["base"] + "/finance/dre", params=d["period"])
    assert after.status_code == 200, after.text
    assert after.json()["summary"]["net_result_cents"] == 4150
    again = await c.get(d["base"] + "/finance/settlements/preview",
                        params={"professional_id": str(d["pro"])})
    assert again.json()["summary"]["payable_cents"] == 0

    updated = await c.put(target,json=config(d,second,rule_cents=1500,version=2))
    assert updated.status_code == 200, updated.text
    assert updated.json()["commission_config_version"] == 3
    # The historical source of truth is the closed appointment, not current rate.
    historical = await c.get(d["base"] + "/finance/commissions", params=d["period"])
    assert next(x for x in historical.json()["items"] if x["id"]==str(d["pro"]))["earned_cents"] == 1350
    async with d["migrator"].connect() as db:
        closed = (await db.execute(text("""
            SELECT commission_cents,commission_mode_snapshot FROM appointments
            WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": d["a"],"id":UUID(visit)})).one()
    assert tuple(closed) == (1350,"per_service")


@pytest.mark.asyncio
async def test_new_professional_is_atomic_idempotent_and_cannot_use_another_tenants_service(management_db):
    d = management_db
    c = d["client"]
    key = "commission-create-"+str(uuid4())
    payload = {
        "name": "Barbeiro adicional", "commission_mode": "per_service",
        "commission_percent": 0, "service_ids": [str(d["service"])],
        "commission_rules": [{
            "service_id": str(d["service"]), "commission_kind": "fixed",
            "commission_fixed_cents": 500,
        }],
        "idempotency_key": key,
    }
    first = await c.post(d["base"] + "/professionals/configure",json=payload)
    assert first.status_code == 201,first.text
    replay = await c.post(d["base"] + "/professionals/configure",json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    assert (await c.post(d["base"] + "/professionals/configure",
                         json={**payload,"name":"Outro nome"})).status_code == 409
    unauthorized = await c.post(d["other_base"] + "/professionals/configure",
                                json={**payload,"idempotency_key":str(uuid4())})
    assert unauthorized.status_code == 422, unauthorized.text
    async with d["migrator"].connect() as db:
        pro_count = (await db.execute(text("""
            SELECT count(*) FROM professionals WHERE tenant_id=:tenant AND id=:id
        """), {"tenant":d["a"],"id":UUID(first.json()["id"])})).scalar_one()
        rule_count = (await db.execute(text("""
            SELECT count(*) FROM professional_commission_rules
            WHERE tenant_id=:tenant AND professional_id=:id
        """), {"tenant":d["a"],"id":UUID(first.json()["id"])})).scalar_one()
    assert (pro_count, rule_count) == (1,1)


@pytest.mark.asyncio
async def test_catalog_service_link_creates_safe_custom_mode_fallback(management_db):
    d = management_db
    c = d["client"]
    rule = {"name":"Profissional de teste","commission_mode":"per_service",
            "commission_percent":25,"service_ids":[],"commission_rules":[],
            "expected_version":1}
    response = await c.put(d["base"]+"/professionals/"+str(d["pro"])+"/configure",json=rule)
    assert response.status_code==200,response.text
    created=await c.post(f"/api/v1/tenants/{d['a']}/professional_services",
                         json={"professional_id":str(d["pro"]),"service_id":str(d["service"])})
    assert created.status_code == 201,created.text
    rules=await c.get(d["base"]+"/professionals/commission-rules")
    assert rules.status_code == 200
    assert rules.json()["items"][0]["commission_kind"]=="percent"
    assert float(rules.json()["items"][0]["commission_percent"])==25.0
