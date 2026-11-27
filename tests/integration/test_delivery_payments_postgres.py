"""Verify delivery credit collections and returns using the actual Finance ledger."""
import asyncio
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.catalog_api import Actor
from app.database import tenant_transaction
from app.management_models import update_row
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import order_payload, receipts, stock
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def sale(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    today = datetime.now(ZoneInfo("America/Cuiaba")).date()
    async with client(actor) as http:
        customer = await http.post(root + "/order-customers", json={
            "name": "Cliente de teste de recebimento", "phone": "67900001234", "idempotency_key": uuid4().hex,
        })
        assert customer.status_code == 201, customer.text
        product = await http.post(root + "/catalog", json=new_product())
        assert product.status_code == 201, product.text
        payload = await order_payload(
            http, root, product.json()["variant_id"], 1, mode="pickup", customer_id=customer.json()["id"]
        )
        created = await http.post(root + "/orders", json=payload)
        assert created.status_code == 201, created.text
        order = root + "/orders/" + created.json()["id"]
        deferred = await http.post(order + "/transitions", json={
            "action": "fulfill", "expected_revision": 1, "payment_method": "on_account", "payment_due_date": str(today),
            "idempotency_key": uuid4().hex,
        })
        assert deferred.status_code == 200, deferred.text
        yield ctx, tenant, http, root, order, payload


async def test_split_at_counter_posts_each_method_once_and_refunds_exact_total(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx,monkeypatch)
    await complete_delivery_setup(ctx,tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        draft = await order_payload(http,root,variant,1,mode="counter")
        order = (await http.post(root+"/orders",json=draft)).json()["id"]
        url = root+"/orders/"+order+"/transitions"
        tenders = [{"method":"pix","amount_cents":400},{"method":"cash","amount_cents":850}]
        payload = {"action":"fulfill","expected_revision":1,"tenders":tenders,"idempotency_key":uuid4().hex}
        assert (await http.post(url,json={**payload,"tenders":[tenders[0],tenders[0]]})).status_code == 422
        assert (await http.post(url,json={**payload,"tenders":[tenders[0],{"method":"cash","amount_cents":851}]})).status_code == 422
        assert await stock(http,root) == (5,1)
        assert not await receipts(ctx,tenant)
        a,b = await asyncio.gather(http.post(url,json=payload),http.post(url,json=payload))
        assert a.status_code == b.status_code == 200, (a.text,b.text)
        assert a.json() == b.json()
        assert sorted((r["payment_method"],r["amount_cents"]) for r in await receipts(ctx,tenant)) == [("cash",850),("pix",400)]
        detail = (await http.get(root+"/orders/"+order)).json()
        assert detail["collected_cents"] == 1250 and detail["outstanding_cents"] == 0
        assert detail["history"][-1]["details"]["tenders"] == tenders
        assert (await http.get(root+"/overview")).json()["metrics"]["paid_cents"] == 1250
        perf = await http.get(root+"/overview/performance",params={"period":"today"})
        assert perf.status_code == 200,perf.text
        day=perf.json()
        assert day["granularity"]=="hour" and [r["hour"] for r in day["series"]]==list(range(24))
        assert day["current"]["orders"]==day["current"]["fulfilled"]==1
        assert day["current"]["average_ticket_cents"]==day["current"]["income_cents"]==day["current"]["sales_cents"]==1250
        assert sum(r["income_cents"] for r in day["series"])==1250 and day["previous"]["income_cents"]==0
        assert (await http.get(root+"/overview/performance",params={"period":"custom","start":day["start"]})).status_code==422
        month=(await http.get(root+"/overview/performance",params={"period":"30d"})).json()
        assert len(month["series"])==30 and month["granularity"]=="day" and month["current"]["fulfilled"]==1
        returned = await http.post(url,json={"action":"return","expected_revision":2,"returned_to_stock":True,
                                             "refunded":True,"idempotency_key":uuid4().hex})
        assert returned.status_code == 200,returned.text
        assert await stock(http,root) == (5,0)
        expenses = [r for r in await receipts(ctx,tenant) if r["kind"]=="expense"]
        assert len(expenses)==1 and expenses[0]["amount_cents"]==1250
        custom=(await http.get(root+"/overview/performance",params={"period":"custom","start":day["start"],"end":day["end"]})).json()
        assert custom["current"]["refunded_cents"]==1250 and custom["current"]["cancelled"]==1
        assert custom["current"]["sales_cents"]==1250


def pix_collection(amount_cents=400):
    return {
        "amount_cents": amount_cents, "expected_revision": 2, "payment_method": "pix",
        "received": True, "idempotency_key": uuid4().hex,
    }


async def test_partial_pix_credits_cash_and_prevents_duplicate_receipts(sale):
    ctx, tenant, http, root, order, _ = sale
    endpoint = order + "/payment-receipts"
    payment = pix_collection()
    invalid = await http.post(endpoint, json={**payment, "received": False})
    assert invalid.status_code == 422
    assert (await http.post(endpoint, json=pix_collection(1251))).status_code == 422
    first, replay = await asyncio.gather(http.post(endpoint, json=payment), http.post(endpoint, json=payment))
    assert first.status_code == replay.status_code == 200, first.text
    assert first.json() == replay.json()
    receipt = first.json()
    assert receipt["outstanding_cents"] == 850 and receipt["receipt_status"] == "pending"
    assert (await http.post(endpoint, json=pix_collection())).status_code == 409
    async with client(Actor("member", account_id=ctx.other)) as outsider:
        assert (await outsider.post(endpoint, json=payment)).status_code == 403

    outstanding, paid = sorted(await receipts(ctx, tenant), key=lambda row: row["status"], reverse=True)
    assert outstanding["status"] == "pending" and outstanding["amount_cents"] == 850
    assert paid["status"] == "paid" and paid["amount_cents"] == 400 and paid["payment_method"] == "pix"
    assert str(paid["id"]) == receipt["financial_entry_id"]
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            with pytest.raises(HTTPException, match="vinculado a um pedido"):
                await update_row(db, tenant, "financial_entries", paid["id"], paid["version"], {"amount_cents": 1})
        async with tenant_transaction(db, ctx.b):
            assert (await db.execute(text("SELECT count(*) FROM delivery_credit_collections"))).scalar_one() == 0

    detail = (await http.get(order)).json()
    assert detail["collected_cents"] == 400 and detail["outstanding_cents"] == 850
    partial_event = detail["history"][-1]
    assert partial_event["action"] == "partially_received" and partial_event["details"]["amount_cents"] == 400
    assert (await http.get(root + "/credit")).json()["summary"]["open_cents"] == 850
    dashboard = (await http.get(root + "/overview")).json()
    assert dashboard["metrics"]["paid_cents"] == 400
    assert dashboard["metrics"]["sales_cents"] == dashboard["metrics"]["credit_cents"] == 1250
    assert dashboard["credit_customers"][0]["outstanding_cents"] == 850
    summary = (await http.get(root + "/reports/sales")).json()["summary"]
    assert summary["paid_cents"] == 400 and summary["pending_cents"] == 850

    # Old clients can still settle the entire remaining balance with the existing endpoint.
    final = await http.post(order + "/transitions", json={
        "action": "settle", "expected_revision": 3, "payment_method": "cash", "idempotency_key": uuid4().hex,
    })
    assert final.status_code == 200, final.text
    detail = (await http.get(order)).json()
    assert detail["collected_cents"] == 1250 and detail["outstanding_cents"] == 0
    assert (await http.get(root + "/overview")).json()["metrics"]["paid_cents"] == 1250
    assert len(await receipts(ctx, tenant)) == 2
    assert await stock(http, root) == (4, 0)

    returned = await http.post(order + "/transitions", json={
        "action": "return", "expected_revision": 4, "returned_to_stock": True, "refunded": True,
        "idempotency_key": uuid4().hex,
    })
    assert returned.status_code == 200, returned.text
    refund = next(row for row in await receipts(ctx, tenant) if row["kind"] == "expense")
    assert refund["amount_cents"] == 1250 and refund["payment_method"] == "other"
    assert refund["id"] == UUID(returned.json()["return_financial_entry_id"])
    assert await stock(http, root) == (5, 0)


async def test_partial_receipt_requires_return_of_received_amount_and_cancels_residual(sale):
    ctx, tenant, http, root, order, payload = sale
    part = await http.post(order + "/payment-receipts", json=pix_collection())
    assert part.status_code == 200, part.text
    returned = {
        "action": "return", "expected_revision": 3, "returned_to_stock": True, "idempotency_key": uuid4().hex,
    }
    omitted = await http.post(order + "/transitions", json=returned)
    assert omitted.status_code == 422
    assert await stock(http, root) == (4, 0)
    assert len(await receipts(ctx, tenant)) == 2
    returned["refunded"] = True
    result = await http.post(order + "/transitions", json=returned)
    assert result.status_code == 200, result.text
    assert result.json() == (await http.post(order + "/transitions", json=returned)).json()
    assert await stock(http, root) == (5, 0)
    money = {(row["kind"], row["status"]): row["amount_cents"] for row in await receipts(ctx, tenant)}
    assert money == {("income", "paid"): 400, ("income", "cancelled"): 850, ("expense", "paid"): 400}
    assert (await http.get(order)).json()["receipt_status"] == "refunded"
    assert (await http.get(root + "/credit")).json()["summary"]["open_cents"] == 0

    # A separately verified Pix can also be registered when the order is fulfilled.
    new_payload = await order_payload(http, root, payload["items"][0]["variant_id"], 1)
    sale_response = await http.post(root + "/orders", json=new_payload)
    assert sale_response.status_code == 201, sale_response.text
    sale_path = root + "/orders/" + sale_response.json()["id"]
    completed = await http.post(sale_path + "/transitions", json={
        "action": "fulfill", "expected_revision": 1, "payment_method": "pix", "idempotency_key": uuid4().hex,
    })
    assert completed.status_code == 200, completed.text
    pix = next(row for row in await receipts(ctx, tenant) if row["id"] == UUID(completed.json()["financial_entry_id"]))
    assert pix["status"] == "paid" and pix["amount_cents"] == 1250 and pix["payment_method"] == "pix"
