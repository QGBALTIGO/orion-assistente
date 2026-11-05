"""Direct sends through real auth, roles, transactions and the shared poll.

Inherited fixtures require disposable loopback PostgreSQL/Redis. WhatsApp and
private object storage remain synthetic; no real recipient or provider is used.
"""
import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import subscription_direct_messages as api
from app import subscription_bulk_store as bulk_store
from app import subscription_chatbot_media as media
from app import subscription_chatbot_store as chatbot_store
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_bulk_messages_postgres import (
    bulk_case, scheduled_case, campaign_case, chatbot_case, device_case,
    disposable_environment, new_owner_http, onboarding_db, private_media,
    PHONE, OTHER_PHONE, TABLES, customer, item as bulk_item,
    review as bulk_review, review_payload as bulk_review_payload,
    confirm as bulk_confirm, confirmation, batch as bulk_batch,
    poll, poll_headers, renew, grant, finish, ledger, snapshot as bulk_snapshot,
    no_external, event, internal, scope, revoke,
)  # noqa: F401 - explicit transitive fixture imports preserve real auth/roles.
from test_subscription_templates_postgres import (
    create_template, template_definition, upload, png_bytes, wav_bytes,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def direct_case(bulk_case):
    c = bulk_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.direct = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/direct-messages"
    yield c


def message(c, *, kind="text", phone=PHONE, contacts=None, **changes):
    recipients = ({"mode": "phone", "phone": phone, "contact_keys": []} if contacts is None else
                  {"mode": "contacts", "phone": None, "contact_keys": ["phone:"+value for value in contacts]})
    value = {"kind": kind, "device_id": c.device["id"], "recipients": recipients}
    if kind == "text":
        value["text"] = "Texto literal {name} 🙂"
    return value | changes


def review_payload(c, value=None, *, key=None):
    return {"idempotency_key": str(key or uuid4()), "message": value or message(c)}


async def review(c, value=None, *, key=None):
    response = await c.client.post(c.direct+"/review", json=review_payload(c, value, key=key))
    assert response.status_code == 201, response.text
    return response.json()


async def confirm(c, prepared):
    return await c.client.post(c.direct+"/confirm", json=confirmation(prepared))


async def send(c, value=None):
    response = await confirm(c, await review(c, value))
    assert response.status_code == 202, response.text
    return response.json()["result"]["send"]


async def detail(c, row):
    response = await c.client.get(c.direct+"/"+row["send_id"])
    assert response.status_code == 200, response.text
    return response.json()


async def snapshot(c):
    result = await bulk_snapshot(c)
    async with c.ctx.migration.connect() as db:
        for table in ("subscription_chatbot_templates", "subscription_chatbot_assets",
                      "subscription_chatbot_jobs"):
            result[table] = (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
                ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""),
                {"t": c.tenant})).scalar_one()
    return result


def without_direct_feature(monkeypatch):
    """Revoke only this capability while retaining real tenant/role resolution."""
    original = chatbot_store.plan_state

    async def state(db, tenant, **kwargs):
        current = await original(db, tenant, **kwargs)
        return current | {"permissions": current["permissions"] | {"direct_messages.manage": False}}

    monkeypatch.setattr(chatbot_store, "plan_state", state)


async def test_read_prepare_and_confirm_are_private_and_do_not_send_or_create_contacts(direct_case, monkeypatch):
    c = direct_case
    before = await snapshot(c)
    async with no_external(c, monkeypatch, readonly=True):
        for suffix in ("/options", "/contacts"):
            response = await c.client.get(c.direct+suffix)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store", response.text
    async with no_external(c, monkeypatch):
        prepared = await review(c)
        prepared_state = await snapshot(c)
        assert prepared_state["subscription_bulk_batches"] == prepared_state["subscription_bulk_recipients"] == []
        assert len(prepared_state["subscription_bulk_requests"]) == 1
        response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    row = response.json()["result"]["send"]
    assert row["progress"]["pending"] == 1 and row["status"] == "pending"
    assert "batch_id" not in row and "interval_seconds" not in row
    after = await snapshot(c)
    assert {r["origin"] for r in after["subscription_bulk_batches"]} == {"direct"}
    assert {r["origin"] for r in after["subscription_bulk_requests"]} == {"direct"}
    assert after["customers"] == before["customers"]
    assert after["subscription_campaign_permissions"] == before["subscription_campaign_permissions"]
    assert after["subscription_chatbot_templates"] == before["subscription_chatbot_templates"]
    assert after["subscription_bulk_recipients"][0]["plan"] == [{"type": "text", "text": "Texto literal {name} 🙂"}]
    assert await ledger(c) == []
    async with no_external(c, monkeypatch, readonly=True):
        assert (await detail(c, row))["send"]["send_id"] == row["send_id"]
        recovered = await c.client.get(c.direct+"/requests/"+prepared["idempotency_key"])
        assert recovered.status_code == 200 and recovered.json()["result"] == response.json()["result"]
        assert recovered.headers["cache-control"] == "no-store"
        hidden = await c.client.get(c.bulk+"/"+row["send_id"])
        assert hidden.status_code == 404
        assert (await c.client.get(c.bulk)).json()["active_batches_total"] == 0


