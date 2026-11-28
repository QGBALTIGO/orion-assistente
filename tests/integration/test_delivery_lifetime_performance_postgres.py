"""Lifetime Delivery totals include old orders and all actual financial events."""
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.catalog_api import Actor
from test_delivery_catalog_postgres import business, client, complete_delivery_setup, new_product
from test_delivery_orders_postgres import order_payload
from test_onboarding_postgres import onboarding_db  # noqa: F401

pytestmark = pytest.mark.integration


async def test_lifetime_delivery_performance(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    endpoint = root+"/overview/performance"
    async with client(actor) as http:
        empty = await http.get(endpoint, params={"period":"all"})
        assert empty.status_code == 200, empty.text
        data = empty.json()
        assert data["granularity"] == "month" and data["previous"] is None
        assert data["previous_start"] is None and data["previous_end"] is None
        assert data["current"]["orders"] == 0 and len(data["series"]) == 1
        assert (await http.get(endpoint, params={"period":"all","start":"2024-01-01"})).status_code == 422
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        created = []
        for qty in (1,2):
            res = await http.post(root+"/orders",json=await order_payload(http,root,variant,qty,mode="pickup"))
            assert res.status_code == 201, res.text
            created.append(res.json())
        paid = await http.post(root+"/orders/"+created[1]["id"]+"/transitions",
                               json={"action":"fulfill","expected_revision":1,"payment_method":"cash","idempotency_key":uuid4().hex})
        assert paid.status_code == 200, paid.text
        old = datetime.now(ZoneInfo("America/Cuiaba"))-timedelta(days=440)
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE commercial_orders SET created_at=:old WHERE tenant_id=:t AND id=:o"),
                             {"old":old,"t":tenant,"o":created[0]["id"]})
        response = await http.get(endpoint,params={"period":"all"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["start"] == old.date().isoformat()
        assert data["current"]["orders"] == 2
        assert data["current"]["fulfilled"] == 1
        assert data["current"]["sales_cents"] == data["current"]["income_cents"] == 2500
        assert data["order_status"]["confirmed"] == data["order_status"]["fulfilled"] == 1
        assert sum(r["orders"] for r in data["hourly_orders"]) == 2
        assert data["series"][0]["date"] == old.date().replace(day=1).isoformat()
        assert data["series"][0]["orders"] == 1
        assert sum(r["orders"] for r in data["series"]) == 2
        assert sum(r["sales_cents"] for r in data["series"]) == 2500
        assert data["top_products"][0]["quantity"] == 2
        for period in ("today","7d","30d"):
            recent = await http.get(endpoint,params={"period":period})
            assert recent.status_code == 200,recent.text
            assert recent.json()["current"]["orders"] == 1
            assert recent.json()["previous"] is not None
    async with client(Actor("member",account_id=ctx.other)) as foreign:
        assert (await foreign.get(endpoint,params={"period":"all"})).status_code == 403
