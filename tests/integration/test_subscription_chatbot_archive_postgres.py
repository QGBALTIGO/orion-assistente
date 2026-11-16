"""Real auth/PostgreSQL archive workflows; media storage is entirely synthetic.

The codec and Pillow really decode and normalize the media. Only private S3 is
replaced; no external HTTP, WhatsApp request or paid provider may run here.
"""
import asyncio
import base64
from datetime import datetime, timezone
from io import BytesIO
import threading
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest
from sqlalchemy import text

from app import subscription_chatbot_archive_format as codec
from app import subscription_chatbot_media as media
from app import subscription_chatbot_store as store
from test_subscription_chatbot_postgres import (
    chatbot_case, definition, device_case, disposable_environment, edit,
    new_owner_http, onboarding_db, private_media, rule_create,
)  # noqa: F401 - pytest also needs the imported fixtures' real dependencies.

pytestmark = pytest.mark.integration


def portable_document(*, with_media=False, count=1):
    """A self-contained export with fresh origin IDs and genuine decoded bytes."""
    document = {"format": codec.FORMAT, "version": codec.VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "rules": [], "templates": [], "assets": []}
    for index in range(count):
        document["rules"].append({"id": str(uuid4()), "creation_order": index + 1,
            "definition": definition(keyword=f"Importada {index + 1}", value=f"Conteúdo {index + 1}")})
    if with_media:
        image = BytesIO()
        Image.new("RGB", (32, 24), (23, 75, 132)).save(image, format="PNG")
        prepared_values = [media.prepare_media(image.getvalue(), "image"),
                           media.prepare_media("Texto completo: ação, informação e 🙂.\n".encode(), "document")]
        actions = []
        for kind, prepared in zip(("image", "document"), prepared_values):
            asset_id, template_id = str(uuid4()), str(uuid4())
            thumbnail = ({"mime": prepared.thumbnail_mime, "size": len(prepared.thumbnail),
                          "sha256": prepared.thumbnail_sha256} if prepared.thumbnail else None)
            document["assets"].append({"id": asset_id, "kind": kind, "mime": prepared.mime,
                "size": prepared.size, "sha256": prepared.sha256, "width": prepared.width,
                "height": prepared.height, "duration_ms": prepared.duration_ms,
                "thumbnail": thumbnail, "data": prepared.data, "thumbnail_data": prepared.thumbnail})
            document["templates"].append({"id": template_id, "definition": {
                "name": "Template " + kind, "kind": kind, "text": "Legenda" if kind == "image" else "",
                "asset_id": asset_id}})
            actions.append({"id": str(uuid4()), "type": "template", "template_id": template_id,
                            "delay_seconds": 1})
        document["rules"][0]["definition"]["response"] = {"kind": "sequence", "actions": actions}
        document["rules"].append({"id": str(uuid4()), "creation_order": count + 1,
            "definition": definition(keyword="Raiz com anexos", response={"kind": "sequence", "actions": [
                {"id": str(uuid4()), "type": "nested_reply", "rule_id": document["rules"][0]["id"],
                 "delay_seconds": 1}]})})
    return document


async def active_rules(c):
    async with c.ctx.migration.connect() as db:
        rows = (await db.execute(text("""SELECT id,origin_id,creation_order,revision,device_id,
            enabled,match_mode,keyword,response FROM subscription_chatbot_rules
            WHERE tenant_id=:t AND removed_at IS NULL ORDER BY creation_order"""), {"t": c.tenant})).mappings().all()
    return [dict(row) for row in rows]


async def current_revision(c):
    async with c.ctx.migration.connect() as db:
        revision = (await db.execute(text("SELECT revision FROM subscription_chatbot_state WHERE tenant_id=:t"),
                                    {"t": c.tenant})).scalar_one_or_none()
    c.chat_revision = revision or 1
    return c.chat_revision


async def preview_import(c, document, *, format="json", mode="add_missing", device_id=None):
    response = await c.client.post(c.chat + "/imports/preview", json={
        "filename": "orion-chatbot." + format,
        "content_base64": base64.b64encode(codec.write_archive(document, format)).decode(),
        "device_id": device_id, "mode": mode})
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["preview"]["can_apply"], bool)
    return response.json()["preview"]


