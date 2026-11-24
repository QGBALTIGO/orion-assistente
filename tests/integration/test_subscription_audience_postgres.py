"""Real PostgreSQL/Redis/API audience import, scope and shared send catalog tests."""
import asyncio
import csv
from io import StringIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import subscription_audience as api
from app import subscription_audience_store as store
from app import subscription_schedule_store as catalog
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_campaigns_postgres import (
    campaign_case, chatbot_case, device_case, disposable_environment, new_owner_http, onboarding_db,
)  # noqa: F401 -- actual cookies, CSRF, roles, plan and tenant boundaries.

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def audience_case(campaign_case):
    c = campaign_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.audience = api.BASE.format(tenant_id=c.tenant)
    try:
        yield c
    finally:
        async with c.ctx.migration.begin() as db:
            for table in ("subscription_audience_operations", "subscription_audience_group_links", "subscription_audience_contacts"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": c.tenant})


async def contact(c, **changes):
    response = await c.client.post(c.audience + "/contacts", json={"idempotency_key": str(uuid4()),
        "contact": {"name": "Pessoa sintética", "phone_e164": "+5567999991234", **changes}})
    assert response.status_code == 201, response.text
    return response.json()["result"]["contact"]


async def group(c, keys=None):
    response = await c.client.post(c.audience + "/groups", json={"idempotency_key": str(uuid4()),
        "name": "Público sintético", "contact_keys": keys or []})
    assert response.status_code == 201, response.text
    return response.json()["result"]["group"]


def file_content(count, *, start=0):
    return "\ufeffTelefone;Nome\r\n" + "".join(f"+5567{900000000 + start + i};Pessoa {i}\r\n" for i in range(count))


async def preview(c, content, groups=None):
    reply = await c.client.post(c.audience + "/imports/preview", json={"file_name": "contatos.csv", "content": content,
        "default_country": "BR", "group_ids": groups or []})
    assert reply.status_code == 200, reply.text
    return reply.json()


def commit_body(value, **changes):
    return {"idempotency_key": str(uuid4()), "preview_token": value["preview_token"], "skip_invalid": False, **changes}


async def total(c):
    response = await c.client.get(c.audience + "/contacts")
    assert response.status_code == 200, response.text
    return response.json()["total"]


async def test_contact_crud_preferences_private_credentials_and_csv(audience_case):
    c = audience_case
    current = await contact(c, name="=HYPERLINK(1)", service_password="synthetic-private-secret",
        service_username="assinante", chatbot_enabled=False, audio_transcription_enabled=False,
        plan_name="Mensal", price_cents=2590, due_date="2027-01-10")
    assert current["password_configured"] is True
    assert "service_password" not in current
    detail = await c.client.get(c.audience + "/contacts/" + current["key"])
    assert detail.json() == current
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        prefs = await store.preferences(db, c.tenant, "5567999991234")
        facts = await store.contact_facts(db, c.tenant, "5567999991234")
        private = await store.contact_facts(db, c.tenant, "5567999991234", include_password=True)
    assert prefs == {"chatbot_enabled": False, "audio_transcription_enabled": False}
    assert facts["plan"] == "Mensal" and facts["amount"] == "25,90" and "password" not in facts
    assert private["password"] == "synthetic-private-secret"
    csv_reply = await c.client.get(c.audience + "/contacts/export")
    assert csv_reply.status_code == 200 and csv_reply.headers["x-total-rows"] == "1"
    assert "synthetic-private-secret" not in csv_reply.text and "assinante" not in csv_reply.text
    assert "'=HYPERLINK(1)" in csv_reply.text
    async with c.ctx.migration.connect() as db:
        saved = (await db.execute(text("SELECT service_password_encrypted FROM subscription_audience_contacts WHERE tenant_id=:t"),
            {"t": c.tenant})).scalar_one()
    assert saved and saved != "synthetic-private-secret"


async def test_contact_updates_preserve_and_explicitly_clear_long_unicode_password(audience_case):
    c = audience_case
    password = "🔑" * 1024
    saved = await contact(c, service_password=password)
    request_key = str(uuid4())
    changed = await c.client.patch(c.audience + "/contacts/" + saved["key"], json={
        "idempotency_key": request_key, "expected_version": saved["version"],
        "contact": {"name": "Nome atualizado", "phone_e164": saved["phone_e164"], "service_username": "atualizado"}})
    assert changed.status_code == 200, changed.text
    current = changed.json()["result"]["contact"]
    assert current["name"] == "Nome atualizado" and current["password_configured"] is True
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        private = await store.contact_facts(db, c.tenant, "5567999991234", include_password=True)
    assert private["password"] == password
    receipt = await c.client.get(c.audience + "/requests/" + request_key)
    assert receipt.status_code == 200 and password not in receipt.text
    assert "service_password" not in str(receipt.json())
    cleared = await c.client.patch(c.audience + "/contacts/" + saved["key"], json={
        "idempotency_key": str(uuid4()), "expected_version": current["version"],
        "contact": {"name": current["name"], "phone_e164": saved["phone_e164"], "clear_password": True}})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["result"]["contact"]["password_configured"] is False
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        private = await store.contact_facts(db, c.tenant, "5567999991234", include_password=True)
    assert private["password"] == ""


async def test_4211_import_exact_pagination_export_catalog_and_idempotency(audience_case):
    c = audience_case
    value = await preview(c, file_content(4211))
    assert value["counts"]["new"] == 4211 and value["counts"]["invalid"] == 0
    assert await total(c) == 0
    body = commit_body(value)
    reply = await c.client.post(c.audience + "/imports/commit", json=body)
    assert reply.status_code == 200, reply.text
    assert reply.json()["result"]["counts"]["added"] == 4211
    replay = await c.client.post(c.audience + "/imports/commit", json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert await total(c) == 4211
    page = await c.client.get(c.audience + "/contacts", params={"page": 43, "page_size": 100})
    assert page.json()["total"] == 4211 and len(page.json()["items"]) == 11
    export = await c.client.get(c.audience + "/contacts/export")
    assert int(export.headers["x-total-rows"]) == 4211
    assert len(list(csv.reader(StringIO(export.text.removeprefix("\ufeff")), delimiter=";"))) == 4212
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        live = await catalog.contacts_page(db, c.tenant, "", 1, await store.clock(db), "America/Cuiaba")
    assert live["total"] == 4211


async def test_5001_contacts_supported_but_group_overflow_has_no_partial_write(audience_case):
    c = audience_case
    destination = await group(c)
    value = await preview(c, file_content(5001), [destination["id"]])
    rejected = await c.client.post(c.audience + "/imports/commit", json=commit_body(value))
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "AUDIENCE_LIMIT"
    assert await total(c) == 0
    current = await c.client.get(c.audience + "/groups/" + destination["id"])
    assert current.json()["group"]["member_count"] == 0
    value = await preview(c, file_content(5001))
    success = await c.client.post(c.audience + "/imports/commit", json=commit_body(value))
    assert success.status_code == 200, success.text
    assert success.json()["result"]["counts"]["added"] == 5001 and await total(c) == 5001


async def test_duplicates_and_existing_names_credentials_preferences_and_optouts_survive(audience_case):
    c = audience_case
    original = await contact(c, name="Nome existente", service_password="keep-private", chatbot_enabled=False)
    destination = await group(c)
    async with c.ctx.migration.begin() as db:
        # A genuine revocation requires an ingress event. Use the existing
        # fixture's synthetic broker event, not a weakened permission row.
        event = (await db.execute(text("""INSERT INTO subscription_device_events
            (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
            VALUES(:t,:d,:g,:event,'text',clock_timestamp(),'{}'::jsonb) RETURNING id"""),
            {"t": c.tenant, "d": UUID(c.device["id"]), "g": c.saved["generation"], "event": str(uuid4())})).scalar_one()
        await db.execute(text("""INSERT INTO subscription_campaign_permissions(tenant_id,phone,revoked,source,event_id)
            VALUES(:t,'5567999991234',true,'optout',:event)"""), {"t": c.tenant, "event": event})
    value = await preview(c, "Telefone,Nome\n+55 (67) 99999-1234,Novo nome\n5567999991234,Duplicado\n+595991234567,Pessoa PY\n", [destination["id"]])
    assert value["counts"]["existing"] == 1 and value["counts"]["duplicates"] == 1 and value["counts"]["new"] == 1
    result = await c.client.post(c.audience + "/imports/commit", json=commit_body(value))
    assert result.status_code == 200, result.text
    current = (await c.client.get(c.audience + "/contacts/" + original["key"])).json()
    assert current["name"] == "Nome existente" and current["chatbot_enabled"] is False and current["password_configured"] is True
    saved_group = (await c.client.get(c.audience + "/groups/" + destination["id"])).json()["group"]
    assert saved_group["member_count"] == 2 and saved_group["blocked_count"] == 1


async def test_concurrent_same_import_is_exactly_once(audience_case):
    c = audience_case
    value = await preview(c, file_content(250))
    body = commit_body(value)
    results = await asyncio.gather(*(c.client.post(c.audience + "/imports/commit", json=body) for _ in range(3)))
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    assert sum(result.json()["replayed"] is False for result in results) == 1
    assert len({result.json()["request_id"] for result in results}) == 1
    assert await total(c) == 250


async def test_invalid_rows_require_explicit_skip_and_malformed_quotes_never_partial(audience_case):
    c = audience_case
    value = await preview(c, "Telefone;Nome\n+5567999991234;Ana\ninvalid;Ruim\n")
    assert not value["can_commit"] and value["can_skip_invalid"] and value["errors"][0]["line"] == 3
    reply = await c.client.post(c.audience + "/imports/commit", json=commit_body(value))
    assert reply.status_code == 422 and await total(c) == 0
    reply = await c.client.post(c.audience + "/imports/commit", json=commit_body(value, skip_invalid=True))
    assert reply.status_code == 200 and reply.json()["result"]["counts"]["invalid_skipped"] == 1
    bad = await preview(c, 'Telefone;Nome\n+5567999991235;"incompleto')
    assert bad["preview_token"] is None and not bad["can_skip_invalid"]
    assert await total(c) == 1


async def test_telegram_membership_archive_and_stale_version_are_consistent(audience_case):
    c = audience_case
    tg = await contact(c, phone_e164=None, telegram="@pessoa_teste")
    destination = await group(c, [tg["key"]])
    assert destination["telegram_count"] == 1 and destination["whatsapp_count"] == 0
    fresh = (await c.client.get(c.audience + "/contacts/" + tg["key"])).json()
    stale = await c.client.request("DELETE", c.audience + "/contacts/" + tg["key"], json={
        "idempotency_key": str(uuid4()), "expected_version": tg["version"]})
    assert stale.status_code == 409
    removed = await c.client.request("DELETE", c.audience + "/contacts/" + tg["key"], json={
        "idempotency_key": str(uuid4()), "expected_version": fresh["version"]})
    assert removed.status_code == 200, removed.text
    assert await total(c) == 0
    result = (await c.client.get(c.audience + "/groups/" + destination["id"])).json()
    assert result["group"]["member_count"] == 0


async def test_ungrouped_filter_checks_active_whatsapp_and_telegram_memberships(audience_case):
    c = audience_case
    wa = await contact(c, name="WhatsApp agrupado")
    tg = await contact(c, phone_e164=None, telegram="@pessoa_filtro", name="Telegram agrupado")
    free = await contact(c, phone_e164="+595991234567", name="Pessoa sem grupo")
    destination = await group(c, [wa["key"], tg["key"]])
    ungrouped = await c.client.get(c.audience + "/contacts", params={"group_id": "none"})
    assert ungrouped.status_code == 200, ungrouped.text
    assert ungrouped.json()["total"] == 1 and ungrouped.json()["items"][0]["key"] == free["key"]
    grouped = await c.client.get(c.audience + "/contacts", params={"group_id": destination["id"]})
    assert grouped.status_code == 200 and {item["key"] for item in grouped.json()["items"]} == {wa["key"], tg["key"]}
    formatted = await c.client.get(c.audience + "/contacts", params={"q": "+55 (67) 99999-1234"})
    assert formatted.status_code == 200 and formatted.json()["total"] == 1
    assert formatted.json()["items"][0]["key"] == wa["key"]
    exported = await c.client.get(c.audience + "/contacts/export", params={"group_id": "none", "q": "sem grupo"})
    assert exported.status_code == 200 and exported.headers["x-total-rows"] == "1"
    assert "Pessoa sem grupo" in exported.text and "agrupado" not in exported.text
    invalid = await c.client.get(c.audience + "/contacts", params={"group_id": "not-a-group"})
    assert invalid.status_code == 422
    missing = await c.client.get(c.audience + "/contacts", params={"group_id": str(uuid4())})
    assert missing.status_code == 404
    archived = await c.client.request("DELETE", c.audience + "/groups/" + destination["id"], json={
        "idempotency_key": str(uuid4()), "expected_version": destination["version"]})
    assert archived.status_code == 200, archived.text
    after = await c.client.get(c.audience + "/contacts", params={"group_id": "none"})
    assert after.status_code == 200 and after.json()["total"] == 3


async def test_legacy_group_only_contacts_survive_membership_removal_and_group_archive(audience_case):
    c = audience_case
    destination = await group(c)
    # The existing extractor stores members directly in this shared catalog.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
            VALUES(:t,:g,'5567999991234','Extraído um'),(:t,:g,'5567999991235','Extraído dois')"""),
            {"t": c.tenant, "g": UUID(destination["id"])})
    assert await total(c) == 2
    changed = await c.client.patch(c.audience + "/groups/" + destination["id"], json={
        "idempotency_key": str(uuid4()), "expected_version": destination["version"], "name": "Grupo atualizado",
        "contact_keys": ["phone:5567999991235"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["result"]["group"]["member_count"] == 1
    assert await total(c) == 2
    free = await c.client.get(c.audience + "/contacts", params={"group_id": "none"})
    assert free.json()["total"] == 1 and free.json()["items"][0]["key"] == "phone:5567999991234"
    removed = await c.client.request("DELETE", c.audience + "/groups/" + destination["id"], json={
        "idempotency_key": str(uuid4()), "expected_version": changed.json()["result"]["group"]["version"]})
    assert removed.status_code == 200, removed.text
    after = await c.client.get(c.audience + "/contacts", params={"group_id": "none"})
    assert after.json()["total"] == 2
    assert {item["name"] for item in after.json()["items"]} == {"Extraído um", "Extraído dois"}
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        available = await catalog.contacts_page(db, c.tenant, "", 1, await store.clock(db), "America/Cuiaba")
    assert available["total"] == 2


async def test_tenant_isolation_owner_revocation_and_runtime_secret_denial(audience_case):
    c = audience_case
    saved = await contact(c, service_password="private")
    forbidden = await c.client.get(api.BASE.format(tenant_id=c.ctx.b) + "/contacts")
    assert forbidden.status_code == 403
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.ctx.b):
            rows = (await db.execute(text("SELECT id FROM subscription_audience_contacts WHERE tenant_id=:t"), {"t": c.tenant})).all()
            assert rows == []
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT service_password_encrypted FROM subscription_audience_contacts"))
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
            {"t": c.tenant, "a": c.ctx.actor.account_id})
    rejected = await c.client.post(c.audience + "/contacts", content=b'{"service_password":"private"')
    assert rejected.status_code == 403 and "private" not in rejected.text


async def test_contact_archive_blocks_send_catalog_and_never_revives_on_import(audience_case):
    c = audience_case
    saved = await contact(c)
    destination = await group(c, [saved["key"]])
    fresh = (await c.client.get(c.audience + "/contacts/" + saved["key"])).json()
    removed = await c.client.request("DELETE", c.audience + "/contacts/" + saved["key"], json={
        "idempotency_key": str(uuid4()), "expected_version": fresh["version"]})
    assert removed.status_code == 200, removed.text
    value = await preview(c, "Telefone;Nome\n+5567999991234;Outro nome\n", [destination["id"]])
    assert value["counts"]["existing"] == 1 and value["counts"]["new"] == 0
    result = await c.client.post(c.audience + "/imports/commit", json=commit_body(value))
    assert result.status_code == 200 and result.json()["result"]["counts"]["archived_skipped"] == 1
    assert result.json()["result"]["counts"]["added"] == 0 and result.json()["result"]["counts"]["new"] == 0
    assert await total(c) == 0
    async with store.owner_transaction(c.tenant, c.ctx.actor) as db:
        projected = await catalog.contacts_page(db, c.tenant, "", 1, await store.clock(db), "America/Cuiaba")
    assert projected["total"] == 0
