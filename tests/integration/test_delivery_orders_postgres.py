"""Resolve manual orders against the shared stock, CRM and Finance in Postgres."""
import asyncio
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from app.catalog_api import Actor
from app.database import tenant_transaction
from app.management_models import update_row
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


async def order_payload(http, root, variant, quantity, **fields):
    lines = {"items": [{"variant_id": variant, "quantity": quantity}]}
    pricing = {key:value for key,value in fields.items() if key in {"mode","delivery_fee_cents"}}
    estimate = await http.post(root + "/orders/estimate", json={**lines,**pricing})
    assert estimate.status_code == 200, estimate.text
    return {**lines, **fields, "estimate_version":estimate.json()["estimate_version"], "idempotency_key":uuid4().hex}


async def stock(http, root):
    row = (await http.get(root + "/catalog")).json()["items"][0]
    return row["quantity_on_hand"], row["reserved_quantity"]


async def receipts(ctx, tenant):
    async with ctx.migration.begin() as db:
        return (await db.execute(text("""
            SELECT id,amount_cents,customer_id,payment_method,status,version
            FROM financial_entries WHERE tenant_id=:t
        """), {"t": tenant})).mappings().all()


async def test_pickup_payment_and_stock_are_recorded_exactly_once(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    customer, outsider_customer = uuid4(), uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active' WHERE id=:t"), {"t":tenant})
        for t, c, name in [(tenant, customer, "Cliente sintético"), (ctx.a, outsider_customer, "Cliente de outro tenant")]:
            await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:c,:t,:name)"),
                             {"c":c,"t":t,"name":name})
    root = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        product = await http.post(root + "/catalog", json=new_product())
        assert product.status_code == 201, product.text
        variant = product.json()["variant_id"]
        args = await order_payload(http, root, variant, 2, mode="pickup", customer_id=str(customer))
        assert (await http.post(root + "/orders", json=args)).status_code == 409
        await complete_delivery_setup(ctx, tenant)
        matches = (await http.get(root + "/order-customers", params={"search":"Cliente"})).json()
        assert [c["id"] for c in matches["items"]] == [str(customer)]
        assert (await http.post(root+"/orders",json={**args,"customer_id":str(outsider_customer)})).status_code == 404
        first, retry = await asyncio.gather(*[http.post(root+"/orders",json=args) for _ in range(2)])
        assert first.status_code == retry.status_code == 201, first.text
        order = first.json()
        assert retry.json() == order
        assert order["total_cents"] == 2500
        assert await stock(http,root) == (5,2)
        assert not await receipts(ctx, tenant)
        path = root+"/orders/"+order["id"]
        transition = {"action":"fulfill","expected_revision":1,"idempotency_key":uuid4().hex}
        assert (await http.post(path+"/transitions",json=transition)).status_code == 422
        assert await stock(http,root) == (5,2)
        tender = {**transition,"payment_method":"debit_card"}
        paid, duplicate = await asyncio.gather(*[http.post(path+"/transitions",json=tender) for _ in range(2)])
        assert paid.status_code == duplicate.status_code == 200, paid.text
        assert paid.json() == duplicate.json()
        assert await stock(http,root) == (3,0)
        assert (await http.post(path+"/transitions",json={**tender,"idempotency_key":uuid4().hex})).status_code == 409
        detail = (await http.get(path)).json()
        assert detail["status"] == "fulfilled" and detail["items"][0]["quantity"] == 2
        assert detail["mode"] == "pickup" and detail["customer_name"] == "Cliente sintético"
        assert [event["action"] for event in detail["history"]] == ["confirmed","fulfilled"]
        assert (await http.get(root+"/orders",params={"state":"cancelled"})).json()["items"] == []
        assert (await http.get(root+"/orders",params={"start":"2000-01-01","end":"2000-01-02"})).json()["items"] == []
        assert (await http.get(root+"/orders",params={"start":"2000-01-02","end":"2000-01-01"})).status_code == 422
        assert (await http.get(root+"/orders",params={"search":order["id"][:8].upper()})).json()["items"][0]["id"] == order["id"]
        history = (await http.get(root+"/catalog/history")).json()["items"]
        assert history[0]["details"]["movement"] == "sold"
    receipt, = await receipts(ctx, tenant)
    assert receipt["amount_cents"] == 2500 and receipt["payment_method"] == "debit_card"
    assert receipt["status"] == "paid" and receipt["customer_id"] == customer
    assert receipt["id"] == UUID(paid.json()["financial_entry_id"])
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            with pytest.raises(HTTPException, match="vinculado a um pedido"):
                await update_row(db, tenant, "financial_entries", receipt["id"], receipt["version"], {"amount_cents": 1})
    async with client(Actor("member", account_id=ctx.other)) as foreign:
        assert (await foreign.get(path)).status_code == 403


