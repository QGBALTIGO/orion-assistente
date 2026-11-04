"""Template library over real auth/PostgreSQL, synthetic media and private storage.

The normalizers and HTTP routes are real. Only S3 and the existing synthetic
WhatsApp bridge are fakes; management and previews must never execute messages,
external URL actions or ASR. All devices start disconnected.
"""
import asyncio
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event
from uuid import UUID, uuid4
import wave
import zipfile

import asyncpg
import httpx
from PIL import Image
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import subscription_chatbot_media as media, subscription_chatbot_store as store
from app.plan_routing import include_plan_router
from test_subscription_chatbot_postgres import (
    accepted, chat_effects, chatbot_case, definition, device_case,
    disposable_environment, edit, grant, input_event, media_headers, new_owner_http,
    onboarding_db, pending, private_media, rule_create,
)  # noqa: F401 - explicit transitive fixture imports are required by pytest.
from test_subscription_chatbot_test_postgres import isolated as isolated_simulation, revoke as revoke_chatbot, simulate
from test_subscription_chatbot_release_postgres import isolated_settings  # noqa: F401
from test_subscription_devices_postgres import action

pytestmark = pytest.mark.integration
CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_templates_contract.json").read_text())
LIVE_TABLES = ("subscription_device_operations", "subscription_device_events", "subscription_device_effects",
               "subscription_device_cooldowns", "subscription_device_audio_usage", "subscription_chatbot_runs",
               "subscription_chatbot_cooldowns", "subscription_chatbot_selections")


@pytest_asyncio.fixture
async def templates_case(chatbot_case, monkeypatch):
    from app import subscription_templates, subscription_chatbot_test
    c = chatbot_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, subscription_templates.router)
    include_plan_router(c.app, subscription_chatbot_test.router)
    c.app.router.routes.extend(generic)
    c.templates = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/templates"
    c.simulator = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/chatbot-test"
    c.simulation_context = str(uuid4())
    # Advertise only the fake private store used by private_media below.
    for key in ("ENDPOINT", "BUCKET", "REGION", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.setenv("ORION_MEDIA_S3_" + key, "synthetic-template-" + key.lower())
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE qr_channels SET enabled=false,lease_owner=NULL,
            lease_expires_at=NULL WHERE tenant_id=:t"""), {"t": c.tenant})
        await db.execute(text("UPDATE subscription_devices SET status='disconnected' WHERE tenant_id=:t"), {"t": c.tenant})
    c.bridge.connections.clear()
    yield c


def template_definition(**changes):
    return deepcopy(CONTRACT["definition_defaults"]) | {"name": "Modelo sintético", "text": "Olá, {name}!"} | changes


def versioned(c, **fields):
    return {"request_id": str(uuid4()), "expected_chatbot_revision": c.chat_revision, **fields}


def assert_code(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code, response.text


async def create_template(c, value=None, *, request=None):
    payload = request or versioned(c, template=value or template_definition())
    response = await c.client.post(c.templates, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["request_id"] == payload["request_id"]
    c.chat_revision = result["chatbot_revision"]
    return result["template"]


async def save_template(c, row, value, **overrides):
    payload = versioned(c, expected_revision=row["revision"], template=value) | overrides
    response = await c.client.put(c.templates + "/" + row["id"], json=payload)
    if response.status_code == 200:
        c.chat_revision = response.json()["chatbot_revision"]
    return response


async def remove_template(c, row, *, request=None):
    payload = request or versioned(c, expected_revision=row["revision"])
    response = await c.client.request("DELETE", c.templates + "/" + row["id"], json=payload)
    if response.status_code == 200:
        c.chat_revision = response.json()["chatbot_revision"]
    return response


async def upload(c, raw, kind, filename, *, request=None):
    payload = request or versioned(c, kind=kind, filename=filename,
                                 content_base64=base64.b64encode(raw).decode())
    response = await c.client.post(c.templates + "/assets", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["request_id"] == payload["request_id"]
    assert result["chatbot_revision"] == c.chat_revision  # File preparation is not a rule edit.
    return result


async def live_state(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in LIVE_TABLES:
            result[table] = (await db.execute(text(f"""SELECT COALESCE(
                jsonb_agg(to_jsonb(entry) ORDER BY to_jsonb(entry)::text),'[]'::jsonb)
                FROM {table} AS entry WHERE tenant_id=:t"""), {"t": c.tenant})).scalar_one()
    return result


async def revoke(c, kind):
    if kind != "revision":
        return await revoke_chatbot(c, kind)
    # Preparing a file intentionally does not create or bump the chatbot
    # state. Make this concurrent edit real even in an otherwise empty library.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_chatbot_state(tenant_id,revision)
            VALUES(:t,:revision) ON CONFLICT(tenant_id) DO UPDATE
            SET revision=subscription_chatbot_state.revision+1"""),
            {"t": c.tenant, "revision": c.chat_revision + 1})


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (64, 40), (31, 89, 137)).save(buffer, format="PNG")
    return buffer.getvalue()