async def apply(c, preview, *, key=None, confirmation=None, expected=None):
    payload = {"expected_chatbot_revision": preview["chatbot_revision"] if expected is None else expected,
               "idempotency_key": str(key or uuid4()), "confirmation": confirmation}
    return await c.client.post(c.chat + "/previews/" + preview["id"] + "/apply", json=payload)


async def run_job(c, job):
    from app import subscription_chatbot_archive as archive
    outcome = await archive.process_restore(c.tenant, UUID(job["id"]))
    async with c.ctx.migration.connect() as db:
        saved = dict((await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "id": UUID(job["id"])})).mappings().one())
    await current_revision(c)
    return outcome, saved


async def delete_rule(c, rule):
    response = await c.client.request("DELETE", c.chat + "/rules/" + rule["id"], json={
        "expected_chatbot_revision": c.chat_revision, "expected_revision": rule["revision"],
        "idempotency_key": str(uuid4())})
    assert response.status_code == 200, response.text
    c.chat_revision = response.json()["chatbot_revision"]


async def backup(c):
    from app import subscription_chatbot_archive as archive
    assert await archive.scheduled_backup(c.tenant) is True
    listing = await c.client.get(c.chat + "/backups")
    assert listing.status_code == 200, listing.text
    return next(item for item in listing.json()["items"] if item["source"] == "scheduled")


async def assert_no_effects(c, calls):
    assert c.bridge.calls == calls
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_effects WHERE tenant_id=:t AND type='chatbot_action'"),
                                {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["json", "zip"])
async def test_import_export_real_media_closed_graph_preview_and_apply_idempotency(chatbot_case, private_media, format):
    from app import subscription_chatbot_archive as archive
    c = chatbot_case
    calls = list(c.bridge.calls)
    source = portable_document(with_media=True, count=2)
    before, revision = await active_rules(c), await current_revision(c)
    preview = await preview_import(c, source, format=format, device_id=c.device["id"])
    assert preview["source"] == "import" and preview["mode"] == "add_missing" and preview["can_apply"]
    assert preview["counts"]["create"] == 3
    assert preview["counts"]["template_clones"] == 2 and preview["counts"]["media_copies"] == 2
    assert await active_rules(c) == before and await current_revision(c) == revision
    assert private_media == {}  # Preview cannot perform even a storage write.
    key = uuid4()
    queued = await apply(c, preview, key=key)
    assert queued.status_code == 202, queued.text
    job = queued.json()["job"]
    replay = await apply(c, preview, key=key)
    assert replay.status_code == 202 and replay.json()["job"]["id"] == job["id"], replay.text
    outcome, saved = await run_job(c, job)
    assert outcome == "succeeded" and saved["status"] == "succeeded", saved
    imported = await active_rules(c)
    assert len(imported) == 3 and all(str(row["device_id"]) == c.device["id"] for row in imported)
    assert {str(row["origin_id"]) for row in imported} == {row["id"] for row in source["rules"]}
    assert {row["keyword"] for row in imported} == {row["definition"]["keyword"] for row in source["rules"]}
    committed_revision, objects = c.chat_revision, dict(private_media)
    assert await archive.process_restore(c.tenant, UUID(job["id"])) == "succeeded"
    assert await active_rules(c) == imported and await current_revision(c) == committed_revision
    assert private_media == objects
    replay_after_commit = await apply(c, preview, key=key)
    assert replay_after_commit.status_code == 202 and replay_after_commit.json()["job"]["id"] == job["id"]
    root = next(row for row in imported if row["keyword"] == "Raiz com anexos")
    reserved = await c.client.post(c.chat + "/exports", json={"ids": [str(root["id"])], "format": format})
    assert reserved.status_code == 200, reserved.text
    exported = reserved.json()["export"]
    content = await c.client.get(exported["url"])
    assert content.status_code == 200 and content.headers.get("cache-control") == "no-store", content.text
    decoded = codec.read_archive(content.content, exported["filename"])
    assert {item["definition"]["keyword"] for item in decoded["rules"]} == {"Importada 1", "Raiz com anexos"}
    assert len(decoded["templates"]) == 2 and len(decoded["assets"]) == 2
    text_asset = next(item for item in decoded["assets"] if item["kind"] == "document")
    assert text_asset["data"] == source["assets"][1]["data"]
    image_asset = next(item for item in decoded["assets"] if item["kind"] == "image")
    with Image.open(BytesIO(image_asset["data"])) as image:
        assert image.format == "JPEG" and image.size == (32, 24)
        assert not image.getexif() and not image.info.get("comment")
    assert image_asset["thumbnail_data"].startswith(b"\xff\xd8")
    repeated = await preview_import(c, decoded, format=format, device_id=c.device["id"])
    assert repeated["counts"]["create"] == 0 and repeated["counts"]["update"] == 0
    assert repeated["counts"]["skip"] == 2
    assert await active_rules(c) == imported and private_media == objects
    await assert_no_effects(c, calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["add_missing", "merge", "exact"])