async def test_last_stock_cannot_be_reserved_twice_and_cancel_releases(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    await complete_delivery_setup(ctx, tenant)
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        args = await order_payload(http, root, variant, 4)
        first, second = await asyncio.gather(
            http.post(root+"/orders",json=args), http.post(root+"/orders",json={**args,"idempotency_key":uuid4().hex})
        )
        assert sorted([first.status_code,second.status_code]) == [201,409], [first.text, second.text]
        order = (first if first.status_code == 201 else second).json()
        assert await stock(http,root) == (5,4)
        endpoint = root+"/orders/"+order["id"]+"/transitions"
        cancel = {"action":"cancel","expected_revision":1,"idempotency_key":uuid4().hex}
        moved = await http.post(endpoint,json=cancel)
        assert moved.status_code == 200, moved.text
        assert (await http.post(endpoint,json=cancel)).json() == moved.json()
        assert (await http.post(endpoint,json={**cancel,"idempotency_key":uuid4().hex})).status_code == 409
        assert await stock(http,root) == (5,0)
        stale = await http.post(root+"/orders",json={**args,"idempotency_key":uuid4().hex})
        assert stale.status_code == 409 and "novamente" in stale.text
        history = (await http.get(root+"/orders/"+order["id"])).json()["history"]
        assert [event["action"] for event in history] == ["confirmed","cancelled"]
    assert not await receipts(ctx, tenant)


async def test_delivery_fee_dispatch_payment_and_safe_return(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    await complete_delivery_setup(ctx, tenant)
    customer = uuid4()
    address = {"street":"Rua da homologação", "number":"15", "neighborhood":"Bairro de teste",
               "city":"Campo Grande", "state":"MS", "postal_code":"79000-000"}
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:c,:t,'Pessoa de teste')"),
                         {"c":customer,"t":tenant})
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        args = await order_payload(http,root,variant,1,mode="delivery",delivery_fee_cents=500,
                                   customer_id=str(customer),address=address)
        assert (await http.post(root+"/orders",json={**args,"address":None})).status_code == 422
        assert (await http.post(root+"/orders",json={**args,"delivery_fee_cents":600})).status_code == 409
        created = await http.post(root+"/orders",json=args)
        assert created.status_code == 201, created.text
        assert created.json()["total_cents"] == 1750
        path = root+"/orders/"+created.json()["id"]
        assert await stock(http,root) == (5,1)
        assert (await http.get(root+"/orders",params={"mode":"counter"})).json()["items"] == []
        listing = (await http.get(root+"/orders",params={"mode":"delivery"})).json()
        assert len(listing["items"]) == 1 and "address" not in listing["items"][0]
        assert (await http.get(path)).json()["address"]["street"] == address["street"]
        payment = {"action":"fulfill","payment_method":"cash","expected_revision":1,"idempotency_key":uuid4().hex}
        assert (await http.post(path+"/transitions",json=payment)).status_code == 409
        dispatch = {"action":"dispatch","expected_revision":1,"idempotency_key":uuid4().hex,"courier_name":"Entregador de teste"}
        dispatched = await http.post(path+"/transitions",json=dispatch)
        assert dispatched.status_code == 200 and dispatched.json()["status"] == "dispatched", dispatched.text
        assert await stock(http,root) == (5,1) and not await receipts(ctx, tenant)
        overview = await http.get(root+"/overview")
        assert overview.status_code == 200, overview.text
        assert overview.json()["metrics"] == {"orders":1,"fulfilled":0,"open_deliveries":1,"paid_cents":0}
        assert (await http.get(path)).json()["courier_name"] == "Entregador de teste"
        cancel = {"action":"cancel","expected_revision":2,"idempotency_key":uuid4().hex}
        assert (await http.post(path+"/transitions",json=cancel)).status_code == 422
        completed = await http.post(path+"/transitions",json={**payment,"expected_revision":2})
        assert completed.status_code == 200, completed.text
        assert await stock(http,root) == (4,0)
        receipt, = await receipts(ctx, tenant)
        assert receipt["amount_cents"] == 1750

        second_args = await order_payload(http,root,variant,2,mode="delivery",customer_id=str(customer),address=address)
        second_response = await http.post(root+"/orders",json=second_args)
        assert second_response.status_code == 201, second_response.text
        second = root+"/orders/"+second_response.json()["id"]
        assert (await http.post(second+"/transitions",json={**dispatch,"idempotency_key":uuid4().hex})).status_code == 200
        stopped = await http.post(second+"/transitions",json={**cancel,"returned_to_stock":True})
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled", stopped.text
        assert await stock(http,root) == (4,0)
        assert len(await receipts(ctx, tenant)) == 1
        overview = (await http.get(root+"/overview")).json()
        assert overview["metrics"] == {"orders":2,"fulfilled":1,"open_deliveries":0,"paid_cents":1750}
        assert len(overview["daily"]) == 7 and overview["daily"][-1]["income_cents"] == 1750
        assert len(overview["recent"]) == 2
