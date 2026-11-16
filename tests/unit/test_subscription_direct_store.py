"""Direct captures use private assets and never create a hidden template."""
from copy import deepcopy
from uuid import UUID

from fastapi import HTTPException
import pytest

from app import subscription_direct_store as store
from test_subscription_bulk_policy import ACTOR, KEY, NOW, PHONE, TENANT, Rows
from test_subscription_bulk_store import CaptureDB, setup_capture
from test_subscription_direct_policy import ASSET, media, message, parse


def asset_row(kind="audio", **changes):
    descriptor = media(kind)[1]
    return {key: value for key, value in descriptor.items() if key not in {"file_name", "thumbnail"}} | {
        "id": ASSET, "tenant_id": TENANT, "status": "ready", "thumbnail_size": None,
        "thumbnail_mime": None, "thumbnail_sha256": None} | changes


class MediaDB(CaptureDB):
    def __init__(self, asset=None, permissions=()):
        super().__init__(permissions)
        self.asset = asset

    async def execute(self, query, params=None):
        if "FROM subscription_chatbot_assets" in str(query):
            self.calls.append((str(query), params))
            assert params["t"] == TENANT and params["id"] == ASSET and "status='ready'" in str(query)
            return Rows([self.asset] if self.asset else [])
        return await super().execute(query, params)


@pytest.mark.asyncio
async def test_raw_audio_review_pins_the_private_asset_and_returns_one_literal_part_without_template(monkeypatch):
    setup_capture(monkeypatch)
    value, _ = media(caption="Não enviar esta legenda {name}")
    db = MediaDB(asset_row())
    snapshot = await store.capture_review(db, TENANT, ACTOR, value, KEY)
    snapshot["review_request_id"] = str(KEY)
    row, = snapshot["rows"]
    assert row["context"]["template"] is None and row["context"]["media"]["id"] == str(ASSET)
    assert row["plan"] == [{"type": "audio", "asset": row["context"]["media"], "caption": "", "file_name": "arquivo.mp3"}]
    assert snapshot["save_template"] is None and snapshot["template_library_revision"] is None
    preview = store.review_preview(snapshot, KEY, NOW)
    assert preview["kind"] == "media" and preview["save_template"] is None
    assert preview["warnings"] == ["AUDIO_CAPTION_OMITTED"]
    assert preview["items"][0]["message_type"] == "media" and preview["items"][0]["template"] is None
    assert preview["items"][0]["message_preview"]["parts"] == [{"type": "audio", "text": "", "file_name": "arquivo.mp3"}]
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", [None, asset_row() | {"kind": "sticker"},
    asset_row(mime="application/octet-stream"), asset_row(size=0), asset_row(size=16_000_001)])
async def test_missing_unready_or_incompatible_asset_never_reaches_a_send_snapshot(monkeypatch, asset):
    setup_capture(monkeypatch)
    db = MediaDB(asset)
    with pytest.raises(HTTPException) as error:
        await store.capture_review(db, TENANT, ACTOR, media()[0], KEY)
    assert error.value.status_code == 422 and len(db.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked,verified", [(True, False), (False, True)])
async def test_direct_manual_consent_does_not_bypass_optout_or_device_own_number(monkeypatch, revoked, verified):
    setup_capture(monkeypatch, verified=verified)
    db = CaptureDB([{"phone": PHONE, "version": 5, "revoked": revoked}])
    snapshot = await store.capture_review(db, TENANT, ACTOR, parse(message()), KEY)
    assert snapshot["counts"]["eligible"] == 0 and snapshot["counts"]["blocked"] == 1
    assert snapshot["rows"][0]["blocked_reason"] == ("BULK_OPTED_OUT" if revoked else "BULK_SELF_CONTACT")
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_save_template_review_validates_library_and_pins_revision_without_writing(monkeypatch):
    setup_capture(monkeypatch)
    state, calls = {"revision": 7}, []
    async def workspace(db, tenant, **kwargs): calls.append(kwargs["feature"])
    async def writable(*args): pass
    async def graph(*args): return {}, {}, {}
    async def revision(*args): return state["revision"]
    monkeypatch.setattr(store.shared, "workspace", workspace)
    monkeypatch.setattr(store.shared, "writable", writable)
    monkeypatch.setattr(store.shared, "load_graph", graph)
    monkeypatch.setattr(store.shared, "revision", revision)
    db, value = CaptureDB(), parse(message(save_template={"name": "Modelo"}))
    first = await store.capture_review(db, TENANT, ACTOR, value, KEY)
    assert first["template_library_revision"] == 7 and first["save_template"]["name"] == "Modelo"
    assert first["rows"][0]["context"]["template"] is None
    assert first["rows"][0]["plan"][0]["text"] == value["text"]
    assert calls == ["templates.manage"] and all(sql.startswith("SELECT") for sql, _ in db.calls)
    state["revision"] = 8
    second = await store.capture_review(db, TENANT, ACTOR, value, KEY)
    assert store.fingerprint(first) != store.fingerprint(second)


@pytest.mark.asyncio
async def test_optional_save_cannot_bypass_library_feature_or_validate_unknown_tags_late(monkeypatch):
    setup_capture(monkeypatch)
    db = CaptureDB()
    async def denied(*args, **kwargs): raise HTTPException(403, "templates disabled")
    monkeypatch.setattr(store.shared, "workspace", denied)
    with pytest.raises(HTTPException) as error:
        await store.capture_review(db, TENANT, ACTOR, parse(message(save_template={"name": "Modelo"})), KEY)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await store.capture_review(db, TENANT, ACTOR,
            parse(message(text='{"literal":true}', save_template={"name": "Modelo"})), KEY)
    assert error.value.status_code == 422 and error.value.detail["code"] == "DIRECT_TEMPLATE_INVALID"


@pytest.mark.asyncio
async def test_selected_template_uses_existing_personalization_without_becoming_raw_media(monkeypatch):
    setup_capture(monkeypatch)
    template_id = UUID("12345678-1234-5678-1234-567812345678")
    template = {"id": str(template_id), "name": "Saudação", "revision": 1, "kind": "text",
                "body": "Olá {name} — {my_name}", "followup_body": ""}
    async def selected(*args): return deepcopy(template)
    monkeypatch.setattr(store.bulk, "template_snapshot", selected)
    raw = message()
    raw.pop("text")
    value = parse(raw | {"kind": "template", "template_id": str(template_id)})
    snapshot = await store.capture_review(CaptureDB(), TENANT, ACTOR, value, KEY)
    row, = snapshot["rows"]
    assert row["context"]["template"] == template and "media" not in row["context"]
    assert row["plan"] == [{"type": "text", "text": "Olá Contato — Empresa"}]


@pytest.mark.asyncio
async def test_direct_literal_snapshot_preserves_edge_spaces_and_newlines(monkeypatch):
    setup_capture(monkeypatch)
    original = "  Olá {name}\n\nMensagem com espaços  \n"
    snapshot = await store.capture_review(CaptureDB(), TENANT, ACTOR, parse(message(text=original)), KEY)
    assert snapshot["rows"][0]["plan"] == [{"type": "text", "text": original}]
