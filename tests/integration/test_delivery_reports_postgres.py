"""Verify operational reports against real tenant data and filter-matched CSV."""
import csv
from decimal import Decimal
from datetime import datetime, timedelta
from io import StringIO
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.catalog_api import Actor
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import order_payload
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


def test_amount_label_accepts_decimal_and_negative_net():
    from app.delivery_reports import amount_label
    assert amount_label(Decimal("2500")) == "25,00"
    assert amount_label(Decimal("-155")) == "-1,55"
    assert amount_label(0) == "0,00"


def rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith("attachment;")
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig")), delimiter=";"))


def metrics(data):
    return {metric["label"]: metric["value"] for metric in data["summary"]}


async def customer(ctx, tenant, name):
    identity = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:id,:t,:name)"),
                         {"id": identity, "t": tenant, "name": name})
    return str(identity)


async def test_report_products_customers_and_stock_use_fulfilled_orders(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    contact = await customer(ctx, tenant, "=Cliente sintético")
    root = f"/api/v1/tenants/{tenant}/delivery"
    reports = root + "/reports/operational"
    async with client(actor) as http:
        product = await http.post(root + "/catalog", json={**new_product("Água do relatório"),
            "profile":{"preset_image":"agua-20l"}})
        assert product.status_code == 201, product.text
        variant = product.json()["variant_id"]
        command = await order_payload(http, root, variant, 2, mode="pickup", customer_id=contact)
        reserved = await http.post(root + "/orders", json=command)
        assert reserved.status_code == 201, reserved.text
        period = {"mode": "pickup", "search": "agua-teste"}
        response = await http.get(reports + "/products", params=period)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert rows(await http.get(reports + "/products/csv", params=period)) == []

        endpoint = root + "/orders/" + reserved.json()["id"] + "/transitions"
        paid = await http.post(endpoint, json={"action":"fulfill","expected_revision":1,"payment_method":"cash",
                                               "idempotency_key":uuid4().hex})
        assert paid.status_code == 200, paid.text
        payment = await http.get(reports + "/payments", params={"status":"cash","search":"cliente sintético"})
        assert payment.status_code == 200, payment.text
        assert metrics(payment.json()) == {"Recebido confirmado":2500,"Estornado":0,"Recebimentos líquidos":2500}
        assert payment.json()["items"][0]["payment_method"] == "Dinheiro"
        exported_payment, = rows(await http.get(reports + "/payments/csv", params={"status":"cash"}))
        assert exported_payment["Recebido"] == "25,00"
        assert rows(await http.get(reports + "/payments/csv", params={"status":"pix"})) == []

        response = await http.get(reports + "/products", params=period)
        assert response.status_code == 200, response.text
        output = response.json()
        assert len(output["items"]) == 1
        assert output["items"][0]["quantity"] == 2 and output["items"][0]["revenue_cents"] == 2500
        assert output["items"][0]["image_url"].endswith("agua-20l.webp")
        assert metrics(output) == {"Variações vendidas":1,"Unidades vendidas":2,"Subtotal dos itens":2500}
        exported, = rows(await http.get(reports + "/products/csv", params=period))
        assert exported["Produto"] == output["items"][0]["product"]
        assert exported["Unidades vendidas"] == "2" and exported["Subtotal dos itens"] == "25,00"
        assert rows(await http.get(reports + "/products/csv", params={**period,"mode":"counter"})) == []
        wrong_window = {"start":"2000-01-01","end":"2000-01-02"}
        assert (await http.get(reports + "/products", params=wrong_window)).json()["items"] == []
        assert (await http.get(reports + "/products", params={**wrong_window,"end":"1999-01-01"})).status_code == 422

        clients = (await http.get(reports + "/customers", params={"search":"cliente sintético"})).json()
        assert clients["items"][0]["customer"] == "=Cliente sintético"
        assert metrics(clients)["Pedidos concluídos"] == 1
        exported, = rows(await http.get(reports + "/customers/csv", params={"search":"cliente sintético"}))
        assert exported["Cliente"] == "'=Cliente sintético"
        assert "CPF" not in exported and "Telefone" not in exported

        inventory_filter = {"status":"low","search":"agua-teste"}
        inventory = (await http.get(reports + "/inventory", params=inventory_filter)).json()
        assert inventory["period"] is None
        assert inventory["items"][0]["available"] == 3
        assert metrics(inventory)["Estoque baixo"] == 1
        exported, = rows(await http.get(reports + "/inventory/csv", params=inventory_filter))
        assert exported["Disponível"] == "3" and exported["Situação da contagem"] == "Estoque baixo"
        moves_filter = {"status":"sold","search":"agua-teste"}
        moves = (await http.get(reports + "/movements", params=moves_filter)).json()
        assert [(r["movement"],r["quantity"],r["balance"]) for r in moves["items"]] == [("Venda",2,3)]
        exported, = rows(await http.get(reports + "/movements/csv", params=moves_filter))
        assert (exported["Movimento"],exported["Saldo contado"]) == ("Venda","3")
        activity_filter={"status":"orders","search":reserved.json()["id"][:8]}
        trace=(await http.get(reports+"/activity",params=activity_filter)).json()
        assert {x["action"] for x in trace["items"]}=={"Pedido reservado","Pedido concluído"}
        assert metrics(trace)["Atividades"]==2
        exported=rows(await http.get(reports+"/activity/csv",params=activity_filter))
        assert {x["Área"] for x in exported}=={"Pedidos"}
        assert {x["Ação"] for x in exported}=={"Pedido reservado","Pedido concluído"}


async def test_report_credit_vessels_and_read_permission(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    contact = await customer(ctx, tenant, "Cliente com saldo")
    root = f"/api/v1/tenants/{tenant}/delivery"
    reports = root + "/reports/operational"
    today = datetime.now(ZoneInfo("America/Cuiaba")).date()
    due = (today + timedelta(days=2)).isoformat()
    async with client(actor) as http:
        product = await http.post(root + "/catalog", json=new_product())
        variant = product.json()["variant_id"]
        command = await order_payload(http,root,variant,1,mode="pickup",customer_id=contact)
        order = await http.post(root + "/orders", json=command)
        assert order.status_code == 201, order.text
        charge = await http.post(root+"/orders/"+order.json()["id"]+"/transitions", json={
            "action":"fulfill","expected_revision":1,"payment_method":"on_account",
            "payment_due_date":due,"idempotency_key":uuid4().hex,
        })
        assert charge.status_code == 200, charge.text
        credit_filter = {"status":"pending","start":due,"end":due}
        credit = (await http.get(reports + "/credit", params=credit_filter)).json()
        assert (credit["items"][0]["pending_cents"], credit["items"][0]["received_cents"]) == (1250,0)
        assert metrics(credit)["Em atraso"] == 0
        exported, = rows(await http.get(reports + "/credit/csv", params=credit_filter))
        assert exported["Saldo aberto"] == "12,50"
        assert rows(await http.get(reports + "/credit/csv", params={**credit_filter,"status":"overdue"})) == []
        assert (await http.get(reports + "/credit", params={"status":"cylinder"})).status_code == 422
        await http.post(root+"/orders/"+order.json()["id"]+"/transitions", json={
            "action":"settle","expected_revision":2,"payment_method":"cash","idempotency_key":uuid4().hex,
        })
        paid = (await http.get(reports + "/credit", params={**credit_filter,"status":"paid"})).json()
        assert metrics(paid)["Saldo aberto"] == 0 and metrics(paid)["Recebido líquido"] == 1250

        flask = await http.post(root+"/containers", json={
            "name":"Garrafão do relatório","kind":"jug","quantity":4,"idempotency_key":uuid4().hex,
        })
        assert flask.status_code == 201, flask.text
        loan = await http.post(root+"/containers/"+flask.json()["id"]+"/movements", json={
            "kind":"lend","customer_id":contact,"quantity":3,"expected_revision":1,"idempotency_key":uuid4().hex,
        })
        assert loan.status_code == 200, loan.text
        vessels = (await http.get(reports + "/containers", params={"status":"jug"})).json()
        assert metrics(vessels) == {"Tipos cadastrados":1,"Vazios em estoque":1,"Com clientes":3}
        exported, = rows(await http.get(reports + "/containers/csv", params={"status":"jug"}))
        assert exported["Com clientes"] == "3"
        assert rows(await http.get(reports + "/containers/csv", params={"status":"cylinder"})) == []

    async with client(Actor("member",account_id=ctx.other)) as foreign:
        assert (await foreign.get(reports + "/credit")).status_code == 403
        assert (await foreign.get(reports + "/containers/csv")).status_code == 403


async def test_reorder_uses_three_distinct_days_and_mixed_receipts_are_not_double_counted(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    contact = await customer(ctx, tenant, "Cliente recorrente")
    root = f"/api/v1/tenants/{tenant}/delivery"
    reports = root + "/reports/operational"
    async with client(actor) as http:
        product = await http.post(root+"/catalog",json=new_product())
        assert product.status_code == 201, product.text
        variant = product.json()["variant_id"]
        for index, days in enumerate((14,7,0)):
            args=await order_payload(http,root,variant,1,mode="pickup",customer_id=contact)
            created=await http.post(root+"/orders",json=args)
            assert created.status_code==201,created.text
            identity=created.json()["id"]
            paid=await http.post(root+"/orders/"+identity+"/transitions",json={
                "action":"fulfill","expected_revision":1,"idempotency_key":uuid4().hex,
                "tenders":[{"method":"cash","amount_cents":500},{"method":"debit_card","amount_cents":750}]})
            assert paid.status_code==200,paid.text
            async with ctx.migration.begin() as db:
                await db.execute(text("UPDATE commercial_orders SET created_at=now()-(:days * interval '1 day') WHERE tenant_id=:t AND id=:o"),
                    {"days":days,"t":tenant,"o":identity})
            data=await http.get(reports+"/reorder")
            assert data.status_code==200,data.text
            if index<2:
                assert data.json()["items"]==[]
        row,=data.json()["items"]
        assert row["purchase_days"]==3 and row["interval_days"]==7 and row["units"]==3
        assert row["reorder_status"]=="Mais adiante"
        assert rows(await http.get(reports+"/reorder/csv",params={"status":"due"}))==[]
        exported,=rows(await http.get(reports+"/reorder/csv"))
        assert exported["Cliente"]=="Cliente recorrente"
        payments=await http.get(reports+"/payments")
        assert payments.status_code==200,payments.text
        assert metrics(payments.json())["Recebido confirmado"]==3750
        amounts={r["payment_method"]:r["received_cents"] for r in payments.json()["items"]}
        assert amounts=={"Dinheiro":1500,"Cartão de débito":2250}
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE customers SET active=false WHERE tenant_id=:t AND id=:c"),{"t":tenant,"c":contact})
        assert (await http.get(reports+"/reorder")).json()["items"]==[]