async def test_backup_restore_modes_are_previewed_and_exact_requires_safety_snapshot(chatbot_case, private_media, mode):
    c = chatbot_case
    calls = list(c.bridge.calls)
    original = await rule_create(c, definition(keyword="Mantida", value="Valor do backup"))
    missing = await rule_create(c, definition(keyword="Ausente", value="Restaurada"))
    source_backup = await backup(c)
    changed = await edit(c, original, definition(keyword="Mantida", value="Edição local"))
    assert changed.status_code == 200, changed.text
    await delete_rule(c, missing)
    await rule_create(c, definition(keyword="Extra", value="Apenas local"))
    before = await active_rules(c)
    preview_response = await c.client.post(c.chat + f"/backups/{source_backup['id']}/preview",
                                         json={"mode": mode, "device_id": None})
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()["preview"]
    counts = preview["counts"]
    assert counts["create"] == 1 and counts["update"] == (0 if mode == "add_missing" else 1)
    assert counts["delete"] == (1 if mode == "exact" else 0)
    assert await active_rules(c) == before
    if mode == "exact":
        refused = await apply(c, preview)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "CHATBOT_RESTORE_CONFIRMATION", refused.text
        assert await active_rules(c) == before
    queued = await apply(c, preview, confirmation="RESTORE" if mode == "exact" else None)
    assert queued.status_code == 202, queued.text
    outcome, job = await run_job(c, queued.json()["job"])
    assert outcome == "succeeded" and job["status"] == "succeeded", job
    restored = {row["keyword"]: row for row in await active_rules(c)}
    assert set(restored) == ({"Mantida", "Ausente"} if mode == "exact" else {"Mantida", "Ausente", "Extra"})
    expected = "Edição local" if mode == "add_missing" else "Valor do backup"
    assert restored["Mantida"]["response"]["actions"][0]["text"] == expected
    assert restored["Ausente"]["response"]["actions"][0]["text"] == "Restaurada"
    if mode == "exact":
        assert job["safety_backup_id"] is not None
        safety = await c.client.get(c.chat + f"/backups/{job['safety_backup_id']}/download")
        assert safety.status_code == 200, safety.text
        saved = codec.read_archive(safety.content, "safety.zip")
        assert {item["definition"]["keyword"] for item in saved["rules"]} == {"Mantida", "Extra"}
        local = next(item for item in saved["rules"] if item["definition"]["keyword"] == "Mantida")
        assert local["definition"]["response"]["actions"][0]["text"] == "Edição local"
    await assert_no_effects(c, calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "expired", "expected_revision"])
async def test_preview_stale_or_expired_never_enqueues_mutations(chatbot_case, change):
    c = chatbot_case
    preview = await preview_import(c, portable_document())
    if change == "revision":
        await rule_create(c, definition(keyword="Mudança posterior"))
    elif change == "expired":
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE subscription_chatbot_previews SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                             {"id": UUID(preview["id"])})
    before = await active_rules(c)
    result = await apply(c, preview, expected=c.chat_revision + 1 if change == "expected_revision" else c.chat_revision)
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] in {"CHATBOT_PREVIEW_STALE", "CHATBOT_PREVIEW_EXPIRED", "CHATBOT_CONFLICT"}
    assert await active_rules(c) == before
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_chatbot_jobs WHERE tenant_id=:t AND kind='restore'"),
                                {"t": c.tenant})).scalar_one() == 0


async def revoke(c, kind):
    async with c.ctx.migration.begin() as db:
        if kind == "owner":
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                             {"t": c.tenant, "a": c.ctx.actor.account_id})
        elif kind == "niche":
            await db.execute(text("""UPDATE business_onboarding SET configuration=jsonb_set(
                configuration,'{niche}','"barbearia"'::jsonb) WHERE tenant_id=:t"""), {"t": c.tenant})
        elif kind == "revision":
            await db.execute(text("UPDATE subscription_chatbot_state SET revision=revision+1 WHERE tenant_id=:t"), {"t": c.tenant})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "niche", "revision"])
