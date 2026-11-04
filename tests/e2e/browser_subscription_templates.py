"""Stage-04 library with shipped UI and a strictly local synthetic HTTP fixture.

Schemas and rule compilation are real. Media files contain synthetic pixels,
short tones/color clips and CSV bytes; no remote storage, WhatsApp connection,
message, external URL action, ASR or real account is used. Browser viewport
emulation does not claim physical-device or virtual-keyboard qualification.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect
import pytest

from app import subscription_chatbot_rules as rules_engine
from app.subscription_templates_schemas import CreateTemplate, UpdateTemplate, DeleteTemplate, UploadAsset, media_filename
from browser_subscription_chatbot import (
    Handler as ChatbotHandler, ROOT, SIZES, CHATBOT_BASE, ALLOWED_ROUTES,
    assert_layout, base, browser, navigation, now,
)  # noqa: F401 - reuse the validated full-panel fixture and browser matrix.
from browser_subscription_chatbot_simulator import (
    Handler as SimulatorHandler, DEVICE_ID, DEVICE, SIMULATOR_BASE,
    CONTRACT as SIMULATOR_CONTRACT, setup_page as simulator_setup_page,
)

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_templates_contract.json").read_text())
MEDIA = json.loads((ROOT / "tests/fixtures/subscription_templates_browser_media.json").read_text())["assets"]
TEMPLATES_BASE = base.BASE + "/subscription-whatsapp/templates"
ARTIFACTS = ROOT / "test-artifacts" / "subscription-templates"
for item in MEDIA.values():
    item["bytes"] = base64.b64decode(item["data_base64"], validate=True)
    item["input_bytes"] = base64.b64decode(item["input_base64"], validate=True)
    assert len(item["bytes"]) == item["size"] and sha256(item["bytes"]).hexdigest() == item["sha256"]


class Handler(SimulatorHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        if cookie and urlsplit(self.path).path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                base._sessions[cookie].update(templates=[], template_assets={}, template_calls=[], template_ledger={},
                    template_next=None, template_revoked=False, template_media_disabled=query.get("fixtureMedia") == ["off"],
                    template_asset_gate=None, template_simulation_tickets={})
        return super().body(value, status, mime, cookie)

    def library_allowed(self, session):
        return session and session["workspace_profile"] == "subscription_services" and not session["chatbot_readonly"] and not session["template_revoked"]

    def error(self, code, status=409):
        messages = {"CHATBOT_CONFLICT": "Os templates mudaram. Confira a versão atual.",
                    "TEMPLATE_IN_USE": "Este template está vinculado a uma resposta.",
                    "WORKSPACE_FEATURE_UNAVAILABLE": "O acesso a Templates foi revogado."}
        return self.body({"detail": {"code": code, "message": messages.get(code, "Confira os dados do template.")}}, status)

    def dependents(self, session, identity):
        return [{key: row[key] for key in ("id", "keyword", "enabled", "device_id")} | {"response_kind": row["response"]["kind"]}
                for row in session["chatbot_rules"] if identity in rules_engine.template_references(row["response"])]

    def asset_dto(self, session, identity):
        asset = deepcopy(session["template_assets"][identity]["descriptor"])
        path = TEMPLATES_BASE + "/assets/" + identity + "/content?"
        asset["content_url"] = path + urlencode({"expected_chatbot_revision": session["chatbot_revision"], "variant": "original"})
        asset["thumbnail_url"] = path + urlencode({"expected_chatbot_revision": session["chatbot_revision"], "variant": "thumbnail"}) if asset["thumbnail"] else None
        return asset

    def template_dto(self, session, row):
        result = deepcopy(row)
        result["dependent_count"] = len(self.dependents(session, row["id"]))
        result["asset"] = self.asset_dto(session, row["asset_id"]) if row["asset_id"] else None
        return result

    def detail(self, session, row):
        return {"chatbot_revision": session["chatbot_revision"], "template": self.template_dto(session, row),
                "dependents": self.dependents(session, row["id"])}

    def listing(self, session, query):
        # The parent method is used for /chatbot. Its contract stays unchanged.
        return ChatbotHandler.listing(self, session, query)

    def library_listing(self, session, query):
        data = deepcopy(CONTRACT["list_response"])
        term = query.get("q", [""])[0].casefold()
        rows = [row for row in session["templates"] if term in (row["name"] + " " + row["text"] + " " + row["followup_text"]).casefold()]
        data.update(chatbot_revision=session["chatbot_revision"], templates=[self.template_dto(session, row) for row in rows],
                    counts={"total": len(session["templates"]), "filtered": len(rows), "limit": 100})
        if session["template_media_disabled"]:
            data["capabilities"].update(upload=False, private_media=False)
            data["capabilities"]["types"] = {kind: kind in {"text", "location"} for kind in data["capabilities"]["types"]}
        return data

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/templates-report":
            return self.body({"calls": session["template_calls"], "unexpected": session["unexpected"],
                "templates": [self.template_dto(session, row) for row in session["templates"]],
                "chatbot_calls": session["chatbot_calls"], "simulator_calls": session["simulator_calls"]})
        if session and path == CHATBOT_BASE + "/catalog":
            session["chatbot_calls"].append({"method": "GET", "path": path})
            from browser_subscription_chatbot import CONTRACT as CHATBOT_CONTRACT
            data = deepcopy(CHATBOT_CONTRACT["catalog_response"])
            data.update(chatbot_revision=session["chatbot_revision"], capabilities=self.capabilities(session),
                rules=[{key: self.summary(rule)[key] for key in ("id", "keyword", "device_id", "enabled", "response_kind", "revision")}
                       for rule in session["chatbot_rules"]],
                templates=[{key: row[key] for key in ("id", "revision", "name", "kind")} | {"available": True} for row in session["templates"]])
            return self.body(data)
        if session and path.startswith(TEMPLATES_BASE):
            session["template_calls"].append({"method": "GET", "path": path, "query": query})
            if not self.library_allowed(session):
                return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
            suffix = path[len(TEMPLATES_BASE):]
            if not suffix:
                return self.body(self.library_listing(session, query))
            if suffix.startswith("/assets/") and suffix.endswith("/content"):
                identity = suffix.split("/")[2]
                if query.get("expected_chatbot_revision") != [str(session["chatbot_revision"])]:
                    return self.error("CHATBOT_CONFLICT")
                saved = session["template_assets"].get(identity)
                if not saved:
                    return self.error("TEMPLATE_ASSET_INVALID", 422)
                if session["template_asset_gate"] is not None:
                    if not session["template_asset_gate"].wait(10):
                        return self.body({"detail": "Synthetic media gate was not released"}, 408)
                variant = query.get("variant", [""])[0]
                if variant == "original":
                    return self.body(saved["bytes"], mime=saved["descriptor"]["mime"])
                if variant == "thumbnail" and saved["thumbnail"]:
                    return self.body(saved["thumbnail"], mime="image/jpeg")
                return self.error("TEMPLATE_NOT_FOUND", 404)
            row = next((row for row in session["templates"] if "/" + row["id"] == suffix), None)
            return self.body(self.detail(session, row)) if row else self.error("TEMPLATE_NOT_FOUND", 404)
        if session and path.startswith(SIMULATOR_BASE + "/assets/"):
            ticket = session["template_simulation_tickets"].get(query.get("ticket", [""])[0])
            identity = path.rsplit("/", 2)[-2]
            if not ticket or ticket["asset_id"] != identity or query.get("variant") != ["original"]:
                return self.error("CHATBOT_TEST_CONTEXT_INVALID")
            saved = session["template_assets"][identity]
            return self.body(saved["bytes"], mime=saved["descriptor"]["mime"])
        return super().do_GET()

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/templates-control" and self.command == "POST":
            value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = value["mode"]
            if mode == "lost":
                session["template_next"] = "lost"
            elif mode == "stale":
                session["chatbot_revision"] += 1
            elif mode == "block_asset":
                session["template_asset_gate"] = threading.Event()
            elif mode == "release_asset":
                session["template_asset_gate"].set()
                session["template_asset_gate"] = None
            elif mode == "revoke":
                session.update(template_revoked=True, chatbot_readonly=True, simulator_revoked=True)
            else:
                return self.reject(session, "Unknown synthetic control")
            return self.body({"ok": True})
        if path == CHATBOT_BASE or path.startswith(CHATBOT_BASE + "/"):
            return ChatbotHandler.mutate(self)
        if not path.startswith(TEMPLATES_BASE):
            return super().mutate()
        if not self.library_allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(TEMPLATES_BASE):]
        model = UploadAsset if suffix == "/assets" and self.command == "POST" else CreateTemplate if not suffix and self.command == "POST" else UpdateTemplate if self.command == "PUT" else DeleteTemplate if self.command == "DELETE" else None
        if model is None:
            return self.reject(session, "Only template CRUD and local file preparation are allowed")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 14000000:
                raise ValueError()
            payload = model.model_validate_json(self.rfile.read(size)).model_dump(mode="json")
        except ValueError:
            return self.body({"detail": "Invalid synthetic template request"}, 422)
        visible = deepcopy(payload)
        if "content_base64" in visible:
            raw = base64.b64decode(visible.pop("content_base64"), validate=True)
            visible.update(source_bytes=len(raw), source_sha256=sha256(raw).hexdigest())
        session["template_calls"].append({"method": self.command, "path": path, "data": visible})
        old = session["template_ledger"].get(payload["request_id"])
        identity = suffix.removeprefix("/")
        if old:
            if old["method"] != self.command or old["path"] != path or old["payload"] != payload:
                return self.error("CHATBOT_CONFLICT")
            return self.body(old["result"], old["status"])
        if payload["expected_chatbot_revision"] != session["chatbot_revision"]:
            return self.error("CHATBOT_CONFLICT")
        status = 201 if self.command == "POST" else 200
        if suffix == "/assets":
            raw = base64.b64decode(payload["content_base64"], validate=True)
            source = MEDIA[payload["kind"]]
            if raw != source["input_bytes"] or session["template_media_disabled"]:
                return self.error("TEMPLATE_ASSET_INVALID", 422)
            saved = next((value for value in session["template_assets"].values() if value["descriptor"]["kind"] == payload["kind"]), None)
            if not saved:
                identity = str(uuid4())
                descriptor = {key: deepcopy(source[key]) for key in ("kind", "mime", "size", "sha256", "width", "height", "duration_ms")}
                descriptor.update(id=identity, file_name=media_filename(payload["filename"], source["mime"]),
                    thumbnail={key: source["thumbnail"][key] for key in ("mime", "size", "sha256")} if source["thumbnail"] else None)
                saved = {"descriptor": descriptor, "bytes": source["bytes"],
                         "thumbnail": base64.b64decode(source["thumbnail"]["data_base64"]) if source["thumbnail"] else None}
                session["template_assets"][identity] = saved
            identity = saved["descriptor"]["id"]
            result = {"request_id": payload["request_id"], "chatbot_revision": session["chatbot_revision"],
                "file_name": media_filename(payload["filename"], source["mime"]), "asset": self.asset_dto(session, identity)}
        else:
            row = next((row for row in session["templates"] if row["id"] == identity), None) if suffix else None
            if suffix and (row is None or row["revision"] != payload["expected_revision"]):
                return self.error("CHATBOT_CONFLICT")
            if self.command == "DELETE":
                if self.dependents(session, identity):
                    return self.error("TEMPLATE_IN_USE")
                session["templates"].remove(row)
                session["chatbot_revision"] += 1
                result = {"request_id": payload["request_id"], "chatbot_revision": session["chatbot_revision"], "deleted": 1, "template_id": identity}
            else:
                value = payload["template"]
                if row and row["kind"] != value["kind"]:
                    return self.error("TEMPLATE_KIND_IMMUTABLE")
                if value["asset_id"] and (value["asset_id"] not in session["template_assets"] or session["template_assets"][value["asset_id"]]["descriptor"]["kind"] != value["kind"]):
                    return self.error("TEMPLATE_ASSET_INVALID", 422)
                if not row:
                    if len(session["templates"]) >= 100:
                        return self.error("TEMPLATE_LIMIT")
                    row = {"id": str(uuid4()), "revision": 0, "created_at": now()}
                    session["templates"].append(row)
                row.update(value, updated_at=now(), revision=row["revision"] + 1)
                session["chatbot_revision"] += 1
                result = self.detail(session, row) | {"request_id": payload["request_id"]}
        session["template_ledger"][payload["request_id"]] = {"method": self.command, "path": path, "payload": payload, "result": deepcopy(result), "status": status}
        if session["template_next"] == "lost":
            session["template_next"] = None
            return self.body({"detail": "Synthetic reply lost after commit"}, 503)
        return self.body(result, status)

    def simulation_result(self, session, payload):
        value = payload.input
        root = next((rule for rule in session["chatbot_rules"] if
            (value.type == "rule" and str(value.rule_id) == rule["id"])
            or (value.type == "text" and rule["keyword"].casefold() == value.text.casefold())), None)
        if root is None or not rules_engine.template_references(root["response"]):
            return super().simulation_result(session, payload)
        templates = {row["id"]: {**row, "body": row["text"], "followup_body": row["followup_text"],
            "asset": deepcopy(session["template_assets"][row["asset_id"]]["descriptor"]) if row["asset_id"] else None}
            for row in session["templates"]}
        facts = {"name": "Contato sintético", "phone_number": "+5511900000001", "my_name": "Empresa sintética",
                 "my_email": "owner@example.invalid", "my_contact_number": "+5511900000002"}
        graph = {rule["id"]: rule for rule in session["chatbot_rules"]}
        plan, _ = rules_engine.compile_plan(root["id"], graph, templates, facts, {})
        data = deepcopy(SIMULATOR_CONTRACT["simulate_response"])
        data.update(request_id=str(payload.request_id), context_id=str(payload.context_id), chatbot_revision=session["chatbot_revision"],
            device=deepcopy(DEVICE), input={"type": value.type, "text": root["keyword"], "rule_id": root["id"] if value.type == "rule" else None, "row_id": None},
            matched_rule={key: root[key] for key in ("id", "keyword", "enabled", "match_mode")} | {"reason": "forced_rule" if value.type == "rule" else "keyword"},
            keywords=[{key: root[key] for key in ("id", "keyword", "enabled", "match_mode")} | {"rule_id": root["id"]}], messages=[])
        data["keywords"][0].pop("id")
        for part in plan:
            action = part["action"]
            message = {"id": str(uuid4()), "delay_seconds": part["delay_seconds"], "type": action["type"]}
            if action["type"] == "text":
                message["text"] = action["text"]
            elif action["type"] == "location":
                message.update(latitude=action["latitude"], longitude=action["longitude"])
            else:
                asset = action["asset"]
                ticket = "synthetic-template-" + uuid4().hex
                session["template_simulation_tickets"][ticket] = {"asset_id": asset["id"], "context_id": str(payload.context_id)}
                reference = {key: asset[key] for key in ("id", "kind", "mime", "size", "width", "height", "duration_ms")}
                reference.update(file_name=action["file_name"], content_url=SIMULATOR_BASE + "/assets/" + asset["id"] + "/content?" + urlencode({"ticket": ticket, "variant": "original"}),
                    thumbnail_url=None, thumbnail_mime=None, thumbnail_size=None)
                message.update(type="media", kind=action["type"], caption=action.get("caption", ""), asset=reference)
            data["messages"].append(message)
        return data


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def setup(browser, fixture_url, size=(1440, 900), theme="light"):
    context, page, external, errors = simulator_setup_page(browser, fixture_url, size, theme)
    context.add_init_script("""(() => {
      const created=new Set(),create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
      URL.createObjectURL=value=>{const url=create(value);created.add(url);return url;};
      URL.revokeObjectURL=value=>{created.delete(value);return revoke(value);};
      window.templateFixtureBlobs=()=>created.size;
    })();""")
    return context, page, external, errors


def report(page, fixture_url):
    data = page.request.get(fixture_url + "/__fixture/templates-report").json()
    assert not data["unexpected"], data["unexpected"]
    assert all(call["method"] == "GET" or call["path"] == CHATBOT_BASE + "/rules" for call in data["chatbot_calls"]), data["chatbot_calls"]
    assert all(call["method"] == "GET" or call["path"] == SIMULATOR_BASE + "/simulate" for call in data["simulator_calls"]), data["simulator_calls"]
    return data


def field(page, name):
    return page.locator('[data-st-editor] [data-st-field="' + name + '"]')


def card(page, name):
    return page.locator("[data-st-template-id]").filter(has=page.get_by_role("heading", name=name, exact=True))


def create_editor(page, tab="text"):
    page.locator('[data-st-action="create"]').click()
    expect(page.locator("[data-st-editor]")).to_be_visible()
    if tab != "text":
        page.locator('[data-st-tab="' + tab + '"]').click()


def select_file(page, kind):
    value = MEDIA[kind]
    page.locator("[data-st-file]").set_input_files({"name": value["input_name"], "mimeType": "application/octet-stream", "buffer": value["input_bytes"]})
    expect(page.locator("[data-st-file-status]")).to_contain_text(media_filename(value["input_name"], value["mime"]))
    expect(page.locator("[data-st-save]")).to_be_enabled()


def save_editor(page, count):
    page.locator("[data-st-save]").click()
    expect(page.locator("[data-st-editor]")).to_have_count(0)
    expect(page.locator("[data-st-template-id]")).to_have_count(count)


def close_dialog(page, kind):
    page.locator('[data-st-form="' + kind + '"] .os-form-actions [data-st-action="close-dialog"]').last.click()
    expect(page.locator('[data-st-form="' + kind + '"]')).to_have_count(0)


def control(page, fixture_url, mode):
    result = page.request.post(fixture_url + "/__fixture/templates-control", data={"mode": mode})
    assert result.ok, result.text()


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_library_forms_private_preview_crud_and_existing_chatbot_simulator(browser, fixture_url, size, theme):
    engine, _ = browser
    width, height = size
    context, page, external, errors = setup(browser, fixture_url, size, theme)
    label = f"{engine}-{width}x{height}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    evidence = {}
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=templates")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "templates")
        expect(page.locator("[data-st-count]")).to_have_text("0 / 100 templates")
        expect(page.locator("[data-st-empty]")).to_contain_text("Nenhum template ainda")
        assert page.locator("#osNav [data-os-route]").evaluate_all("nodes=>nodes.map(node=>node.dataset.osRoute)") == ALLOWED_ROUTES
        page.locator("[data-st-search]").fill("inexistente")
        expect(page.locator("[data-st-empty]")).to_contain_text("Nenhum modelo corresponde")
        expect(page.locator("[data-st-search]")).to_have_value("inexistente")
        page.locator("[data-st-search]").fill("")
        expect(page.locator("[data-st-empty]")).to_contain_text("Nenhum template ainda")
        page.evaluate("document.fonts.ready")
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-empty.png"), animations="disabled")

        create_editor(page)
        expect(page.locator("[data-st-tab]")).to_have_count(5)
        field(page, "text").fill("Rascunho da aba Texto")
        for tab, suffix in (("media", ".apk"), ("sticker", ".png"), ("ptv", ".mp4")):
            page.locator('[data-st-tab="' + tab + '"]').click()
            expect(page.locator("[data-st-file]")).to_have_attribute("accept", re.compile(re.escape(suffix)))
            expect(page.get_by_label("Arquivo", exact=True)).to_have_count(1)
        expect(page.locator("#st-file-help")).to_contain_text("60 segundos")
        page.locator('[data-st-tab="location"]').click()
        expect(field(page, "latitude")).to_have_attribute("min", "-90")
        expect(field(page, "longitude")).to_have_attribute("max", "180")
        page.locator('[data-st-tab="text"]').click()
        expect(field(page, "text")).to_have_value("Rascunho da aba Texto")
        field(page, "text").evaluate("node=>{node.value='x'.repeat(1001);node.dispatchEvent(new Event('input',{bubbles:true}));}")
        expect(page.locator('[data-st-counter="text"]')).to_have_text("1001 / 1000")
        page.locator("[data-st-save]").click()
        expect(page.locator("[data-st-editor] [data-st-dialog-error]")).to_contain_text("1000")
        assert not [call for call in report(page, fixture_url)["calls"] if call["method"] != "GET"]
        field(page, "text").fill("🙂" * 500)
        expect(page.locator('[data-st-counter="text"]')).to_have_text("1000 / 1000")
        page.locator('[data-st-action="tags"]').click()
        for key in ("name", "phone_number", "my_name", "my_email", "my_contact_number"):
            expect(page.locator('[data-st-form="tags"]')).to_contain_text("{" + key + "}")
        close_dialog(page, "tags")
        expect(field(page, "text")).to_have_value("🙂" * 500)
        field(page, "name").fill("Boas-vindas")
        field(page, "text").fill("*Olá*, {name}!")
        save_editor(page, 1)
        card(page, "Boas-vindas").locator('[data-st-action="preview"]').click()
        expect(page.locator("[data-st-preview] strong").last).to_have_text("Olá")
        expect(page.locator("[data-st-preview]")).to_contain_text("{name}")
        close_dialog(page, "preview")
        card(page, "Boas-vindas").locator('[data-st-action="edit"]').click()
        expect(page.locator('[data-st-tab="media"]')).to_be_disabled()
        field(page, "name").fill("Boas-vindas revisadas")
        field(page, "text").fill("*Olá*, {name}! Como vai?")
        save_editor(page, 1)

        create_editor(page, "media")
        field(page, "name").fill("Imagem de atendimento")
        select_file(page, "image")
        field(page, "text").fill("Legenda, {name}")
        field(page, "followup_text").fill("Mensagem depois do arquivo")
        page.locator('[data-st-action="preview-draft"]').click()
        image = page.locator("[data-st-preview] [data-st-media-content] img")
        expect(image).to_have_attribute("src", re.compile("^blob:"))
        image.scroll_into_view_if_needed()
        page.wait_for_function("() => {const image=document.querySelector('[data-st-preview] img');return image?.complete&&image.naturalWidth===320;}")
        expect(page.locator("[data-st-preview]")).to_contain_text("Legenda, {name}")
        expect(page.locator("[data-st-preview]")).to_contain_text("Mensagem depois do arquivo")
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-private-image.png"), animations="disabled")
        close_dialog(page, "preview")
        assert page.evaluate("window.templateFixtureBlobs()") == 0
        save_editor(page, 2)

        create_editor(page, "location")
        field(page, "name").fill("Local de atendimento")
        field(page, "latitude").fill("91")
        field(page, "longitude").fill("0")
        page.locator("[data-st-save]").click()
        expect(page.locator("[data-st-editor] [data-st-dialog-error]")).to_contain_text("latitude")
        field(page, "latitude").fill("-22.9068")
        field(page, "longitude").fill("-43.1729")
        page.locator('[data-st-action="preview-draft"]').click()
        expect(page.locator("[data-st-preview]")).to_contain_text("-22.9068")
        expect(page.locator("[data-st-preview]")).to_contain_text("-43.1729")
        expect(page.locator("[data-st-preview] img,[data-st-preview] iframe")).to_have_count(0)
        close_dialog(page, "preview")
        save_editor(page, 3)
        page.locator("[data-st-search]").fill("depois do arquivo")
        expect(page.locator("[data-st-template-id]")).to_have_count(1)
        expect(card(page, "Imagem de atendimento")).to_be_visible()
        page.locator("[data-st-search]").fill("")
        expect(page.locator("[data-st-template-id]")).to_have_count(3)
        text_id = next(row["id"] for row in report(page, fixture_url)["templates"] if row["kind"] == "text")

        navigation(page)
        page.locator("#osNav [data-os-route=chatbot]").click()
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        page.locator('[data-sc-action="create"]').click()
        page.locator('[data-sc-field="keyword"]').fill("biblioteca-template")
        page.locator('[data-sc-field="response.actions.0.type"]').select_option("template")
        page.locator('[data-sc-field="response.actions.0.template_id"]').select_option(text_id)
        page.locator('[data-sc-form="editor"] button[type=submit]').click()
        expect(page.locator("[data-sc-feedback]")).to_contain_text("Resposta criada")
        navigation(page)
        page.locator("#osNav [data-os-route=testar-chatbot]").click()
        page.locator("[data-sct-device]").select_option(DEVICE_ID)
        page.locator("[data-sct-input]").fill("biblioteca-template")
        page.locator("[data-sct-input]").press("Enter")
        expect(page.locator("[data-sct-message]")).to_have_count(2)
        expect(page.locator('[data-sct-message][data-direction="in"]')).to_contain_text("*Olá*, Contato sintético! Como vai?")
        navigation(page)
        page.locator("#osNav [data-os-route=templates]").click()
        expect(page.locator("[data-st-template-id]")).to_have_count(3)
        card(page, "Boas-vindas revisadas").locator('[data-st-action="delete"]').click()
        expect(page.locator('[data-st-form="delete"]')).to_contain_text("biblioteca-template")
        expect(page.locator('[data-st-form="delete"] button[type=submit]')).to_have_count(0)
        close_dialog(page, "delete")
        card(page, "Local de atendimento").locator('[data-st-action="delete"]').click()
        page.locator('[data-st-form="delete"] button[type=submit]').click()
        expect(page.locator('[data-st-form="delete"]')).to_have_count(0)
        expect(page.locator("[data-st-template-id]")).to_have_count(2)
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-library.png"), animations="disabled")
        evidence = report(page, fixture_url)
        writes = [call for call in evidence["calls"] if call["method"] in {"POST", "PUT"} and call["path"] != TEMPLATES_BASE + "/assets"]
        media_write = next(call["data"]["template"] for call in writes if call["data"]["template"]["kind"] == "image")
        assert media_write["text"] == "Legenda, {name}" and media_write["followup_text"] == "Mensagem depois do arquivo"
        assert len([call for call in evidence["calls"] if call["method"] == "DELETE"]) == 1
        assert not external, external
        assert not errors, errors
        assert page.evaluate("window.templateFixtureBlobs()") == 0
    finally:
        if errors or external:
            page.screenshot(path=str(ARTIFACTS / f"{label}-failure.png"), animations="disabled")
        (ARTIFACTS / f"{label}.json").write_text(json.dumps({"engine": engine, "viewport": size, "theme": theme,
            "evidence": evidence, "page_errors": errors, "external_requests": external,
            "limitations": ["synthetic HTTP and private media", "viewport emulation", "no WhatsApp, ASR, storage or external HTTP"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()


def test_real_synthetic_media_decodes_and_document_download_is_explicit(browser, fixture_url):
    engine, _ = browser
    context, page, external, errors = setup(browser, fixture_url)
    console_errors, failed_requests = [], []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: failed_requests.append({"url": request.url, "failure": request.failure}))
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=templates")
        expect(page.locator("[data-st-count]")).to_have_text("0 / 100 templates")
        for index, kind in enumerate(("audio", "video", "sticker", "ptv"), 1):
            create_editor(page, kind if kind in {"sticker", "ptv"} else "media")
            field(page, "name").fill("Mídia " + kind)
            select_file(page, kind)
            if kind == "audio":
                field(page, "text").fill("Legenda do áudio")
                field(page, "followup_text").fill("Mensagem depois do áudio")
            save_editor(page, index)
            card(page, "Mídia " + kind).locator('[data-st-action="preview"]').click()
            element = "img" if kind == "sticker" else "audio" if kind == "audio" else "video"
            expect(page.locator("[data-st-preview] " + element)).to_have_attribute("src", re.compile("^blob:"))
            try:
                page.wait_for_function("kind=>{const root=document.querySelector('[data-st-preview]');if(kind==='sticker'){const image=root?.querySelector('img');return image?.complete&&image.naturalWidth===512;}const media=root?.querySelector(kind==='audio'?'audio':'video');return media?.readyState>=1&&media.duration>0&&(kind!=='ptv'||media.videoWidth===512);}", arg=kind)
            except PlaywrightTimeoutError as error:
                diagnostics = {"engine": engine, "kind": kind, "console_errors": console_errors,
                    "failed_requests": failed_requests, "external_requests": external, "page_errors": errors,
                    "player": page.evaluate("""kind => {
                        const root=document.querySelector('[data-st-preview]');
                        const node=root?.querySelector(kind==='sticker'?'img':kind==='audio'?'audio':'video');
                        const video=document.createElement('video'),audio=document.createElement('audio');
                        return {src:node?.getAttribute('src'),currentSrc:node?.currentSrc,
                            readyState:node?.readyState,networkState:node?.networkState,
                            duration:Number.isFinite(node?.duration)?node.duration:String(node?.duration),
                            error:node?.error?{code:node.error.code,message:node.error.message}:null,
                            complete:node?.complete,naturalWidth:node?.naturalWidth,
                            videoWidth:node?.videoWidth,videoHeight:node?.videoHeight,
                            mp3Support:audio.canPlayType('audio/mpeg'),
                            h264Support:video.canPlayType('video/mp4; codecs="avc1.42E01E"'),
                            previewText:root?.textContent};
                    }""", kind)}
                ARTIFACTS.mkdir(parents=True, exist_ok=True)
                (ARTIFACTS / f"{engine}-{kind}-decode-failure.json").write_text(
                    json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
                page.screenshot(path=str(ARTIFACTS / f"{engine}-{kind}-decode-failure.png"), animations="disabled")
                raise AssertionError("Synthetic media did not decode: " + json.dumps(diagnostics, ensure_ascii=False)) from error
            if kind == "audio":
                expect(page.locator("[data-st-preview]")).to_contain_text("Legenda enviada depois do áudio")
                expect(page.locator("[data-st-preview]")).to_contain_text("Mensagem depois do áudio")
            close_dialog(page, "preview")
            assert page.evaluate("window.templateFixtureBlobs()") == 0
        create_editor(page, "media")
        field(page, "name").fill("Arquivo CSV")
        select_file(page, "document")
        save_editor(page, 5)
        reads_before = len([call for call in report(page, fixture_url)["calls"] if call["path"].endswith("/content")])
        card(page, "Arquivo CSV").locator('[data-st-action="preview"]').click()
        expect(page.locator("[data-st-preview]")).to_contain_text("dados.csv")
        assert len([call for call in report(page, fixture_url)["calls"] if call["path"].endswith("/content")]) == reads_before
        with page.expect_download() as download:
            page.locator('[data-st-preview] [data-st-action="download"]').click()
        assert download.value.suggested_filename == "dados.csv"
        assert Path(download.value.path()).read_bytes() == MEDIA["document"]["bytes"]
        close_dialog(page, "preview")
        assert page.evaluate("window.templateFixtureBlobs()") == 0
        report(page, fixture_url)
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


def test_lost_response_conflict_abort_revocation_and_other_niche_preserve_boundaries(browser, fixture_url):
    context, page, external, errors = setup(browser, fixture_url)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=templates")
        create_editor(page)
        field(page, "name").fill("Recuperação")
        field(page, "text").fill("Mensagem confirmada")
        control(page, fixture_url, "lost")
        page.locator("[data-st-save]").click()
        expect(page.locator('[data-st-action="retry"]')).to_be_visible()
        expect(field(page, "text")).to_have_value("Mensagem confirmada")
        expect(field(page, "text")).to_be_disabled()
        page.locator('[data-st-action="retry"]').click()
        expect(page.locator("[data-st-editor]")).to_have_count(0)
        expect(page.locator("[data-st-template-id]")).to_have_count(1)
        posts = [call["data"] for call in report(page, fixture_url)["calls"] if call["method"] == "POST"]
        assert len(posts) == 2 and posts[0] == posts[1]
        card(page, "Recuperação").locator('[data-st-action="edit"]').click()
        field(page, "text").fill("Rascunho preservado")
        control(page, fixture_url, "stale")
        page.locator("[data-st-save]").click()
        expect(page.locator('[data-st-action="reconcile"]')).to_be_visible()
        expect(field(page, "text")).to_have_value("Rascunho preservado")
        page.locator('[data-st-action="reconcile"]').click()
        expect(page.locator('[data-st-form="reload-editor"]')).to_contain_text("Mensagem confirmada")
        close_dialog(page, "reload-editor")
        expect(field(page, "text")).to_have_value("Rascunho preservado")
        page.locator('[data-st-action="reconcile"]').click()
        page.locator('[data-st-form="reload-editor"] button[type=submit]').click()
        expect(field(page, "text")).to_have_value("Mensagem confirmada")
        field(page, "text").fill("Edição revisada")
        save_editor(page, 1)

        create_editor(page, "media")
        field(page, "name").fill("Imagem pendente")
        select_file(page, "image")
        save_editor(page, 2)
        control(page, fixture_url, "block_asset")
        with page.expect_request(lambda request: request.method == "GET" and "/assets/" in request.url and "/content?" in request.url):
            card(page, "Imagem pendente").locator('[data-st-action="preview"]').click()
        close_dialog(page, "preview")
        control(page, fixture_url, "release_asset")
        expect(page.locator("[data-st-media-content]")).to_have_count(0)
        assert page.evaluate("window.templateFixtureBlobs()") == 0
        control(page, fixture_url, "revoke")
        page.locator('[data-st-action="refresh"]').click()
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "novidades")
        expect(page.locator("#osNav [data-os-route=templates]")).to_have_count(0)
        expect(page.locator("[data-st-results]")).to_have_count(0)
        report(page, fixture_url)

        for query, route in (("fixtureProfile=standard", "plano"), ("fixtureReadonly=1", "novidades")):
            page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=templates&" + query)
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", route)
            expect(page.locator("#osNav [data-os-route=templates]")).to_have_count(0)
            assert not report(page, fixture_url)["calls"]
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=templates&fixtureMedia=off")
        create_editor(page)
        for tab in ("media", "sticker", "ptv"):
            expect(page.locator('[data-st-tab="' + tab + '"]')).to_be_disabled()
        expect(page.locator('[data-st-tab="text"]')).to_be_enabled()
        expect(page.locator('[data-st-tab="location"]')).to_be_enabled()
        report(page, fixture_url)
        assert not errors, errors
        assert not external, external
    finally:
        context.close()
