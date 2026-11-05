"""Group imports over real cookie/CSRF, tenant guards and PostgreSQL grants.

Only the provider read is synthetic. No WhatsApp session or recipient is real.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_group_contacts as api
from app import subscription_group_contacts_store as store
from app import subscription_group_contacts_schemas as schemas
from app import subscription_scheduled_messages as scheduled_api
from app import subscription_chatbot_store as shared
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_campaigns_postgres import (
    campaign_case, chatbot_case, device_case, disposable_environment, new_owner_http, onboarding_db,
    group as local_group, edit_group, snapshot as campaign_snapshot, event, revoke,
)  # noqa: F401 - real auth/role/transaction fixtures, only provider data is fake.

pytestmark = pytest.mark.integration
REMOTE = "120363111111111111@g.us"
PHONE, RESTORED, UNSELECTED, INTERNATIONAL = (
    "+5567999999901", "+5567999999902", "+5567999999903", "+12025550123")


@pytest_asyncio.fixture
async def group_contacts_case(campaign_case, monkeypatch):
    c = campaign_case
    # Campaign eligibility also covers offline devices. This fixture exercises
    # successful live reads, so its synthetic device must explicitly be connected.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='connected' WHERE tenant_id=:t AND id=:d"),
                         {"t": c.tenant, "d": UUID(c.device["id"])})
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    for router in (api.router, scheduled_api.router):
        include_plan_router(c.app, router)
    c.app.router.routes.extend(generic)
    c.extractor = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/group-contacts"
    c.scheduled = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/scheduled-messages"
    c.group_reads, c.after_group_read = [], None
    c.provider_members = [
        {"phone": PHONE, "name": "", "role": "owner"},
        {"phone": RESTORED, "name": "Nome informado pelo provedor", "role": "admin"},
        {"phone": INTERNATIONAL, "name": "Pessoa internacional", "role": "member"}]
    original = qr_channels._bridge_control

    async def transport(channel, action, method, payload=None):
        if not action.startswith("device/group-contacts/"):
            return await original(channel, action, method, payload)
        assert channel["tenant_id"] == c.tenant and channel["id"] == c.row["id"]
        assert method == "POST" and payload["device_id"] == c.device["id"]
        c.group_reads.append({"action": action, "payload": deepcopy(payload)})
        context = {key: payload[key] for key in ("device_id", "generation", "settings_revision")}
        group = {"id": REMOTE, "name": "Grupo remoto sintético", "member_count": len(c.provider_members)+2}
        if c.after_group_read:
            await c.after_group_read()
        if action.endswith("/groups"):
            return {**context, "groups": [group]}
        assert action.endswith("/members") and payload["group_id"] == REMOTE
        return {**context, "group": group, "members": deepcopy(c.provider_members),
            "total": len(c.provider_members)+2, "unavailable_count": 1, "duplicate_count": 1}

    monkeypatch.setattr(qr_channels, "_bridge_control", transport)
    yield c


async def snapshot(c):
    value = await campaign_snapshot(c)
    async with c.ctx.migration.connect() as db:
        for table in ("subscription_schedules", "subscription_schedule_runs", "subscription_schedule_recipients",
                      "subscription_bulk_batches", "subscription_bulk_recipients", "subscription_bulk_requests", "customers"):
            value[table] = (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
                ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""),
                {"t": c.tenant})).scalar_one()
    return value


async def prepared(c):
    response = await c.client.get(c.extractor+"/members", params={"device_id": c.device["id"], "group_id": REMOTE})
    assert response.status_code == 200, response.text
    return response.json()


def import_body(preview, *, key=None, phones=None, target=None):
    return {"idempotency_key": str(key or uuid4()), "preview_token": preview["preview_token"],
        "phones": phones if phones is not None else [member["phone"] for member in preview["members"]],
        "target_group_id": target["id"] if target else None, "expected_version": target["version"] if target else None,
        "new_group_name": None if target else "Grupo: remoto sintético"}


async def saved(c, body):
    response = await c.client.post(c.extractor+"/import", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_options_group_and_member_reads_are_pure_and_keep_missing_names(group_contacts_case):
    c = group_contacts_case
    before, transport = await snapshot(c), list(c.bridge.calls)
    options = await c.client.get(c.extractor+"/options")
    assert options.status_code == 200 and options.json()["groups"] == []
    assert options.json()["devices"][0]["id"] == c.device["id"]
    assert options.json()["devices"][0]["status"] == "connected" and options.json()["devices"][0]["available"] is True
    groups = await c.client.get(c.extractor+"/groups", params={"device_id": c.device["id"]})
    assert groups.status_code == 200 and groups.json()["total"] == 1
    value = await prepared(c)
    assert value["members"][0]["name"] == ""
    assert value["total"] == 5 and value["unavailable_count"] == value["duplicate_count"] == 1
    assert value["device"]["version"] == options.json()["devices"][0]["version"]
    assert {read["action"] for read in c.group_reads} == {"device/group-contacts/groups", "device/group-contacts/members"}
    assert c.bridge.calls == transport and await snapshot(c) == before
    assert "generation" not in value and "settings_revision" not in value


@pytest.mark.parametrize("status", ["stopped", "disconnected", "connecting"])
async def test_offline_options_and_both_reads_never_call_bridge_or_change_local_data(group_contacts_case, status):
    c = group_contacts_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status=:s WHERE tenant_id=:t AND id=:d"),
                         {"s": status, "t": c.tenant, "d": UUID(c.device["id"])})
    before = await snapshot(c)
    options = await c.client.get(c.extractor+"/options")
    device = options.json()["devices"][0]
    assert options.status_code == 200 and device["status"] == status and device["available"] is False
    assert device["blocked_reason"] == store.ERRORS["GROUP_CONTACTS_DEVICE_UNAVAILABLE"]
    for suffix, params in (("/groups", {"device_id": c.device["id"]}),
            ("/members", {"device_id": c.device["id"], "group_id": REMOTE})):
        response = await c.client.get(c.extractor+suffix, params=params)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "GROUP_CONTACTS_DEVICE_UNAVAILABLE"
    assert c.group_reads == [] and await snapshot(c) == before


@pytest.mark.parametrize("suffix", ["/groups", "/members"])
async def test_disconnect_during_provider_read_rejects_success_without_a_preview(group_contacts_case, suffix):
    c = group_contacts_case
    async def disconnect():
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE tenant_id=:t AND id=:d"),
                             {"t": c.tenant, "d": UUID(c.device["id"])})
    c.after_group_read = disconnect
    params = {"device_id": c.device["id"]}
    if suffix == "/members": params["group_id"] = REMOTE
    response = await c.client.get(c.extractor+suffix, params=params)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "GROUP_CONTACTS_DEVICE_UNAVAILABLE"
    assert len(c.group_reads) == 1 and "preview_token" not in response.json()
    after = await snapshot(c)
    assert after["subscription_campaign_groups"] == after["subscription_campaign_requests"] == []


async def test_valid_preview_can_be_imported_after_disconnect_without_another_provider_read(group_contacts_case):
    c = group_contacts_case
    value = await prepared(c)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE tenant_id=:t AND id=:d"),
                         {"t": c.tenant, "d": UUID(c.device["id"])})
    reads = deepcopy(c.group_reads)
    result = await saved(c, import_body(value, phones=[PHONE]))
    assert result["result"]["origin"] == "group_contacts" and result["result"]["added_count"] == 1
    assert c.group_reads == reads


async def test_selected_subset_creates_local_group_and_shared_catalog_without_sending(group_contacts_case):
    c = group_contacts_case
    value = await prepared(c)
    before, reads = await snapshot(c), deepcopy(c.group_reads)
    result = await saved(c, import_body(value, phones=[PHONE, INTERNATIONAL]))
    assert result["status"] == "succeeded" and result["kind"] == "group_create" and result["campaign_id"] is None
    assert result["result"]["origin"] == "group_contacts"
    assert {key: result["result"][key] for key in ("selected_count", "added_count", "existing_count", "restored_count")} == {
        "selected_count": 2, "added_count": 2, "existing_count": 0, "restored_count": 0}
    after = await snapshot(c)
    assert {row["phone"] for row in after["subscription_campaign_members"]} == {PHONE[1:], INTERNATIONAL[1:]}
    assert next(row["name"] for row in after["subscription_campaign_members"] if row["phone"] == PHONE[1:]) == PHONE
    for table in before:
        if table not in {"subscription_campaign_groups", "subscription_campaign_members", "subscription_campaign_requests"}:
            assert after[table] == before[table], table
    assert c.group_reads == reads
    contacts = await c.client.get(c.scheduled+"/contacts")
    assert contacts.status_code == 200 and contacts.json()["total"] == 2, contacts.text
    assert {item["key"] for item in contacts.json()["items"]} == {"phone:"+PHONE[1:], "phone:"+INTERNATIONAL[1:]}
    assert c.group_reads == reads


async def test_additive_import_preserves_names_unselected_members_and_global_optouts(group_contacts_case):
    c = group_contacts_case
    target = await local_group(c, members=[{"phone": PHONE, "name": "Nome local"},
        {"phone": RESTORED, "name": "Nome local removido"}, {"phone": UNSELECTED, "name": "Não selecionado"}])
    edited = await edit_group(c, target, [{"phone": PHONE, "name": "Nome local"},
        {"phone": UNSELECTED, "name": "Não selecionado"}])
    target = edited["group"] if "group" in edited else edited
    await event(c, text="Não quero mensagens promocionais", phone=RESTORED[1:])
    value = await prepared(c)
    before = await snapshot(c)
    result = await saved(c, import_body(value, target=target))
    assert {key: result["result"][key] for key in ("selected_count", "added_count", "existing_count", "restored_count")} == {
        "selected_count": 3, "added_count": 1, "existing_count": 1, "restored_count": 1}
    after = await snapshot(c)
    rows = {row["phone"]: row for row in after["subscription_campaign_members"]}
    assert rows[PHONE[1:]]["name"] == "Nome local"
    assert rows[RESTORED[1:]]["name"] == "Nome local removido" and rows[RESTORED[1:]]["removed_at"] is None
    assert rows[UNSELECTED[1:]] == next(row for row in before["subscription_campaign_members"] if row["phone"] == UNSELECTED[1:])
    assert after["subscription_campaign_permissions"] == before["subscription_campaign_permissions"]
    assert result["result"]["group"]["blocked_count"] == 1
    assert result["result"]["group"]["member_count"] == 4


async def test_concurrent_retry_and_recovery_preserve_one_committed_import(group_contacts_case, monkeypatch):
    c = group_contacts_case
    body = import_body(await prepared(c))
    first, second = await asyncio.gather(saved(c, body), saved(c, body))
    assert first["request_id"] == second["request_id"] and first["group_id"] == second["group_id"]
    assert sorted([first["replayed"], second["replayed"]]) == [False, True]
    before, reads = await snapshot(c), deepcopy(c.group_reads)
    def no_decrypt(*args): raise AssertionError("Completed import cannot require its expired preview")
    monkeypatch.setattr(store, "decode_preview", no_decrypt)
    replay = await saved(c, body)
    recovered = await c.client.get(c.extractor+"/requests/"+body["idempotency_key"])
    assert recovered.status_code == 200 and replay["replayed"] is True
    assert recovered.json()["result"] == replay["result"] == first["result"]
    assert len(before["subscription_campaign_requests"]) == 1 and len(before["subscription_campaign_groups"]) == 1
    assert await snapshot(c) == before and c.group_reads == reads


async def test_existing_members_are_a_noop_and_no_local_names_are_replaced(group_contacts_case):
    c = group_contacts_case
    target = await local_group(c, members=[{"phone": PHONE, "name": "Nome manual"}])
    body = import_body(await prepared(c), phones=[PHONE], target=target)
    before = await snapshot(c)
    result = await saved(c, body)
    assert result["result"]["existing_count"] == 1 and result["result"]["added_count"] == result["result"]["restored_count"] == 0
    after = await snapshot(c)
    assert after["subscription_campaign_groups"] == before["subscription_campaign_groups"]
    assert after["subscription_campaign_members"] == before["subscription_campaign_members"]
    assert result["result"]["group"]["version"] == target["version"]


@pytest.mark.parametrize("change", ["phone", "cipher", "tenant", "actor", "expired", "device_revision"])
async def test_invalid_or_stale_preview_has_no_partial_import(group_contacts_case, monkeypatch, change):
    c = group_contacts_case
    body = import_body(await prepared(c))
    if change == "phone": body["phones"] = [UNSELECTED]
    elif change == "cipher":
        token = body["preview_token"]
        body["preview_token"] = token[:40]+("x" if token[40] != "x" else "y")+token[41:]
    elif change in {"tenant", "actor"}:
        cipher = qr_channels._cipher()
        value = json.loads(cipher.decrypt(body["preview_token"].encode()))
        value["tenant_id" if change == "tenant" else "actor_account_id"] = str(uuid4())
        body["preview_token"] = cipher.encrypt(json.dumps(value).encode()).decode()
    elif change == "expired":
        original = store.clock
        async def later(db): return await original(db) + timedelta(seconds=301)
        monkeypatch.setattr(store, "clock", later)
    else:
        await revoke(c, "device_revision")
    before = await snapshot(c)
    result = await c.client.post(c.extractor+"/import", json=body)
    assert result.status_code == (410 if change == "expired" else 409 if change == "device_revision" else 422), result.text
    assert await snapshot(c) == before


@pytest.mark.parametrize("change", ["owner", "viewer", "niche", "paused", "feature"])
async def test_current_authority_precedes_network_recovery_and_body_decoding(group_contacts_case, monkeypatch, change):
    c = group_contacts_case
    body = import_body(await prepared(c))
    await saved(c, body)
    if change == "paused":
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:t"), {"t": c.tenant})
    elif change == "feature":
        original = shared.plan_state
        async def state(db, tenant, **kwargs):
            value = await original(db, tenant, **kwargs)
            return value | {"permissions": value["permissions"] | {store.FEATURE: False}}
        monkeypatch.setattr(shared, "plan_state", state)
    else: await revoke(c, change)
    reads = deepcopy(c.group_reads)
    for suffix in ("/options", "/groups?device_id="+c.device["id"],
                   "/members?device_id="+c.device["id"]+"&group_id="+REMOTE, "/requests/"+body["idempotency_key"]):
        result = await c.client.get(c.extractor+suffix)
        assert result.status_code == 403, result.text
    malformed = await c.client.post(c.extractor+"/import", content=b"not-json", headers={"Content-Type": "application/json"})
    assert malformed.status_code == 403 and c.group_reads == reads


@pytest.mark.parametrize("change", ["owner", "device_revision"])
async def test_provider_result_is_rechecked_after_inflight_revocation(group_contacts_case, change):
    c = group_contacts_case
    async def after(): await revoke(c, change)
    c.after_group_read = after
    response = await c.client.get(c.extractor+"/members", params={"device_id": c.device["id"], "group_id": REMOTE})
    assert response.status_code == (403 if change == "owner" else 409), response.text
    after_state = await snapshot(c)
    assert after_state["subscription_campaign_groups"] == after_state["subscription_campaign_requests"] == []


async def test_cross_namespace_keys_and_changed_intents_do_not_recover_or_overwrite_other_operations(group_contacts_case):
    c = group_contacts_case
    target = await local_group(c)
    async with c.ctx.migration.connect() as db:
        foreign_key = (await db.execute(text("SELECT idempotency_key FROM subscription_campaign_requests WHERE tenant_id=:t"),
            {"t": c.tenant})).scalar_one()
    value = await prepared(c)
    borrowed = import_body(value, key=foreign_key)
    assert (await c.client.get(c.extractor+"/requests/"+str(foreign_key))).status_code == 404
    assert (await c.client.post(c.extractor+"/import", json=borrowed)).status_code == 409
    body = import_body(value)
    await saved(c, body)
    before = await snapshot(c)
    changed = await c.client.post(c.extractor+"/import", json=body | {"phones": [PHONE]})
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "GROUP_CONTACTS_IDEMPOTENCY_CONFLICT"
    foreign = await c.client.post(c.campaigns+"/groups", json={"idempotency_key": body["idempotency_key"],
        "name": "Outra intenção", "members": [{"phone": PHONE, "name": "Outro"}]})
    assert foreign.status_code == 409
    assert await snapshot(c) == before


async def test_stale_target_version_has_explicit_safe_rejection_and_can_be_corrected(group_contacts_case):
    c = group_contacts_case
    target = await local_group(c)
    body = import_body(await prepared(c), target=target)
    await edit_group(c, target, [{"phone": UNSELECTED, "name": "Novo membro local"}])
    before = await snapshot(c)
    stale = await c.client.post(c.extractor+"/import", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["rejection"] == {
        "reason": "stale_group_version", "idempotency_key": body["idempotency_key"],
        "group_id": target["id"], "expected_version": target["version"]}
    assert (await c.client.get(c.extractor+"/requests/"+body["idempotency_key"])).status_code == 404
    assert await snapshot(c) == before
    result = await saved(c, body | {"expected_version": target["version"]+1})
    assert result["result"]["group"]["member_count"] == 4


async def test_failure_after_member_insert_rolls_back_group_members_and_request(group_contacts_case, monkeypatch):
    c = group_contacts_case
    body = import_body(await prepared(c))
    before = await snapshot(c)
    async def fail_after_rows(*args, **kwargs): raise HTTPException(503, "Synthetic transaction failure")
    monkeypatch.setattr(store.campaigns, "save_request", fail_after_rows)
    response = await c.client.post(c.extractor+"/import", json=body)
    assert response.status_code == 503
    assert await snapshot(c) == before


async def test_group_and_member_limits_are_atomic_and_do_not_remove_existing_contacts(group_contacts_case, monkeypatch):
    c = group_contacts_case
    target = await local_group(c, members=[{"phone": UNSELECTED, "name": "Preservado"}])
    value = await prepared(c)
    before = await snapshot(c)
    monkeypatch.setattr(schemas, "MAX_GROUPS", 1)
    full = await c.client.post(c.extractor+"/import", json=import_body(value))
    assert full.status_code == 422 and full.json()["detail"]["code"] == "GROUP_CONTACTS_LIMIT"
    monkeypatch.setattr(schemas, "MAX_MEMBERS", 3)
    crowded = await c.client.post(c.extractor+"/import", json=import_body(value, target=target))
    assert crowded.status_code == 422 and crowded.json()["detail"]["code"] == "GROUP_CONTACTS_LIMIT"
    assert await snapshot(c) == before


async def test_cookie_csrf_cross_tenant_device_and_database_permissions_are_enforced(group_contacts_case):
    c = group_contacts_case
    body = import_body(await prepared(c))
    assert (await c.private.get(c.extractor+"/options")).status_code == 401
    assert (await c.client.post(c.extractor+"/import", json=body, headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.extractor.replace(str(c.tenant), str(c.ctx.b))+"/options")).status_code == 403
    before_reads = deepcopy(c.group_reads)
    assert (await c.client.get(c.extractor+"/groups", params={"device_id": str(uuid4())})).status_code == 404
    assert c.group_reads == before_reads
    await saved(c, body)
    for table in ("subscription_campaign_groups", "subscription_campaign_members", "subscription_campaign_requests"):
        async with c.ctx.runtime() as db:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
