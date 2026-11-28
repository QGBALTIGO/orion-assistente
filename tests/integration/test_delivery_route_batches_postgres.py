"""Pilot route reservations and loading use real PostgreSQL, never live customers."""
import asyncio
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.delivery_driver_api import router
from app.panel_auth import panel_actor
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import order_payload, stock
from test_onboarding_postgres import onboarding_db  # noqa: F401

pytestmark=pytest.mark.integration

def driver_client(claims):
    app=FastAPI()
    app.include_router(router)
    app.dependency_overrides[panel_actor]=lambda:claims
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test")


async def test_reserve_batch_exclusive_loading_and_legacy_stock(onboarding_db,monkeypatch):
    ctx=onboarding_db
    tenant,actor=await business(ctx,monkeypatch)
    await complete_delivery_setup(ctx,tenant)
    base=f"/api/v1/tenants/{tenant}/delivery"
    root=f"/api/v1/entregador/{tenant}"
    c1={"role":"member","account_id":str(ctx.other),"email":"driver1@example.test","email_verified":True}
    c2={"role":"member","account_id":str(ctx.actor.account_id),"email":"driver2@example.test","email_verified":True}
    address={"street":"Rua sintética","number":"1","neighborhood":"Centro","city":"Campo Grande","state":"MS"}
    def request(orders,**extra):
        return {"orders":orders,"idempotency_key":uuid4().hex,**extra}
    def ref(o):
        return {"order_id":o["id"],"expected_revision":o["revision"]}
    async with client(actor) as admin,driver_client(c1) as d1,driver_client(c2) as d2:
        for claims,driver in ((c1,d1),(c2,d2)):
            courier=await admin.post(base+"/logistics/couriers",json={"name":claims["email"],"idempotency_key":uuid4().hex})
            assert courier.status_code==201,courier.text
            grant=await admin.put(base+"/logistics/couriers/"+courier.json()["id"]+"/portal",json={"email":claims["email"],"expected_revision":0})
            assert grant.status_code==200,grant.text
            assert (await driver.put(root+"/shift",json={"state":"available"})).status_code==200
        variant=(await admin.post(base+"/catalog",json=new_product())).json()["variant_id"]
        customer=(await admin.post(base+"/order-customers",json={"name":"Cliente sintético","idempotency_key":uuid4().hex})).json()["id"]
        created=[]
        for _ in range(2):
            payload=await order_payload(admin,base,variant,1,mode="delivery",customer_id=customer,address=address)
            res=await admin.post(base+"/orders",json=payload)
            assert res.status_code==201,res.text
            created.append(res.json())
        assert await stock(admin,base)==(5,2)
        assert (await d1.get(root+"/queue")).json()["available_orders"]==[]
        assert (await d1.post(root+"/reservations",json=request([ref(created[0])]))).status_code==403
        monkeypatch.setenv("ORION_DELIVERY_ROUTE_V2_TENANTS",str(tenant))
        avail=(await d1.get(root+"/queue")).json()["available_orders"]
        assert len(avail)==2 and "street" not in avail[0]["address"] and "customer_name" not in avail[0]
        body1=request([ref(o) for o in created])
        body2=request([ref(created[0])])
        first,second=await asyncio.gather(d1.post(root+"/reservations",json=body1),d2.post(root+"/reservations",json=body2))
        assert sorted([first.status_code,second.status_code])==[200,409],(first.text,second.text)
        if first.status_code!=200:
            row=(await d2.get(root+"/queue")).json()["orders"][0]
            assert (await d2.post(root+"/reservations/release",json=request([ref(row)]))).status_code==200
            avail=(await d1.get(root+"/queue")).json()["available_orders"]
            body1=request([ref(o) for o in avail])
            first=await d1.post(root+"/reservations",json=body1)
        assert first.status_code==200,first.text
        assert (await d1.post(root+"/reservations",json=body1)).json()==first.json()
        reserved=(await d1.get(root+"/queue")).json()["orders"]
        assert len(reserved)==2 and all(o["status"]=="reserved" for o in reserved)
        assert not (await d2.get(root+"/queue")).json()["available_orders"]
        detail=(await admin.get(base+"/orders/"+reserved[0]["id"])).json()
        assert detail["status"]=="confirmed" and detail["assignment_status"]=="reserved"
        assert detail["assignment_courier_name"]==c1["email"]
        ops=(await admin.get(base+"/logistics/positions")).json()
        assert ops["summary"]["waiting_load"]==2 and ops["summary"]["on_route"]==0
        assert sum(row["waiting_load"] for row in ops["items"])==2
        assert await stock(admin,base)==(5,2)
        assert (await d1.put(root+"/shift",json={"state":"off"})).status_code==409
        bad=request([ref(o) for o in reserved],load=[{"variant_id":variant,"quantity":1}],checked=True)
        assert (await d1.post(root+"/routes/start",json=bad)).status_code==422
        assert all(o["status"]=="reserved" for o in (await d1.get(root+"/queue")).json()["orders"])
        good=request([ref(o) for o in reserved],load=[{"variant_id":variant,"quantity":2}],checked=True)
        assert (await d2.post(root+"/routes/start",json={**good,"idempotency_key":uuid4().hex})).status_code==409
        start=await d1.post(root+"/routes/start",json=good)
        assert start.status_code==200,start.text
        assert (await d1.post(root+"/routes/start",json=good)).json()==start.json()
        assert await stock(admin,base)==(5,2)
        route=(await d1.get(root+"/queue")).json()["orders"]
        assert [o["route_position"] for o in route]==[1,2]
        for o in route:
            assert (await admin.get(base+"/orders/"+o["id"])).json()["status"]=="dispatched"
        complete={"action":"complete","expected_revision":route[0]["revision"],"received":True,"payment_method":"cash","idempotency_key":uuid4().hex}
        done=await d1.post(root+"/orders/"+route[0]["id"]+"/actions",json=complete)
        assert done.status_code==200,done.text
        assert (await d1.post(root+"/orders/"+route[0]["id"]+"/actions",json=complete)).json()==done.json()
        assert await stock(admin,base)==(4,1)
        assert (await admin.get(base+"/orders/"+route[0]["id"])).json()["collected_cents"]==1250