async def test_ten_contacts_work_but_eleven_never_persist_a_partial_review(direct_case):
    c = direct_case
    phones = [f"556799999{index:04d}" for index in range(901, 912)]
    for index, phone in enumerate(phones):
        await customer(c, phone=phone, name=f"Contato sintético {index}")
    before = await snapshot(c)
    rejected = await c.client.post(c.direct+"/review", json=review_payload(c, message(c, contacts=phones)))
    assert rejected.status_code == 422, rejected.text
    assert await snapshot(c) == before
    prepared = await review(c, message(c, contacts=phones[:10]))
    assert prepared["preview"]["total"] == prepared["preview"]["eligible"] == 10
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    value = await detail(c, response.json()["result"]["send"])
    assert value["send"]["progress"]["total"] == 10 and value["recipients"]["total"] == 10
    state = await snapshot(c)
    assert {r["phone"] for r in state["subscription_bulk_recipients"]} == set(phones[:10])


@pytest.mark.parametrize("foreign_first", ["bulk", "direct"])
async def test_same_idempotency_key_cannot_cross_origins_or_leak_its_recovery(direct_case, foreign_first):
    c, key = direct_case, uuid4()
    if foreign_first == "bulk":
        prepared = await bulk_review(c, key=key)
        created = await bulk_confirm(c, prepared)
        assert created.status_code == 202, created.text
        original, other = c.bulk, c.direct
        payload = review_payload(c, key=key)
        identity = created.json()["result"]["batch"]["batch_id"]
    else:
        prepared = await review(c, key=key)
        created = await confirm(c, prepared)
        assert created.status_code == 202, created.text
        original, other = c.direct, c.bulk
        payload = bulk_review_payload(c, key=key)
        identity = created.json()["result"]["send"]["send_id"]
    before = await snapshot(c)
    rejected_review = await c.client.post(other+"/review", json=payload)
    assert rejected_review.status_code == 409, rejected_review.text
    for response in (
        await c.client.post(other+"/confirm", json=confirmation(prepared)),
        await c.client.get(other+"/requests/"+str(key)),
        await c.client.get(other+"/"+identity),
        await c.client.post(other+"/"+identity+"/actions", json={"idempotency_key": str(uuid4()),
            "expected_version": 1, "action": "cancel", "confirmation": "CANCELAR"}),
    ):
        assert response.status_code == 404, response.text
    assert await snapshot(c) == before
    recovered = await c.client.get(original+"/requests/"+str(key))
    assert recovered.status_code == 200 and recovered.json()["result"] == created.json()["result"]


