"""Stage-02 Chatbot with shipped Orion assets and a loopback-only fixture.

No database, external URL, provider, real account, message or media upload is used.
Testar Resposta and URL inspection are opened and cancelled, never executed.
Viewport emulation does not claim physical-device or virtual-keyboard coverage.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
import zipfile

from playwright.sync_api import expect, sync_playwright
import pytest

from app.subscription_chatbot_schemas import CreateRule, SaveRule, ExportSelection, ImportPreview, RestorePreview
from scripts.plans_preview import Handler as PlansHandler, base

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "subscription-chatbot"
CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_chatbot_contract.json").read_text())
CHATBOT_BASE = base.BASE + "/subscription-whatsapp/chatbot"
RULE_ID = CONTRACT["detail_response"]["rule"]["id"]
BACKUP_ID = CONTRACT["backups_response"]["items"][0]["id"]
EXPORT_ID = CONTRACT["export_response"]["export"]["id"]
SIZES = [(390, 844), (768, 1024), (1440, 900)]
PROFILE = "subscription_services"
ALLOWED_ROUTES = ["dashboard", "dispositivos", "chatbot", "testar-chatbot", "templates", "novidades", "ajuda", "member"]


def now():
    return datetime.now(timezone.utc).isoformat()


class Handler(PlansHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        url = urlsplit(self.path)
        if cookie and url.path in {"/", "/panel"}:
            query = parse_qs(url.query)
            profile = query.get("fixtureProfile", [PROFILE])[0]
            with base._lock:
                session = base._sessions[cookie]
                first = deepcopy(CONTRACT["detail_response"]["rule"])
                first["response"]["actions"][0]["text"] = "*Olá*, {name}!\nComo podemos ajudar?"
                second = {**deepcopy(first), "id": "30000000-0000-4000-8000-000000000002",
                          "keyword": "menu", "creation_order": 2, "enabled": False,
                          "response": deepcopy(CONTRACT["list_definition"])}
                session.update(workspace_profile=profile if profile in {PROFILE, "standard"} else PROFILE,
                    chatbot_readonly=query.get("fixtureReadonly") == ["1"], chatbot_calls=[],
                    chatbot_rules=[first, second], chatbot_revision=1, chatbot_previews={}, chatbot_exports={})
        session = self.session()
        if session and url.path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**row, "workspace_profile": session["workspace_profile"],
                      "name": "Empresa sintética de respostas"} for row in value]
        if session and url.path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": session["workspace_profile"]}
            if session["workspace_profile"] == PROFILE:
                plan["features"] = ["account.manage", "business.setup", "devices.manage"]
                if not session["chatbot_readonly"]:
                    plan["features"].extend(["chatbot.manage", "templates.manage"])
                plan["permissions"] = {key: key in plan["features"] for key in
                    {*plan["permissions"], "devices.manage", "chatbot.manage", "templates.manage"}}
            else:
                plan["permissions"].update({"devices.manage": False, "chatbot.manage": False, "templates.manage": False})
            plan["revision"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            value = {"effective_plan": plan}
        return super().body(value, status, mime, cookie)

    def capabilities(self, session):
        result = deepcopy(CONTRACT["catalog_response"]["capabilities"])
        if session["chatbot_readonly"]:
            for key in ["manage", "test_response", "read_url_tags", "import", "restore"]:
                result[key] = False
        return result

    def summary(self, rule):
        actions = rule["response"].get("actions", [])
        return {**{key: rule[key] for key in ["id", "creation_order", "revision", "device_id", "enabled",
                 "match_mode", "keyword", "created_at", "updated_at"]},
            "device_name": "Todos os Dispositivos" if not rule["device_id"] else "Dispositivo de teste",
            "response_kind": rule["response"]["kind"], "response_types": [row["type"] for row in actions],
            "preview": actions[0].get("text", "Resposta configurada") if actions else rule["response"]["description"],
            "action_count": len(actions), "total_delay_seconds": sum(row["delay_seconds"] for row in actions)}

    def listing(self, session, query):
        all_rules = session["chatbot_rules"]
        rules = list(all_rules)
        for name in ["keyword", "match_mode"]:
            if query.get(name):
                rules = [rule for rule in rules if rule[name] == query[name][0]]
        if query.get("status"):
            rules = [rule for rule in rules if rule["enabled"] == (query["status"][0] == "active")]
        if query.get("q"):
            term = query["q"][0].casefold()
            search = query.get("search_in", ["both"])[0]
            rules = [rule for rule in rules if term in (rule["keyword"] if search == "keyword" else
                json.dumps(rule["response"], ensure_ascii=False) if search == "response" else
                rule["keyword"] + json.dumps(rule["response"], ensure_ascii=False)).casefold()]
        if query.get("device_id"):
            scope = None if query["device_id"][0] == "all" else query["device_id"][0]
            rules = [rule for rule in rules if rule["device_id"] == scope]
        page, per_page = int(query.get("page", [1])[0]), int(query.get("per_page", [20])[0])
        return {**deepcopy(CONTRACT["list_response"]), "items": [self.summary(rule) for rule in rules[(page - 1) * per_page:page * per_page]],
            "counts": {"total": len(all_rules), "active": sum(rule["enabled"] for rule in all_rules),
                       "inactive": sum(not rule["enabled"] for rule in all_rules)},
            "filtered_total": len(rules), "page": page, "per_page": per_page,
            "total_pages": max(1, (len(rules) + per_page - 1) // per_page),
            "chatbot_revision": session["chatbot_revision"], "capabilities": self.capabilities(session), "pending_job": None}

    def do_GET(self):
        url = urlsplit(self.path)
        path, query, session = url.path, parse_qs(url.query), self.session()
        if session and path == "/__fixture/chatbot-calls":
            return self.body({"calls": session["chatbot_calls"], "unexpected": session["unexpected"],
                              "rules": session["chatbot_rules"]})
        if session and path.startswith(("/api/", "/auth/")):
            session["chatbot_calls"].append({"method": "GET", "path": path})
        if session and path == base.BASE + "/subscription-whatsapp/devices" and session["workspace_profile"] == PROFILE:
            return self.body({"items": [], "usage": {"used": 0, "limit": 1}})
        if session and path.startswith(CHATBOT_BASE):
            if session["workspace_profile"] != PROFILE:
                return self.reject(session, "Chatbot is unavailable to standard workspaces")
            suffix = path[len(CHATBOT_BASE):]
            if suffix == "":
                return self.body(self.listing(session, query))
            if suffix == "/catalog":
                return self.body({**deepcopy(CONTRACT["catalog_response"]),
                    "chatbot_revision": session["chatbot_revision"], "capabilities": self.capabilities(session),
                    "rules": [{key: self.summary(rule)[key] for key in ["id", "keyword", "device_id", "enabled", "response_kind", "revision"]}
                              for rule in session["chatbot_rules"]]})
            if suffix.startswith("/rules/"):
                rule = next((rule for rule in session["chatbot_rules"] if rule["id"] == suffix.split("/")[-1]), None)
                if rule:
                    return self.body({"rule": rule, "chatbot_revision": session["chatbot_revision"]})
            if suffix == "/backups":
                result = deepcopy(CONTRACT["backups_response"])
                result["items"][0]["download_url"] = CHATBOT_BASE + "/backups/" + BACKUP_ID + "/download"
                return self.body(result)
            if suffix == "/backups/" + BACKUP_ID + "/download" or suffix == "/exports/" + EXPORT_ID:
                exported = session["chatbot_exports"].get(EXPORT_ID)
                format = exported["format"] if suffix.startswith("/exports/") and exported else "zip"
                selected = exported["ids"] if suffix.startswith("/exports/") and exported else None
                document = {"format": "orion.subscription-chatbot", "version": 1, "exported_at": now(),
                    "rules": [{"id": rule["id"], "creation_order": rule["creation_order"],
                        "definition": {key: rule[key] for key in ["device_id", "enabled", "match_mode", "keyword", "response"]}}
                        for rule in session["chatbot_rules"] if selected is None or rule["id"] in selected],
                    "templates": [], "assets": []}
                data = json.dumps(document).encode()
                if format == "zip":
                    output = BytesIO()
                    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("chatbot.json", data)
                    data = output.getvalue()
                filename = "orion-chatbot-backup.zip" if suffix.startswith("/backups/") else "orion-chatbot." + format
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if format == "json" else "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self.reject(session, "Unknown synthetic Chatbot read")
        return super().do_GET()

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if not session or session["workspace_profile"] != PROFILE or not path.startswith(CHATBOT_BASE):
            return self.reject(session, "Only synthetic subscription Chatbot writes are permitted")
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(CHATBOT_BASE):]
        if session["chatbot_readonly"] and suffix != "/exports":
            return self.reject(session, "This fixture permits only read-only export")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 100000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.body({"detail": "Invalid synthetic payload"}, 400)
        call = {"method": self.command, "path": path}
        try:
            if suffix == "/rules" and self.command == "POST":
                parsed = CreateRule.model_validate(payload)
                if parsed.expected_chatbot_revision != session["chatbot_revision"]:
                    return self.body({"detail": "Changed synthetic revision"}, 409)
                rule = {**parsed.rule.model_dump(mode="json"), "id": str(uuid4()), "revision": 1,
                        "creation_order": len(session["chatbot_rules"]) + 1, "created_at": now(), "updated_at": now()}
                session["chatbot_rules"].append(rule)
                session["chatbot_revision"] += 1
                session["chatbot_calls"].append(call)
                return self.body({"rule": rule, "chatbot_revision": session["chatbot_revision"]}, 201)
            if suffix.startswith("/rules/") and self.command == "PUT":
                parsed = SaveRule.model_validate(payload)
                rule = next((rule for rule in session["chatbot_rules"] if rule["id"] == suffix.split("/")[-1]), None)
                if not rule or rule["revision"] != parsed.expected_revision or parsed.expected_chatbot_revision != session["chatbot_revision"]:
                    return self.body({"detail": "Changed synthetic revision"}, 409)
                rule.update(parsed.rule.model_dump(mode="json"))
                rule["revision"] += 1
                session["chatbot_revision"] += 1
                session["chatbot_calls"].append(call)
                return self.body({"rule": rule, "chatbot_revision": session["chatbot_revision"]})
            if suffix == "/exports" and self.command == "POST":
                parsed = ExportSelection.model_validate(payload)
                ids = {str(item) for item in parsed.ids}
                if not ids or ids - {rule["id"] for rule in session["chatbot_rules"]}:
                    return self.reject(session, "Invalid synthetic selection")
                call["selected_count"] = len(ids)
                session["chatbot_calls"].append(call)
                session["chatbot_exports"][EXPORT_ID] = {"format": parsed.format, "ids": sorted(ids)}
                return self.body({"export": {**CONTRACT["export_response"]["export"],
                    "filename": "orion-chatbot." + parsed.format, "url": CHATBOT_BASE + "/exports/" + EXPORT_ID}})
            if suffix == "/imports/preview" and self.command == "POST":
                parsed = ImportPreview.model_validate(payload)
                source, mode = "import", parsed.mode
            elif suffix == "/backups/" + BACKUP_ID + "/preview" and self.command == "POST":
                parsed = RestorePreview.model_validate(payload)
                source, mode = "backup", parsed.mode
            else:
                # This visual scenario never sends, probes an external URL,
                # deletes a rule, or commits a restore/import.
                return self.reject(session, "This visual scenario did not authorize this command")
        except ValueError as error:
            return self.body({"detail": str(error)}, 422)
        preview = deepcopy(CONTRACT["preview_response"])
        preview["preview"].update(source=source, mode=mode, chatbot_revision=session["chatbot_revision"],
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        preview["preview"]["counts"].update(create=1, update=1 if mode != "add_missing" else 0,
            delete=2 if mode == "exact" else 0)
        call["mode"] = mode
        session["chatbot_calls"].append(call)
        return self.body(preview)


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


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    with sync_playwright() as playwright:
        options = {}
        executable = os.getenv("ORION_TEST_CHROMIUM_EXECUTABLE")
        if request.param == "chromium" and executable:
            options["executable_path"] = executable
        instance = getattr(playwright, request.param).launch(**options)
        yield request.param, instance
        instance.close()


def navigation(page):
    menu = page.locator("#osMenu")
    if menu.is_visible() and menu.get_attribute("aria-expanded") != "true":
        menu.click()
        expect(menu).to_have_attribute("aria-expanded", "true")


def assert_layout(page, width):
    measured = page.evaluate("""() => ({width: innerWidth, scroll: document.documentElement.scrollWidth,
      dialogs: [...document.querySelectorAll('dialog[open]')].map(node => ({
        left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
        width: node.clientWidth, scroll: node.scrollWidth}))})""")
    assert measured["width"] == width and measured["scroll"] <= width + 1, measured
    assert all(row["left"] >= 0 and row["right"] <= width + 1 and row["scroll"] <= row["width"] + 1
               for row in measured["dialogs"]), measured


def close_modal(page, kind):
    page.locator(f"[data-sc-form={kind}] .os-form-actions [data-sc-action=close-dialog]").click()


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chatbot_editor_transfer_and_workspace_isolation(browser, fixture_url, size, theme):
    engine, instance = browser
    width, height = size
    context = instance.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
        locale="pt-BR", has_touch=width == 390, is_mobile=engine == "webkit" and width == 390,
        service_workers="block", accept_downloads=True)
    origin = urlsplit(fixture_url)
    external, errors, calls = [], [], []
    label = f"{engine}-{width}x{height}-{theme}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    def local_only(route):
        url = urlsplit(route.request.url)
        if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            external.append(route.request.url)
            return route.abort()
        return route.continue_()

    context.route("**/*", local_only)
    context.add_init_script("localStorage.setItem('orion-theme', " + json.dumps(theme) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    f = lambda name: page.locator(f'[data-sc-field="{name}"]')
    a = lambda name: page.locator(f'[data-sc-action="{name}"]')
    try:
        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&aba=chatbot")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "chatbot")
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        assert page.locator("#osNav [data-os-route]").evaluate_all("nodes=>nodes.map(n=>n.dataset.osRoute)") == ALLOWED_ROUTES
        expect(page.locator("#osSidebarContext [data-os-route=site]")).to_have_count(0)
        assert page.locator(".os-chatbot-metric strong").all_text_contents() == ["2", "1", "1"]
        page.evaluate("document.fonts.ready")
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-list.png"), animations="disabled")

        page.locator("[data-sc-advanced]>summary").click()
        f("filter.status").select_option("inactive")
        page.locator("[data-sc-form=filters] button[type=submit]").click()
        expect(page.locator("[data-sc-card]")).to_have_count(1)
        a("clear-filters").click()
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        page.locator(f'[data-sc-details="{RULE_ID}"]>summary').click()
        expect(page.locator(".os-chatbot-preview")).to_contain_text("Como podemos ajudar?")
        a("preview-raw").click()
        expect(page.locator(".os-chatbot-raw")).to_contain_text("*Olá*")
        a("preview-formatted").click()
        a("test").click()
        f("test_phone").fill("+55 11 99999-9999")
        expect(page.locator("[data-sc-form=test]")).to_contain_text("telefone real")
        close_modal(page, "test")

        page.locator(f'[data-sc-card="{RULE_ID}"] [data-sc-action=edit]').click()
        expect(f("keyword")).to_have_value("Olá")
        f("response.actions.0.text").fill("*Olá*, {name}!\nTexto sintético revisado.")
        page.locator("[data-sc-form=editor] button[type=submit]").click()
        expect(page.locator("[data-sc-feedback]")).to_contain_text("Resposta atualizada")
        a("create").click()
        f("keyword").fill("valores, planos de exemplo")
        f("match_mode").select_option("array")
        f("response.actions.0.text").fill("Olá, {name}! Esta é uma resposta sintética.")
        a("step-add").click()
        f("response.actions.1.type").select_option("url")
        f("response.actions.1.url").fill("https://example.invalid/resposta")
        f("response.actions.1.custom_template").fill("{{status}}")
        f("response.actions.1.delay_seconds").fill("2")
        a("read-url-tags").click()
        expect(page.locator("[data-sc-form=url-tags]")).to_contain_text("dados de exemplo")
        close_modal(page, "url-tags")
        f("response.actions.1.url").scroll_into_view_if_needed()
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-sequence.png"), animations="disabled")
        page.locator("[data-sc-form=editor] button[type=submit]").click()
        expect(page.locator("[data-sc-feedback]")).to_contain_text("Resposta criada")
        expect(page.locator("[data-sc-card]")).to_have_count(3)

        a("create").click()
        f("keyword").fill("opções sintéticas")
        a("response-list").click()
        f("response.title").fill("Atendimento")
        f("response.description").fill("Escolha uma opção para continuar.")
        for index, name in enumerate(["Informações", "Contato"]):
            f(f"response.sections.0.rows.{index}.title").fill(name)
            f(f"response.sections.0.rows.{index}.action.text").fill("Olá, {name}! Opção sintética.")
        f("response.sections.0.rows.0.title").scroll_into_view_if_needed()
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-menu.png"), animations="disabled")
        page.locator("[data-sc-form=editor] button[type=submit]").click()
        expect(page.locator("[data-sc-card]")).to_have_count(4)
        a("create").click()
        f("keyword").fill("Rascunho que será descartado")
        page.locator(".os-chatbot-editor .os-close").click()
        expect(page.locator("[data-sc-form=discard]")).to_be_visible()
        close_modal(page, "discard")
        expect(f("keyword")).to_have_value("Rascunho que será descartado")
        page.locator(".os-chatbot-editor .os-close").click()
        page.locator("[data-sc-form=discard] button[type=submit]").click()

        a("export").click()
        expect(page.locator("[data-sc-select-id]")).to_have_count(4)
        expect(page.locator("[data-sc-form=export] button[type=submit]")).to_be_disabled()
        page.locator("[data-sc-export-all]").check()
        f("export_format").select_option("json")
        with page.expect_download() as download_info:
            page.locator("[data-sc-form=export] button[type=submit]").click()
        assert download_info.value.suggested_filename == "orion-chatbot.json"
        assert download_info.value.failure() is None
        assert json.loads(Path(download_info.value.path()).read_text())["format"] == "orion.subscription-chatbot"
        a("import").click()
        page.locator("[data-sc-import-file]").set_input_files({"name": "orion-chatbot.json",
            "mimeType": "application/json", "buffer": Path(download_info.value.path()).read_bytes()} )
        page.locator("[data-sc-form=import] button[type=submit]").click()
        expect(page.locator(".os-chatbot-preview-counts>div")).to_have_count(6)
        assert_layout(page, width)
        page.screenshot(path=str(ARTIFACTS / f"{label}-import-preview.png"), animations="disabled")
        close_modal(page, "import")
        a("backups").click()
        expect(a("backup-restore")).to_be_visible()
        for mode in ["add_missing", "merge", "exact"]:
            a("backup-restore").click()
            page.locator(f'[data-sc-field="transfer_mode"][value="{mode}"]').check()
            page.locator("[data-sc-form=restore] button[type=submit]").click()
            expect(page.locator(".os-chatbot-preview-counts>div")).to_have_count(6)
            if mode == "exact":
                expect(page.locator("[data-sc-transfer-submit]")).to_be_disabled()
                f("restore_confirmation").fill("RESTORE")
                expect(page.locator("[data-sc-transfer-submit]")).to_be_enabled()
                assert_layout(page, width)
                page.screenshot(path=str(ARTIFACTS / f"{label}-exact-preview.png"), animations="disabled")
            close_modal(page, "restore")
        close_modal(page, "backups")
        a("delete-all").click()
        expect(f("delete_all_confirm")).to_have_value("no")
        expect(page.locator("[data-sc-form=delete-all] button[type=submit]")).to_be_disabled()
        close_modal(page, "delete-all")

        navigation(page)
        page.locator("#osNav [data-os-route=ajuda]").click()
        expect(page.locator("#osHelpSearch")).to_be_visible()
        navigation(page)
        page.locator("#osNav [data-os-route=chatbot]").click()
        expect(page.locator("[data-sc-card]")).to_have_count(4)
        report = page.request.get(fixture_url + "/__fixture/chatbot-calls").json()
        calls = report["calls"]
        writes = [call for call in calls if call["method"] != "GET"]
        assert [(call["method"], call["path"]) for call in writes] == [
            ("PUT", CHATBOT_BASE + "/rules/" + RULE_ID), ("POST", CHATBOT_BASE + "/rules"),
            ("POST", CHATBOT_BASE + "/rules"), ("POST", CHATBOT_BASE + "/exports"),
            ("POST", CHATBOT_BASE + "/imports/preview"),
            *[("POST", CHATBOT_BASE + "/backups/" + BACKUP_ID + "/preview")] * 3]
        assert not report["unexpected"], report["unexpected"]
        assert not any("/test" in call["path"] or "/url-tags" in call["path"] or "/apply" in call["path"] for call in writes)

        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=none&fixtureReadonly=1&aba=chatbot")
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        assert page.locator("#osNav [data-os-route]").evaluate_all("nodes=>nodes.map(n=>n.dataset.osRoute)") == [route for route in ALLOWED_ROUTES if route not in {"testar-chatbot", "templates"}]
        expect(a("create")).to_be_disabled()
        expect(a("import")).to_be_disabled()
        a("backups").click()
        expect(a("backup-restore")).to_be_disabled()
        expect(a("backup-download")).to_be_enabled()
        with page.expect_download() as download_info:
            a("backup-download").click()
        assert download_info.value.suggested_filename == "orion-chatbot-backup.zip"
        with zipfile.ZipFile(download_info.value.path()) as archive:
            assert json.loads(archive.read("chatbot.json"))["version"] == 1
        close_modal(page, "backups")
        a("export").click()
        page.locator("[data-sc-export-all]").check()
        with page.expect_download() as download_info:
            page.locator("[data-sc-form=export] button[type=submit]").click()
        assert download_info.value.failure() is None
        assert download_info.value.suggested_filename == "orion-chatbot.zip"
        with zipfile.ZipFile(download_info.value.path()) as archive:
            assert json.loads(archive.read("chatbot.json"))["format"] == "orion.subscription-chatbot"
        readonly = page.request.get(fixture_url + "/__fixture/chatbot-calls").json()
        assert [call["path"] for call in readonly["calls"] if call["method"] != "GET"] == [CHATBOT_BASE + "/exports"]
        assert not readonly["unexpected"]

        page.goto(f"{fixture_url}/panel?fixtureRole=owner&fixturePlan=elite&fixtureProfile=standard&aba=chatbot")
        expect(page.locator("#osNav [data-os-route=chatbot]")).to_have_count(0)
        expect(page.locator("#osNav [data-os-route=agenda]")).to_have_count(1)
        expect(page.locator("#osSidebarContext [data-os-route=site]")).to_have_count(1)
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "plano")
        ordinary = page.request.get(fixture_url + "/__fixture/chatbot-calls").json()
        assert not any(call["path"].startswith(CHATBOT_BASE) for call in ordinary["calls"])
        assert not ordinary["unexpected"], ordinary["unexpected"]
        assert not errors, errors
        assert not external, external
    finally:
        if errors:
            page.screenshot(path=str(ARTIFACTS / f"{label}-error.png"), animations="disabled")
        (ARTIFACTS / f"{label}.json").write_text(json.dumps({"engine": engine, "viewport": size,
            "theme": theme, "calls": calls, "errors": errors, "external_requests": external,
            "limitations": ["synthetic in-memory API", "viewport emulation", "no messages, URL requests or restore/import commit"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
