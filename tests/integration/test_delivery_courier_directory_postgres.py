"""Owner enrollment grants only the existing courier portal, never company membership."""
from uuid import uuid4
import pytest
from sqlalchemy import text

from app.catalog_api import Actor
from test_delivery_catalog_postgres import business, client, complete_delivery_setup
from test_delivery_route_batches_postgres import driver_client
from test_onboarding_postgres import onboarding_db  # noqa: F401

pytestmark=pytest.mark.integration


async def test_enrollment_directory_first_access_and_revocation(onboarding_db,monkeypatch):
    ctx=onboarding_db
    tenant,owner=await business(ctx,monkeypatch)
    await complete_delivery_setup(ctx,tenant)
    root=f"/api/v1/tenants/{tenant}/delivery/logistics/couriers"
    portal=f"/api/v1/entregador/{tenant}"
    email="entregador@example.test"
    payload={"name":"Entregador sintético","phone":"67999990001","active":True,
             "email":email.upper(),"idempotency_key":uuid4().hex}
    claims={"role":"member","account_id":str(ctx.other),"email":email,"email_verified":False}
    async with client(owner) as admin,client(Actor("member",account_id=ctx.other)) as foreign,driver_client(claims) as driver:
        assert (await foreign.get(root+"/access-directory")).status_code==403
        assert (await foreign.post(root+"/enroll",json=payload)).status_code==403
        first=await admin.post(root+"/enroll",json=payload)
        assert first.status_code==201,first.text
        record=first.json()
        assert record["email"]==email and record["access_revision"]==1
        assert (await admin.post(root+"/enroll",json=payload)).json()==record
        duplicate=await admin.post(root+"/enroll",json={**payload,"name":"Duplicado","idempotency_key":uuid4().hex})
        assert duplicate.status_code==409,duplicate.text
        directory=(await admin.get(root+"/access-directory")).json()
        assert directory["summary"]=={"total":1,"active":1,"authorized":1,"in_shift":0}
        assert len(directory["items"])==1
        row=directory["items"][0]
        assert row["email"]==email and not row["linked"] and row["open_orders"]==0
        assert (await admin.get(root+"/access-directory",params={"search":email})).json()["items"][0]["id"]==record["id"]
        assert (await driver.get(portal+"/queue")).status_code==403
        claims["email_verified"]=True
        assert (await driver.get(portal+"/queue")).status_code==200
        assert (await driver.put(portal+"/shift",json={"state":"available"})).status_code==200
        linked=(await admin.get(root+"/access-directory")).json()
        assert linked["items"][0]["linked"] and linked["summary"]["in_shift"]==1
        async with ctx.migration.begin() as db:
            count=await db.scalar(text("SELECT count(*) FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a"),{"t":tenant,"a":ctx.other})
            assert count==0
        assert (await foreign.get(root+"/access-directory")).status_code==403
        grant=root+"/"+record["id"]+"/portal"
        revoked=await admin.put(grant,json={"email":None,"expected_revision":1})
        assert revoked.status_code==200,revoked.text
        assert (await driver.get(portal+"/queue")).status_code==403
        final=(await admin.get(root+"/access-directory")).json()
        assert final["summary"]["authorized"]==0 and final["summary"]["in_shift"]==0
        assert not final["items"][0]["linked"]