async def test_save_template_and_send_commit_once_with_concurrent_confirmation_and_recovery(direct_case, monkeypatch):
    c = direct_case
    payload = review_payload(c, message(c, save_template={"name": "Atendimento 🙂"}))
    before = await snapshot(c)
    async with no_external(c, monkeypatch):
        reviews = await asyncio.gather(*(c.client.post(c.direct+"/review", json=payload) for _ in range(2)))
        assert [r.status_code for r in reviews] == [201, 201], [r.text for r in reviews]
        assert reviews[0].json()["request_id"] == reviews[1].json()["request_id"]
        assert sorted(r.json()["replayed"] for r in reviews) == [False, True]
        assert (await snapshot(c))["subscription_chatbot_templates"] == before["subscription_chatbot_templates"]
        prepared = reviews[0].json()
        responses = await asyncio.gather(confirm(c, prepared), confirm(c, prepared))
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    result = responses[0].json()["result"]
    assert result == responses[1].json()["result"]
    state = await snapshot(c)
    assert len(state["subscription_bulk_batches"]) == len(state["subscription_bulk_requests"]) == 1
    template, = state["subscription_chatbot_templates"]
    assert template["id"] == result["saved_template"]["id"]
    assert template["name"] == "Atendimento 🙂" and template["body"] == payload["message"]["text"]
    assert len(state["subscription_bulk_recipients"]) == 1 and await ledger(c) == []
    recovered = await c.client.get(c.direct+"/requests/"+payload["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["result"] == result
    changed = deepcopy(payload)
    changed["message"]["save_template"]["name"] = "Outro nome"
    assert (await c.client.post(c.direct+"/review", json=changed)).status_code == 409
    assert await snapshot(c) == state


async def test_failure_after_template_and_recipients_rolls_back_every_write_and_allows_same_key_retry(direct_case, monkeypatch):
    c = direct_case
    prepared = await review(c, message(c, save_template={"name": "Modelo atômico"}))
    before = await snapshot(c)
    observed = []

    async def fail_completion(db, tenant, request_id, result, now, *, bulk_id):
        for table in ("subscription_chatbot_templates", "subscription_bulk_batches", "subscription_bulk_recipients"):
            observed.append((await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                                               {"t": tenant})).scalar_one())
        raise HTTPException(503, {"code": "SYNTHETIC_TRANSACTION_FAILURE"})

    with monkeypatch.context() as patch:
        patch.setattr(bulk_store, "complete_request", fail_completion)
        response = await confirm(c, prepared)
    assert response.status_code == 503 and observed == [1, 1, 1], response.text
    assert await snapshot(c) == before
    recovered = await c.client.get(c.direct+"/requests/"+prepared["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["status"] == "prepared"
    response = await confirm(c, prepared)
    assert response.status_code == 202 and response.json()["result"]["saved_template"], response.text
    state = await snapshot(c)
    assert len(state["subscription_chatbot_templates"]) == len(state["subscription_bulk_batches"]) == 1


@pytest.mark.parametrize("reason", ["optout", "paused", "capability"])
async def test_rejected_confirmation_does_not_create_the_optional_template(direct_case, monkeypatch, reason):
    c = direct_case
    if reason == "optout":
        await event(c, text="Não quero mensagens promocionais", phone=PHONE)
    prepared = await review(c, message(c, save_template={"name": "Não deve existir"}))
    if reason == "paused":
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:t"), {"t": c.tenant})
    elif reason == "capability":
        without_direct_feature(monkeypatch)
    before = await snapshot(c)
    response = await confirm(c, prepared)
    assert response.status_code == (409 if reason == "optout" else 403), response.text
    assert await snapshot(c) == before
    assert before["subscription_chatbot_templates"] == before["subscription_bulk_batches"] == []
    assert await ledger(c) == []


@pytest.mark.parametrize("kind", ["image", "audio"])
async def test_raw_media_keeps_renamed_file_and_audio_never_emits_caption_or_followup(direct_case, private_media, kind):
    c = direct_case
    resource = await upload(c, png_bytes() if kind == "image" else wav_bytes(), kind,
                            "synthetic.png" if kind == "image" else "synthetic.wav")
    filename = "Atendimento.jpg" if kind == "image" else "Atendimento.mp3"
    prepared = await review(c, message(c, kind="media", asset_id=resource["asset"]["id"],
        file_name=filename, caption="Primeira\nSegunda\r\nTerceira", save_template={"name": "Arquivo reutilizável"}))
    if kind == "audio":
        assert "AUDIO_CAPTION_OMITTED" in str(prepared["preview"]["warnings"])
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    row = response.json()["result"]["send"]
    state = await snapshot(c)
    recipient, = state["subscription_bulk_recipients"]
    action, = recipient["plan"]
    assert action["type"] == kind and action["asset"]["id"] == resource["asset"]["id"]
    assert action["file_name"] == filename
    template, = state["subscription_chatbot_templates"]
    assert template["file_name"] == filename
    if kind == "audio":
        assert not action.get("caption") and not action.get("text")
        assert not template["body"] and not template["followup_body"]
    else:
        assert action["caption"] == "Primeira Segunda Terceira"
    effect, = await poll(c)
    permit = await grant(c, effect)
    assert permit["authorized"] and permit["payload"]["action"] == action
    assert len(await ledger(c)) == 1
    await finish(c, effect)
    assert (await detail(c, row))["send"]["progress"]["sent"] == 1
    assert await poll(c) == []
    history = await c.client.get(c.history+"/"+effect["operation_id"])
    assert history.status_code == 200 and history.json()["item"]["origin"]["code"] == "direct", history.text
    assert history.json()["item"]["actions"] == [] and not history.json()["item"]["resend"]["eligible"]
    assert (await c.client.get(c.bulk)).json()["counts"]["total"] == 0


async def test_raw_media_cannot_target_catalog_contacts(direct_case, private_media):
    c = direct_case
    await customer(c)
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    before = await snapshot(c)
    response = await c.client.post(c.direct+"/review", json=review_payload(c,
        message(c, kind="media", contacts=[PHONE], asset_id=resource["asset"]["id"], file_name="foto.jpg", caption="Legenda")))
    assert response.status_code == 422, response.text
    assert await snapshot(c) == before


async def test_raw_asset_retired_after_review_cannot_create_send_or_saved_template(direct_case, private_media):
    c = direct_case
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    prepared = await review(c, message(c, kind="media", asset_id=resource["asset"]["id"],
        file_name="foto.jpg", caption="Legenda", save_template={"name": "Arquivo retirado"}))
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_chatbot_assets SET status='failed' WHERE tenant_id=:t AND id=:id"),
                         {"t": c.tenant, "id": UUID(resource["asset"]["id"])})
    before = await snapshot(c)
    response = await confirm(c, prepared)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "TEMPLATE_ASSET_INVALID"
    assert await snapshot(c) == before
    assert before["subscription_bulk_batches"] == before["subscription_chatbot_templates"] == []


async def test_template_selection_uses_frozen_library_content_and_keeps_raw_text_literal(direct_case):
    c = direct_case
    await customer(c, name="Nome sintético")
    template = await create_template(c, template_definition(text="Olá, {name}!"))
    row = await send(c, message(c, kind="template", contacts=[PHONE], template_id=template["id"]))
    state = await snapshot(c)
    recipient, = state["subscription_bulk_recipients"]
    assert recipient["template_id"] == template["id"] and recipient["template_revision"] == template["revision"]
    assert recipient["plan"] == [{"type": "text", "text": "Olá, Nome sintético!"}]
    assert (await detail(c, row))["send"]["progress"]["pending"] == 1


async def test_shared_admission_and_receipt_gap_apply_across_bulk_and_direct_origins(direct_case):
    c = direct_case
    await send(c)
    await bulk_batch(c, [bulk_item(c, phone=OTHER_PHONE)])
    await renew(c)
    replies = await asyncio.gather(*(internal(c, "/effects/pending", method="GET", headers=poll_headers(c)) for _ in range(3)))
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    identities = {effect["operation_id"] for response in replies for effect in response.json()["effects"]}
    assert len(identities) == 1
    first = {"operation_id": identities.pop()}
    assert (await grant(c, first))["authorized"]
    await finish(c, first)
    assert await poll(c) == []
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_bulk_recipients
            SET admitted_at=clock_timestamp()-interval '8 seconds',finished_at=clock_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND status='sent'"""), {"t": c.tenant})
    second, = await poll(c)
    assert second["operation_id"] != first["operation_id"] and (await grant(c, second))["authorized"]
    saved = await ledger(c)
    async with c.ctx.migration.connect() as db:
        origins = (await db.execute(text("""SELECT DISTINCT b.origin FROM subscription_bulk_batches b
            JOIN subscription_device_effects e ON e.tenant_id=b.tenant_id AND e.bulk_id=b.id
            WHERE b.tenant_id=:t"""), {"t": c.tenant})).scalars().all()
    assert set(origins) == {"bulk", "direct"} and len(saved) == 2


async def test_revoking_only_direct_capability_does_not_cancel_or_block_bulk_work(direct_case, monkeypatch):
    c = direct_case
    row = await send(c)
    pending_bulk = await bulk_batch(c, [bulk_item(c, phone=OTHER_PHONE)])
    without_direct_feature(monkeypatch)
    effect, = await poll(c)
    permit = await grant(c, effect)
    assert permit["authorized"] and permit["batch_id"] == pending_bulk["batch_id"]
    state = await snapshot(c)
    direct = next(r for r in state["subscription_bulk_recipients"] if r["bulk_id"] == row["send_id"])
    assert direct["status"] == "cancelled"
    assert all(str(e["bulk_id"]) != row["send_id"] for e in await ledger(c))


async def test_revoked_direct_feature_is_checked_before_reading_private_media(direct_case, private_media, monkeypatch):
    c = direct_case
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    await send(c, message(c, kind="media", asset_id=resource["asset"]["id"], file_name="foto.jpg", caption="Legenda"))
    effect, = await poll(c)
    permit = await grant(c, effect)
    assert permit["authorized"]
    asset = permit["payload"]["action"]["asset"]
    path = f"/internal/qr/channels/{c.row['id']}/device/bulk/assets/{asset['id']}"
    params = scope(c) | {"operation_id": effect["operation_id"], "variant": "original"}
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    without_direct_feature(monkeypatch)
    monkeypatch.setattr(media, "get_media", lambda *a, **k: pytest.fail("Revoked direct effect reached object storage"))
    response = await c.private.get(path, params=params, headers=headers)
    assert response.status_code == 409, response.text
    assert not (await grant(c, effect, recheck=True))["authorized"]


async def test_direct_cancel_preserves_version_conflict_and_recovers_one_committed_action(direct_case):
    c = direct_case
    row = await send(c)
    effect, = await poll(c)
    payload = {"idempotency_key": str(uuid4()), "expected_version": row["version"],
               "action": "cancel", "confirmation": "CANCELAR"}
    path = c.direct+"/"+row["send_id"]+"/actions"
    rejected = await c.client.post(path, json=payload)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "DIRECT_CONFLICT", rejected.text
    assert rejected.json()["detail"]["rejection"] == {"reason": "stale_version",
        "idempotency_key": payload["idempotency_key"], "kind": "cancel", "send_id": row["send_id"],
        "expected_version": row["version"]}
    assert (await c.client.get(c.direct+"/requests/"+payload["idempotency_key"])).status_code == 404
    current = (await detail(c, row))["send"]
    payload["expected_version"] = current["version"]
    responses = await asyncio.gather(*(c.client.post(path, json=payload) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    result = responses[0].json()["result"]
    assert responses[1].json()["result"] == result and result["send"]["status"] == "cancelled"
    recovered = await c.client.get(c.direct+"/requests/"+payload["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["result"] == result
    assert not (await grant(c, effect))["authorized"] and await poll(c) == []


async def test_cookie_csrf_tenant_scope_origin_immutability_and_cross_origin_fk_are_enforced(direct_case):
    c = direct_case
    prepared = await review(c)
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    other = await bulk_batch(c, [bulk_item(c, phone=OTHER_PHONE)])
    assert (await c.private.get(c.direct+"/options")).status_code == 401
    assert (await c.client.post(c.direct+"/review", json={}, headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.direct.replace(str(c.tenant), str(c.ctx.b))+"/options")).status_code == 403
    async with c.ctx.runtime() as db:
        for table in TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        for table in ("subscription_bulk_batches", "subscription_bulk_requests"):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(f"UPDATE {table} SET origin='bulk' WHERE tenant_id=:t AND origin='direct'"), {"t": c.tenant})
            assert denied.value.orig.sqlstate == "42501"
        with pytest.raises(DBAPIError) as invalid_link:
            async with db.begin():
                await db.execute(text("""UPDATE subscription_bulk_requests SET bulk_id=:other
                    WHERE tenant_id=:t AND idempotency_key=:key"""),
                    {"t": c.tenant, "key": UUID(prepared["idempotency_key"]), "other": UUID(other["batch_id"])})
        assert invalid_link.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_revoked_owner_or_workspace_cannot_recover_or_parse_a_direct_request(direct_case, change):
    c = direct_case
    prepared = await review(c)
    await revoke(c, change)
    before = await snapshot(c)
    for suffix in ("/options", "/contacts", "/requests/"+prepared["idempotency_key"]):
        assert (await c.client.get(c.direct+suffix)).status_code == 403
    for suffix in ("/review", "/confirm"):
        response = await c.client.post(c.direct+suffix, content=b"not-json", headers={"content-type": "application/json"})
        assert response.status_code == 403, response.text
    assert await snapshot(c) == before