def wav_bytes():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return buffer.getvalue()


def pdf_bytes():
    # A complete one-page PDF with actual object offsets, root and xref.
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> /Contents 4 0 R >>",
               b"<< /Length 0 >>\nstream\n\nendstream"]
    for number, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def package_bytes(kind):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if kind == "apk":
            archive.writestr("AndroidManifest.xml", '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.synthetic"><application android:label="Synthetic" /></manifest>')
        else:
            part = "word/document.xml" if kind == "docx" else "xl/workbook.xml"
            mime = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" if kind == "docx"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/' + part + '" ContentType="' + mime + '"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="' + part + '"/></Relationships>')
            archive.writestr(part, '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic</w:t></w:r></w:p></w:body></w:document>' if kind == "docx" else '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>')
    return buffer.getvalue()


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "The dedicated real-media gate must install FFmpeg and FFprobe"
    path = tmp_path_factory.mktemp("synthetic-templates") / "sample.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x100:rate=10", "-t", "1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", str(path)],
                   check=True, timeout=15)
    return path.read_bytes()


@pytest.mark.asyncio
async def test_library_crud_is_idempotent_searchable_and_works_without_connected_device(templates_case):
    c = templates_case
    before, calls = await live_state(c), list(c.bridge.calls)
    listing = await c.client.get(c.templates)
    assert listing.status_code == 200, listing.text
    assert listing.json()["counts"] == {"total": 0, "filtered": 0, "limit": 100}
    assert listing.json()["capabilities"]["manage"] is True
    assert listing.json()["limits"] == CONTRACT["list_response"]["limits"]
    payload = versioned(c, template=template_definition(name="", text="🙂" * 500))
    row = await create_template(c, request=payload)
    assert row["name"] == "Texto sem nome" and row["text"] == "🙂" * 500
    assert row["revision"] == 1 and row["dependent_count"] == 0
    replay = await c.client.post(c.templates, json=payload)
    assert replay.status_code == 201 and replay.json()["template"] == row
    changed = await c.client.post(c.templates, json=payload | {"template": template_definition(text="Outro")})
    assert_code(changed, 409, "CHATBOT_CONFLICT")
    update = versioned(c, expected_revision=row["revision"], template=template_definition(name="Boas-vindas", text="Mensagem revista"))
    saved = await save_template(c, row, update["template"], **{key: value for key, value in update.items() if key != "template"})
    assert saved.status_code == 200, saved.text
    updated = saved.json()["template"]
    repeated = await c.client.put(c.templates + "/" + row["id"], json=update)
    assert repeated.status_code == 200 and repeated.json() == saved.json()
    assert updated["id"] == row["id"] and updated["revision"] == 2
    found = await c.client.get(c.templates, params={"q": "BOAS"})
    assert found.status_code == 200 and found.json()["counts"] == {"total": 1, "filtered": 1, "limit": 100}
    missing = await c.client.get(c.templates, params={"q": "inexistente"})
    assert missing.status_code == 200 and missing.json()["counts"]["filtered"] == 0
    catalog = await c.client.get(c.chat + "/catalog")
    assert catalog.status_code == 200 and catalog.json()["templates"] == [{
        "id": row["id"], "revision": 2, "name": "Boas-vindas", "kind": "text", "available": True}]
    deletion = versioned(c, expected_revision=updated["revision"])
    deleted = await remove_template(c, updated, request=deletion)
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 1
    repeated_delete = await c.client.request("DELETE", c.templates + "/" + row["id"], json=deletion)
    assert repeated_delete.status_code == 200 and repeated_delete.json() == deleted.json()
    assert (await c.client.get(c.templates + "/" + row["id"])).status_code == 404
    assert (await c.client.get(c.templates)).json()["counts"]["total"] == 0
    assert await live_state(c) == before and c.bridge.calls == calls


