"""Verify full stock, empty exchanges, customer loans and reversals in one transaction."""
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.database import tenant_transaction
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import order_payload, receipts, stock
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


async def add_container(http, root, quantity):
    response = await http.post(root + "/containers", json={
        "kind": "jug", "name": "Garrafão de avaliação", "quantity": quantity, "idempotency_key": uuid4().hex,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def packaging_balance(http, root):
    response = await http.get(root + "/containers")
    assert response.status_code == 200, response.text
    row, = response.json()["items"]
    return row["empty_quantity"], row["loaned"], row["revision"]


async def transition(http, root, order_id, action, revision, **fields):
    return await http.post(root + "/orders/" + order_id + "/transitions", json={
        "action": action, "expected_revision": revision, "idempotency_key": uuid4().hex, **fields,
    })


async def move_empty(http, root, container, kind, quantity, **fields):
    _, _, revision = await packaging_balance(http, root)
    result = await http.post(root + "/containers/" + container + "/movements", json={
        "kind": kind, "quantity": quantity, "expected_revision": revision, "idempotency_key": uuid4().hex, **fields,
    })
    assert result.status_code == 200, result.text


async def delivery_context(ctx, monkeypatch):
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    customer = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO customers(tenant_id,id,name) VALUES (:tenant,:id,'Cliente de avaliação')"),
                         {"tenant": tenant, "id": customer})
    return tenant, actor, str(customer)


async def test_exchange_and_loan_move_only_after_confirmation_and_reverse_atomically(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor, customer = await delivery_context(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    outsider_container = uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("""
            INSERT INTO delivery_containers(tenant_id,id,kind,name,empty_quantity)
            VALUES (:tenant,:id,'jug','Outro estabelecimento',10)
        """), {"tenant": ctx.b, "id": outsider_container})

    async with client(actor) as http:
        product = await http.post(root + "/catalog", json=new_product())
        assert product.status_code == 201, product.text
        variant = product.json()["variant_id"]
        container = await add_container(http, root, 0)
        plan = {"variant_id": variant, "container_id": container, "exchange_quantity": 1, "loan_quantity": 1}

        payload = await order_payload(http, root, variant, 2, customer_id=customer, container_plan=[plan])
        invalid = await http.post(root + "/orders", json={**payload, "container_plan": [
            {**plan, "container_id": str(outsider_container)},
        ]})
        assert invalid.status_code == 404, invalid.text
        assert (await http.post(root + "/orders", json={**payload, "customer_id": None})).status_code == 422
        assert (await http.post(root + "/orders", json={**payload, "container_plan": [
            {**plan, "loan_quantity": 2},
        ]})).status_code == 422
        assert await stock(http, root) == (5, 0)

        created = await http.post(root + "/orders", json=payload)
        assert created.status_code == 201, created.text
        order_id = created.json()["id"]
        assert await stock(http, root) == (5, 2)
        assert (await packaging_balance(http, root))[:2] == (0, 0)
        detail = (await http.get(root + "/orders/" + order_id)).json()
        assert detail["container_plan"] == [{**plan, "container_name": "Garrafão de avaliação"}]

        unconfirmed = await transition(http, root, order_id, "fulfill", 1, payment_method="cash")
        assert unconfirmed.status_code == 422, unconfirmed.text
        assert await stock(http, root) == (5, 2)
        assert not await receipts(ctx, tenant)

        endpoint = root + "/orders/" + order_id + "/transitions"
        fulfillment = {"action": "fulfill", "expected_revision": 1, "payment_method": "cash",
                       "containers_confirmed": True, "idempotency_key": uuid4().hex}
        first, replay = await asyncio.gather(*(http.post(endpoint, json=fulfillment) for _ in range(2)))
        assert first.status_code == replay.status_code == 200, [first.text, replay.text]
        assert first.json() == replay.json()
        assert await stock(http, root) == (3, 0)
        assert (await packaging_balance(http, root))[:2] == (1, 1)
        assert len(await receipts(ctx, tenant)) == 1

        unconfirmed_return = await transition(http, root, order_id, "return", 2, returned_to_stock=True, refunded=True)
        assert unconfirmed_return.status_code == 422, unconfirmed_return.text
        await move_empty(http, root, container, "out", 1)
        insufficient = await transition(http, root, order_id, "return", 2, returned_to_stock=True, refunded=True,
                                        containers_confirmed=True)
        assert insufficient.status_code == 409, insufficient.text
        assert await stock(http, root) == (3, 0)
        assert (await packaging_balance(http, root))[:2] == (0, 1)
        assert len(await receipts(ctx, tenant)) == 1
        await move_empty(http, root, container, "in", 1)

        reversal = {"action": "return", "expected_revision": 2, "returned_to_stock": True, "refunded": True,
                    "containers_confirmed": True, "idempotency_key": uuid4().hex}
        returned, replay = await asyncio.gather(*(http.post(endpoint, json=reversal) for _ in range(2)))
        assert returned.status_code == replay.status_code == 200, [returned.text, replay.text]
        assert returned.json() == replay.json()
        assert await stock(http, root) == (5, 0)
        assert (await packaging_balance(http, root))[:2] == (0, 0)
        assert len(await receipts(ctx, tenant)) == 2
        reserved = await order_payload(http, root, variant, 2, customer_id=customer, container_plan=[plan])
        second = await http.post(root + "/orders", json=reserved)
        assert second.status_code == 201, second.text
        cancelled = await transition(http, root, second.json()["id"], "cancel", 1)
        assert cancelled.status_code == 200, cancelled.text
        assert await stock(http, root) == (5, 0)
        assert (await packaging_balance(http, root))[:2] == (0, 0)

    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            rows = (await db.execute(text("""
                SELECT order_phase,kind,quantity FROM delivery_container_movements
                WHERE order_id=:order_id ORDER BY ledger_sequence
            """), {"order_id": UUID(order_id)})).all()
            assert [tuple(row) for row in rows] == [
                ("fulfill", "in", 1), ("fulfill", "lend", 1), ("return", "out", 1), ("return", "return", 1),
            ]
    async with ctx.runtime() as db:
        async with tenant_transaction(db, ctx.b):
            quantity = (await db.execute(text("SELECT count(*) FROM delivery_order_container_plans"))).scalar_one()
            assert quantity == 0


async def test_order_return_preserves_other_loans_after_manual_repayments(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor, customer = await delivery_context(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        product = await http.post(root + "/catalog", json=new_product())
        assert product.status_code == 201, product.text
        variant = product.json()["variant_id"]
        container = await add_container(http, root, 4)
        await move_empty(http, root, container, "lend", 2, customer_id=customer)
        orders = []
        for quantity in (2, 1):
            plan = {"variant_id": variant, "container_id": container, "loan_quantity": quantity}
            payload = await order_payload(http, root, variant, quantity, customer_id=customer, container_plan=[plan])
            response = await http.post(root + "/orders", json=payload)
            assert response.status_code == 201, response.text
            order_id = response.json()["id"]
            orders.append(order_id)
            fulfilled = await transition(http, root, order_id, "fulfill", 1, payment_method="cash",
                                         containers_confirmed=True)
            assert fulfilled.status_code == 200, fulfilled.text

        assert (await packaging_balance(http, root))[:2] == (2, 5)
        await move_empty(http, root, container, "return", 3, customer_id=customer)
        assert (await packaging_balance(http, root))[:2] == (5, 2)
        for order_id, balance in zip(orders, (1, 0), strict=True):
            returned = await transition(http, root, order_id, "return", 2, returned_to_stock=True, refunded=True,
                                        containers_confirmed=True)
            assert returned.status_code == 200, returned.text
            assert (await packaging_balance(http, root))[:2] == (5, balance)
        assert await stock(http, root) == (5, 0)
