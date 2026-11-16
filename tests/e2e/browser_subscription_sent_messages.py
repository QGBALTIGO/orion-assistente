"""Stage-05 history on shipped Orion assets with local synthetic HTTP records.

No database, external transport, paid provider or real WhatsApp message is used.
The PostgreSQL suite separately exercises actual cookies, CSRF, RLS and resend
admission. Viewport emulation does not claim physical-device keyboard coverage.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import (
    Handler as ChatbotHandler, ROOT, SIZES, assert_layout, base, browser, navigation, now,
)  # noqa: F401 - existing browser matrix uses official Chrome when configured.
from browser_subscription_chatbot_simulator import setup_page
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_sent_messages_contract.json").read_text())
MESSAGES_BASE = base.BASE + "/subscription-whatsapp/sent-messages"
ARTIFACTS = ROOT / "test-artifacts" / "subscription-sent-messages"
SNAPSHOT = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
DEVICE_ID = CONTRACT["list_response"]["devices"][0]["id"]
FAILED_ID = CONTRACT["item_examples"]["failed"]["id"]
UNCERTAIN_ID = CONTRACT["item_examples"]["uncertain"]["id"]


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def records():
    rows = [deepcopy(CONTRACT["item_examples"][status]) for status in ("sent", "failed", "uncertain", "pending")]
    for index in range(4, 35):
        row = deepcopy(CONTRACT["item_examples"]["sent"])
        row["id"] = row["root_effect_id"] = f"81000000-0000-4000-8000-{index + 1:012d}"
        row["version"] = sha256(row["id"].encode()).hexdigest()
        row["recipient"]["display_name"] = f"Contato sintético {index + 1:02d}"
        row["sent"]["text"] = f"Mensagem sintética {index + 1:02d}."
        rows.append(row)
    for index, row in enumerate(rows):
        row["created_at"] = stamp(SNAPSHOT - timedelta(minutes=index + 1))
        if row["finished_at"] is not None:
            row["finished_at"] = stamp(SNAPSHOT - timedelta(minutes=index + 1) + timedelta(seconds=2))
    rows[0]["recipient"]["display_name"] = "<img src=x onerror=alert(1)>"
    rows[0]["sent"]["text"] = "Texto literal <script>alert(1)</script> com *destaque*."
    rows[1]["recipient"]["display_name"] = "Contato da renovação"
    rows[1]["recipient"]["phone_e164"] = "+5567999999903"
    return rows


class Handler(ChatbotHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                base._sessions[cookie].update(
                    sm_items=[] if query.get("fixtureEmpty") == ["1"] else records(),
                    sm_calls=[], sm_ledger={}, sm_next=None, sm_revoked=False,
                    sm_clock=SNAPSHOT, sm_gate=None, sm_unknown_only=query.get("fixtureUnknown") == ["1"],
                )
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            subscription = session["workspace_profile"] == "subscription_services"
            plan["workspace_profile"] = session["workspace_profile"]
            if subscription:
                features = ["account.manage", "business.setup", "devices.manage"]
                if not session["chatbot_readonly"]:
                    features.extend(["chatbot.manage", "templates.manage"])
                if not session["chatbot_readonly"] and not session["sm_revoked"]:
                    features.append("sent_messages.manage")
                plan["features"] = features
                plan["permissions"] = {key: key in features for key in
                    {*plan["permissions"], "devices.manage", "chatbot.manage", "templates.manage", "sent_messages.manage"}}
            else:
                plan["permissions"].update({"devices.manage": False, "chatbot.manage": False,
                    "templates.manage": False, "sent_messages.manage": False})
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            # An aborted obsolete read must not keep its browser context alive.
            return None

    def allowed(self, session):
        return (session and session["workspace_profile"] == "subscription_services"
                and not session["chatbot_readonly"] and not session["sm_revoked"])

    def error(self, code, status=409):
        labels = {
            "WORKSPACE_FEATURE_UNAVAILABLE": "O acesso às mensagens enviadas foi revogado.",
            "MESSAGE_VERSION_CONFLICT": "A mensagem mudou. Atualize os dados antes de reenviar.",
            "MESSAGE_IDEMPOTENCY_CONFLICT": "A identidade desta solicitação já foi utilizada.",
            "MESSAGE_NOT_FOUND": "Mensagem não encontrada.",
            "MESSAGE_ALREADY_RETRIED": "Esta tentativa já tem um reenvio registrado.",
        }
        return self.body({"detail": {"code": code, "message": labels.get(code, "Não foi possível concluir a solicitação.")}}, status)

    def history(self, session, query):
        data = deepcopy(CONTRACT["list_response"])
        snapshot = query.get("snapshot", [stamp(session["sm_clock"])])[0]
        boundary = datetime.fromisoformat(snapshot.replace("Z", "+00:00"))
        rows = [deepcopy(row) for row in session["sm_items"]
                if datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) <= boundary]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        total = len(rows)
        status = query.get("status", [""])[0]
        if status == "failed":
            rows = [row for row in rows if row["status"] in {"failed", "uncertain"}]
        elif status == "failed_resendable":
            rows = [row for row in rows if row["status"] == "failed" and row["resend"]["eligible"]]
        for name in ("q", "contact"):
            if query.get(name):
                term = query[name][0].casefold()
                rows = [row for row in rows if term in (json.dumps(row["recipient"], ensure_ascii=False)
                    + (" " + json.dumps({"received": row["received"], "sent": row["sent"]}, ensure_ascii=False) if name == "q" else "")).casefold()]
        if query.get("device_id"):
            rows = [row for row in rows if row["device"]["id"] == query["device_id"][0]]
        page = int(query.get("page", ["1"])[0])
        data.update(items=rows[(page - 1) * 30:page * 30], page=page, per_page=30,
                    filtered_total=len(rows), total_pages=max(1, (len(rows) + 29) // 30),
                    snapshot=snapshot, generated_at=now(),
                    counts={"total": total, "today": total,
                            "last_30_days": total, "scope": "message_attempts"})
        if session["sm_unknown_only"]:
            data["transcription"]["total"] = {"known_seconds": 0, "unknown_completions": 2, "complete": False}
            data["transcription"]["month"] = {"known_seconds": 0, "unknown_completions": 1, "complete": False}
        return data

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/sent-messages-report":
            return self.body({"calls": session["sm_calls"], "items": session["sm_items"],
                "ledger": list(session["sm_ledger"].values()), "unexpected": session["unexpected"]})
        if not path.startswith(MESSAGES_BASE):
            return super().do_GET()
        if session:
            session["sm_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(MESSAGES_BASE):]
        if not suffix:
            if session["sm_next"] == "read_error":
                session["sm_next"] = None
                return self.error("SYNTHETIC_UNAVAILABLE", 503)
            result = self.history(session, query)
            if query.get("q") == ["antigo"] and session["sm_gate"] is not None:
                gate = session["sm_gate"]
                if not gate.wait(15):
                    return self.error("SYNTHETIC_GATE_TIMEOUT", 408)
            return self.body(result)
        if suffix.startswith("/resend-requests/"):
            saved = session["sm_ledger"].get(suffix.rsplit("/", 1)[-1])
            return self.body({**saved["result"], "replayed": True}) if saved else self.error("MESSAGE_NOT_FOUND", 404)
        item = next((row for row in session["sm_items"] if suffix == "/" + row["id"]), None)
        return self.body({"item": deepcopy(item), "timezone": "America/Cuiaba", "generated_at": now()}) if item else self.error("MESSAGE_NOT_FOUND", 404)

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/sent-messages-control" and self.command == "POST":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = data["mode"]
            if mode in {"lost", "read_error", "conflict"}:
                session["sm_next"] = mode
            elif mode == "revoke":
                session["sm_revoked"] = True
            elif mode == "block":
                session["sm_gate"] = threading.Event()
            elif mode == "release":
                session["sm_gate"].set()
                session["sm_gate"] = None
            elif mode == "insert":
                row = deepcopy(CONTRACT["item_examples"]["sent"])
                row["id"] = row["root_effect_id"] = str(uuid4())
                session["sm_clock"] += timedelta(seconds=1)
                row["created_at"] = row["finished_at"] = stamp(session["sm_clock"])
                row["sent"]["text"] = "Entrada criada depois da primeira página."
                session["sm_items"].append(row)
            else:
                return self.reject(session, "Unknown synthetic sent-history control")
            return self.body({"ok": True})
        if not path.startswith(MESSAGES_BASE):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.command != "POST" or not path.endswith("/resend"):
            return self.reject(session, "Only explicit synthetic resend admission is allowed")
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if set(data) != {"expected_version", "idempotency_key"}:
                raise ValueError()
            UUID(data["idempotency_key"])
            if not re.fullmatch("[0-9a-f]{64}", data["expected_version"]):
                raise ValueError()
        except (ValueError, TypeError):
            return self.body({"detail": "Invalid synthetic resend request"}, 422)
        session["sm_calls"].append({"method": "POST", "path": path, "data": deepcopy(data)})
        old = session["sm_ledger"].get(data["idempotency_key"])
        if old:
            if old["path"] != path or old["data"] != data:
                return self.error("MESSAGE_IDEMPOTENCY_CONFLICT")
            return self.body({**old["result"], "replayed": True}, 202)
        original_id = path.rsplit("/", 2)[-2]
        original = next((row for row in session["sm_items"] if row["id"] == original_id), None)
        if original is None:
            return self.error("MESSAGE_NOT_FOUND", 404)
        if not original["resend"]["eligible"]:
            return self.error("MESSAGE_ALREADY_RETRIED")
        if session["sm_next"] == "conflict" or original["version"] != data["expected_version"]:
            session["sm_next"] = None
            original["version"] = sha256((original["version"] + "changed").encode()).hexdigest()
            return self.error("MESSAGE_VERSION_CONFLICT")
        item = deepcopy(original)
        item.update(id=str(uuid4()), retry_of_effect_id=original["id"], status="pending", status_label="Pendente",
                    outcome="pending", failure=None, finished_at=None,
                    actions=[], resend={"eligible": False, "blocked_reason": "A tentativa ainda está em andamento."})
        item["origin"] = {**item["origin"], "is_resend": True}
        session["sm_clock"] += timedelta(seconds=1)
        item["created_at"] = stamp(session["sm_clock"])
        item["version"] = sha256(item["id"].encode()).hexdigest()
        session["sm_items"].append(item)
        original["actions"] = []
        original["resend"] = {"eligible": False, "blocked_reason": "Já existe um reenvio desta tentativa."}
        result = {"request_id": str(uuid4()), "original_effect_id": original["id"],
                  "effect_id": item["id"], "root_effect_id": original["root_effect_id"],
                  "replayed": False, "status": "pending", "outcome": "pending"}
        session["sm_ledger"][data["idempotency_key"]] = {"path": path, "data": deepcopy(data), "result": result}
        if session["sm_next"] == "lost":
            session["sm_next"] = None
            return self.error("SYNTHETIC_REPLY_LOST", 503)
        return self.body(result, 202)


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


def report(page, fixture_url):
    data = page.request.get(fixture_url + "/__fixture/sent-messages-report").json()
    assert not data["unexpected"], data["unexpected"]
    assert all(call["method"] == "GET" or call["path"].endswith("/resend") for call in data["calls"])
    return data


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/sent-messages-control", data={"mode": mode})
    assert response.ok, response.text()


def writes(data):
    return [call for call in data["calls"] if call["method"] == "POST"]


def row(page, identity):
    return page.locator('[data-sm-row="' + identity + '"]')


def field(page, name):
    return page.locator('[data-sm-field="' + name + '"]')


def action(page, name):
    return page.locator('[data-sm-action="' + name + '"]')


def filtered(page, **values):
    for key, value in values.items():
        if key in {"status", "device_id"}:
            field(page, key).select_option(value)
        else:
            field(page, key).fill(value)
    action(page, "filter").click()


def last_listing(data):
    return [call for call in data["calls"] if call["method"] == "GET" and call["path"] == MESSAGES_BASE][-1]


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_history_snapshot_filters_details_and_explicit_resend(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=mensagens-enviadas")
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "mensagens-enviadas")
        expect(page.locator("[data-sm-row]")).to_have_count(30)
        expect(page.locator('[data-sm-card="total"]')).to_contain_text("35")
        navigation(page)
        expect(page.locator('#osNav [data-os-route="mensagens-enviadas"]')).to_be_visible()
        menu = page.locator("#osMenu")
        if menu.is_visible() and menu.get_attribute("aria-expanded") == "true":
            # The open drawer intentionally covers the header menu trigger.
            # Use its visible closing control, as a user does on a phone.
            page.locator("#osCollapse").click()
            expect(menu).to_have_attribute("aria-expanded", "false")
        assert_layout(page, size[0])
        assert page.locator("[data-sm-results] img, [data-sm-results] script").count() == 0
        assert not writes(report(page, fixture_url))
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
        page.screenshot(path=str(ARTIFACTS / (label + "-history.png")), full_page=True)

        first_ids = page.locator("[data-sm-row]").evaluate_all("rows => rows.map(row => row.dataset.smRow)")
        control(page, fixture_url, "insert")
        action(page, "next").click()
        expect(page.locator("[data-sm-row]")).to_have_count(5)
        second_ids = page.locator("[data-sm-row]").evaluate_all("rows => rows.map(row => row.dataset.smRow)")
        assert not set(first_ids) & set(second_ids)
        request = last_listing(report(page, fixture_url))
        assert request["query"]["page"] == ["2"]
        assert request["query"]["snapshot"] == [stamp(SNAPSHOT)]
        expect(action(page, "next")).to_be_disabled()
        action(page, "prev").click()
        expect(page.locator("[data-sm-row]")).to_have_count(30)
        assert page.locator("[data-sm-row]").evaluate_all("rows => rows.map(row => row.dataset.smRow)") == first_ids

        filtered(page, status="failed")
        expect(page.locator("[data-sm-row]")).to_have_count(2)
        expect(row(page, UNCERTAIN_ID)).to_be_visible()
        expect(row(page, UNCERTAIN_ID).locator('[data-sm-action="resend"]')).to_have_count(0)
        filtered(page, status="failed_resendable", device_id=DEVICE_ID, contact="+5567999999903", q="renov")
        expect(page.locator("[data-sm-row]")).to_have_count(1)
        request = last_listing(report(page, fixture_url))
        assert request["query"]["status"] == ["failed_resendable"]
        assert request["query"]["device_id"] == [DEVICE_ID]
        assert request["query"]["contact"] == ["+5567999999903"]
        assert request["query"]["q"] == ["renov"]
        assert "snapshot" not in request["query"]
        expect(page.locator('[data-sm-card="total"]')).to_contain_text("36")
        row(page, FAILED_ID).locator('[data-sm-action="detail"]').click()
        dialog = page.locator("[data-sm-dialog]")
        expect(dialog).to_have_attribute("data-sm-view", "detail")
        expect(dialog).to_contain_text("Sua assinatura pode ser renovada hoje.")
        expect(dialog).to_contain_text("Quero renovar minha assinatura.")
        expect(dialog).to_contain_text("A conexão não estava disponível antes do envio.")
        assert not writes(report(page, fixture_url))
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-detail.png")), full_page=True)
        dialog.locator('[data-sm-action="close-dialog"]').last.click()
        expect(dialog).to_have_count(0)

        row(page, FAILED_ID).locator('[data-sm-action="resend"]').click()
        expect(dialog).to_have_attribute("data-sm-view", "confirm")
        dialog.locator('[data-sm-action="close-dialog"]').last.click()
        assert not writes(report(page, fixture_url))
        row(page, FAILED_ID).locator('[data-sm-action="resend"]').click()
        expect(dialog).to_have_attribute("data-sm-view", "confirm")
        dialog.locator('[data-sm-action="confirm-resend"]').click()
        expect(dialog).to_have_attribute("data-sm-view", "result")
        data = report(page, fixture_url)
        sent, = writes(data)
        assert sent["path"] == MESSAGES_BASE + "/" + FAILED_ID + "/resend"
        assert set(sent["data"]) == {"idempotency_key", "expected_version"}
        assert sent["data"]["expected_version"] == CONTRACT["item_examples"]["failed"]["version"]
        assert len(data["ledger"]) == 1
        assert data["ledger"][0]["result"]["request_id"] != sent["data"]["idempotency_key"]
        assert len([item for item in data["items"] if item["retry_of_effect_id"] == FAILED_ID]) == 1
        original = next(item for item in data["items"] if item["id"] == FAILED_ID)
        assert original["status"] == "failed" and original["sent"] == CONTRACT["item_examples"]["failed"]["sent"]
        dialog.locator('[data-sm-action="close-dialog"]').last.click()
        action(page, "reset").click()
        expect(page.locator("[data-sm-row]")).to_have_count(30)
        query = last_listing(report(page, fixture_url))["query"]
        assert not {"status", "device_id", "contact", "q", "snapshot"} & set(query)
        expect(field(page, "contact")).to_have_value("")
        expect(field(page, "q")).to_have_value("")
        assert_layout(page, size[0])
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


def test_lost_admission_response_recovers_after_reload_by_get_only(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=mensagens-enviadas")
        expect(row(page, FAILED_ID)).to_be_visible()
        row(page, FAILED_ID).locator('[data-sm-action="resend"]').click()
        expect(page.locator("[data-sm-dialog]")).to_have_attribute("data-sm-view", "confirm")
        control(page, fixture_url, "lost")
        action(page, "confirm-resend").click()
        expect(page.locator("[data-sm-dialog]")).to_have_attribute("data-sm-view", "pending")
        expect(page.locator("[data-sm-dialog]")).to_contain_text("Não foi possível confirmar a solicitação")
        initial = report(page, fixture_url)
        request, = writes(initial)
        assert len(initial["ledger"]) == 1
        # Preserve the client recovery state, while removing only fixture reset
        # parameters so the server retains this same synthetic browser session.
        page.evaluate("history.replaceState(history.state, '', '/panel?aba=mensagens-enviadas')")
        page.reload()
        expect(page.locator("[data-sm-feedback]")).to_contain_text("Reenvio registrado")
        expect(page.locator("[data-sm-dialog]")).to_have_count(0)
        data = report(page, fixture_url)
        assert writes(data) == [request]
        recoveries = [call for call in data["calls"] if "/resend-requests/" in call["path"]]
        assert recoveries and all(call["method"] == "GET" for call in recoveries)
        assert recoveries[-1]["path"].endswith("/" + request["data"]["idempotency_key"])
        assert len(data["ledger"]) == 1
        assert not errors, errors
        assert not external, external
    finally:
        context.close()


def test_conflict_revocation_other_niche_and_empty_state_do_not_send(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=mensagens-enviadas")
        expect(row(page, FAILED_ID)).to_be_visible()
        row(page, FAILED_ID).locator('[data-sm-action="resend"]').click()
        expect(page.locator("[data-sm-dialog]")).to_have_attribute("data-sm-view", "confirm")
        control(page, fixture_url, "conflict")
        action(page, "confirm-resend").click()
        expect(page.locator("[data-sm-dialog]")).to_have_attribute("data-sm-view", "error")
        expect(page.locator("[data-sm-dialog]")).to_contain_text("mudou")
        assert len(writes(report(page, fixture_url))) == 1
        assert not report(page, fixture_url)["ledger"]
        action(page, "close-dialog").last.click()
        control(page, fixture_url, "revoke")
        action(page, "refresh").click()
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "novidades")
        expect(page.locator('#osNav [data-os-route="mensagens-enviadas"]')).to_have_count(0)
        expect(page.locator("[data-sm-row]")).to_have_count(0)
        assert len(writes(report(page, fixture_url))) == 1

        for query, fallback in (("fixtureProfile=standard", "plano"), ("fixtureReadonly=1", "novidades")):
            page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=mensagens-enviadas&" + query)
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", fallback)
            expect(page.locator('#osNav [data-os-route="mensagens-enviadas"]')).to_have_count(0)
            assert not report(page, fixture_url)["calls"]

        page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=mensagens-enviadas&fixtureEmpty=1&fixtureUnknown=1")
        expect(page.locator("[data-sm-empty]")).to_be_visible()
        expect(page.locator('[data-sm-card="total"]')).to_contain_text("0")
        expect(page.locator("[data-sm-row]")).to_have_count(0)
        expect(page.locator('[data-sm-card="asr_total"] strong')).to_have_text("Não informado")
        expect(page.locator('[data-sm-card="asr_month"] strong')).to_have_text("Não informado")
        assert not writes(report(page, fixture_url))
        assert not errors, errors
        assert not external, external
    finally:
        context.close()