@pytest.mark.asyncio
async def test_library_limit_counts_all_active_templates_and_frees_only_logical_deletion(templates_case):
    c = templates_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_chatbot_templates(tenant_id,origin_id,name,kind,body)
            SELECT :t,gen_random_uuid(),'Modelo '||number,'text','Texto sintético'
            FROM generate_series(1,99) number"""), {"t": c.tenant})
    last = await create_template(c)
    blocked = await c.client.post(c.templates, json=versioned(c, template=template_definition()))
    assert_code(blocked, 409, "TEMPLATE_LIMIT")
    assert (await c.client.get(c.templates)).json()["counts"]["total"] == 100
    assert (await remove_template(c, last)).status_code == 200
    replacement = await create_template(c, template_definition(name="Substituto"))
    assert replacement["id"] != last["id"]
    assert (await c.client.get(c.templates)).json()["counts"]["total"] == 100
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_chatbot_templates WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 101


@pytest.mark.asyncio
async def test_concurrent_creates_with_one_revision_cannot_overwrite_each_other(templates_case):
    c = templates_case
    first, second = await asyncio.gather(*(c.client.post(c.templates, json=versioned(c,
        template=template_definition(name=name))) for name in ("Primeiro", "Segundo")))
    assert sorted((first.status_code, second.status_code)) == [201, 409], (first.text, second.text)
    failed = first if first.status_code == 409 else second
    assert_code(failed, 409, "CHATBOT_CONFLICT")
    listing = await c.client.get(c.templates)
    assert listing.json()["counts"]["total"] == 1 and listing.json()["chatbot_revision"] == 2


@pytest.mark.asyncio
async def test_strict_definition_boundaries_and_immutable_kind_fail_before_mutation(templates_case):
    c = templates_case
    invalid = [template_definition(text=""), template_definition(text="🙂" * 500 + "x"),
               template_definition(name="🙂" * 50 + "x"), template_definition(text="Olá {unknown}"),
               template_definition(text="a\x00b"),
               template_definition(kind="location", text="", location={"latitude": 90.1, "longitude": 0}),
               template_definition(kind="location", text="", location={"latitude": 0, "longitude": -180.1})]
    for value in invalid:
        failed = await c.client.post(c.templates, json=versioned(c, template=value))
        assert failed.status_code == 422, failed.text
    assert (await c.client.get(c.templates)).json()["counts"]["total"] == 0
    row = await create_template(c)
    wrong_revision = await save_template(c, row, template_definition(), expected_revision=2)
    assert_code(wrong_revision, 409, "CHATBOT_CONFLICT")
    changed_kind = await save_template(c, row, template_definition(kind="location", text="",
        location={"latitude": -22.9068, "longitude": -43.1729}))
    assert_code(changed_kind, 409, "TEMPLATE_KIND_IMMUTABLE")
    stale = await save_template(c, row, template_definition(), expected_chatbot_revision=c.chat_revision - 1)
    assert_code(stale, 409, "CHATBOT_CONFLICT")
    detail = await c.client.get(c.templates + "/" + row["id"])
    assert detail.json()["template"] == row and detail.json()["chatbot_revision"] == c.chat_revision


@pytest.mark.asyncio
async def test_template_routes_enforce_real_session_csrf_and_tenant_ownership(templates_case):
    c = templates_case
    row = await create_template(c)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as anonymous:
        assert (await anonymous.get(c.templates)).status_code == 401
    missing_csrf = await c.client.post(c.templates, headers={"x-orion-csrf": ""}, json=versioned(c, template=template_definition()))
    assert missing_csrf.status_code == 403
    foreign = c.templates.replace(str(c.tenant), str(c.ctx.b))
    assert (await c.client.get(foreign + "/" + row["id"])).status_code in {403, 404}
    assert (await c.client.get(c.templates + "/" + str(uuid4()))).status_code == 404
    assert (await c.client.get(c.templates)).json()["counts"]["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_each_library_operation_rechecks_owner_and_subscription_niche(templates_case, change):
    c = templates_case
    row = await create_template(c)
    await revoke(c, change)
    for response in [await c.client.get(c.templates), await c.client.get(c.templates + "/" + row["id"]),
                     await c.client.post(c.templates, json=versioned(c, template=template_definition())),
                     await save_template(c, row, template_definition()), await remove_template(c, row)]:
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_referenced_deletion_includes_inactive_sequences_and_list_rows_without_cascade(templates_case):
    c = templates_case
    row = await create_template(c)
    ref = {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1}
    inactive = await rule_create(c, definition(keyword="inativa", enabled=False,
        response={"kind": "sequence", "actions": [ref]}))
    menu = await rule_create(c, definition(keyword="menu", response={"kind": "list", "title": "Lista",
        "description": "Escolha", "footer": "", "button_text": "Ver", "sections": [{"id": str(uuid4()),
        "title": "Opções", "rows": [{"id": str(uuid4()), "title": "Template", "description": "",
                                    "action": ref | {"id": str(uuid4())}}]}]}))
    detail = await c.client.get(c.templates + "/" + row["id"])
    assert detail.status_code == 200 and detail.json()["template"]["dependent_count"] == 2
    assert {item["id"] for item in detail.json()["dependents"]} == {inactive["id"], menu["id"]}
    denied = await remove_template(c, row)
    assert_code(denied, 409, "TEMPLATE_IN_USE")
    assert (await c.client.get(c.chat + "/rules/" + inactive["id"])).status_code == 200
    for rule in (inactive, menu):
        result = await c.client.request("DELETE", c.chat + "/rules/" + rule["id"], json={
            "idempotency_key": str(uuid4()), "expected_chatbot_revision": c.chat_revision,
            "expected_revision": rule["revision"]})
        assert result.status_code == 200, result.text
        c.chat_revision = result.json()["chatbot_revision"]
    assert (await remove_template(c, row)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["png", "pdf", "docx", "xlsx", "csv", "txt", "apk", "wav", "mp4", "sticker", "ptv"])
async def test_real_supported_file_formats_are_private_idempotent_and_usable_templates(templates_case, private_media, video_bytes, format):
    c = templates_case
    cases = {
        "png": ("image", "imagem.png", png_bytes(), "image/jpeg"),
        "pdf": ("document", "arquivo.pdf", pdf_bytes(), "application/pdf"),
        "docx": ("document", "arquivo.docx", package_bytes("docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "xlsx": ("document", "arquivo.xlsx", package_bytes("xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "csv": ("document", "dados.csv", "nome,valor\nExemplo,1\n".encode(), "text/plain; charset=utf-8"),
        "txt": ("document", "orientações.txt", "Conteúdo integral.\nSegunda linha.\n".encode(), "text/plain; charset=utf-8"),
        "apk": ("document", "aplicativo.apk", package_bytes("apk"), "application/vnd.android.package-archive"),
        "wav": ("audio", "audio.wav", wav_bytes(), "audio/mpeg"),
        "mp4": ("video", "video.mp4", video_bytes, "video/mp4"),
        "sticker": ("sticker", "figurinha.png", png_bytes(), "image/webp"),
        "ptv": ("ptv", "recado.mp4", video_bytes, "video/mp4"),
    }
    kind, filename, raw, mime = cases[format]
    before, calls = await live_state(c), list(c.bridge.calls)
    request = versioned(c, kind=kind, filename=filename, content_base64=base64.b64encode(raw).decode())
    uploaded = await upload(c, raw, kind, filename, request=request)
    asset, objects = uploaded["asset"], dict(private_media)
    assert asset["kind"] == kind and asset["mime"] == mime and asset["size"] > 0
    repeated = await upload(c, raw, kind, filename, request=request)
    assert repeated == uploaded and private_media == objects
    # A new request for identical normalized content reuses its immutable asset.
    duplicate = await upload(c, raw, kind, filename)
    assert duplicate["asset"]["id"] == asset["id"] and private_media == objects
    response = await c.client.get(asset["content_url"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith(mime)
    assert len(response.content) == asset["size"] and sha256(response.content).hexdigest() == asset["sha256"]
    if kind == "document":
        assert response.content == raw
        assert response.headers["content-disposition"].startswith("attachment;")
    if kind in {"image", "sticker"}:
        with Image.open(BytesIO(response.content)) as image:
            image.load()
            assert image.format == ("WEBP" if kind == "sticker" else "JPEG")
            assert not image.getexif() and not image.info.get("comment")
    if kind == "audio":
        assert asset["duration_ms"] >= 900
        assert asset["thumbnail"] is None and asset["width"] is None and asset["height"] is None
        assert uploaded["file_name"] == "audio.mp3"
    if kind == "ptv":
        assert asset["width"] == asset["height"] == 512 and asset["duration_ms"] <= 60000
    if asset["thumbnail_url"]:
        thumbnail = await c.client.get(asset["thumbnail_url"])
        assert thumbnail.status_code == 200 and thumbnail.content.startswith(b"\xff\xd8")
    row = await create_template(c, template_definition(name="Mídia " + format, kind=kind, asset_id=asset["id"],
        text="" if kind in {"sticker", "ptv"} else "Legenda", file_name=uploaded["file_name"]))
    assert row["asset"]["id"] == asset["id"] and row["file_name"] == uploaded["file_name"]
    assert_code(await c.client.get(asset["content_url"]), 409, "CHATBOT_CONFLICT")
    fresh = await c.client.get(row["asset"]["content_url"])
    assert fresh.status_code == 200 and fresh.content == response.content
    async with c.ctx.migration.connect() as db:
        count = (await db.execute(text("SELECT count(*) FROM subscription_chatbot_assets WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one()
        assert count == 1
    assert await live_state(c) == before and c.bridge.calls == calls


@pytest.mark.asyncio
async def test_upload_rejects_cross_tenant_or_wrong_kind_assets_and_enforces_durable_quota(templates_case, private_media, monkeypatch):
    c = templates_case
    uploaded = await upload(c, png_bytes(), "image", "imagem.png")
    identity = uploaded["asset"]["id"]
    bad_kind = await c.client.post(c.templates, json=versioned(c, template=template_definition(
        kind="audio", text="", asset_id=identity)))
    assert_code(bad_kind, 422, "TEMPLATE_ASSET_INVALID")
    unknown = await c.client.post(c.templates, json=versioned(c, template=template_definition(
        kind="image", text="", asset_id=str(uuid4()))))
    assert_code(unknown, 422, "TEMPLATE_ASSET_INVALID")
    async with c.ctx.migration.begin() as db:
        foreign_id = uuid4()
        await db.execute(text("""INSERT INTO subscription_chatbot_assets
            (id,tenant_id,kind,mime,size,sha256,width,height,duration_ms,thumbnail_mime,thumbnail_size,thumbnail_sha256,status)
            SELECT :id,:foreign,kind,mime,size,sha256,width,height,duration_ms,thumbnail_mime,thumbnail_size,thumbnail_sha256,status
            FROM subscription_chatbot_assets WHERE id=:source"""),
            {"id": foreign_id, "foreign": c.ctx.b, "source": UUID(identity)})
    other_tenant = await c.client.post(c.templates, json=versioned(c, template=template_definition(
        kind="image", text="", asset_id=str(foreign_id))))
    assert_code(other_tenant, 422, "TEMPLATE_ASSET_INVALID")
    assert other_tenant.json() == unknown.json()
    wrong_file = await c.client.post(c.templates + "/assets", json=versioned(c, kind="document", filename="documento.pdf",
        content_base64=base64.b64encode(png_bytes()).decode()))
    assert wrong_file.status_code == 422, wrong_file.text
    too_large = await c.client.post(c.templates + "/assets", json=versioned(c, kind="sticker", filename="figurinha.png",
        content_base64=base64.b64encode(b"x" * 200001).decode()))
    assert too_large.status_code == 413, too_large.text
    objects = dict(private_media)
    monkeypatch.setattr(store, "MAX_TENANT_MEDIA_BYTES", uploaded["asset"]["size"] + uploaded["asset"]["thumbnail"]["size"])
    quota = await c.client.post(c.templates + "/assets", json=versioned(c, kind="document", filename="novo.txt",
        content_base64=base64.b64encode(b"new synthetic file").decode()))
    assert_code(quota, 413, "CHATBOT_MEDIA_QUOTA")
    assert private_media == objects
    # Reusing a reserved asset consumes no additional quota.
    duplicate = await upload(c, png_bytes(), "image", "repetida.png")
    assert duplicate["asset"]["id"] == identity


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "niche", "revision"])
async def test_upload_reauthorizes_after_storage_before_asset_is_published(templates_case, private_media, monkeypatch, change):
    c = templates_case
    original = media.put_media
    entered, release = Event(), Event()
    def blocked_put(*args, **kwargs):
        original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "Synthetic storage writer was not released"
    monkeypatch.setattr(media, "put_media", blocked_put)
    payload = versioned(c, kind="image", filename="imagem.png", content_base64=base64.b64encode(png_bytes()).decode())
    writing = asyncio.create_task(c.client.post(c.templates + "/assets", json=payload))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.wait_for(revoke(c, change), timeout=3)
        release.set()
        result = await asyncio.wait_for(writing, timeout=5)
        assert result.status_code in {403, 409}, result.text
        async with c.ctx.migration.connect() as db:
            assert (await db.execute(text("SELECT count(*) FROM subscription_chatbot_assets WHERE tenant_id=:t AND status='ready'"), {"t": c.tenant})).scalar_one() == 0
            assert (await db.execute(text("SELECT status FROM subscription_chatbot_jobs WHERE tenant_id=:t AND idempotency_key=:key"),
                {"t": c.tenant, "key": UUID(payload["request_id"])})).scalar_one() == "failed"
    finally:
        release.set()
        if not writing.done():
            writing.cancel()
        await asyncio.gather(writing, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "niche", "revision"])
async def test_preview_reauthorizes_after_private_read_before_returning_bytes(templates_case, private_media, monkeypatch, change):
    c = templates_case
    uploaded = await upload(c, png_bytes(), "image", "imagem.png")
    original = media.get_media
    entered, release = Event(), Event()
    def blocked_get(*args, **kwargs):
        data = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "Synthetic storage reader was not released"
        return data
    monkeypatch.setattr(media, "get_media", blocked_get)
    reading = asyncio.create_task(c.client.get(uploaded["asset"]["content_url"]))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.wait_for(revoke(c, change), timeout=3)
        release.set()
        result = await asyncio.wait_for(reading, timeout=5)
        assert result.status_code in {403, 409}, result.text
        assert not result.content.startswith(b"\xff\xd8")
    finally:
        release.set()
        if not reading.done():
            reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)


@pytest.mark.asyncio
async def test_private_preview_requires_current_session_scope_and_integrity(templates_case, private_media):
    c = templates_case
    uploaded = await upload(c, png_bytes(), "image", "imagem.png")
    url, asset = uploaded["asset"]["content_url"], uploaded["asset"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="https://test") as anonymous:
        assert (await anonymous.get(url)).status_code == 401
    foreign = await c.client.get(url.replace(str(c.tenant), str(c.ctx.b)))
    assert foreign.status_code in {403, 404}
    wrong_asset = await c.client.get(url.replace(asset["id"], str(uuid4())))
    assert_code(wrong_asset, 422, "TEMPLATE_ASSET_INVALID")
    missing_revision = await c.client.get(url.split("?", 1)[0])
    assert missing_revision.status_code == 422
    invalid_variant = await c.client.get(url.replace("variant=original", "variant=../../other"))
    assert invalid_variant.status_code == 422
    # The shared fake asserts integrity itself. Inspect the registry and the
    # stored value here without turning that assertion into an app response.
    stored = private_media[(str(c.tenant), asset["id"], "original")]
    assert len(stored) == asset["size"] and sha256(stored).hexdigest() == asset["sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "audio", "document"])
async def test_media_caption_and_followup_preserve_order_in_simulator_without_live_effects(templates_case, private_media, monkeypatch, kind):
    c = templates_case
    raw, filename = {"image": (png_bytes(), "foto.png"), "audio": (wav_bytes(), "audio.wav"),
                     "document": ("nome,valor\nExemplo,1\n".encode(), "dados.csv")}[kind]
    uploaded = await upload(c, raw, kind, filename)
    row = await create_template(c, template_definition(kind=kind, asset_id=uploaded["asset"]["id"],
        text="Legenda para {name}", followup_text="Depois do arquivo, {name}", file_name=uploaded["file_name"]))
    await rule_create(c, definition(keyword="arquivo", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1}]}))
    async with isolated_simulation(c, monkeypatch):
        result = await simulate(c, "arquivo")
        messages = result["messages"]
        assert [item["type"] for item in messages] == (["media", "text", "text"] if kind == "audio" else ["media", "text"])
        assert messages[0]["kind"] == kind
        assert messages[0]["asset"]["file_name"] == uploaded["file_name"]
        if kind == "audio":
            assert messages[0]["caption"] == ""
            assert messages[1]["text"].startswith("Legenda para ")
        else:
            assert messages[0]["caption"].startswith("Legenda para ")
        assert messages[-1]["text"].startswith("Depois do arquivo, ")
        assert "{name}" not in json.dumps(messages)
        assert [item["delay_seconds"] for item in messages] == [1] + [0] * (len(messages) - 1)
        content = await c.client.get(messages[0]["asset"]["content_url"])
        assert content.status_code == 200 and sha256(content.content).hexdigest() == uploaded["asset"]["sha256"]


@pytest.mark.asyncio
async def test_location_is_numeric_and_never_fetches_a_map_or_sends_a_message(templates_case, monkeypatch):
    c = templates_case
    row = await create_template(c, template_definition(name="Local sintético", kind="location", text="",
        location={"latitude": -22.9068, "longitude": -43.1729}))
    await rule_create(c, definition(keyword="local", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1}]}))
    async with isolated_simulation(c, monkeypatch):
        result = await simulate(c, "local")
        message, = result["messages"]
        assert message["type"] == "location" and message["latitude"] == -22.9068 and message["longitude"] == -43.1729
        assert set(message) == {"id", "type", "delay_seconds", "latitude", "longitude"}


@pytest.mark.asyncio
async def test_edit_revalidates_expanded_dependents_and_revokes_previous_chatbot_permits(templates_case, private_media):
    c = templates_case
    uploaded = await upload(c, png_bytes(), "image", "imagem.png")
    definition_value = template_definition(kind="image", text="Legenda", asset_id=uploaded["asset"]["id"], file_name=uploaded["file_name"])
    row = await create_template(c, definition_value)
    await rule_create(c, definition(keyword="sequência", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1} for _ in range(16)]}))
    too_many = await save_template(c, row, definition_value | {"followup_text": "Mensagem depois de cada arquivo"})
    assert_code(too_many, 422, "CHATBOT_INVALID_GRAPH")
    unchanged = await c.client.get(c.templates + "/" + row["id"])
    assert unchanged.json()["template"]["followup_text"] == "" and unchanged.json()["template"]["revision"] == 1
    # Exercise only the synthetic lease and private authorization path, not a
    # WhatsApp send. Editing a template must invalidate an already granted plan.
    await action(c, "connect")
    effects = chat_effects(await input_event(c, "sequência"))
    assert effects and (await grant(c, effects[0]))["authorized"]
    device_revision = c.device["revision"]
    saved = await save_template(c, row, definition_value | {"text": "Legenda atualizada"})
    assert saved.status_code == 200, saved.text
    assert (await grant(c, effects[0], recheck=True))["authorized"] is False
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT revision FROM subscription_devices WHERE id=:id"),
            {"id": UUID(c.device["id"])})).scalar_one() == device_revision


@pytest.mark.asyncio
async def test_audio_caption_and_followup_are_admitted_only_after_previous_acceptance(templates_case, private_media):
    c = templates_case
    unrelated = await upload(c, png_bytes(), "image", "outra.png")
    uploaded = await upload(c, wav_bytes(), "audio", "voz.wav")
    row = await create_template(c, template_definition(kind="audio", asset_id=uploaded["asset"]["id"],
        text="Legenda do áudio", followup_text="Mensagem posterior", file_name=uploaded["file_name"]))
    await rule_create(c, definition(keyword="voz", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1}]}))
    await action(c, "connect")
    first, = chat_effects(await input_event(c, "voz"))
    permit = await grant(c, first)
    assert permit["authorized"] and permit["payload"]["action"]["type"] == "audio"
    assert permit["payload"]["action"]["caption"] == ""
    private_path = f"/internal/qr/channels/{c.row['id']}/device/chatbot/assets/"
    content = await c.private.get(private_path + uploaded["asset"]["id"] + "/content", headers=media_headers(c, first))
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("audio/mpeg")
    assert len(content.content) == uploaded["asset"]["size"] and sha256(content.content).hexdigest() == uploaded["asset"]["sha256"]
    denied = await c.private.get(private_path + unrelated["asset"]["id"] + "/content", headers=media_headers(c, first))
    assert denied.status_code == 404, denied.text
    assert chat_effects(await pending(c)) == []
    second, = chat_effects(await accepted(c, first))
    caption = await grant(c, second)
    assert caption["authorized"] and caption["payload"]["action"] == {"type": "text", "text": "Legenda do áudio"}
    assert chat_effects(await pending(c)) == []
    third, = chat_effects(await accepted(c, second))
    followup = await grant(c, third)
    assert followup["authorized"] and followup["payload"]["action"] == {"type": "text", "text": "Mensagem posterior"}
    assert chat_effects(await accepted(c, third)) == []


@pytest.mark.asyncio
async def test_library_fields_survive_chatbot_export_import_and_restored_simulation(templates_case, private_media, monkeypatch):
    from app import subscription_chatbot_archive_format as codec
    from test_subscription_chatbot_archive_postgres import preview_import, apply, run_job
    c = templates_case
    file = await upload(c, b"nome,valor\nSintetico,1\n", "document", "relatório.csv")
    document = await create_template(c, template_definition(name="Planilha CSV", kind="document",
        text="Legenda", followup_text="Depois do CSV", asset_id=file["asset"]["id"], file_name=file["file_name"]))
    audio_file = await upload(c, wav_bytes(), "audio", "instruções.wav")
    audio = await create_template(c, template_definition(name="Áudio", kind="audio",
        text="Texto do áudio", followup_text="Depois do áudio", asset_id=audio_file["asset"]["id"], file_name=audio_file["file_name"]))
    location = await create_template(c, template_definition(name="Localização", kind="location", text="",
        location={"latitude": 90, "longitude": -180}))
    rows = [document, audio, location]
    rule = await rule_create(c, definition(keyword="originais", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "template", "template_id": row["id"], "delay_seconds": 1} for row in rows]}))
    export = await c.client.post(c.chat + "/exports", json={"ids": [rule["id"]], "format": "json"})
    assert export.status_code == 200, export.text
    downloaded = await c.client.get(export.json()["export"]["url"])
    assert downloaded.status_code == 200, downloaded.text
    decoded = codec.read_archive(downloaded.content, export.json()["export"]["filename"])
    by_kind = {item["definition"]["kind"]: item["definition"] for item in decoded["templates"]}
    assert by_kind["document"]["followup_text"] == "Depois do CSV"
    assert by_kind["document"]["file_name"] == "relatório.csv"
    assert by_kind["audio"]["text"] == "Texto do áudio" and by_kind["audio"]["followup_text"] == "Depois do áudio"
    assert by_kind["audio"]["file_name"] == "instruções.mp3"
    assert by_kind["location"]["location"] == {"latitude": 90, "longitude": -180}
    # New portable identities and names force real template inserts, not a
    # no-op replay that could hide dropped columns during restoration.
    identities = {item["id"]: str(uuid4()) for item in decoded["templates"]}
    for item in decoded["templates"]:
        item["id"] = identities[item["id"]]
        item["definition"]["name"] += " restaurado"
    decoded["rules"][0]["id"] = str(uuid4())
    decoded["rules"][0]["definition"]["keyword"] = "restaurados"
    for item in decoded["rules"][0]["definition"]["response"]["actions"]:
        item["template_id"] = identities[item["template_id"]]
    preview = await preview_import(c, decoded)
    assert preview["can_apply"] and preview["counts"]["template_clones"] == 3
    queued = await apply(c, preview)
    assert queued.status_code == 202, queued.text
    result, job = await run_job(c, queued.json()["job"])
    assert result == "succeeded" and job["status"] == "succeeded", job
    listing = await c.client.get(c.templates)
    assert listing.status_code == 200 and listing.json()["counts"]["total"] == 6
    restored = {row["kind"]: row for row in listing.json()["templates"] if row["name"].endswith(" restaurado")}
    assert restored["document"]["file_name"] == "relatório.csv" and restored["audio"]["followup_text"] == "Depois do áudio"
    assert restored["location"]["location"] == {"latitude": 90, "longitude": -180}
    async with isolated_simulation(c, monkeypatch):
        result = await simulate(c, "restaurados")
        messages = result["messages"]
        assert [item["type"] for item in messages] == ["media", "text", "media", "text", "text", "location"]
        assert messages[0]["asset"]["file_name"] == "relatório.csv"
        assert messages[1]["text"] == "Depois do CSV" and messages[4]["text"] == "Depois do áudio"
        assert messages[5]["latitude"] == 90 and messages[5]["longitude"] == -180


def test_template_release_contract_reads_the_real_migrated_catalog(isolated_settings):
    from scripts import backup_and_migrate_subscription_templates as release
    assert release.schema_ready(isolated_settings.migration_url)


@pytest.mark.asyncio
async def test_runtime_schema_barrier_passes_without_exposing_private_template_fields(isolated_settings):
    from scripts import wait_for_subscription_templates_schema as barrier
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        for column in ("followup_body", "location", "file_name"):
            assert not await db.fetchval("SELECT has_column_privilege(current_user,'public.subscription_chatbot_templates',$1,'SELECT')", column)
    finally:
        db.terminate()


@pytest.mark.asyncio
async def test_missing_template_location_guard_fails_schema_barrier_without_ddl(isolated_settings):
    from scripts import wait_for_subscription_templates_schema as barrier
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace("'subscription_chatbot_templates_location_check'", "'missing_template_location_guard'")
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()
