"""Mensagens Programadas with real Orion assets and a synthetic HTTP ledger.

No provider, production record, paid addon or real WhatsApp recipient is used.
Viewport tests cover layout; they do not claim physical keyboard/device tests.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser, navigation  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from subscription_scheduled_messages_fixture import CONTRACT, Handler, RUN_ID, SCHEDULE_ID, SCHEDULES_BASE

ARTIFACTS = ROOT / "test-artifacts" / "subscription-scheduled-messages"


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
    return page.locator('[data-sc-action="' + name + '"]')


def field(page, name):
    return page.locator('[data-sc-field="' + name + '"]')


def report(page, fixture_url):
    value = page.request.get(fixture_url + "/__fixture/schedules-report").json()
    assert not value["unexpected"], value["unexpected"]
    assert not value["effects"], "Public pages never execute the synthetic scheduler or transport"
    return value


def writes(value):
    return [row for row in value["calls"] if row["method"] != "GET" and not row["path"].endswith("/audience")]


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/schedules-control", data={"mode": mode})
    assert response.ok, response.text()


def open_schedules(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=programadas" + extra)
    expect(page.locator("#osSubscriptionScheduledMessages")).to_be_visible()
    expect(action(page, "new")).to_be_enabled()


def fill_schedule(page, *, new=True, template=False):
    if new:
        action(page, "new").click()
    expect(field(page, "title")).to_be_visible()
    field(page, "title").fill(CONTRACT["schedule_input"]["title"])
    field(page, "device_id").select_option(CONTRACT["schedule_input"]["device_id"])
    field(page, "start_date").fill(CONTRACT["schedule_input"]["start_date"])
    field(page, "start_time").fill(CONTRACT["schedule_input"]["start_time"])
    field(page, "interval_seconds").select_option("60")
    page.locator('[data-sc-group="' + CONTRACT["schedule_input"]["group_ids"][0] + '"]').check()
    expect(page.locator("[data-sc-estimate]")).to_contain_text("2 destinatário(s)")
    page.locator('[data-sc-contact="' + CONTRACT["schedule_input"]["contact_keys"][0] + '"]').check()
    expect(page.locator("[data-sc-estimate]")).to_contain_text("1 repetição(ões)")
    if template:
        field(page, "message_type").select_option("template")
        field(page, "template_id").select_option(CONTRACT["options_response"]["templates"][1]["id"])
    else:
        field(page, "text").fill(CONTRACT["schedule_input"]["text"])


def prepare(page):
    action(page, "review").click()
    expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "review")


def confirm(page):
    page.locator("[data-sc-consent]").check()
    action(page, "confirm").click()


def reload_same_session(page):
    page.evaluate("history.replaceState(history.state, '', '/panel?aba=programadas')")
    page.reload()
    expect(page.locator("#osSubscriptionScheduledMessages")).to_be_visible()


def assert_recovery(page, key):
    saved = page.evaluate("history.state.orionScheduleRecovery")
    assert set(saved) == {"tenant_id", "account_scope", "idempotency_key", "kind", "schedule_id", "run_id", "confirming"}
    assert saved["idempotency_key"] == key
    encoded = json.dumps(saved)
    assert not any(secret in encoded for secret in ("556799", "Contato sintético", "text", "contact_keys", "group_ids"))
    storage = page.evaluate("({local:{...localStorage},session:{...sessionStorage}})")
    assert all("Schedule" not in key and "schedule" not in key for values in storage.values() for key in values)


def assert_review_geometry(page, size, label):
    width, height = size
    dialog = page.locator("[data-sc-dialog]")
    body = dialog.locator(".os-sc-dialog-body")
    assert_layout(page, width)
    rects = dialog.evaluate("""node => {
      const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height};};
      return {dialog:rect(node),body:rect(node.querySelector('.os-sc-dialog-body')),footer:rect(node.querySelector('.os-sc-dialog-footer'))};
    }""")
    assert rects["dialog"]["top"] >= -1 and rects["dialog"]["bottom"] <= height + 1, rects
    assert rects["body"]["height"] >= 200, rects
    assert rects["footer"]["bottom"] <= height + 1, rects
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    body.evaluate("node => {node.scrollTop=0}")
    page.screenshot(path=str(ARTIFACTS / (label + "-review-top.png")), full_page=True, animations="disabled")
    for selector in [".os-sc-review-counts", ".os-sc-facts", "[data-sc-preview]", ".os-sc-attestation"]:
        target = dialog.locator(selector)
        target.scroll_into_view_if_needed()
        measured = target.evaluate("""node=>{const t=node.getBoundingClientRect(),b=node.closest('.os-sc-dialog-body').getBoundingClientRect();return {top:t.top,bottom:t.bottom,height:t.height,bodyTop:b.top,bodyBottom:b.bottom,bodyHeight:b.height};}""")
        assert measured["height"] > 0 and measured["bottom"] > measured["bodyTop"] and measured["top"] < measured["bodyBottom"], measured
        if measured["height"] <= measured["bodyHeight"] - 1:
            assert measured["top"] >= measured["bodyTop"] - 1 and measured["bottom"] <= measured["bodyBottom"] + 1, measured
    page.screenshot(path=str(ARTIFACTS / (label + "-review-confirmation.png")), full_page=True, animations="disabled")


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_schedule_list_editor_dedup_estimate_review_confirmation_and_details(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
    try:
        open_schedules(page, fixture_url)
        expect(page.locator("[data-sc-metric]")).to_have_count(4)
        expect(page.locator("[data-sc-row]")).to_have_count(1)
        assert_layout(page, size[0])
        assert not writes(report(page, fixture_url))
        action(page, "new").click()
        expect(field(page, "start_date")).to_have_value(CONTRACT["options_response"]["defaults"]["start_date"])
        expect(field(page, "start_date")).to_have_attribute("min", CONTRACT["options_response"]["today"])
        expect(field(page, "start_time")).to_have_value("14:00")
        expect(field(page, "start_time")).to_have_attribute("step", "60")
        expect(field(page, "timezone")).to_have_value("America/Sao_Paulo")
        assert field(page, "timezone").evaluate("node => node.readOnly")
        assert not field(page, "device_id").evaluate("node => node.multiple")
        assert field(page, "interval_seconds").locator("option").all_text_contents() == [f"{value} segundos" for value in [10, 20, 30, 40, 50, 60]]
        fill_schedule(page, new=False)
        expect(page.locator("[data-sc-estimate]")).to_contain_text("14:02")
        field(page, "start_time").fill("23:59")
        field(page, "interval_seconds").focus()
        expect(page.locator("[data-sc-estimate]")).to_contain_text("11/01/2030, 00:01")
        field(page, "start_time").fill("14:00")
        field(page, "repeat_every_30_days").select_option("true")
        expect(page.locator("[data-sc-estimate]")).to_contain_text("14:02")
        field(page, "message_type").select_option("template")
        expect(field(page, "text")).to_have_count(0)
        expect(field(page, "template_id").locator("option")).to_have_count(9)
        field(page, "template_id").select_option(CONTRACT["options_response"]["templates"][1]["id"])
        prepare(page)
        expect(page.locator("[data-sc-preview]")).to_contain_text("Template sintético image")
        action(page, "cancel-review").click()
        field(page, "message_type").select_option("text")
        field(page, "text").fill(CONTRACT["schedule_input"]["text"])
        assert_layout(page, size[0])
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / (label + "-editor.png")), full_page=True, animations="disabled")
        prepare(page)
        expect(page.locator("[data-sc-consent]")).not_to_be_checked()
        expect(action(page, "confirm")).to_be_disabled()
        expect(page.locator("[data-sc-dialog]")).to_contain_text("A cada 30 dias")
        before = report(page, fixture_url)
        assert not before["confirmations"] and len(before["runs"]) == 1
        assert_review_geometry(page, size, label)
        page.locator("[data-sc-consent]").focus()
        page.keyboard.press("Space")
        expect(page.locator("[data-sc-consent]")).to_be_checked()
        expect(page.locator("[data-sc-consent]")).to_be_focused()
        action(page, "confirm").click()
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "result")
        after = report(page, fixture_url)
        assert len(after["confirmations"]) == 1 and len(after["runs"]) == 2
        assert writes(after)[-1]["data"]["idempotency_key"] == writes(after)[-2]["data"]["idempotency_key"]
        assert writes(after)[-1]["data"]["confirmation"] == "AGENDAR"
        action(page, "acknowledge").click()
        expect(page.locator("[data-sc-recipient]")).to_have_count(2)
        expect(page.locator(".os-sc-runs [data-sc-row]")).to_have_count(1)
        expect(page.locator(".os-sc-summary")).to_contain_text("A cada 30 dias")
        assert page.evaluate("history.state.orionScheduleRecovery || null") is None
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-detail.png")), full_page=True, animations="disabled")
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["review", "confirm", "pending_review", "pending_confirm", "action"])
def test_lost_operation_reload_recovers_same_request_without_repeating(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        if phase.startswith("pending"):
            control(page, fixture_url, "due")
            if phase == "pending_confirm":
                action(page, "pending-review").click()
                expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "review")
        elif phase != "action":
            fill_schedule(page)
            if phase == "confirm":
                prepare(page)
        else:
            page.locator('[data-sc-action="cancel_run"][data-sc-run="' + RUN_ID + '"]').click()
            expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "cancel")
        control(page, fixture_url, "lost_" + phase)
        if phase == "review":
            action(page, "review").click()
        elif phase == "pending_review":
            action(page, "pending-review").click()
        elif phase == "action":
            action(page, "commit-cancel").click()
        else:
            confirm(page)
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "unknown")
        before = report(page, fixture_url)
        key = writes(before)[-1]["data"]["idempotency_key"]
        assert_recovery(page, key)
        reload_same_session(page)
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "review" if phase in {"review", "pending_review"} else "result")
        after = report(page, fixture_url)
        assert writes(after) == writes(before)
        assert any(row["path"].endswith("/requests/" + key) for row in after["calls"])
        assert len(after["confirmations"]) == (1 if phase == "confirm" else 0)
        assert len(after["releases"]) == (1 if phase == "pending_confirm" else 0)
        if phase in {"review", "pending_review"}:
            expect(page.locator("[data-sc-consent]")).not_to_be_checked()
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("mode", ["missing_review", "invalid_review"])
def test_missing_or_invalid_review_requires_explicit_same_key_retry_or_correction(browser, fixture_url, mode):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        fill_schedule(page)
        control(page, fixture_url, mode)
        action(page, "review").click()
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "unknown")
        before = report(page, fixture_url)
        assert not before["operations"] and not before["confirmations"]
        key = writes(before)[0]["data"]["idempotency_key"]
        with page.expect_response(lambda response: response.status == 404 and response.url.endswith("/requests/" + key)):
            page.locator('[data-sc-dialog] [data-sc-action="recover"]').click()
        assert_recovery(page, key)
        if mode == "invalid_review":
            action(page, "correct").click()
            field(page, "title").fill("Correção sintética explícita")
            action(page, "review").click()
        else:
            action(page, "retry-same").click()
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "review")
        after = report(page, fixture_url)
        assert len(writes(after)) == 2 and writes(after)[1]["data"]["idempotency_key"] == key
        if mode == "missing_review":
            assert writes(after)[1] == writes(after)[0]
        assert len(after["operations"]) == 1 and not after["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_pending_release_review_and_both_cancel_scopes_preserve_occurrences(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        control(page, fixture_url, "due")
        action(page, "pending-review").click()
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "review")
        expect(page.locator(".os-sc-pending-list li")).to_have_count(1)
        expect(page.locator(".os-sc-pending-list")).to_contain_text("Ocorrência sintética pending")
        assert not report(page, fixture_url)["releases"]
        confirm(page)
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "result")
        value = report(page, fixture_url)
        assert len(value["releases"]) == 1 and len(value["releases"][0]) == 1
        assert writes(value)[-1]["data"]["confirmation"] == "ENVIAR PENDENTES"
        action(page, "acknowledge").click()
        expect(action(page, "new")).to_be_enabled()
        for verb in ("cancel_run", "cancel_series"):
            # New synthetic session for each independent cancellation scope.
            open_schedules(page, fixture_url)
            page.locator('[data-sc-action="' + verb + '"][data-sc-run="' + RUN_ID + '"]').click()
            expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "cancel")
            assert not writes(report(page, fixture_url))
            action(page, "commit-cancel").click()
            expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "result")
            value = report(page, fixture_url)
            assert len(value["runs"]) == 1 and value["runs"][0]["schedule"]["bucket"] == "history"
            assert writes(value)[0]["data"]["action"] == verb
            assert writes(value)[0]["data"]["run_id"] == (RUN_ID if verb == "cancel_run" else None)
            action(page, "acknowledge").click()
            expect(page.locator("[data-sc-dialog]")).to_have_count(0)
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_search_tabs_pagination_expiry_revocation_and_niche_boundary(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        control(page, fixture_url, "many")
        action(page, "refresh").click()
        expect(page.locator("[data-sc-row]")).to_have_count(30)
        page.locator('[data-sc-pagination="list"] [data-sc-action="next"]').click()
        expect(page.locator('[data-sc-pagination="list"]')).to_contain_text("página 2")
        field(page, "q").fill("Página sintética 61")
        action(page, "filter").click()
        expect(page.locator("[data-sc-row]")).to_have_count(1)
        page.locator('[data-sc-tab="history"]').click()
        expect(page.locator("[data-sc-empty]")).to_be_visible()
        expect(field(page, "q")).to_have_value("Página sintética 61")
        expect(page.locator("[data-sc-status]")).to_have_count(0)
        action(page, "reset").click()
        expect(page.locator("[data-sc-row]")).to_have_count(30)
        expect(page.locator('[data-sc-tab="active"]')).to_have_attribute("aria-selected", "true")
        assert "Página" not in page.url and "sint" not in page.url
        fill_schedule(page)
        field(page, "contact_q").fill("contato que não existe")
        action(page, "contacts-search").click()
        expect(page.locator("[data-sc-contact-empty]")).to_be_visible()
        expect(page.locator(".os-sc-contact-selection")).to_contain_text("Contato sintético 1")
        prepare(page)
        control(page, fixture_url, "expire_review")
        reload_same_session(page)
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "expired")
        action(page, "acknowledge").click()
        expect(action(page, "new")).to_be_enabled()
        before = writes(report(page, fixture_url))
        control(page, fixture_url, "revoke")
        with page.expect_response(lambda response: response.status == 403 and SCHEDULES_BASE in response.url):
            action(page, "refresh").click()
        expect(page.locator('#osNav [data-os-route="programadas"]')).to_have_count(0)
        assert writes(report(page, fixture_url)) == before
        assert page.evaluate("history.state.orionScheduleRecovery || null") is None
        for extra in ("&fixtureProfile=standard", "&fixtureReadonly=1"):
            page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=programadas" + extra)
            expect(page.locator('#osNav [data-os-route="programadas"]')).to_have_count(0)
            assert report(page, fixture_url)["calls"] == []
        open_schedules(page, fixture_url, "&fixtureEmpty=1")
        expect(page.locator("[data-sc-empty]")).to_be_visible()
        assert not writes(report(page, fixture_url))
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_version_rejection_and_conflict_after_confirmation_do_not_duplicate(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        page.locator('[data-sc-action="edit"][data-sc-run="' + RUN_ID + '"]').click()
        expect(field(page, "title")).to_be_visible()
        control(page, fixture_url, "stale_review")
        action(page, "review").click()
        expect(page.locator(".os-sc-feedback.os-notice")).to_contain_text("pedido não foi registrado")
        expect(page.locator("[data-sc-dialog]")).to_have_count(0)
        assert page.evaluate("history.state.orionScheduleRecovery || null") is None
        expect(action(page, "review")).to_be_enabled()
        prepare(page)
        before = report(page, fixture_url)
        assert writes(before)[0]["data"]["expected_version"] == 1
        assert writes(before)[1]["data"]["expected_version"] == 2
        assert writes(before)[0]["data"]["idempotency_key"] != writes(before)[1]["data"]["idempotency_key"]
        control(page, fixture_url, "marker_confirm")
        confirm(page)
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "unknown")
        saved = report(page, fixture_url)
        assert len(saved["confirmations"]) == 1
        key = writes(saved)[-1]["data"]["idempotency_key"]
        assert_recovery(page, key)
        page.locator('[data-sc-dialog] [data-sc-action="recover"]').click()
        expect(page.locator("[data-sc-dialog]")).to_have_attribute("data-sc-view", "result")
        after = report(page, fixture_url)
        assert writes(after) == writes(saved) and len(after["confirmations"]) == 1
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_detail_recipient_statuses_preserve_uncertain_results_without_retry(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_schedules(page, fixture_url)
        control(page, fixture_url, "progress")
        action(page, "refresh").click()
        expect(page.locator('[data-sc-metric="queue"] strong')).to_have_text("1")
        page.locator('[data-sc-action="detail"][data-sc-run="' + RUN_ID + '"]').click()
        expect(page.locator("[data-sc-recipient]")).to_have_count(6)
        expect(page.locator(".os-sc-summary progress")).to_have_attribute("value", "66.67")
        for status in ("pending", "running", "sent", "failed", "uncertain", "cancelled"):
            page.locator('[data-sc-recipient-tab="' + status + '"]').click()
            expect(page.locator("[data-sc-recipient]")).to_have_count(1)
            expect(page.locator("[data-sc-recipient]")).to_contain_text("Contato sintético " + status)
            if status == "uncertain":
                expect(page.locator("[data-sc-recipient]")).to_contain_text("SYNTHETIC_UNCERTAIN")
                expect(page.locator("[data-sc-recipient] button")).to_have_count(0)
        assert not writes(report(page, fixture_url))
        assert not external, external
        assert not errors, errors
    finally:
        context.close()
