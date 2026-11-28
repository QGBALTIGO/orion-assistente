"""Resolve manual orders against the shared stock, CRM and Finance in Postgres."""
import asyncio
import csv
from datetime import datetime, timedelta
from io import StringIO
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
            SELECT id,kind,amount_cents,customer_id,payment_method,status,version
            FROM financial_entries WHERE tenant_id=:t
        """), {"t": tenant})).mappings().all()


async def test_private_named_addresses_concurrent_edits_erasure_and_order_snapshot(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    addr = {"street": "Rua do Teste", "number": "15", "neighborhood": "Centro", "city": "Campo Grande", "state": "MS"}
    values = {"label": "Casa", "address": addr, "expected_revision": 0}
    async with client(actor) as http:
        customer = (await http.post(root + "/order-customers", json={
            "name": "Pessoa sintética", "idempotency_key": uuid4().hex})).json()["id"]
        book = f"{root}/order-customers/{customer}/addresses"
        home, work = [f"{book}/{uuid4()}" for _ in range(2)]
        assert (await http.get(book)).json() == {"items": []}
        first, retry = await asyncio.gather(*[http.put(home, json=values) for _ in range(2)])
        assert first.status_code == retry.status_code == 200, first.text
        assert first.json() == retry.json() and first.json()["revision"] == 1
        assert (await http.put(work, json={**values, "label": "casa"})).status_code == 409
        assert (await http.put(work, json={**values, "label": "Trabalho"})).status_code == 200
        assert [a["label"] for a in (await http.get(book)).json()["items"]] == ["Casa", "Trabalho"]
        variant = (await http.post(root + "/catalog", json=new_product())).json()["variant_id"]
        order = await order_payload(http, root, variant, 1, mode="delivery", customer_id=customer, address=addr)
        created = await http.post(root + "/orders", json=order)
        assert created.status_code == 201, created.text
        changes = await asyncio.gather(*[http.put(home, json={
            **values, "expected_revision": 1, "address": {**addr, "number": n}}) for n in ("16", "17")])
        assert sorted(r.status_code for r in changes) == [200, 409]
        assert (await http.delete(home, params={"revision": 1})).status_code == 409
        assert (await http.delete(home, params={"revision": 2})).status_code == 200
        assert (await http.delete(home, params={"revision": 2})).status_code == 200
        assert (await http.put(home, json=values)).status_code == 409
        assert [a["label"] for a in (await http.get(book)).json()["items"]] == ["Trabalho"]
        detail = await http.get(root + "/orders/" + created.json()["id"])
        assert detail.json()["address"]["number"] == "15"
        assert (await http.get(f"{root}/order-customers/{uuid4()}/addresses")).status_code == 404
    async with client(Actor("member", account_id=ctx.other)) as foreign:
        assert (await foreign.get(book)).status_code == 403
        assert (await foreign.put(work, json=values)).status_code == 403
    async with ctx.runtime() as session:
        async with tenant_transaction(session, ctx.a):
            assert not (await session.execute(text("SELECT 1 FROM delivery_customer_addresses WHERE customer_id=:c"),
                                              {"c": UUID(customer)})).first()


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
        profile = await http.get(root + f"/order-customers/{customer}/profile")
        assert profile.status_code == 200, profile.text
        customer_profile = profile.json()
        assert customer_profile["summary"]["completed"] == 1
        assert customer_profile["summary"]["purchased_cents"] == 2500
        assert customer_profile["summary"]["received_cents"] == 2500
        assert customer_profile["summary"]["average_ticket_cents"] == 2500
        assert customer_profile["summary"]["average_interval_days"] is None
        assert customer_profile["items"][0]["id"] == order["id"]
        assert customer_profile["favorites"][0]["quantity"] == 2
        assert (await http.get(root + f"/order-customers/{outsider_customer}/profile")).status_code == 404
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


async def test_phone_sale_change_is_instruction_until_actual_receipt(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog", json=new_product())).json()["variant_id"]
        base = await order_payload(http, root, variant, 2, mode="counter", intake_channel="phone",
            tender_preference="cash", change_for_cents=5000, operator_note="Retirada pelo responsável")
        assert (await http.post(root+"/orders", json={**base, "change_for_cents":2499})).status_code == 422
        assert (await http.post(root+"/orders", json={**base, "tender_preference":"pix"})).status_code == 422
        created = await http.post(root+"/orders", json=base)
        assert created.status_code == 201, created.text
        assert (await http.post(root+"/orders", json=base)).json() == created.json()
        detail = (await http.get(root+"/orders/"+created.json()["id"])).json()
        assert detail["tender_preference"] == "cash" and detail["sales_channel"] == "phone"
        assert detail["change_for_cents"] - detail["total_cents"] == 2500
        assert detail["operator_note"] == base["operator_note"] and detail["financial_entry_id"] is None
        assert await receipts(ctx, tenant) == []
        sales = (await http.get(root+"/reports/sales", params={"channel":"phone"})).json()
        assert sales["summary"]["orders"] == 1 and sales["items"][0]["id"] == detail["id"]
        assert (await http.get(root+"/reports/sales", params={"channel":"storefront"})).json()["items"] == []
        archive = await http.get(root+"/reports/sales.csv", params={"channel":"phone"})
        assert list(csv.DictReader(StringIO(archive.content.decode("utf-8-sig")), delimiter=";"))[0]["Origem"] == "Telefone"
        day = await http.post(root+"/orders/"+detail["id"]+"/transitions", json={
            "action":"fulfill", "payment_method":"debit_card", "expected_revision":1, "idempotency_key":uuid4().hex})
        assert day.status_code == 200 and (await receipts(ctx, tenant))[0]["payment_method"] == "debit_card"


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
    address = {"street":"Rua da homologação", "number":"15", "neighborhood":"Bairro de teste",
               "city":"Campo Grande", "state":"MS", "postal_code":"79000-000"}
    async with client(actor) as http:
        new_customer = {"name":"Pessoa de teste","phone":"(67) 99999-9909","idempotency_key":uuid4().hex}
        first = await http.post(root+"/order-customers",json=new_customer)
        assert first.status_code == 201, first.text
        person = first.json()
        again = await http.post(root+"/order-customers",json=new_customer)
        assert again.json() == person
        original = await http.post(root+"/order-customers",json={**new_customer,"name":"Nome alternativo","idempotency_key":uuid4().hex})
        assert original.json() == person
        result = await http.get(root+"/order-customers",params={"search":"(67) 99999-9909"})
        assert result.json()["items"] == [person]
        customer = person["id"]
        address_path = root+"/order-customers/"+customer+"/previous-address"
        assert (await http.get(address_path)).json() == {"address":None}
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
        dashboard = overview.json()
        assert dashboard["metrics"] == {"orders":1,"fulfilled":0,"open_deliveries":1,"paid_cents":0,
                                        "sales_cents":0,"credit_cents":0}
        assert dashboard["operations"] == {"in_route":1,"awaiting_dispatch":0,"average_ticket_cents":None}
        assert [item["id"] for item in dashboard["deliveries"]] == [created.json()["id"]]
        scheduled_for = datetime.fromisoformat(dashboard["date"]).replace(tzinfo=ZoneInfo("America/Cuiaba"), hour=12)
        scheduled_for += timedelta(days=1)
        async with ctx.migration.begin() as db:
            await db.execute(text("""
                UPDATE delivery_order_details SET scheduled_at=:scheduled
                WHERE tenant_id=:t AND order_id=:o
            """), {"t":tenant,"o":UUID(created.json()["id"]),"scheduled":scheduled_for})
        scheduled_day = scheduled_for.date().isoformat()
        scheduled_dashboard = (await http.get(root+"/overview", params={"day":scheduled_day})).json()
        assert scheduled_dashboard["metrics"]["orders"] == 0
        assert scheduled_dashboard["metrics"]["open_deliveries"] == 1
        scheduled_queue = (await http.get(root+"/orders", params={
            "mode":"delivery","by_delivery_date":True,"start":scheduled_day,"end":scheduled_day,
        })).json()
        assert [item["id"] for item in scheduled_queue["items"]] == [created.json()["id"]]
        assert scheduled_dashboard["deliveries"] == scheduled_queue["items"]
        assert (await http.get(path)).json()["courier_name"] == "Entregador de teste"
        cancel = {"action":"cancel","expected_revision":2,"idempotency_key":uuid4().hex}
        assert (await http.post(path+"/transitions",json=cancel)).status_code == 422
        completed = await http.post(path+"/transitions",json={**payment,"expected_revision":2})
        assert completed.status_code == 200, completed.text
        assert await stock(http,root) == (4,0)
        assert (await http.get(address_path)).json()["address"]["street"] == address["street"]
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
        assert overview["metrics"] == {"orders":2,"fulfilled":1,"open_deliveries":0,"paid_cents":1750,
                                       "sales_cents":1750,"credit_cents":0}
        assert len(overview["daily"]) == 7
        assert overview["daily"][-1] == {"date":overview["date"],"income_cents":1750,"sales_cents":1750,"credit_cents":0}
        assert len(overview["recent"]) == 2
        assert overview["operations"] == {"in_route":0,"awaiting_dispatch":0,"average_ticket_cents":1750}
        performance = (await http.get(root+"/overview/performance",params={"period":"today"})).json()
        assert performance["order_status"] == {"confirmed":0,"dispatched":0,"fulfilled":1,"cancelled":1,"other":0}
        assert len(performance["hourly_orders"]) == 24
        assert sum(row["orders"] for row in performance["hourly_orders"]) == 2
        assert performance["current"]["orders"] == 2
        product, = performance["top_products"]
        assert product["quantity"] == 1
        assert product["revenue_cents"] == 1250
        assert performance["current"]["sales_cents"] == 1750


async def test_on_account_stays_pending_until_manual_receipt(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    await complete_delivery_setup(ctx, tenant)
    customer = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:c,:t,'+Pessoa a prazo')"),
                         {"c":customer,"t":tenant})
    today = datetime.now(ZoneInfo("America/Cuiaba")).date()
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        args = await order_payload(http,root,variant,1,mode="pickup",customer_id=str(customer))
        created = await http.post(root+"/orders",json=args)
        assert created.status_code == 201, created.text
        endpoint = root+"/orders/"+created.json()["id"]
        payment = {"action":"fulfill","expected_revision":1,"payment_method":"on_account",
                   "idempotency_key":uuid4().hex}
        assert (await http.post(endpoint+"/transitions",json=payment)).status_code == 422
        assert (await http.post(endpoint+"/transitions",json={**payment,"payment_due_date":str(today-timedelta(days=1))})).status_code == 422
        completed = await http.post(endpoint+"/transitions",json={**payment,"payment_due_date":str(today)})
        assert completed.status_code == 200, completed.text
        assert await stock(http,root) == (4,0)
        dashboard = (await http.get(root+"/overview")).json()
        assert dashboard["metrics"]["paid_cents"] == 0
        assert dashboard["metrics"]["sales_cents"] == dashboard["metrics"]["credit_cents"] == 1250
        assert dashboard["credit_customers"] == [{"name":"+Pessoa a prazo","orders":1,"outstanding_cents":1250,"overdue_cents":0}]
        credit = (await http.get(root+"/credit",params={"status":"pending","search":"prazo"})).json()
        assert len(credit["items"]) == 1 and credit["summary"] == {"open_cents":1250,"overdue_cents":0}
        filters = {"start":str(today),"end":str(today),"mode":"pickup","receipt":"pending"}
        reports = root+"/reports/sales"
        pending = (await http.get(reports,params=filters)).json()
        assert pending["summary"] == {"orders":1,"cancelled":0,"paid_cents":0,"pending_cents":1250}
        assert [row["id"] for row in pending["items"]] == [created.json()["id"]]
        archive = await http.get(reports+".csv",params=filters)
        assert archive.status_code == 200 and archive.headers["cache-control"] == "no-store"
        assert archive.headers["content-disposition"] == 'attachment; filename="orion-delivery-vendas.csv"'
        exported = list(csv.reader(StringIO(archive.content.decode("utf-8-sig")),delimiter=";"))
        assert exported[1][0] == created.json()["id"]
        assert exported[1][2:] == ["'+Pessoa a prazo","Retirada","Concluído","A receber","12,50","0,00","Painel"]
        async with ctx.migration.begin() as db:
            await db.execute(text("""
                UPDATE delivery_order_details SET payment_due_date=:due WHERE tenant_id=:t AND order_id=:o
            """), {"t":tenant,"o":UUID(created.json()["id"]),"due":today-timedelta(days=1)})
            await db.execute(text("""
                UPDATE financial_entries SET occurred_at=occurred_at-interval '24 hours' WHERE tenant_id=:t
            """), {"t":tenant})
        due = (await http.get(root+"/credit",params={"status":"overdue"})).json()
        assert due["items"][0]["receipt_status"] == "pending" and due["summary"]["overdue_cents"] == 1250
        received = {"action":"settle","payment_method":"cash","expected_revision":2,"idempotency_key":uuid4().hex}
        first, repeated = await asyncio.gather(*[http.post(endpoint+"/transitions",json=received) for _ in range(2)])
        assert first.status_code == repeated.status_code == 200, first.text
        assert first.json() == repeated.json()
        assert first.json()["financial_entry_id"] == completed.json()["financial_entry_id"]
        assert (await http.post(endpoint+"/transitions",json={**received,"idempotency_key":uuid4().hex})).status_code == 409
        dashboard = (await http.get(root+"/overview")).json()
        assert dashboard["metrics"]["paid_cents"] == 1250
        # Changing the accounting entry date must not move the sale out of its completion day.
        assert dashboard["metrics"]["sales_cents"] == dashboard["metrics"]["credit_cents"] == 1250
        assert dashboard["daily"][-1]["sales_cents"] == 1250
        assert dashboard["daily"][-1]["income_cents"] == 1250
        assert dashboard["daily"][-2]["sales_cents"] == 0
        assert dashboard["credit_customers"] == []
        assert (await http.get(root+"/credit",params={"status":"pending"})).json()["items"] == []
        assert (await http.get(root+"/credit",params={"status":"paid"})).json()["items"][0]["receipt_status"] == "paid"
        assert (await http.get(reports,params=filters)).json()["items"] == []
        settled_report = (await http.get(reports,params={**filters,"receipt":"paid"})).json()
        assert settled_report["summary"]["paid_cents"] == 1250 and settled_report["summary"]["pending_cents"] == 0
        assert (await http.get(reports,params={**filters,"mode":"delivery"})).json()["summary"]["orders"] == 0
        history = (await http.get(endpoint)).json()["history"]
        assert [event["action"] for event in history] == ["confirmed","fulfilled","received"]
    ledger, = await receipts(ctx,tenant)
    assert ledger["status"] == "paid" and ledger["version"] == 2 and ledger["payment_method"] == "cash"


@pytest.mark.parametrize("payment_method", ["cash","on_account"])
async def test_full_return_restores_stock_and_reverses_financial_effect(onboarding_db, monkeypatch, payment_method):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    await complete_delivery_setup(ctx, tenant)
    customer = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:c,:t,'Cliente sintético')"),
                         {"c":customer,"t":tenant})
    async with client(actor) as http:
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        args = await order_payload(http,root,variant,2,mode="pickup",customer_id=str(customer))
        order = await http.post(root+"/orders",json=args)
        assert order.status_code == 201, order.text
        endpoint = root+"/orders/"+order.json()["id"]
        payload = {"action":"fulfill","expected_revision":1,"payment_method":payment_method,"idempotency_key":uuid4().hex}
        paid = payment_method == "cash"
        if not paid:
            payload["payment_due_date"] = str(datetime.now(ZoneInfo("America/Cuiaba")).date())
        completed = await http.post(endpoint+"/transitions",json=payload)
        assert completed.status_code == 200, completed.text
        assert await stock(http,root) == (3,0)
        restoring = {"action":"return","expected_revision":2,"refunded":paid,"idempotency_key":uuid4().hex}
        assert (await http.post(endpoint+"/transitions",json=restoring)).status_code == 422
        assert (await http.post(endpoint+"/transitions",json={**restoring,"returned_to_stock":True,"refunded":not paid})).status_code == 422
        assert await stock(http,root) == (3,0)
        command = {**restoring,"returned_to_stock":True}
        reversed_order, replay = await asyncio.gather(*[http.post(endpoint+"/transitions",json=command) for _ in range(2)])
        assert reversed_order.status_code == replay.status_code == 200, reversed_order.text
        assert reversed_order.json() == replay.json() and reversed_order.json()["status"] == "cancelled"
        assert await stock(http,root) == (5,0)
        detail = (await http.get(endpoint)).json()
        assert detail["receipt_status"] == ("refunded" if paid else "cancelled")
        assert detail["history"][-1]["action"] == "returned"
        assert (await http.get(root+"/credit",params={"status":"pending"})).json()["summary"]["open_cents"] == 0
        history = (await http.get(root+"/catalog/history")).json()["items"]
        assert history[0]["details"]["movement"] == "sale_returned"
        assert (await http.get(root+"/reports/sales")).json()["summary"] == {
            "orders":1,"cancelled":1,"paid_cents":0,"pending_cents":0,
        }
    finances = {entry["kind"]:entry for entry in await receipts(ctx,tenant)}
    if paid:
        assert finances["income"]["status"] == finances["expense"]["status"] == "paid"
        assert finances["income"]["amount_cents"] == finances["expense"]["amount_cents"] == 2500
        assert finances["expense"]["id"] == UUID(reversed_order.json()["return_financial_entry_id"])
    else:
        assert set(finances) == {"income"} and finances["income"]["status"] == "cancelled"
