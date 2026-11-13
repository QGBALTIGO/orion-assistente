"""Group extraction and local import through shipped Orion UI assets.

The local ledger uses synthetic identities and the production input schema.
The suite never opens a real WhatsApp socket or mutates real contact data.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import expect
import pytest

from browser_subscription_chatbot import ROOT, SIZES, assert_layout, browser  # noqa: F401
from browser_subscription_chatbot_simulator import setup_page
from subscription_group_contacts_fixture import Handler, GROUP_CONTACTS_BASE, contract

ARTIFACTS = ROOT / "test-artifacts" / "subscription-group-contacts"
HOST = "#osSubscriptionGroupContacts"


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
    return page.locator(f'[data-gc-field="{name}"]')


def action(page, name):
    return page.locator(f'[data-gc-action="{name}"]')


def report(page, fixture_url):
    result = page.request.get(fixture_url + "/__fixture/group-contacts-report").json()
    assert not result["unexpected"], result["unexpected"]
    assert not result["effects"], "A local import must not send or alter remote group membership"
    return result


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/group-contacts-control", data={"mode": mode})
    assert response.ok, response.text()


def open_extractor(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=none&aba=extrator-contatos" + extra)
    expect(page.locator(HOST)).to_be_visible()
    expect(field(page, "device_id")).to_be_visible()


def load_members(page):
    sample = contract()
    field(page, "device_id").select_option(sample["options"]["devices"][0]["id"])
    remote = sample["groups"]["items"][0]
    expect(field(page, "group_id").locator(f'option[value="{remote["id"]}"]')).to_have_count(1)
    field(page, "group_id").select_option(remote["id"])
    action(page, "load-members").click()
    expect(page.locator("[data-gc-member]")).to_have_count(len(sample["members"]["members"]))
    return sample


def save_evidence(label, browser, size, theme, value, external, errors):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / (label + ".json")).write_text(json.dumps({
        "engine": browser[0], "viewport": size, "theme": theme,
        "fixture": value, "external_requests": external, "page_errors": errors,
        "limits": "Synthetic loopback ledger; no real provider, contact or physical-device claim.",
    }, ensure_ascii=False, indent=2))


def assert_clean(external, errors):
    assert not external, external
    assert not errors, errors


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_group_contact_read_selection_and_layout_without_commercial_plan(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label, value = f"{browser[0]}-{size[0]}x{size[1]}-{theme}", {}
    try:
        open_extractor(page, fixture_url)
        sample = load_members(page)
        expect(page.get_by_role("heading", name="Extrator de contatos de grupos", exact=True)).to_be_visible()
        expect(page.locator("[data-gc-select-all]")).to_be_checked()
        assert page.locator("[data-gc-member]").evaluate_all("nodes => nodes.every(node => node.checked)")
        expect(page.locator(HOST + " table thead th")).to_have_text(["", "WhatsApp", "Nome", "Função"])
        expect(page.locator(HOST)).to_contain_text("Não informado")
        page.locator("[data-gc-select-all]").uncheck()
        expect(action(page, "import")).to_be_disabled()
        page.locator('[data-gc-member="' + sample["members"]["members"][0]["phone"] + '"]').check()
        expect(action(page, "import")).to_be_enabled()
        assert page.locator("[data-gc-member]").evaluate_all("nodes => nodes.filter(node => node.checked).length") == 1
        page.locator("[data-gc-select-all]").check()
        field(page, "target_group_id").select_option("new")
        expect(field(page, "new_group_name")).to_have_value("Grupo: " + sample["groups"]["items"][0]["name"])
        expect(field(page, "new_group_name")).to_have_attribute("maxlength", "100")
        page.evaluate("document.fonts.ready")
        assert_layout(page, size[0])
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / (label + ".png")), full_page=True, animations="disabled")
        value = report(page, fixture_url)
        assert value["effective_plan"]["active"] is False
        assert value["state"]["active"] is True
        assert value["effective_plan"]["permissions"]["group_contacts.manage"] is True
        assert all(call["method"] == "GET" for call in value["calls"]), value
        assert not value["imports"]
        assert_clean(external, errors)
    finally:
        save_evidence(label, browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("target", ["existing", "new"])
def test_group_contact_import_is_exact_additive_and_does_not_send(browser, fixture_url, target):
    size, theme = (390, 844) if target == "new" else (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value, label = {}, f"{browser[0]}-import-{target}"
    try:
        open_extractor(page, fixture_url)
        sample = load_members(page)
        page.locator("[data-gc-select-all]").uncheck()
        chosen = sample["members"]["members"][1]["phone"]
        page.locator('[data-gc-member="' + chosen + '"]').check()
        if target == "new":
            field(page, "target_group_id").select_option("new")
            field(page, "new_group_name").fill("Lista sintética selecionada")
        else:
            field(page, "target_group_id").select_option(sample["options"]["groups"][0]["id"])
        with page.expect_response(lambda response: response.url.endswith(GROUP_CONTACTS_BASE + "/import")) as response:
            action(page, "import").click()
        assert response.value.ok
        expect(page.locator("[data-gc-result]")).to_be_visible()
        expect(action(page, "new")).to_be_visible()
        assert_layout(page, size[0])
        value = report(page, fixture_url)
        writes = [call for call in value["calls"] if call["method"] != "GET"]
        assert len(writes) == 1 and writes[0]["path"] == GROUP_CONTACTS_BASE + "/import"
        assert writes[0]["data"]["phones"] == [chosen]
        assert len(value["imports"]) == 1
        result = value["imports"][0]
        assert result["selected_count"] == result["added_count"] == 1
        local = value["local_members"][result["group"]["id"]]
        if target == "existing":
            first = sample["members"]["members"][0]["phone"]
            assert local[first] == "Nome local preservado"
            assert local["+5567999999909"] == "Contato local fora do grupo"
            assert set(local) == {first, chosen, "+5567999999909"}
            assert result["group"]["blocked_count"] == sample["options"]["groups"][0]["blocked_count"]
        else:
            assert result["group"]["name"] == "Lista sintética selecionada"
            assert set(local) == {chosen}
        action(page, "new").click()
        expect(field(page, "group_id")).to_be_visible()
        expect(page.locator("[data-gc-result]")).to_have_count(0)
        assert_clean(external, errors)
    finally:
        save_evidence(label, browser, size, theme, value, external, errors)
        context.close()


def test_group_contact_lost_save_recovers_without_another_import(browser, fixture_url):
    size, theme = (1440, 900), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_extractor(page, fixture_url)
        sample = load_members(page)
        field(page, "target_group_id").select_option(sample["options"]["groups"][0]["id"])
        control(page, fixture_url, "lost_import")
        action(page, "import").click()
        expect(action(page, "recover")).to_be_visible()
        value = report(page, fixture_url)
        assert len(value["imports"]) == 1
        action(page, "recover").click()
        expect(page.locator("[data-gc-result]")).to_be_visible()
        value = report(page, fixture_url)
        assert len(value["imports"]) == 1
        writes = [row for row in value["calls"] if row["method"] != "GET"]
        assert len(writes) == 1
        key = writes[0]["data"]["idempotency_key"]
        assert any(row["path"] == GROUP_CONTACTS_BASE + "/requests/" + key for row in value["calls"])
        storage = page.evaluate("JSON.stringify({local:{...localStorage},session:{...sessionStorage},history:history.state})")
        assert not any(secret in storage for secret in ("synthetic-preview-", "+55679999999", "Nome local preservado"))
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-recover", browser, size, theme, value, external, errors)
        context.close()


def test_group_contact_change_of_source_clears_the_old_selection(browser, fixture_url):
    size, theme = (390, 844), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_extractor(page, fixture_url)
        sample = load_members(page)
        field(page, "group_id").select_option(sample["groups"]["items"][1]["id"])
        expect(page.locator("[data-gc-member]")).to_have_count(0)
        action(page, "load-members").click()
        expect(page.locator(HOST)).to_contain_text("Nenhum")
        expect(page.locator("[data-gc-member]")).to_have_count(0)
        value = report(page, fixture_url)
        assert not value["imports"]
        assert all(row["method"] == "GET" for row in value["calls"])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-source-change", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("case,query", [
    ("standard", "&fixtureProfile=standard"),
    ("inactive", "&fixtureInactive=1"),
    ("missing-feature", "&fixtureGroupContacts=0"),
    ("non-owner", "&fixtureRole=reader"),
])
def test_group_contact_direct_route_requires_owner_active_company_workspace_and_feature(browser, fixture_url, case, query):
    size, theme = (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        role = "reader" if case == "non-owner" else "owner"
        query = query.replace("&fixtureRole=reader", "")
        page.goto(fixture_url + f"/panel?fixtureRole={role}&fixturePlan=none&aba=extrator-contatos" + query)
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "plano" if case == "standard" else "dashboard")
        expect(page.locator(HOST)).to_have_count(0)
        expect(page.locator('#osNav [data-os-route="extrator-contatos"]')).to_have_count(0)
        value = report(page, fixture_url)
        assert not value["calls"], value["calls"]
        assert not value["imports"]
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-{case}", browser, size, theme, value, external, errors)
        context.close()


@pytest.mark.parametrize("case,query", [
    ("no-groups", "&fixtureEmpty=1"),
    ("disconnected", "&fixtureDisconnected=1"),
    ("disconnected-stale-flag", "&fixtureDisconnected=1&fixtureLegacyAvailability=1"),
])
def test_group_contact_empty_or_disconnected_state_never_imports(browser, fixture_url, case, query):
    size, theme = (390, 844), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_extractor(page, fixture_url, query)
        if case == "no-groups":
            field(page, "device_id").select_option(contract()["options"]["devices"][0]["id"])
            expect(page.locator(HOST)).to_contain_text("Nenhum grupo")
        else:
            expect(page.locator("[data-gc-device-state]")).to_contain_text("Desconectado")
            expect(action(page, "load-members")).to_be_disabled()
            expect(page.locator(HOST + ' [data-os-route="dispositivos"]')).to_have_count(0)
        expect(page.locator("[data-gc-member]")).to_have_count(0)
        value = report(page, fixture_url)
        assert not value["imports"]
        assert all(row["method"] == "GET" for row in value["calls"])
        if case != "no-groups":
            assert [(row["method"], row["path"]) for row in value["calls"]] == [("GET", GROUP_CONTACTS_BASE + "/options")]
        assert_layout(page, size[0])
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-{case}", browser, size, theme, value, external, errors)
        context.close()


def test_disconnected_device_opens_devices_only_with_its_permission(browser, fixture_url):
    size, theme = (390, 844), "dark"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_extractor(page, fixture_url, "&fixtureDisconnected=1&fixtureDevicesAccess=1")
        expect(page.locator("[data-gc-device-state]")).to_contain_text("Desconectado")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / f"{browser[0]}-offline-extractor.png"), full_page=True)
        page.locator(HOST).get_by_role("button", name="Dispositivos", exact=True).click()
        expect(page.get_by_role("heading", name="Dispositivos", exact=True)).to_be_visible()
        expect(page.locator("#osSubscriptionDevices")).to_contain_text("Desconectado")
        assert "aba=dispositivos" in page.url
        value = report(page, fixture_url)
        assert [(row["method"], row["path"]) for row in value["calls"]] == [("GET", GROUP_CONTACTS_BASE + "/options")]
        assert value["device_calls"] and all(row["method"] == "GET" for row in value["device_calls"])
        assert not value["imports"]
        assert_layout(page, size[0])
        assert_clean(external, errors)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / f"{browser[0]}-offline-devices-cta.png"), full_page=True)
    finally:
        save_evidence(f"{browser[0]}-offline-devices-cta", browser, size, theme, value, external, errors)
        context.close()


def test_group_contact_expired_preview_requires_a_new_read(browser, fixture_url):
    size, theme = (1440, 900), "light"
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    value = {}
    try:
        open_extractor(page, fixture_url)
        load_members(page)
        control(page, fixture_url, "expire")
        action(page, "import").click()
        expect(page.locator("[data-gc-error]")).to_be_visible()
        value = report(page, fixture_url)
        assert not value["imports"]
        assert len([row for row in value["calls"] if row["method"] != "GET"]) == 1
        assert_clean(external, errors)
    finally:
        save_evidence(f"{browser[0]}-expired", browser, size, theme, value, external, errors)
        context.close()