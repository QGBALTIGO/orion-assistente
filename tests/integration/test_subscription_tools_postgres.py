"""Tools with real owner cookies, CSRF, permissions, groups and PostgreSQL.

Only the external WhatsApp lookup is replaced. No real message or contact is
sent, created, edited or removed by these two tools.
"""
from copy import deepcopy
from uuid import UUID, uuid4
from urllib.parse import urlsplit, parse_qs

import pytest
import pytest_asyncio
from sqlalchemy import text

from app import qr_channels, subscription_number_check as checker, subscription_whatsapp_link as links
from app.plan_routing import include_plan_router
from test_subscription_campaigns_postgres import (
    campaign_case, chatbot_case, device_case, disposable_environment, new_owner_http,
    onboarding_db, group, snapshot,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def tools_case(campaign_case, monkeypatch):
    c = campaign_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    for router in (checker.router, links.router):
        include_plan_router(c.app, router)
    c.app.router.routes.extend(generic)
    c.checker = checker.BASE.format(tenant_id=c.tenant)
    c.links = links.BASE.format(tenant_id=c.tenant)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='connected' WHERE tenant_id=:t AND id=:d"),
                         {"t": c.tenant, "d": UUID(c.device["id"])})
    c.checks = []
    original = qr_channels._bridge_control
    async def lookup(channel, action, method, payload=None):
        if action != "device/number-check/check":
            return await original(channel, action, method, payload)
        assert channel["tenant_id"] == c.tenant and channel["id"] == c.row["id"]
        assert method == "POST" and payload["device_id"] == c.device["id"]
        c.checks.append(deepcopy(payload))
        return {**{key: payload[key] for key in ("device_id", "generation", "settings_revision")},
            "total": len(payload["numbers"]), "results": [{"number": number, "status": "registered", "reason": None} for number in payload["numbers"]]}
    monkeypatch.setattr(qr_channels, "_bridge_control", lookup)
    yield c


async def test_options_and_group_check_share_catalog_without_mutating_it(tools_case):
    c = tools_case
    target = await group(c, members=[{"phone": "+5567999999901", "name": "Pessoa A"}, {"phone": "+595981234567", "name": "Pessoa B"}])
    before = await snapshot(c)
    options = await c.client.get(c.checker + "/options")
    assert options.status_code == 200, options.text
    assert options.json()["groups"][0]["id"] == target["id"]
    response = await c.client.post(c.checker + "/check", json={"device_id": c.device["id"], "group_id": target["id"]})
    assert response.status_code == 200, response.text
    assert {row["number"] for row in response.json()["items"]} == {"+5567999999901", "+595981234567"}
    assert await snapshot(c) == before


async def test_thousand_numbers_are_checked_and_a_larger_group_is_never_truncated(tools_case):
    c = tools_case
    numbers = ["+5567" + str(900000000+i) for i in range(1000)]
    response = await c.client.post(c.checker + "/check", json={"device_id": c.device["id"], "numbers": numbers})
    assert response.status_code == 200 and response.json()["total"] == 1000, response.text
    assert c.checks[-1]["numbers"] == numbers
    target = await group(c, members=[{"phone": "+5567" + str(900000000+i), "name": f"Pessoa {i}"} for i in range(1001)])
    count = len(c.checks)
    response = await c.client.post(c.checker + "/check", json={"device_id": c.device["id"], "group_id": target["id"]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "NUMBER_CHECK_LIMIT", response.text
    assert len(c.checks) == count


async def test_cross_tenant_unknown_group_and_offline_device_do_not_query_provider(tools_case):
    c = tools_case
    foreign = checker.BASE.format(tenant_id=c.ctx.b)
    response = await c.client.post(foreign + "/check", json={"device_id": c.device["id"], "numbers": ["+5567999999901"]})
    assert response.status_code in {403, 404}, response.text
    response = await c.client.post(c.checker + "/check", json={"device_id": c.device["id"], "group_id": str(uuid4())})
    assert response.status_code == 404, response.text
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE tenant_id=:t AND id=:d"),
                         {"t": c.tenant, "d": UUID(c.device["id"])})
    response = await c.client.post(c.checker + "/check", json={"device_id": c.device["id"], "numbers": ["+5567999999901"]})
    assert response.status_code == 409 and c.checks == [], response.text


async def test_link_generation_is_independent_of_whatsapp_and_does_not_touch_data(tools_case):
    c = tools_case
    before = await snapshot(c)
    message = "Olá, José 💙 & planos?\nObrigado!"
    response = await c.client.post(c.links + "/generate", json={"phone": "+595 981234567", "message": message})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["phone"] == "+595981234567" and value["qr"]["svg"].startswith("<svg")
    assert parse_qs(urlsplit(value["url"]).query)["text"] == [message]
    assert c.checks == [] and await snapshot(c) == before
    response = await c.client.post(links.BASE.format(tenant_id=c.ctx.b) + "/generate", json={"phone": "+5567999999901"})
    assert response.status_code in {403, 404}, response.text
