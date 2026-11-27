"""Exercise logistics configuration, courier dispatch and tenant isolation."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import delivery_logistics as logistics
from app import delivery_registry
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


@pytest.mark.parametrize("method", ["cash","pix"])
async def test_driver_offer_refusal_retry_delivery_and_location_visibility(onboarding_db,monkeypatch,method):
    from fastapi import FastAPI
    from app.delivery_driver_api import router as driver_router
    from app.panel_auth import panel_actor
    from app import delivery_storefront
    from test_delivery_storefront_postgres import client as shop_client
    ctx=onboarding_db
    tenant,actor=await business(ctx,monkeypatch)
    await complete_delivery_setup(ctx,tenant)
    base=f"/api/v1/tenants/{tenant}/delivery"
    monkeypatch.setenv("ORION_DELIVERY_PUBLIC_TENANTS",str(tenant))
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY",uuid4().hex+uuid4().hex)
    async def test_limit(*args,**kwargs):
        return None
    monkeypatch.setattr(delivery_storefront,"public_limit",test_limit)
    driver_base=f"/api/v1/entregador/{tenant}"
    portal=FastAPI()
    portal.include_router(driver_router)
    email="motorista@example.test"
    claims={"role":"member","account_id":str(ctx.other),"email":email,"email_verified":False}
    portal.dependency_overrides[panel_actor]=lambda:claims
    async with shop_client(actor) as admin,httpx.AsyncClient(transport=httpx.ASGITransport(app=portal),base_url="https://orion.test") as driver:
        container=(await admin.post(base+"/containers",json={"name":"Garrafão 20 L","kind":"jug","quantity":0,
                        "idempotency_key":uuid4().hex})).json()["id"]
        new=new_product()
        new["details"]["exchange_container_id"]=container
        variant=(await admin.post(base+"/catalog",json=new)).json()["variant_id"]
        courier=(await admin.post(base+"/logistics/couriers",json={"name":"Entregadora de teste","idempotency_key":uuid4().hex})).json()["id"]
        grant=base+"/logistics/couriers/"+courier+"/portal"
        assert (await admin.put(grant,json={"email":email,"expected_revision":0})).status_code==200
        assert (await driver.get(driver_base+"/queue")).status_code==403
        claims["email_verified"]=True
        assert (await driver.get(driver_base+"/queue")).json()["state"]=="off"
        second_courier=(await admin.post(base+"/logistics/couriers",json={"name":"Entregador fora do expediente","idempotency_key":uuid4().hex})).json()["id"]
        area={"city":ADDRESS["city"],"state":"MS","neighborhood":ADDRESS["neighborhood"],"fee_cents":100}
        assert (await admin.put(base+"/logistics",json={"expected_revision":0,"coverage_mode":"areas","areas":[area]})).status_code==200
        product=(await admin.get(base+"/storefront/items")).json()["items"][0]
        listing={k:product[k] for k in ("variant_id","variant_revision","product_revision")}
        slug="ensaio-"+uuid4().hex[:12]
        store={"revision":0,"slug":slug,"title":"Distribuidora de homologação","pickup_details":"Depósito de teste",
               "enabled":True,"confirm_catalogue":True,"allow_delivery":True,"payment_methods":[method],
               "items":[{**listing,"label":"Água mineral 20 L","kind":"water"}]}
        published=await admin.put(base+"/storefront",json=store)
        assert published.status_code==200,published.text
        shop="/api/v1/delivery-store/"+slug
        draft={"mode":"delivery","address":ADDRESS,"items":[{"variant_id":variant,"quantity":1}],
               "payment_preference":method,"change_for_cents":2000 if method=="cash" else None,"accept_exchange":True}
        quote=await admin.post(shop+"/quote",json=draft)
        assert quote.status_code==200,quote.text
        checkout=await admin.post(shop+"/orders",json={**draft,"quote":quote.json()["quote"],"customer_name":"Pessoa de avaliação",
            "customer_phone":"67999990000","confirmed":True,"idempotency_key":str(uuid4())})
        assert checkout.status_code==201,checkout.text
        tracking={"tracking_code":checkout.json()["tracking_code"],"include_delivery":True}
        order=(await admin.get(base+"/orders")).json()["items"][0]["id"]
        offr={"action":"offer","courier_id":courier,"expected_revision":1,"idempotency_key":uuid4().hex}
        route=base+"/orders/"+order+"/transitions"
        assert (await admin.post(route,json=offr)).status_code==409
        assert (await driver.put(driver_base+"/shift",json={"state":"available"})).status_code==200
        assert (await admin.post(route,json={**offr,"courier_id":second_courier,"idempotency_key":uuid4().hex})).status_code==409
        key=(await driver.post(driver_base+"/location")).json()["sharing_key"]
        point={"sharing_key":key,"latitude":-20.45,"longitude":-54.6,"observed_at":datetime.now(timezone.utc).isoformat()}
        pos=await driver.put(driver_base+"/location",json=point)
        assert pos.status_code==200,pos.text
        assert (await admin.get(base+"/logistics/positions")).json()["items"][0]["latitude"]==-20.45
        await driver.delete(driver_base+"/location")
        assert (await driver.put(driver_base+"/location",json=point)).status_code==409
        first=await admin.post(route,json=offr)
        assert first.status_code==200,first.text
        queue=(await driver.get(driver_base+"/queue")).json()["orders"]
        assert len(queue)==1 and queue[0]["status"]=="offered" and "street" not in queue[0]["address"]
        assert queue[0]["items"][0]["name"]==new["product"]["name"] and queue[0]["phone"] is None
        other_claims={**claims,"email":"outra-pessoa@example.test"}
        portal.dependency_overrides[panel_actor]=lambda:other_claims
        assert (await driver.get(driver_base+"/queue")).status_code==403
        portal.dependency_overrides[panel_actor]=lambda:claims
        actions=driver_base+"/orders/"+order+"/actions"
        refused=await driver.post(actions,json={"action":"refuse","expected_revision":2,"idempotency_key":uuid4().hex})
        assert refused.status_code==200,refused.text
        assert not (await driver.get(driver_base+"/queue")).json()["orders"]
        second=await admin.post(route,json={**offr,"expected_revision":3,"idempotency_key":uuid4().hex})
        assert second.status_code==200,second.text
        accept={"action":"accept","expected_revision":4,"idempotency_key":uuid4().hex}
        one,two=await asyncio.gather(driver.post(actions,json=accept),driver.post(actions,json=accept))
        assert one.status_code==two.status_code==200,(one.text,two.text)
        assert one.json()==two.json()
        queue=(await driver.get(driver_base+"/queue")).json()["orders"]
        assert queue[0]["address"]["street"]==ADDRESS["street"] and queue[0]["change_for_cents"]==draft["change_for_cents"]
        reached=await driver.post(actions,json={"action":"arrive","expected_revision":5,"idempotency_key":uuid4().hex})
        assert reached.status_code==200,reached.text
        assert (await admin.post(shop+"/status",json=tracking)).json()["delivery_stage"]=="arrived"
        finish={"action":"complete","expected_revision":6,"received":True,"payment_method":"cash","containers_confirmed":True,"idempotency_key":uuid4().hex}
        if method=="cash":
            assert (await driver.post(actions,json={**finish,"received":False})).status_code==422
        else:
            assert queue[0]["pix_state"]=="pending"
            assert (await driver.post(actions,json={**finish,"payment_method":"pix"})).status_code==409
            confirmation=await admin.post(base+"/orders/"+order+"/pix/confirm",json={
                "received":True,"expected_revision":6,"idempotency_key":uuid4().hex})
            assert confirmation.status_code==200,confirmation.text
            assert (await driver.get(driver_base+"/queue")).json()["orders"][0]["pix_state"]=="confirmed"
            finish.update(expected_revision=confirmation.json()["revision"],received=False,payment_method=None)
        completed=await driver.post(actions,json=finish)
        assert completed.status_code==200,completed.text
        assert (await driver.post(actions,json=finish)).json()==completed.json()
        assert not (await driver.get(driver_base+"/queue")).json()["orders"]
        assert await stock(admin,base)==(4,0)
        assert (await admin.get(base+"/orders/"+order)).json()["collected_cents"]==1350
        assert (await admin.get(base+"/orders/"+order)).json()["customer_id"]
        assert (await admin.get(base+"/containers")).json()["items"][0]["empty_quantity"]==1
        assert (await admin.get(base+"/reports/sales")).json()["summary"]["paid_cents"]==1350
        public_status=(await admin.post(shop+"/status",json=tracking)).json()
        assert public_status["delivery_stage"]=="delivered" and public_status["payment_status"]=="received"
        assert not ({"phone","address","courier_name","courier_id","customer_name","latitude"} & public_status.keys())
        assert (await driver.put(driver_base+"/shift",json={"state":"off"})).status_code==200
        assert not (await admin.get(base+"/logistics/positions")).json()["items"]
        assert (await admin.put(grant,json={"email":None,"expected_revision":1})).status_code==200
        assert (await driver.get(driver_base+"/queue")).status_code==403


async def test_anp_uses_only_registered_company_and_exposes_unavailability(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    base = f"/api/v1/tenants/{tenant}/delivery/logistics/anp"
    async def limit(*args, **kwargs):
        pass
    from app import delivery_api
    monkeypatch.setattr(delivery_api, "public_limit", limit)
    async with client(actor) as http:
        assert (await http.get(base)).status_code == 404
        async with ctx.migration.begin() as db:
            await db.execute(text("""UPDATE business_onboarding SET configuration=
                configuration || '{"delivery_offering":"gas_agua"}'::jsonb WHERE tenant_id=:t"""), {"t": tenant})
        assert (await http.get(base)).status_code == 422
        async with ctx.migration.begin() as db:
            await db.execute(text("""INSERT INTO management_preferences (tenant_id,preferences) VALUES
                (:t,'{"business":{"tax_document_kind":"cnpj","tax_document":"11222333000181"}}'::jsonb)"""), {"t": tenant})
        http_client = httpx.AsyncClient
        replies = [{"succeeded":True,"data":[{"cnpj":"11222333000181","autorizacao":"TESTE","classe":"Teste"}]},
                   {"succeeded":True,"data":[{"cnpj":"00000000000191","autorizacao":"OUTRA"}]},
                   {"succeeded":True,"data":[]}, {"succeeded":False,"data":[]}]
        def agency(request):
            assert str(request.url).startswith(delivery_registry.REGISTRY+"?")
            assert request.url.params["cnpj"] == "11222333000181"
            return httpx.Response(200, json=replies.pop(0))
        monkeypatch.setattr(delivery_registry.httpx, "AsyncClient",
                            lambda **kwargs: http_client(transport=httpx.MockTransport(agency), **kwargs))
        found = await http.get(base)
        assert found.status_code == 200 and found.json()["records"][0]["authorization"] == "TESTE"
        assert found.headers["cache-control"] == "no-store"
        assert (await http.get(base)).status_code == 503
        assert (await http.get(base)).json()["records"] == []
        assert (await http.get(base)).status_code == 503


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
