"""Exercise logistics configuration, courier dispatch and tenant isolation."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import delivery_logistics as logistics
from app.catalog_api import Actor
from app.database import tenant_transaction
from app.onboarding_schemas import Address
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import stock
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration
MONDAY = datetime(2026, 11, 30, 13, tzinfo=timezone.utc)
ADDRESS = {"street":"Rua sintética", "number":"1", "city":"campo grande",
           "state":"MS", "neighborhood":"vila sao   joao"}
AREAS = [
    {"city":"Campo Grande", "state":"MS", "neighborhood":"Vila São João", "kind":"included", "fee_cents":650},
    {"city":"Campo Grande", "state":"MS", "neighborhood":"Centro", "kind":"excluded", "fee_cents":0},
]


def schedule_payload():
    return {"coverage_mode":"areas", "areas":AREAS, "scheduling_enabled":True,
            "lead_minutes":60, "horizon_days":5,
            "windows":[{"weekday":0, "start":"09:00", "end":"17:00"}], "expected_revision":0}


async def test_coverage_and_appointments_resolve_in_tenant_timezone(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    route = f"/api/v1/tenants/{tenant}/delivery/logistics"
    async with client(actor) as http:
        default = (await http.get(route)).json()
        assert default["coverage_mode"] == "manual" and default["revision"] == 0
        async with ctx.runtime() as db:
            async with tenant_transaction(db, tenant):
                manual = await logistics.quote_delivery(db, tenant, None, requested_fee_cents=350)
                assert manual["delivery_fee_cents"] == 350 and manual["area"] is None
                with pytest.raises(HTTPException, match="cobertura"):
                    await logistics.quote_delivery(db, tenant, Address(**ADDRESS), require_coverage=True)
        payload = schedule_payload()
        overlapping = {**payload, "windows":[*payload["windows"], {"weekday":0, "start":"12:00", "end":"18:00"}]}
        assert (await http.put(route, json=overlapping)).status_code == 422
        duplicates = {**payload, "areas":[*AREAS, {**AREAS[0], "neighborhood":"vila sao joao"}]}
        assert (await http.put(route, json=duplicates)).status_code == 422
        saves = await asyncio.gather(
            http.put(route, json=payload), http.put(route, json={**payload,"lead_minutes":90}),
        )
        assert sorted(response.status_code for response in saves) == [200,409]
        policy = (await http.get(route)).json()
        assert policy["revision"] == 1 and policy["timezone"] == "America/Cuiaba"
        async with ctx.runtime() as db:
            async with tenant_transaction(db, tenant):
                quote = await logistics.quote_delivery(db, tenant, Address(**ADDRESS), MONDAY+timedelta(hours=5),
                    requested_fee_cents=100, require_coverage=True, now=MONDAY, lock=True)
                assert quote["delivery_fee_cents"] == 650
                assert quote["scheduled_at"] == datetime(2026,11,30,18,tzinfo=timezone.utc)
                assert quote["area"]["neighborhood"] == "Vila São João"
                with pytest.raises(HTTPException, match="fora da área"):
                    await logistics.quote_delivery(db, tenant, Address(**{**ADDRESS,"neighborhood":"Centro"}))
                with pytest.raises(HTTPException, match="fora da área"):
                    await logistics.quote_delivery(db, tenant, Address(**{**ADDRESS,"city":"Corumbá"}))
                with pytest.raises(HTTPException, match="antecedência"):
                    await logistics.quote_delivery(db, tenant, Address(**ADDRESS), MONDAY+timedelta(minutes=20), now=MONDAY)
                with pytest.raises(HTTPException, match="janela"):
                    await logistics.quote_delivery(db, tenant, Address(**ADDRESS), MONDAY+timedelta(hours=8), now=MONDAY)
                with pytest.raises(HTTPException, match="limite"):
                    await logistics.quote_delivery(db, tenant, Address(**ADDRESS), MONDAY+timedelta(days=6), now=MONDAY)
        async with ctx.runtime() as db:
            async with tenant_transaction(db, ctx.b):
                assert (await logistics.get_policy(db, ctx.b))["revision"] == 0
            with pytest.raises(DBAPIError):
                async with tenant_transaction(db, ctx.b):
                    await db.execute(text("""
                        INSERT INTO delivery_logistics_policies (tenant_id,configuration,revision)
                        VALUES (:tenant,'{}',1)
                    """), {"tenant":tenant})
    async with ctx.migration.begin() as db:
        await db.execute(text("""
            INSERT INTO tenant_memberships (tenant_id,account_id,role) VALUES (:tenant,:account,'editor')
        """), {"tenant":tenant,"account":ctx.other})
    async with client(Actor("member",account_id=ctx.other)) as editor:
        assert (await editor.get(route)).status_code == 200
        assert (await editor.put(route,json={**schedule_payload(),"expected_revision":1})).status_code == 403
        courier = {"name":"Pessoa entregadora","idempotency_key":uuid4().hex}
        assert (await editor.post(route+"/couriers",json=courier)).status_code == 201


async def test_orders_reprice_before_confirm_and_dispatch_to_active_courier(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx,tenant)
    monkeypatch.setattr(logistics.commerce, "now_utc", lambda: MONDAY)
    root = f"/api/v1/tenants/{tenant}/delivery"
    customer, other_courier = uuid4(), uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:id,:t,'Cliente de teste')"),
                         {"id":customer,"t":tenant})
        await db.execute(text("INSERT INTO delivery_couriers(id,tenant_id,name) VALUES(:id,:t,'Outro entregador')"),
                         {"id":other_courier,"t":ctx.b})
    async with client(actor) as http:
        product = await http.post(root+"/catalog",json=new_product())
        assert product.status_code == 201, product.text
        policy = schedule_payload()
        assert (await http.put(root+"/logistics",json=policy)).status_code == 200
        scheduled = MONDAY+timedelta(hours=3)
        estimate = {"items":[{"variant_id":product.json()["variant_id"],"quantity":1}],
                    "mode":"delivery","address":ADDRESS,"scheduled_at":scheduled.isoformat(),"delivery_fee_cents":25}
        first = await http.post(root+"/orders/estimate",json=estimate)
        assert first.status_code == 200, first.text
        assert first.json()["total_cents"] == 1900
        revised = {**policy,"expected_revision":1,"areas":[{**AREAS[0],"fee_cents":800},AREAS[1]]}
        assert (await http.put(root+"/logistics",json=revised)).status_code == 200
        confirmation = {**estimate,"customer_id":str(customer),"idempotency_key":uuid4().hex,
                        "estimate_version":first.json()["estimate_version"]}
        changed = await http.post(root+"/orders",json=confirmation)
        assert changed.status_code == 409, changed.text
        second = await http.post(root+"/orders/estimate",json=estimate)
        assert second.status_code == 200, second.text
        confirmation["estimate_version"] = second.json()["estimate_version"]
        placed = await http.post(root+"/orders",json=confirmation)
        assert placed.status_code == 201 and placed.json()["total_cents"] == 2050, placed.text
        order_id = placed.json()["id"]
        path = root+"/orders/"+order_id
        courier = {"name":"Pessoa entregadora","phone":"67999999901","idempotency_key":uuid4().hex}
        created = await http.post(root+"/logistics/couriers",json=courier)
        assert created.status_code == 201, created.text
        assert (await http.post(root+"/logistics/couriers",json=courier)).json() == created.json()
        key = created.json()["id"]
        assert (await http.get(root+"/logistics/couriers?active_only=true")).json()["items"] == [created.json()]
        dispatch = {"action":"dispatch","expected_revision":1,"idempotency_key":uuid4().hex}
        unauthorized = await http.post(path+"/transitions",json={**dispatch,"courier_id":str(other_courier)})
        assert unauthorized.status_code == 404, unauthorized.text
        sent = await http.post(path+"/transitions",json={**dispatch,"courier_id":key})
        assert sent.status_code == 200, sent.text
        altered = {"name":"Entregador renomeado","active":False,"expected_revision":1}
        assert (await http.put(root+"/logistics/couriers/"+key,json=altered)).status_code == 200
        assert (await http.put(root+"/logistics/couriers/"+key,json=altered)).status_code == 409
        assert (await http.get(root+"/logistics/couriers?active_only=true")).json()["items"] == []
        detail = (await http.get(path)).json()
        assert detail["courier_id"] == key and detail["courier_name"] == courier["name"]
        assert datetime.fromisoformat(detail["scheduled_at"]) == scheduled
        reattempt = {"action":"recipient_absent","idempotency_key":uuid4().hex,"expected_revision":2,
                     "note":"Destinatário não encontrado"}
        unreturned = await http.post(path+"/transitions",json=reattempt)
        assert unreturned.status_code == 422, unreturned.text
        assert (await http.get(path)).json()["courier_id"] == key
        absent = await http.post(path+"/transitions",json={**reattempt,"returned_to_stock":True})
        assert absent.status_code == 200 and absent.json()["status"] == "confirmed", absent.text
        recovery = (await http.get(path)).json()
        assert recovery["courier_id"] is None and recovery["courier_name"] is None
        assert recovery["scheduled_at"] is None and await stock(http,root) == (5,1)
        revised_schedule = scheduled + timedelta(hours=1)
        rescheduled = await http.post(path+"/transitions",json={"action":"reschedule","expected_revision":3,
            "idempotency_key":uuid4().hex,"scheduled_at":revised_schedule.isoformat()})
        assert rescheduled.status_code == 200, rescheduled.text
        revised_order = (await http.get(path)).json()
        assert datetime.fromisoformat(revised_order["scheduled_at"]) == revised_schedule
        assert revised_order["total_cents"] == 2050 and await stock(http,root) == (5,1)
        async with ctx.runtime() as db:
            async with tenant_transaction(db, tenant):
                row = (await db.execute(text("""
                    SELECT logistics_snapshot FROM delivery_order_details WHERE tenant_id=:t AND order_id=:id
                """), {"t":tenant,"id":UUID(order_id)})).scalar_one()
                assert row["policy_revision"] == 2 and row["delivery_fee_cents"] == 800
    async with client(Actor("member",account_id=ctx.other)) as outsider:
        assert (await outsider.get(root+"/logistics/couriers")).status_code == 403
