"""Enviar Mensagem through real Orion assets and a synthetic HTTP ledger.

Viewport emulation covers layout and focus, not physical mobile keyboards.
External requests, service workers and real transports are absent or blocked.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser, navigation  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from subscription_direct_messages_fixture import CONTRACT, Handler, SEND_ID, DIRECT_BASE, TEMPLATE_BASE, KINDS, uid

ARTIFACTS = ROOT / "test-artifacts" / "subscription-direct-messages"


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


def action(page, name):
    return page.locator('[data-dm-action="' + name + '"]')


def field(page, name):
    return page.locator('[data-dm-field="' + name + '"]')


def report(page, fixture_url):
    value = page.request.get(fixture_url + "/__fixture/direct-report").json()
    assert not value["unexpected"], value["unexpected"]
    assert not value["effects"], "The synthetic ledger never invokes a transport"
    return value


def writes(value):
    return [row for row in value["calls"] if row["method"] != "GET"]


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/direct-control", data={"mode": mode})
    assert response.ok, response.text()


def open_direct(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=enviar-mensagem" + extra)
    expect(page.locator("#osSubscriptionDirectMessages")).to_be_visible()
    expect(field(page, "text")).to_be_visible()
    expect(action(page, "review")).to_be_enabled()


def fill_text(page, text="Olá, {name}. Texto literal.", phone="5567999999901"):
    field(page, "device_id").select_option(CONTRACT["options"]["devices"][0]["id"])
    field(page, "phone").fill(phone)
    field(page, "text").fill(text)


def prepare(page):
    action(page, "review").click()
    expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "review")


def confirm(page):
    page.locator("[data-dm-consent]").check()
    field(page, "confirmation").fill("ENVIAR")
    action(page, "confirm").click()


def reload_same_session(page, send_id=None):
    path = "/panel?aba=enviar-mensagem" + ("&message_id=" + send_id if send_id else "")
    page.evaluate("path => history.replaceState(history.state, '', path)", path)
    page.reload()
    expect(page.locator("#osSubscriptionDirectMessages")).to_be_visible()


def assert_recovery(page, key):
    value = page.evaluate("history.state.orionDirectRecovery")
    assert set(value) == {"tenant_id", "account_scope", "idempotency_key", "kind", "send_id", "confirming"}
    assert value["idempotency_key"] == key
    encoded = json.dumps(value)
    assert not any(secret in encoded for secret in ("556799", "Contato sintético", "Texto literal", "content_base64", "device_id"))
    storage = page.evaluate("({local:{...localStorage},session:{...sessionStorage}})")
    assert all("direct" not in key.lower() for values in storage.values() for key in values)


def screenshot(page, name):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / (name + ".png")), full_page=True, animations="disabled")


def assert_dialog_geometry(page, size):
    assert_layout(page, size[0])
    rects = page.locator("[data-dm-dialog]").evaluate("""node => {
      const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height};};
      return {dialog:rect(node),body:rect(node.querySelector('.os-dialog-body')),footer:rect(node.querySelector('.os-dialog-footer'))};
    }""")
    assert rects["dialog"]["top"] >= -1 and rects["dialog"]["bottom"] <= size[1] + 1, rects
    assert rects["body"]["height"] >= 200, rects
    assert rects["footer"]["bottom"] <= size[1] + 1, rects


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_native_tabs_drafts_format_emoji_contacts_review_and_confirm(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
    try:
        open_direct(page, fixture_url)
        expect(page.locator("[data-dm-tab]")).to_have_count(4)
        expect(page.locator("[data-dm-tab]")).to_have_text(["Texto", "Mídia", "Template", "Botão com link"])
        assert not writes(report(page, fixture_url))
        fill_text(page, text="Olá, {name}.\nConteúdo literal.", phone="+55 (67) 99999-9901")
        field(page, "text").evaluate("node => {node.focus();node.setSelectionRange(0,3);node.dispatchEvent(new Event('select',{bubbles:true}));}")
        page.locator('[data-dm-format="bold"]').click()
        expect(field(page, "text")).to_have_value("*Olá*, {name}.\nConteúdo literal.")
        field(page, "text").evaluate("node => {node.focus();node.setSelectionRange(5,5);node.dispatchEvent(new Event('select',{bubbles:true}));}")
        action(page, "emoji").click()
        field(page, "emoji_q").fill("sorriso leve")
        page.locator('[data-dm-emoji="🙂"]').click()
        expect(field(page, "text")).to_have_value("*Olá*🙂, {name}.\nConteúdo literal.")
        expect(field(page, "text")).to_be_focused()
        page.locator('[data-dm-tab="media"]').click()
        field(page, "device_id").select_option(uid(30))
        field(page, "phone").fill("5567999999902")
        page.locator("[data-dm-file]").set_input_files({"name": "orientacoes.txt", "mimeType": "text/plain", "buffer": b"Synthetic instructions only"})
        expect(page.locator("[data-dm-file-status]")).to_contain_text("arquivo preparado")
        field(page, "caption").fill("Legenda guardada")
        page.locator('[data-dm-tab="template"]').click()
        field(page, "template_id").select_option(uid(101))
        page.locator('[data-dm-tab="text"]').click()
        expect(field(page, "text")).to_have_value("*Olá*🙂, {name}.\nConteúdo literal.")
        expect(field(page, "phone")).to_have_value("+55 (67) 99999-9901")
        page.locator('[data-dm-tab="media"]').click()
        expect(field(page, "caption")).to_have_value("Legenda guardada")
        expect(page.locator("[data-dm-file-status]")).to_contain_text("orientacoes.txt")
        page.locator('[data-dm-tab="template"]').click()
        expect(field(page, "template_id")).to_have_value(uid(101))
        page.locator('[data-dm-tab="text"]').click()
        page.locator('[data-dm-mode="contacts"]').check()
        expect(page.locator("[data-dm-contact]")).to_have_count(30)
        for index in range(9):
            page.locator('[data-dm-contact="phone:' + str(5567999999901 + index) + '"]').check()
        action(page, "contacts-next").click()
        expect(page.locator("[data-dm-contact]")).to_have_count(5)
        page.locator('[data-dm-contact="phone:5567999999931"]').check()
        expect(page.locator('[data-dm-contact="phone:5567999999932"]')).to_be_disabled()
        expect(page.locator("[data-dm-selected-count]")).to_have_text("10 de 10 contatos selecionados.")
        page.locator("[data-dm-save]").check()
        field(page, "name").fill("Saudação escolhida")
        assert_layout(page, size[0])
        screenshot(page, label + "-form")
        prepare(page)
        expect(page.locator("[data-dm-review-row]")).to_have_count(10)
        expect(action(page, "confirm")).to_be_disabled()
        expect(page.locator("[data-dm-consent]")).not_to_be_checked()
        assert_dialog_geometry(page, size)
        screenshot(page, label + "-review-top")
        field(page, "confirmation").scroll_into_view_if_needed()
        screenshot(page, label + "-review-confirm")
        prepared = report(page, fixture_url)
        direct = [row for row in writes(prepared) if row["path"] == DIRECT_BASE + "/review"]
        assert len(direct) == 1 and not prepared["confirmations"] and not prepared["saved_templates"]
        message = direct[0]["data"]["message"]
        assert set(message) == {"kind", "device_id", "recipients", "text", "save_template"}
        assert message["text"] == "*Olá*🙂, {name}.\nConteúdo literal."
        assert len(set(message["recipients"]["contact_keys"])) == 10
        confirm(page)
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "result")
        expect(page.locator("[data-dm-saved-template]")).to_contain_text("Saudação escolhida")
        action(page, "acknowledge").click()
        expect(page.locator("[data-dm-recipient]")).to_have_count(10)
        expect(page.locator("[data-dm-progress]")).to_have_text("0 de 10 destinatários processados · 0 aceitos pelo transporte")
        assert_layout(page, size[0])
        screenshot(page, label + "-result")
        final = report(page, fixture_url)
        assert len(final["confirmations"]) == len(final["saved_templates"]) == 1
        assert "message_id=" in page.url and "batch_id=" not in page.url
        action(page, "new").click()
        expect(field(page, "text")).to_have_value("")
        page.locator('[data-dm-tab="media"]').click()
        expect(page.locator("[data-dm-file-status]")).to_have_text("Nenhum arquivo preparado.")
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_url_button_draft_literal_safe_preview_and_cancel_without_transport(browser, fixture_url, size, theme):
    """Real form/rendering only; the fixture deliberately cannot confirm CTA."""
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    try:
        open_direct(page, fixture_url)
        fill_text(page, text="Rascunho de texto separado")
        tab = page.get_by_role("tab", name="Botão com link", exact=True)
        tab.click()
        expect(tab).to_have_attribute("aria-selected", "true")
        for label in ("Texto do botão", "Link do botão", "Rodapé (opcional)"):
            expect(page.locator("#osSubscriptionDirectMessages").get_by_text(label, exact=True)).to_be_visible()
        expect(field(page, "text")).to_have_attribute("maxlength", "1024")
        expect(field(page, "button_text")).to_have_attribute("maxlength", "20")
        expect(field(page, "url")).to_have_attribute("type", "url")
        expect(field(page, "url")).to_have_attribute("maxlength", "2048")
        expect(field(page, "footer")).to_have_attribute("maxlength", "60")
        expect(page.locator("[data-dm-save],[data-dm-file],[data-dm-field=template_id]")).to_have_count(0)
        body = "Olá, {name}. <img src=x onerror='window.__dmXss=1'>"
        footer = "<svg onload=window.__dmFooterXss=1>"
        button_text = "<Abrir>"
        url = "https://orionagenda.pro/acesso?origem=teste&modelo=%3Csvg%3E"
        field(page, "device_id").select_option(uid(30))
        field(page, "phone").fill("5567999999903")
        for name, value in (("text", body), ("footer", footer), ("button_text", button_text), ("url", url)):
            field(page, name).fill(value)
        page.get_by_role("tab", name="Texto", exact=True).click()
        expect(field(page, "text")).to_have_value("Rascunho de texto separado")
        expect(field(page, "phone")).to_have_value("5567999999901")
        tab.click()
        expect(field(page, "device_id")).to_have_value(uid(30))
        expect(field(page, "phone")).to_have_value("5567999999903")
        for name, value in (("text", body), ("footer", footer), ("button_text", button_text), ("url", url)):
            expect(field(page, name)).to_have_value(value)
        assert not writes(report(page, fixture_url))
        assert_layout(page, size[0])
        prepare(page)
        preview = page.locator("[data-dm-cta-preview]")
        expect(preview).to_have_count(1)
        expect(preview.locator("code")).to_have_text(url)
        expect(preview.locator("strong")).to_have_text(button_text)
        expect(preview.locator("small")).to_have_text(footer)
        expect(page.locator("[data-dm-review-row]")).to_contain_text(body)
        expect(preview.locator("a,img,svg,iframe,script")).to_have_count(0)
        expect(page.locator("[data-dm-review-row] .os-dm-message img,[data-dm-review-row] .os-dm-message svg")).to_have_count(0)
        assert page.evaluate("window.__dmXss || window.__dmFooterXss || null") is None
        expect(page.locator("[data-dm-dialog]")).to_contain_text("Prévia sintética sem transporte habilitado.")
        expect(action(page, "confirm")).to_be_disabled()
        expect(page.locator("[data-dm-consent]")).not_to_be_checked()
        assert_dialog_geometry(page, size)
        screenshot(page, f"{browser[0]}-{size[0]}x{size[1]}-{theme}-url-button-preview")
        prepared = report(page, fixture_url)
        requests = writes(prepared)
        assert len(requests) == 1 and requests[0]["path"] == DIRECT_BASE + "/review"
        assert requests[0]["data"]["message"] == {"kind": "url_button", "device_id": uid(30),
            "recipients": {"mode": "phone", "phone": "5567999999903", "contact_keys": []}, "text": body,
            "footer": footer, "button_text": button_text, "url": url}
        frozen = prepared["operations"][0]["operation"]["preview"]
        assert (frozen["eligible"], frozen["blocked"]) == (0, 1)
        assert frozen["items"][0]["message_preview"]["parts"] == [{"type": "url_button", "text": body,
            "footer": footer, "button_text": button_text, "url": url}]
        action(page, "cancel-review").click()
        expect(page.locator("[data-dm-dialog]")).to_have_count(0)
        expect(action(page, "review")).to_be_focused()
        expect(field(page, "text")).to_have_value(body)
        final = report(page, fixture_url)
        assert writes(final) == requests
        assert not final["confirmations"] and not final["saved_templates"] and not final["uploads"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_media_caption_normalization_audio_suppression_and_canonical_filename(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url, (390, 844), "dark")
    try:
        open_direct(page, fixture_url)
        page.locator('[data-dm-tab="media"]').click()
        expect(page.locator("[data-dm-mode]")).to_have_count(0)
        field(page, "device_id").select_option(uid(30))
        field(page, "phone").fill("5567999999901")
        page.locator("[data-dm-file]").set_input_files({"name": "foto.png", "mimeType": "image/png", "buffer": b"Synthetic fixture media"})
        expect(page.locator("[data-dm-file-status]")).to_contain_text("foto.jpg")
        field(page, "caption").fill("  Primeira\nSegunda  ")
        prepare(page)
        expect(page.locator("[data-dm-dialog]")).to_contain_text("Primeira Segunda")
        expect(page.locator("[data-dm-dialog]")).to_contain_text("quebras de linha")
        first = report(page, fixture_url)["operations"][0]["operation"]["preview"]["items"][0]["message_preview"]
        assert first["caption"] == "  Primeira Segunda  "
        action(page, "cancel-review").click()
        page.locator("[data-dm-file]").set_input_files({"name": "recado.wav", "mimeType": "audio/wav", "buffer": b"Synthetic audio fixture"})
        expect(page.locator("[data-dm-file-status]")).to_contain_text("recado.mp3")
        field(page, "caption").fill("Nunca enviar esta legenda")
        prepare(page)
        expect(page.locator("[data-dm-dialog]")).to_contain_text("não acompanha arquivos de áudio")
        expect(page.locator("[data-dm-dialog]")).not_to_contain_text("Nunca enviar esta legenda")
        value = report(page, fixture_url)
        content = value["operations"][-1]["operation"]["preview"]["items"][0]["message_preview"]
        assert len(content["parts"]) == 1 and content["parts"][0]["type"] == "audio"
        assert content["caption"] is None and content["followup"] is None
        assert not value["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_existing_eight_library_types_keep_template_semantics_and_no_hidden_union_fields(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url, (768, 1024), "light")
    try:
        open_direct(page, fixture_url)
        page.locator('[data-dm-tab="template"]').click()
        field(page, "device_id").select_option(uid(30))
        field(page, "phone").fill("5567999999901")
        expect(page.locator("[data-dm-save],[data-dm-file]")).to_have_count(0)
        for index, kind in enumerate(KINDS):
            field(page, "template_id").select_option(uid(100 + index))
            prepare(page)
            row = report(page, fixture_url)["operations"][-1]
            assert row["operation"]["preview"]["items"][0]["message_preview"]["type"] == kind
            assert set(row["data"]["message"]) == {"kind", "device_id", "recipients", "template_id"}
            action(page, "cancel-review").click()
            expect(action(page, "review")).to_be_focused()
        assert not report(page, fixture_url)["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["review", "confirm", "action"])
def test_lost_response_reload_recovers_same_request_without_duplicate_write(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url, (390, 844), "light")
    try:
        open_direct(page, fixture_url)
        if phase == "action":
            reload_same_session(page, SEND_ID)
            expect(action(page, "cancel")).to_be_visible()
            action(page, "cancel").click()
            expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "cancel")
            control(page, fixture_url, "lost_action")
            action(page, "confirm-cancel").click()
        else:
            fill_text(page)
            if phase == "review":
                control(page, fixture_url, "lost_review")
                action(page, "review").click()
            else:
                prepare(page)
                control(page, fixture_url, "lost_confirm")
                confirm(page)
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "unresolved")
        before = report(page, fixture_url)
        key = writes(before)[-1]["data"]["idempotency_key"]
        assert_recovery(page, key)
        reload_same_session(page, SEND_ID if phase == "action" else None)
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "review" if phase == "review" else "result")
        after = report(page, fixture_url)
        assert writes(after) == writes(before)
        assert len(after["operations"]) == 1
        assert_recovery(page, key)
        if phase == "review":
            expect(page.locator("[data-dm-consent]")).not_to_be_checked()
            expect(field(page, "confirmation")).to_have_value("")
            assert not after["confirmations"]
        elif phase == "confirm":
            assert len(after["confirmations"]) == 1
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("mode", ["retry-same", "correct"])
def test_missing_admission_requires_explicit_same_key_retry_or_correction(browser, fixture_url, mode):
    context, page, external, errors = setup_page(browser, fixture_url, (390, 844), "dark")
    try:
        open_direct(page, fixture_url)
        fill_text(page)
        control(page, fixture_url, "missing_review")
        action(page, "review").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "unresolved")
        expect(action(page, mode)).to_have_count(0)
        action(page, "recover").last.click()
        expect(action(page, mode)).to_be_visible()
        original = writes(report(page, fixture_url))[0]["data"]
        action(page, mode).click()
        if mode == "correct":
            field(page, "text").fill("Correção explícita")
            action(page, "review").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "review")
        requests = writes(report(page, fixture_url))
        assert len(requests) == 2
        assert requests[-1]["data"]["idempotency_key"] == original["idempotency_key"]
        if mode == "retry-same":
            assert requests[-1]["data"] == original
        else:
            assert requests[-1]["data"]["message"]["text"] == "Correção explícita"
        assert not report(page, fixture_url)["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_cancel_proof_progress_and_uncertain_result_remain_distinct(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url, (1440, 900), "light")
    try:
        open_direct(page, fixture_url)
        control(page, fixture_url, "progress")
        reload_same_session(page, SEND_ID)
        expect(page.locator("[data-dm-recipient]")).to_have_count(6)
        expect(page.locator("[data-dm-progress]")).to_have_text("4 de 6 destinatários processados · 1 aceitos pelo transporte")
        expect(page.locator("[data-dm-recipient] button")).to_have_count(0)
        action(page, "cancel").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "cancel")
        control(page, fixture_url, "stale_action")
        action(page, "confirm-cancel").click()
        expect(page.locator("[data-dm-dialog]")).to_have_count(0)
        expect(page.locator("#osSubscriptionDirectMessages")).to_contain_text("cancelamento não foi registrado")
        assert page.evaluate("history.state.orionDirectRecovery || null") is None
        action(page, "cancel").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "cancel")
        action(page, "confirm-cancel").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "result")
        action(page, "acknowledge").click()
        expect(page.locator(".os-dm-result > header h2")).to_have_text("Resultado incerto")
        final = report(page, fixture_url)
        details = next(item for item in final["sends"] if item["send"]["send_id"] == SEND_ID)
        assert next(row for row in details["recipients"]["items"] if row["name"].endswith("uncertain"))["status"] == "uncertain"
        assert len(final["operations"]) == 1 and not final["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_expiry_malformed_reply_revocation_and_subscription_boundary(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url, (768, 1024), "dark")
    try:
        open_direct(page, fixture_url)
        fill_text(page)
        control(page, fixture_url, "malformed_review")
        action(page, "review").click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "unresolved")
        expect(action(page, "confirm")).to_have_count(0)
        action(page, "recover").last.click()
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "review")
        control(page, fixture_url, "expire_review")
        reload_same_session(page)
        expect(page.locator("[data-dm-dialog]")).to_have_attribute("data-dm-view", "expired")
        action(page, "acknowledge").click()
        expect(action(page, "review")).to_be_enabled()
        before = writes(report(page, fixture_url))
        control(page, fixture_url, "revoke")
        with page.expect_response(lambda response: response.status == 403 and DIRECT_BASE in response.url):
            action(page, "refresh").click()
        expect(page.locator('#osNav [data-os-route="enviar-mensagem"]')).to_have_count(0)
        assert writes(report(page, fixture_url)) == before
        assert page.evaluate("history.state.orionDirectRecovery || null") is None
        for extra, route in (("&fixtureProfile=standard", "plano"), ("&fixtureReadonly=1", "novidades")):
            # The denied route still reads identity and loads its fallback.
            # Wait for that concrete bootstrap before replacing the document.
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/v1/account/business-identity") as identity:
                page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=enviar-mensagem" + extra)
            response = identity.value
            assert response.status == 200
            identity_data = response.json()
            expect(page.locator("#memberBusinessName")).to_have_value(identity_data["name"])
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", route)
            expect(page.locator("#page-suite .os-loading")).to_have_count(0)
            expect(page.locator('#osNav [data-os-route="enviar-mensagem"]')).to_have_count(0)
            expect(page.locator('#osNav [data-os-route="conversas"]')).to_have_count(0)
            assert report(page, fixture_url)["calls"] == []
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_reload_drops_unconfirmed_private_drafts_without_local_storage(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url, (390, 844), "light")
    try:
        open_direct(page, fixture_url)
        fill_text(page, text="Rascunho privado")
        page.locator('[data-dm-tab="media"]').click()
        field(page, "caption").fill("Legenda privada")
        page.locator("[data-dm-file]").set_input_files({"name": "privado.txt", "mimeType": "text/plain", "buffer": b"Private synthetic draft"})
        expect(page.locator("[data-dm-file-status]")).to_contain_text("arquivo preparado")
        page.locator('[data-dm-tab="template"]').click()
        field(page, "template_id").select_option(uid(101))
        assert page.evaluate("history.state.orionDirectRecovery || null") is None
        reload_same_session(page)
        expect(field(page, "text")).to_have_value("")
        page.locator('[data-dm-tab="media"]').click()
        expect(field(page, "caption")).to_have_value("")
        expect(page.locator("[data-dm-file-status]")).to_have_text("Nenhum arquivo preparado.")
        page.locator('[data-dm-tab="template"]').click()
        expect(field(page, "template_id")).to_have_value("")
        value = report(page, fixture_url)
        assert len(writes(value)) == 1 and not value["operations"] and not value["confirmations"]
        storage = page.evaluate("JSON.stringify({local:{...localStorage},session:{...sessionStorage},history:history.state})")
        assert not any(secret in storage for secret in ("privado.txt", "Rascunho privado", "Legenda privada", "content_base64"))
        assert not external, external
        assert not errors, errors
    finally:
        context.close()
