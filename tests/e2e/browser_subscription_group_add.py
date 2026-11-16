"""Group addition intent, results and recovery through shipped Orion assets."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from subscription_group_add_fixture import Handler, GROUP_ADD_BASE, contract

ARTIFACTS = ROOT / "test-artifacts" / "subscription-group-add"
HOST = "#osSubscriptionGroupAdd"


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


def field(page, name):
    return page.locator(f'[data-ga-field="{name}"]')


def action(page, name):
    return page.locator(f'[data-ga-action="{name}"]')


def report(page, fixture_url):
    result = page.request.get(fixture_url + "/__fixture/group-add-report").json()
    assert not result["unexpected"], result["unexpected"]
    assert not result["effects"], "The fixture cannot contact or mutate WhatsApp"
    return result


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/group-add-control", data={"mode": mode})
    assert response.ok, response.text()


def open_group_add(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=none&aba=adicionar-ao-grupo" + extra)
    expect(page.locator(HOST)).to_be_visible()
    expect(field(page, "device_id")).to_be_visible()


def load_preview(page, *, source="contacts", destination=0):
    sample = contract()
    field(page, "device_id").select_option(sample["options"]["devices"][0]["id"])
    remote = sample["groups"]["items"][destination]
    expect(field(page, "destination_group_id").locator(f'option[value="{remote["id"]}"]')).to_have_count(1)
    field(page, "destination_group_id").select_option(remote["id"])
    if source == "group":
        field(page, "source").select_option("group")
        field(page, "source_group_id").select_option(sample["groups"]["items"][2]["id"])
    action(page, "load").click()
    expect(page.locator("[data-ga-member]")).to_have_count(4)
    return sample


def save_evidence(label, browser, size, theme, value, external, errors):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / (label + ".json")).write_text(json.dumps({
        "engine": browser[0], "viewport": size, "theme": theme, "fixture": value,
        "external_requests": external, "page_errors": errors,
        "limits": "Synthetic receipts and loopback HTTP. No real WhatsApp participant, invite or message.",
    }, ensure_ascii=False, indent=2))


def assert_clean(external, errors):
    assert not external, external
    assert not errors, errors


def creates(value):
    return [row for row in value["calls"] if row["method"] == "POST" and row["path"].endswith("/batches")]


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_group_add_selection_layout_and_local_blocks_without_commercial_plan(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label, value = f"{browser[0]}-{size[0]}x{size[1]}-{theme}", {}
    try:
        open_group_add(page, fixture_url)
        sample = load_preview(page)
        expect(page.get_by_role("heading", name="Adicionar contatos ao grupo", exact=True)).to_be_visible()
        expect(page.locator(HOST + " table thead th")).to_have_text(["Seleção", "WhatsApp", "Nome", "Status"])
        expect(page.locator("[data-ga-select-all]")).to_be_checked()
        rows = page.locator("[data-ga-member]")
        assert rows.evaluate_all("es=>es.filter(e=>e.checked).length") == 2
        assert rows.evaluate_all("es=>es.filter(e=>e.disabled).length") == 2
        expect(page.locator(HOST)).to_contain_text("Não informado")
        expect(page.locator(HOST)).to_contain_text("intervalo de 3 segundos")
        page.locator("[data-ga-select-all]").uncheck()
        expect(action(page, "add")).to_be_disabled()
        chosen = sample["preview_contacts"]["members"][0]["phone"]
        page.locator('[data-ga-member="' + chosen + '"]').check()
        expect(action(page, "add")).to_be_enabled()
        expect(action(page, "add")).to_contain_text("1")
        page.evaluate("document.fonts.ready")
        assert_layout(page, size[0])
        page.evaluate("window.scrollTo(0,0)")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / (label + ".png")), full_page=True, animations="disabled")
        value = report(page, fixture_url)
        assert value["effective_plan"]["active"] is False and value["state"]["active"] is True
        assert value["effective_plan"]["permissions"]["group_add.manage"] is True
        assert not creates(value) and not value["creates"]
        assert_clean(external, errors)
    finally:
        save_evidence(label, browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("source", ["contacts", "group"])
def test_group_add_only_selected_numbers_enter_one_durable_intent(browser, fixture_url, source):
    size, theme = (390, 844) if source == "group" else (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        sample = load_preview(page, source=source)
        page.locator("[data-ga-select-all]").uncheck()
        chosen = sample["preview_contacts"]["members"][1]["phone"]
        page.locator('[data-ga-member="' + chosen + '"]').check()
        action(page, "add").click()
        expect(page.locator("[data-ga-result]")).to_be_visible()
        action(page, "refresh").click()
        expect(page.get_by_role("heading", name="Adição concluída", exact=True)).to_be_visible()
        value = report(page, fixture_url)
        assert len(creates(value)) == len(value["creates"]) == len(value["batches"]) == 1
        assert creates(value)[0]["data"]["phones"] == [chosen]
        batch = value["batches"][0]
        assert batch["batch"]["source"] == source and batch["batch"]["interval_seconds"] == 3
        assert batch["batch"]["total"] == batch["batch"]["progress"]["added"] == 1
        assert batch["members"][0]["phone"] == chosen
        expect(page.locator("[data-ga-result-member]")).to_have_count(1)
        assert_layout(page, size[0])
        action(page, "new").click()
        expect(field(page, "device_id")).to_be_visible()
        expect(page.locator("[data-ga-result]")).to_have_count(0)
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-create-{source}", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("reload", [False, True], ids=["same-page", "reload"])
def test_group_add_lost_response_recovers_same_request_without_another_creation(browser, fixture_url, reload):
    size, theme = (1440, 900), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        load_preview(page)
        # Remove fixture bootstrap flags so reload preserves this account's
        # synthetic durable records, as the ordinary product URL does.
        page.evaluate("history.replaceState(history.state,'','/panel?aba=adicionar-ao-grupo')")
        control(page, fixture_url, "lost_batch")
        action(page, "add").click()
        expect(page.locator("[data-ga-pending]")).to_be_visible()
        expect(action(page, "recover")).to_be_enabled()
        value = report(page, fixture_url)
        assert len(value["creates"]) == 1
        storage = page.evaluate("JSON.stringify({local:{...localStorage},session:{...sessionStorage},history:history.state})")
        assert not any(secret in storage for secret in ("synthetic-group-add-", "+55119999000", "Contato Um"))
        if reload:
            page.reload()
        else:
            action(page, "recover").click()
        expect(page.get_by_role("heading", name="Adição concluída", exact=True)).to_be_visible()
        value = report(page, fixture_url)
        assert len(creates(value)) == len(value["creates"]) == 1
        key = creates(value)[0]["data"]["idempotency_key"]
        assert any(row["path"] == GROUP_ADD_BASE + "/requests/" + key for row in value["calls"])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-recovery-{reload}", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("outcome,heading", [("partial", "Concluído com ocorrências"), ("unknown", "Resultado incerto")])
def test_group_add_partial_or_unknown_result_is_reported_without_retry_or_invitation(browser, fixture_url, outcome, heading):
    size, theme = (390, 844), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        load_preview(page)
        control(page, fixture_url, outcome)
        action(page, "add").click()
        expect(page.locator("[data-ga-result]")).to_be_visible()
        action(page, "refresh").click()
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(page.locator("[data-ga-result-member]")).to_have_count(2)
        expect(page.locator(HOST)).to_contain_text("nenhum convite")
        expect(page.locator(HOST).get_by_role("button", name="Tentar novamente", exact=True)).to_have_count(0)
        value = report(page, fixture_url)
        assert len(creates(value)) == 1
        result = value["batches"][0]
        assert result["batch"]["status"] == outcome
        if outcome == "unknown":
            assert [m["status"] for m in result["members"]] == ["unknown", "cancelled"]
            assert result["batch"]["progress"]["added"] == 0
        else:
            assert [m["status"] for m in result["members"]] == ["added", "not_added"]
        assert_layout(page, size[0])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-{outcome}", browser, size, theme, value, external, errors)
        context.close()


def test_group_add_filters_and_source_change_invalidate_the_old_preview(browser, fixture_url):
    size, theme = (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        sample = load_preview(page)
        field(page, "contact_group_id").select_option("ungrouped")
        expect(page.locator("[data-ga-member]")).to_have_count(0)
        field(page, "created_from").fill("2026-11-01")
        field(page, "created_to").fill("2026-11-05")
        field(page, "search").fill("nenhum-contato-sintetico")
        action(page, "load").click()
        expect(page.locator(HOST)).to_contain_text("Nenhum contato corresponde")
        expect(action(page, "add")).to_be_disabled()
        value = report(page, fixture_url)
        request = [r for r in value["calls"] if r["path"].endswith("/preview")][-1]["data"]
        assert request["without_group"] is True and request["contact_group_id"] is None
        assert (request["search"], request["created_from"], request["created_to"]) == ("nenhum-contato-sintetico", "2026-11-01", "2026-11-05")
        field(page, "source").select_option("group")
        expect(field(page, "search")).to_have_count(0)
        expect(page.locator("[data-ga-member]")).to_have_count(0)
        field(page, "source_group_id").select_option(sample["groups"]["items"][2]["id"])
        action(page, "load").click()
        expect(page.locator("[data-ga-member]")).to_have_count(4)
        value = report(page, fixture_url)
        request = [r for r in value["calls"] if r["path"].endswith("/preview")][-1]["data"]
        assert request == sample["preview_request_group"]
        assert not creates(value)
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-filters", browser, size, theme, value, external, errors)
        context.close()


def test_group_add_destination_change_rechecks_admin_and_preserves_eligible_selection(browser, fixture_url):
    size, theme = (390, 844), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        sample = load_preview(page)
        page.locator("[data-ga-select-all]").uncheck()
        chosen = sample["preview_contacts"]["members"][0]["phone"]
        page.locator('[data-ga-member="' + chosen + '"]').check()
        field(page, "destination_group_id").select_option(sample["groups"]["items"][1]["id"])
        expect(page.locator("[data-ga-destination-state]")).to_contain_text("administrador")
        expect(action(page, "add")).to_be_disabled()
        expect(page.locator("[data-ga-member]")).to_have_count(4)
        field(page, "destination_group_id").select_option(sample["groups"]["items"][0]["id"])
        expect(action(page, "add")).to_be_enabled()
        assert page.locator("[data-ga-member]").evaluate_all("es=>es.filter(e=>e.checked).length") == 1
        value = report(page, fixture_url)
        assert len([r for r in value["calls"] if r["path"].endswith("/preview")]) == 3
        assert not creates(value)
        assert_layout(page, size[0])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-destination", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("case,query", [
    ("standard", "&fixtureProfile=standard"), ("inactive", "&fixtureInactive=1"),
    ("missing-feature", "&fixtureGroupAdd=0"), ("non-owner", "&fixtureRole=reader"),
])
def test_group_add_route_requires_owner_active_subscription_company_and_feature(browser, fixture_url, case, query):
    size, theme = (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        role = "reader" if case == "non-owner" else "owner"
        query = query.replace("&fixtureRole=reader", "")
        page.goto(fixture_url + f"/panel?fixtureRole={role}&fixturePlan=none&aba=adicionar-ao-grupo" + query)
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "plano" if case == "standard" else "dashboard")
        expect(page.locator(HOST)).to_have_count(0)
        expect(page.locator('#osNav [data-os-route="adicionar-ao-grupo"]')).to_have_count(0)
        value = report(page, fixture_url)
        assert not value["calls"] and not value["creates"]
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-{case}", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("legacy", [False, True], ids=["offline", "legacy-availability"])
def test_group_add_disconnected_device_never_queries_groups_or_creates_intent(browser, fixture_url, legacy):
    size, theme = (390, 844), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url, "&fixtureDisconnected=1" + ("&fixtureLegacyAvailability=1" if legacy else ""))
        expect(page.locator("[data-ga-device-state]")).to_contain_text("Desconectado")
        expect(action(page, "load")).to_be_disabled()
        value = report(page, fixture_url)
        assert [(r["method"], r["path"]) for r in value["calls"]] == [("GET", GROUP_ADD_BASE + "/options")]
        assert not value["creates"]
        assert_layout(page, size[0])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-offline-{legacy}", browser, size, theme, value, external, errors)
        context.close()


def test_group_add_expired_preview_does_not_create_a_batch_or_retry(browser, fixture_url):
    size, theme = (1440, 900), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_group_add(page, fixture_url)
        load_preview(page)
        control(page, fixture_url, "expire")
        action(page, "add").click()
        expect(page.locator("[data-ga-error]")).to_be_visible()
        expect(page.locator("[data-ga-member]")).to_have_count(0)
        expect(page.locator("[data-ga-pending]")).to_have_count(0)
        expect(action(page, "load")).to_be_enabled()
        value = report(page, fixture_url)
        assert len(creates(value)) == 1 and not value["creates"]
        assert not any(r["path"].startswith(GROUP_ADD_BASE + "/requests/") for r in value["calls"])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-expired", browser, size, theme, value, external, errors)
        context.close()