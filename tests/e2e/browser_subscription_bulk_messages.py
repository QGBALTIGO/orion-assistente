"""Mensagem em Massa through real Orion assets and a synthetic HTTP ledger.

Viewport emulation covers layout and keyboard focus, not physical keyboards.
All external requests and service workers are blocked by the shared harness.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser, navigation  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from subscription_bulk_messages_fixture import CONTRACT, Handler, BATCH_ID, BULK_BASE, uid

ARTIFACTS = ROOT / "test-artifacts" / "subscription-bulk-messages"


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
    return page.locator('[data-bm-action="' + name + '"]')


def field(page, name):
    return page.locator('[data-bm-field="' + name + '"]')


def report(page, fixture_url):
    result = page.request.get(fixture_url + "/__fixture/bulk-report").json()
    assert not result["unexpected"], result["unexpected"]
    assert not result["effects"], "The synthetic ledger never invokes a transport"
    return result


def writes(value):
    return [row for row in value["calls"] if row["method"] != "GET" and not row["path"].endswith("/audience")]


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/bulk-control", data={"mode": mode})
    assert response.ok, response.text()


def open_bulk(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=massa" + extra)
    expect(page.locator("#osSubscriptionBulkMessages")).to_be_visible()
    expect(action(page, "new")).to_be_enabled()


def stage_manual(page, *, new=True, phones="5567999999901", text="Olá, {name}. Texto literal.", template=None):
    if new:
        action(page, "new").click()
    expect(field(page, "interval_seconds")).to_be_visible()
    action(page, "add").click()
    field(page, "device_id").select_option(CONTRACT["options"]["devices"][0]["id"])
    page.locator('[data-bm-add-tab="manual"]').click()
    field(page, "manual").fill(phones)
    if template:
        field(page, "message_type").select_option("template")
        field(page, "template_id").select_option(template)
    else:
        field(page, "text").fill(text)
    action(page, "stage").click()
    expect(page.locator("[data-bm-dialog]")).to_have_count(0)


def prepare(page):
    action(page, "review").click()
    expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "review")


def confirm(page):
    page.locator("[data-bm-consent]").check()
    field(page, "confirmation").fill("ENVIAR")
    action(page, "confirm").click()


def reload_same_session(page):
    page.evaluate("history.replaceState(history.state, '', '/panel?aba=massa')")
    page.reload()
    expect(page.locator("#osSubscriptionBulkMessages")).to_be_visible()


def assert_recovery(page, key):
    value = page.evaluate("history.state.orionBulkRecovery")
    assert set(value) == {"tenant_id", "account_scope", "idempotency_key", "kind", "batch_id", "confirming"}
    assert value["idempotency_key"] == key
    encoded = json.dumps(value)
    assert not any(secret in encoded for secret in ("556799", "Contato sintético", "Texto literal", "contact_key", "device_id"))
    storage = page.evaluate("({local:{...localStorage},session:{...sessionStorage}})")
    assert all("bulk" not in key.lower() for values in storage.values() for key in values)


def screenshot(page, name):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / (name + ".png")), full_page=True, animations="disabled")


def assert_dialog_geometry(page, size):
    width, height = size
    assert_layout(page, width)
    rects = page.locator("[data-bm-dialog]").evaluate("""node => {
      const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height};};
      return {dialog:rect(node),body:rect(node.querySelector('.os-bm-dialog-body')),footer:rect(node.querySelector('.os-bm-dialog-footer'))};
    }""")
    assert rects["dialog"]["top"] >= -1 and rects["dialog"]["bottom"] <= height + 1, rects
    assert rects["body"]["height"] >= 200, rects
    assert rects["footer"]["bottom"] <= height + 1, rects


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_history_workbench_catalog_duplicates_review_and_confirmation(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
    try:
        open_bulk(page, fixture_url)
        expect(page.locator("[data-bm-metric]")).to_have_count(3)
        expect(page.locator("[data-bm-history-row]")).to_have_count(1)
        assert_layout(page, size[0])
        screenshot(page, label + "-history")
        assert not writes(report(page, fixture_url))
        stage_manual(page, phones="+55 (67) 99999-9901\n5567999999901")
        expect(field(page, "interval_seconds")).to_have_attribute("type", "number")
        expect(field(page, "interval_seconds")).to_have_value("10")
        expect(field(page, "interval_seconds")).to_have_attribute("min", "10")
        expect(field(page, "interval_seconds")).to_have_attribute("max", "60")
        expect(field(page, "interval_seconds")).to_have_attribute("step", "1")
        expect(page.locator("[data-bm-row]")).to_have_count(2)
        action(page, "add").click()
        field(page, "device_id").select_option(uid(30))
        assert not field(page, "device_id").evaluate("node => node.multiple")
        page.locator('[data-bm-group="' + uid(200) + '"]').check()
        field(page, "message_type").select_option("template")
        expect(field(page, "template_id").locator("option")).to_have_count(9)
        field(page, "template_id").select_option(uid(101))
        assert_dialog_geometry(page, size)
        screenshot(page, label + "-add")
        action(page, "stage").click()
        expect(page.locator("[data-bm-dialog]")).to_have_count(0)
        expect(page.locator("[data-bm-row]")).to_have_count(4)
        expect(page.locator("[data-bm-row-text]")).to_have_count(2)
        page.locator("[data-bm-row-text]").first.fill('Texto individual {name}. {"literal":true}')
        page.locator('[data-bm-action="remove"]').nth(1).click()
        expect(page.locator("[data-bm-row]")).to_have_count(3)
        field(page, "interval_seconds").fill("17")
        assert_layout(page, size[0])
        screenshot(page, label + "-workbench")
        assert not writes(report(page, fixture_url))
        prepare(page)
        expect(page.locator("[data-bm-review-row]")).to_have_count(3)
        expect(page.locator(".os-bm-duplicates")).to_contain_text("2 mensagens")
        expect(page.locator("[data-bm-dialog]")).to_contain_text('Texto individual {name}. {"literal":true}')
        expect(page.locator("[data-bm-consent]")).not_to_be_checked()
        expect(action(page, "confirm")).to_be_disabled()
        assert_dialog_geometry(page, size)
        page.locator(".os-bm-dialog-body").evaluate("node => {node.scrollTop=0}")
        screenshot(page, label + "-review-top")
        for selector in (".os-bm-review-counts", ".os-bm-duplicates", "[data-bm-review-row]", ".os-bm-attestation"):
            target = page.locator(selector).last
            target.scroll_into_view_if_needed()
            assert target.is_visible()
        field(page, "confirmation").scroll_into_view_if_needed()
        screenshot(page, label + "-review-confirmation")
        prepared = report(page, fixture_url)
        assert len(writes(prepared)) == 1 and not prepared["confirmations"]
        payload = writes(prepared)[0]["data"]["batch"]
        assert payload["interval_seconds"] == 17
        assert len(payload["items"]) == 3 and len({row["row_key"] for row in payload["items"]}) == 3
        assert len({row["device_id"] for row in payload["items"]}) == 2
        assert set(payload) == {"interval_seconds", "items"}
        confirm(page)
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "result")
        action(page, "acknowledge").click()
        expect(page.locator("[data-bm-recipient]")).to_have_count(3)
        expect(page.locator(".os-bm-summary [data-bm-progress]")).to_contain_text("0 / 3")
        assert_layout(page, size[0])
        screenshot(page, label + "-detail")
        value = report(page, fixture_url)
        assert len(value["confirmations"]) == 1 and len(writes(value)) == 2
        assert writes(value)[1]["data"]["confirmation"] == "ENVIAR"
        assert writes(value)[0]["data"]["idempotency_key"] == writes(value)[1]["data"]["idempotency_key"]
        assert page.evaluate("history.state.orionBulkRecovery || null") is None
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["review", "confirm", "action"])
def test_lost_response_reload_recovers_the_same_request_without_repeating(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_bulk(page, fixture_url)
        if phase == "action":
            page.locator('[data-bm-action="detail"][data-bm-id="' + BATCH_ID + '"]').first.click()
            action(page, "cancel").click()
            expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "cancel")
            control(page, fixture_url, "lost_action")
            action(page, "commit-cancel").click()
        else:
            stage_manual(page)
            if phase == "review":
                control(page, fixture_url, "lost_review")
                action(page, "review").click()
            else:
                prepare(page)
                control(page, fixture_url, "lost_confirm")
                confirm(page)
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "unknown")
        before = report(page, fixture_url)
        key = writes(before)[-1]["data"]["idempotency_key"]
        assert_recovery(page, key)
        reload_same_session(page)
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "review" if phase == "review" else "result")
        assert_recovery(page, key)
        after = report(page, fixture_url)
        assert writes(after) == writes(before)
        assert any(row["method"] == "GET" and row["path"].endswith("/requests/" + key) for row in after["calls"])
        if phase == "review":
            expect(page.locator("[data-bm-consent]")).not_to_be_checked()
            expect(field(page, "confirmation")).to_have_value("")
            action(page, "cancel-review").click()
        else:
            action(page, "acknowledge").click()
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_manual_validation_draft_reload_contacts_selection_and_focus(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_bulk(page, fixture_url)
        action(page, "new").click()
        action(page, "add").click()
        expect(page.locator("[data-bm-dialog] h2")).to_be_focused()
        action(page, "close-dialog").click()
        expect(action(page, "add")).to_be_focused()
        action(page, "add").click()
        field(page, "device_id").select_option(CONTRACT["options"]["devices"][0]["id"])
        page.locator('[data-bm-add-tab="manual"]').click()
        field(page, "manual").fill("0\n0\nabc")
        field(page, "text").fill("Validação")
        action(page, "stage").click()
        expect(page.locator("[data-bm-dialog]")).to_contain_text("Confira a linha 1")
        assert not writes(report(page, fixture_url))
        page.locator('[data-bm-add-tab="contacts"]').click()
        expect(page.locator("[data-bm-contact]")).to_have_count(3)
        field(page, "contact_q").fill("complementar")
        action(page, "contact-search").click()
        expect(page.locator("[data-bm-contact]")).to_have_count(1)
        page.locator("[data-bm-all-contacts]").check()
        action(page, "stage").click()
        expect(page.locator("[data-bm-dialog]")).to_have_count(0)
        expect(page.locator("[data-bm-row]")).to_have_count(3)
        assert not writes(report(page, fixture_url))
        assert page.evaluate("history.state.orionBulkRecovery || null") is None
        reload_same_session(page)
        expect(action(page, "new")).to_be_enabled()
        action(page, "new").click()
        expect(page.locator("[data-bm-row]")).to_have_count(0)
        expect(action(page, "review")).to_be_disabled()
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("mode", ["missing_review", "invalid_review"])
def test_missing_review_correction_or_retry_never_creates_an_implicit_new_key(browser, fixture_url, mode):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_bulk(page, fixture_url)
        stage_manual(page)
        control(page, fixture_url, mode)
        action(page, "review").click()
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "unknown")
        expect(action(page, "retry-same")).to_have_count(0)
        page.locator('[data-bm-dialog] [data-bm-action="recover"]').click()
        expect(action(page, "retry-same")).to_be_enabled()
        original = writes(report(page, fixture_url))[0]
        if mode == "invalid_review":
            action(page, "correct").click()
            page.locator("[data-bm-row-text]").fill("Correção com a mesma chave")
            prepare(page)
        else:
            action(page, "retry-same").click()
            expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "review")
        value = report(page, fixture_url)
        assert len(writes(value)) == 2
        assert writes(value)[1]["data"]["idempotency_key"] == original["data"]["idempotency_key"]
        if mode == "missing_review":
            assert writes(value)[1] == original
        assert not value["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_cancel_version_proof_and_conflict_after_confirm_preserve_identity(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_bulk(page, fixture_url)
        page.locator('[data-bm-action="detail"][data-bm-id="' + BATCH_ID + '"]').first.click()
        action(page, "cancel").click()
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "cancel")
        control(page, fixture_url, "stale_action")
        action(page, "commit-cancel").click()
        expect(page.locator("[data-bm-dialog]")).to_have_count(0)
        expect(page.locator(".os-bm-feedback[role=status]")).to_contain_text("não foi registrado")
        assert page.evaluate("history.state.orionBulkRecovery || null") is None
        action(page, "cancel").click()
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "cancel")
        action(page, "commit-cancel").click()
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "result")
        value = report(page, fixture_url)
        assert [row["data"]["expected_version"] for row in writes(value)] == [1, 2]
        action(page, "acknowledge").click()
        action(page, "back").click()
        stage_manual(page)
        prepare(page)
        control(page, fixture_url, "marker_confirm")
        confirm(page)
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "unknown")
        before = report(page, fixture_url)
        assert len(before["confirmations"]) == 1
        assert_recovery(page, writes(before)[-1]["data"]["idempotency_key"])
        page.locator('[data-bm-dialog] [data-bm-action="recover"]').click()
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "result")
        assert writes(report(page, fixture_url)) == writes(before)
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_history_pages_recipient_states_expiry_permissions_and_niche(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_bulk(page, fixture_url)
        control(page, fixture_url, "many")
        action(page, "refresh").click()
        expect(page.locator("[data-bm-history-row]")).to_have_count(30)
        page.locator('[data-bm-pagination="history"] [data-bm-action="next"]').click()
        expect(page.locator('[data-bm-pagination="history"]')).to_contain_text("página 2")
        field(page, "q").fill("Página sintética 61")
        action(page, "search").click()
        expect(page.locator("[data-bm-history-row]")).to_have_count(1)
        action(page, "reset").click()
        expect(page.locator("[data-bm-history-row]")).to_have_count(30)
        assert "sint" not in page.url
        control(page, fixture_url, "progress")
        action(page, "refresh").click()
        expect(page.locator('[data-bm-metric="total"] strong')).to_have_text("1")
        page.locator('[data-bm-action="detail"][data-bm-id="' + BATCH_ID + '"]').first.click()
        expect(page.locator("[data-bm-recipient]")).to_have_count(6)
        for status in ("pending", "running", "sent", "failed", "uncertain", "cancelled"):
            page.locator('[data-bm-recipient-tab="' + status + '"]').click()
            expect(page.locator("[data-bm-recipient]")).to_have_count(1)
            expect(page.locator("[data-bm-recipient]")).to_contain_text("Contato sintético " + status)
            expect(page.locator("[data-bm-recipient] button")).to_have_count(0)
        action(page, "back").click()
        stage_manual(page)
        prepare(page)
        control(page, fixture_url, "expire_review")
        reload_same_session(page)
        expect(page.locator("[data-bm-dialog]")).to_have_attribute("data-bm-view", "expired")
        action(page, "acknowledge").click()
        expect(action(page, "new")).to_be_enabled()
        before = writes(report(page, fixture_url))
        control(page, fixture_url, "revoke")
        with page.expect_response(lambda response: response.status == 403 and BULK_BASE in response.url):
            action(page, "refresh").click()
        expect(page.locator('#osNav [data-os-route="massa"]')).to_have_count(0)
        assert writes(report(page, fixture_url)) == before
        assert page.evaluate("history.state.orionBulkRecovery || null") is None
        for extra in ("&fixtureProfile=standard", "&fixtureReadonly=1"):
            page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=massa" + extra)
            expect(page.locator('#osNav [data-os-route="massa"]')).to_have_count(0)
            assert report(page, fixture_url)["calls"] == []
            # A denied route mounts the existing account/plan fallback. Finish
            # those real reads before replacing the document in the next case.
            page.wait_for_load_state("networkidle")
        open_bulk(page, fixture_url, "&fixtureEmpty=1")
        expect(page.locator("[data-bm-empty]")).to_be_visible()
        assert not writes(report(page, fixture_url))
        assert not external, external
        assert not errors, errors
    finally:
        context.close()