async def test_restore_worker_rechecks_authority_and_revision_after_enqueue(chatbot_case, change):
    c = chatbot_case
    await rule_create(c, definition(keyword="Preservada"))
    preview = await preview_import(c, portable_document())
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    before = await active_rules(c)
    await revoke(c, change)
    outcome, job = await run_job(c, queued.json()["job"])
    assert outcome == "failed" and job["status"] == "failed", job
    assert await active_rules(c) == before
    assert job["safety_backup_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "niche"])
async def test_restore_rechecks_after_media_storage_before_publishing_rules(chatbot_case, private_media, monkeypatch, change):
    c = chatbot_case
    preview = await preview_import(c, portable_document(with_media=True))
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    before = await active_rules(c)
    original_put = media.put_media
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def blocked_put(tenant, asset, prepared):
        original_put(tenant, asset, prepared)
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(10):
            raise RuntimeError("Synthetic storage release timed out")
    monkeypatch.setattr(media, "put_media", blocked_put)
    task = asyncio.create_task(run_job(c, queued.json()["job"]))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        await revoke(c, change)
    finally:
        release.set()
        outcome, job = await asyncio.wait_for(task, timeout=10)
    assert outcome == "failed" and job["status"] == "failed", job
    assert await active_rules(c) == before
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_chatbot_assets WHERE tenant_id=:t AND status='ready'"),
                                {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_restore_quota_is_rechecked_before_media_and_keeps_rules_atomic(chatbot_case, private_media, monkeypatch):
    c = chatbot_case
    await rule_create(c, definition(keyword="Preservada"))
    preview = await preview_import(c, portable_document(with_media=True))
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    before = await active_rules(c)
    monkeypatch.setattr(store, "MAX_TENANT_MEDIA_BYTES", 1)
    outcome, job = await run_job(c, queued.json()["job"])
    assert outcome == "failed" and job["status"] == "failed", job
    assert job["error_code"] == "CHATBOT_MEDIA_QUOTA"
    assert private_media == {} and await active_rules(c) == before


@pytest.mark.asyncio
async def test_enqueued_restore_recovers_stale_worker_after_preview_expiry_once(chatbot_case, private_media):
    from app import subscription_chatbot_archive as archive
    c = chatbot_case
    preview = await preview_import(c, portable_document(with_media=True))
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    job = queued.json()["job"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_chatbot_previews SET expires_at=clock_timestamp()-interval '1 minute' WHERE id=:id"),
                         {"id": UUID(preview["id"])})
        await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='running',
            started_at=clock_timestamp()-interval '11 minutes',
            payload=jsonb_set(payload,'{worker_nonce}',to_jsonb(CAST(:nonce AS text))) WHERE id=:id"""),
            {"id": UUID(job["id"]), "nonce": str(uuid4())})
    outcome, saved = await run_job(c, job)
    assert outcome == "succeeded" and saved["status"] == "succeeded", saved
    rows, revision, objects = await active_rules(c), c.chat_revision, dict(private_media)
    assert len(rows) == 2 and len(objects) == 3
    assert await archive.process_restore(c.tenant, UUID(job["id"])) == "succeeded"
    assert await active_rules(c) == rows and await current_revision(c) == revision and private_media == objects


@pytest.mark.asyncio
async def test_restore_with_lost_private_preview_payload_becomes_terminal_without_retry_loop(chatbot_case):
    from app import subscription_chatbot_archive as archive
    c = chatbot_case
    preview = await preview_import(c, portable_document())
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    job = queued.json()["job"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_chatbot_previews SET snapshot='{}'::jsonb WHERE id=:id"),
                         {"id": UUID(preview["id"])})
    outcome, saved = await run_job(c, job)
    assert outcome == "failed" and saved["status"] == "failed"
    assert saved["error_code"] == "CHATBOT_PREVIEW_STALE"
    assert await active_rules(c) == []
    assert await archive.process_restore(c.tenant, UUID(job["id"])) == "failed"


@pytest.mark.asyncio
async def test_backup_preserves_inactive_dependency_and_unrelated_import_still_works(chatbot_case):
    c = chatbot_case
    calls = list(c.bridge.calls)
    leaf = await rule_create(c, definition(keyword="Dependência"))
    parent = await rule_create(c, definition(keyword="Pai ativo", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "nested_reply", "rule_id": leaf["id"], "delay_seconds": 1}]}))
    disabled = await edit(c, leaf, definition(keyword="Dependência", enabled=False))
    assert disabled.status_code == 200, disabled.text
    source = await backup(c)
    downloaded = await c.client.get(source["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    document = codec.read_archive(downloaded.content, "snapshot.zip")
    flags = {item["definition"]["keyword"]: item["definition"]["enabled"] for item in document["rules"]}
    assert flags == {"Dependência": False, "Pai ativo": True}
    unrelated = await preview_import(c, portable_document())
    queued = await apply(c, unrelated)
    assert queued.status_code == 202, queued.text
    assert (await run_job(c, queued.json()["job"]))[0] == "succeeded"
    previewed = await c.client.post(c.chat + f"/backups/{source['id']}/preview", json={"mode": "exact", "device_id": None})
    assert previewed.status_code == 200, previewed.text
    queued = await apply(c, previewed.json()["preview"], confirmation="RESTORE")
    assert queued.status_code == 202, queued.text
    assert (await run_job(c, queued.json()["job"]))[0] == "succeeded"
    restored = {row["keyword"]: row for row in await active_rules(c)}
    assert set(restored) == {"Dependência", "Pai ativo"}
    assert restored["Dependência"]["enabled"] is False and restored["Pai ativo"]["enabled"] is True
    assert restored["Pai ativo"]["response"]["actions"][0]["rule_id"] == str(restored["Dependência"]["id"])
    await assert_no_effects(c, calls)


@pytest.mark.asyncio
async def test_archive_routes_reject_oversized_invalid_and_foreign_content(chatbot_case, private_media):
    from app import subscription_chatbot_schemas as schemas
    c = chatbot_case
    too_large = await c.client.post(c.chat + "/imports/preview", content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": str(schemas.IMPORT_BODY_BYTES + 1)})
    assert too_large.status_code == 413
    invalid = await c.client.post(c.chat + "/imports/preview", json={
        "filename": "orion.json", "content_base64": base64.b64encode(b'{"format":"botbot"}').decode()})
    assert invalid.status_code == 422, invalid.text
    row = await rule_create(c)
    reserve = await c.client.post(c.chat + "/exports", json={"ids": [row["id"]], "format": "json"})
    assert reserve.status_code == 200, reserve.text
    export = reserve.json()["export"]
    foreign = await c.client.get(export["url"].replace(str(c.tenant), str(c.ctx.b)))
    assert foreign.status_code in {403, 404}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as anonymous:
        denied = await anonymous.get(export["url"])
        assert denied.status_code == 401
    other_id = await c.client.post(c.chat + "/exports", json={"ids": [str(uuid4())], "format": "json"})
    assert other_id.status_code == 404, other_id.text
    assert private_media == {} and len(await active_rules(c)) == 1


@pytest.mark.asyncio
async def test_daily_backup_is_deduplicated_and_preserves_first_snapshot(chatbot_case):
    from app import subscription_chatbot_archive as archive
    c = chatbot_case
    row = await rule_create(c, definition(keyword="Diário", value="Primeiro snapshot"))
    results = await asyncio.gather(archive.scheduled_backup(c.tenant), archive.scheduled_backup(c.tenant))
    assert sorted(results) == [False, True]
    changed = await edit(c, row, definition(keyword="Diário", value="Mudança após backup"))
    assert changed.status_code == 200
    assert await archive.scheduled_backup(c.tenant) is False
    listing = await c.client.get(c.chat + "/backups")
    assert listing.status_code == 200 and listing.json()["total"] == 1, listing.text
    item, = listing.json()["items"]
    assert item["source"] == "scheduled" and item["rules_count"] == 1
    downloaded = await c.client.get(item["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    snapshot = codec.read_archive(downloaded.content, "diario.zip")
    assert snapshot["rules"][0]["definition"]["response"]["actions"][0]["text"] == "Primeiro snapshot"
    async with c.ctx.migration.connect() as db:
        rows = (await db.execute(text("SELECT source,backup_day FROM subscription_chatbot_backups WHERE tenant_id=:t"),
                                {"t": c.tenant})).mappings().all()
        assert len(rows) == 1 and rows[0]["backup_day"] is not None
