"""Verify empty packaging and customer loans with the tenant runtime role."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.catalog_api import Actor
from app.database import tenant_transaction
from test_delivery_catalog_postgres import business, client
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


async def test_loans_update_empty_stock_and_customer_balance_atomically(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    customer, foreign_customer = uuid4(), uuid4()
    async with ctx.migration.begin() as db:
        for t, identity in ((tenant, customer), (ctx.b, foreign_customer)):
            await db.execute(text("INSERT INTO customers(tenant_id,id,name) VALUES(:t,:c,'Cliente sintético')"),
                             {"t":t,"c":identity})
    root = f"/api/v1/tenants/{tenant}/delivery/containers"
    payload = {"kind":"jug","name":"Garrafão 20 L","quantity":3,"idempotency_key":uuid4().hex}
    async with client(actor) as http:
        created = await http.post(root,json=payload)
        assert created.status_code == 201, created.text
        assert (await http.post(root,json=payload)).json() == created.json()
        assert (await http.post(root,json={**payload,"idempotency_key":uuid4().hex})).status_code == 409
        endpoint = root+"/"+created.json()["id"]
        loan = {"kind":"lend","quantity":2,"expected_revision":1,"idempotency_key":uuid4().hex}
        assert (await http.post(endpoint+"/movements",json={**loan,"customer_id":str(foreign_customer)})).status_code == 404
        outgoing = {**loan,"customer_id":str(customer)}
        moved, replay = await asyncio.gather(*[http.post(endpoint+"/movements",json=outgoing) for _ in range(2)])
        assert moved.status_code == replay.status_code == 200, moved.text
        assert moved.json() == replay.json() and moved.json()["empty_quantity"] == 1
        listed = (await http.get(root,params={"kind":"jug","search":"20 l"})).json()["items"]
        assert len(listed) == 1 and listed[0]["loaned"] == 2
        loans = (await http.get(endpoint+"/loans")).json()["items"]
        assert loans == [{"customer_id":str(customer),"customer_name":"Cliente sintético","quantity":2}]
        returning = {"kind":"return","quantity":3,"expected_revision":2,"customer_id":str(customer),
                     "idempotency_key":uuid4().hex}
        assert (await http.post(endpoint+"/movements",json=returning)).status_code == 409
        one, two = await asyncio.gather(*[
            http.post(endpoint+"/movements",json={**returning,"quantity":2,"idempotency_key":uuid4().hex}) for _ in range(2)
        ])
        assert sorted([one.status_code,two.status_code]) == [200,409], [one.text,two.text]
        assert (await http.get(endpoint+"/loans")).json()["items"] == []
        history = (await http.get(endpoint+"/movements")).json()["items"]
        assert [(x["kind"],x["balance_before"],x["balance_after"]) for x in history] == [
            ("return",1,3),("lend",3,1),("initial",0,3),
        ]
    async with client(Actor("member", account_id=ctx.other)) as outsider:
        assert (await outsider.get(endpoint+"/loans")).status_code == 403
    async with ctx.runtime() as db:
        async with tenant_transaction(db,ctx.b):
            count = (await db.execute(text("SELECT count(*) FROM delivery_container_movements"))).scalar_one()
            assert count == 0
