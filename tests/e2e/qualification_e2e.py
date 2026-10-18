"""Twenty real UI→HTTPS API→PostgreSQL journeys, explicitly selected in CI.

This filename intentionally does not match default pytest test_*.py collection:
run it explicitly, with the real services and browser installed. No API is mocked.
"""
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic#E2E-2026-Only"


def fresh(journey, **account_options):
    account = journey.account(**account_options)
    journey.login(account)
    return account


def services(journey):
    fresh(journey)
    state = journey.create()
    journey.location()
    journey.professional()
    state = journey.service()
    return state


def test_01_signup_login_and_business_create_are_separate_real_transactions(journey):
    h, page = journey, journey.page
    email = f"signup-{uuid4().hex}@example.test"
    page.goto(h.server.origin + "/panel")
    page.locator("#authTabRegister").click()
    for field, value in {"registerName": "Pessoa criada no navegador", "registerEmail": email,
                         "registerPassword": PASSWORD, "registerConfirm": PASSWORD}.items():
        page.locator("#" + field).fill(value)
    with page.expect_response(lambda r: urlsplit(r.url).path == "/auth/register") as created:
        page.locator("#authSubmitRegister").click()
    assert created.value.status == 201
    account = h.query("SELECT id,role,verified_at FROM panel_accounts WHERE email=%s", (email,))[0]
    assert account["role"] == "member" and account["verified_at"] is None
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 0
    h.login({"email": email})
    expect(page.locator('#orionSetup [data-ob-form="create"]')).to_be_visible()
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 0
    state = h.create()
    assert h.query("SELECT status,bot_enabled FROM tenants WHERE id=%s", (state["tenant_id"],))[0] == {"status": "paused", "bot_enabled": False}
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s AND role='owner'", (account["id"],)) == 1
    h.no_activation(state["tenant_id"])


def test_02_services_wizard_persists_private_address_bindings_buffers_and_real_hours(journey):
    h = journey
    fresh(h)
    state = h.create()
    location = h.location(private=True)
    assert location["configuration"]["private_address"]["street"] == "Rua sintética E2E"
    h.professional()
    state = h.service()
    tenant = state["tenant_id"]
    assert h.query("SELECT price_cents,duration_min,prep_min,cleanup_min,active FROM services WHERE tenant_id=%s", (tenant,))[0] == {"price_cents":3500,"duration_min":40,"prep_min":5,"cleanup_min":10,"active":False}
    h.hours()
    assert h.scalar("SELECT count(*) FROM working_hours WHERE tenant_id=%s", (tenant,)) == 2
    assert h.scalar("SELECT count(*) FROM working_breaks WHERE tenant_id=%s", (tenant,)) == 2
    h.step("channels")
    h.page.locator('#orionSetup [name="assistant_name"]').fill("Recepção sintética E2E")
    h.page.locator('#orionSetup [name="capabilities"][value="catalog_answers"]').check()
    draft = h.submit("channels", "/steps/channels")
    assert draft["channels"]["assistant"]["status"] == "draft"
    done = h.complete()
    assert all(item["active"] for item in done["catalog"]["services"] + done["catalog"]["professionals"])
    assert h.scalar("SELECT count(*) FROM professional_services WHERE tenant_id=%s", (tenant,)) == 1
    assert h.scalar("SELECT status FROM tenants WHERE id=%s", (tenant,)) == "active"
    h.no_activation(tenant)


def test_03_products_only_finishes_without_professionals_hours_or_invented_stock(journey):
    h = journey
    fresh(h)
    state = h.create(activity="products")
    h.location()
    expect(h.page.locator('[data-ob-step="professionals"]')).to_have_count(0)
    expect(h.page.locator('[data-ob-step="hours"]')).to_have_count(0)
    h.product()
    done = h.complete()
    tenant = state["tenant_id"]
    assert done["configuration"]["activity"] == "products"
    assert h.query("SELECT unit_price_cents,price_kind,active FROM commercial_variants WHERE tenant_id=%s", (tenant,))[0] == {"unit_price_cents":1990,"price_kind":"fixed","active":True}
    for table in ("professionals", "working_hours", "services"):
        assert h.scalar(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)) == 0
    assert h.query("SELECT mode,quantity_on_hand,reserved_quantity FROM commercial_inventory WHERE tenant_id=%s", (tenant,))[0] == {"mode":"unknown","quantity_on_hand":None,"reserved_quantity":0}
    h.no_activation(tenant)


def test_04_mixed_business_reviews_service_and_product_without_cross_coupled_activation(journey):
    h = journey
    fresh(h)
    state = h.create(activity="both")
    h.location()
    h.professional()
    h.service()
    h.product()
    h.hours()
    done = h.complete()
    assert len(done["catalog"]["services"]) == len(done["catalog"]["products"]) == 1
    assert h.scalar("SELECT count(*) FROM commercial_products WHERE tenant_id=%s AND active", (state["tenant_id"],)) == 1
    h.no_activation(state["tenant_id"])


def test_05_reload_resumes_saved_step_and_cancel_edit_never_changes_real_price(journey):
    h = journey
    state = services(h)
    tenant = state["tenant_id"]
    h.page.reload()
    expect(h.page.locator('#orionSetup [data-ob-title]')).to_have_text(state["name"])
    h.step("catalog")
    h.page.locator('[data-ob-action="edit"][data-ob-kind="service"]').click()
    expect(h.page.locator('#orionSetup [name="price"]')).to_have_value("35,00")
    h.page.locator('#orionSetup [name="price"]').fill("99,90")
    h.page.locator('[data-ob-action="cancel-edit"]').click()
    assert h.scalar("SELECT price_cents FROM services WHERE tenant_id=%s", (tenant,)) == 3500
    h.page.reload()
    h.step("catalog")
    h.page.locator('[data-ob-action="edit"][data-ob-kind="service"]').click()
    expect(h.page.locator('#orionSetup [name="price"]')).to_have_value("35,00")
    assert h.scalar("SELECT count(*) FROM services WHERE tenant_id=%s", (tenant,)) == 1


def test_06_multiple_business_choice_loads_only_the_selected_legacy_scope(journey):
    h = journey
    account = h.account()
    a = h.tenant(account, name="Empresa A sintética")
    b = h.tenant(account, name="Empresa B sintética")
    h.login(account)
    expect(h.page.locator('#orionSetup [data-ob-action="choose-business"]')).to_have_count(2)
    before = len(h.events)
    h.page.locator(f'[data-ob-tenant="{b["id"]}"]').click()
    expect(h.page.locator('#orionSetup [data-ob-title]')).to_have_text(b["name"])
    assert not any(event["path"] == f'/api/v1/tenants/{a["id"]}/onboarding' for event in h.events[before:])
    assert h.scalar("SELECT count(*) FROM business_onboarding WHERE tenant_id IN (%s,%s)", (a["id"], b["id"])) == 0
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 2


def test_07_explicit_new_business_preserves_existing_company_and_creates_one_independent_scope(journey):
    h = journey
    account = h.account()
    legacy = h.tenant(account, configured=True, name="Negócio original sintético")
    h.login(account)
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    h.page.locator('[data-os-action="business-new"]').click()
    expect(h.page.locator('#orionSetup [data-ob-form="create"] [name="name"]')).to_have_value("")
    state = h.create(name="Segundo negócio sintético")
    assert state["tenant_id"] != legacy["id"]
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 2
    assert h.scalar("SELECT count(*) FROM services WHERE tenant_id=%s", (legacy["id"],)) == 1
    assert h.scalar("SELECT count(*) FROM services WHERE tenant_id=%s", (state["tenant_id"],)) == 0


def test_08_configured_legacy_owner_opens_dashboard_without_retroactive_creation(journey):
    h = journey
    account = h.account()
    legacy = h.tenant(account, configured=True)
    h.login(account)
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    assert h.scalar("SELECT count(*) FROM business_onboarding WHERE tenant_id=%s", (legacy["id"],)) == 0
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 1
    assert h.scalar("SELECT count(*) FROM services WHERE tenant_id=%s AND active", (legacy["id"],)) == 1


def test_09_empty_legacy_setup_reuses_the_same_tenant_instead_of_duplicate_business(journey):
    h = journey
    account = h.account()
    legacy = h.tenant(account)
    h.login(account)
    expect(h.page.locator('#orionSetup [data-ob-title]')).to_have_text(legacy["name"])
    h.step("business")
    form = h.page.locator('[data-ob-form="business"]')
    form.locator('[name="niche"]').select_option("barbearia")
    form.locator('[name="activity"]').select_option("services")
    state = h.submit("business", "/steps/business")
    assert state["tenant_id"] == legacy["id"] and state["required"] is False
    assert h.scalar("SELECT count(*) FROM business_onboarding_creations WHERE account_id=%s", (account["id"],)) == 0
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 1


def test_10_editor_login_cannot_claim_owner_setup_or_mutate_onboarding(journey):
    h = journey
    account = h.account()
    company = h.tenant(account, role="editor", configured=True)
    h.login(account)
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    expect(h.page.locator('[data-os-action="business-setup"]')).to_have_count(0)
    result = h.api(f'/api/v1/tenants/{company["id"]}/onboarding/steps/location', method="PUT", data={"expected_version":0,"idempotency_key":str(uuid4()),"data":{"modality":"online"}})
    assert result["status"] == 403
    assert h.scalar("SELECT count(*) FROM business_onboarding WHERE tenant_id=%s", (company["id"],)) == 0


def test_11_viewer_login_is_read_only_even_for_a_handcrafted_browser_write(journey):
    h = journey
    account = h.account()
    company = h.tenant(account, role="viewer", configured=True)
    h.login(account)
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    state = h.api(f'/api/v1/tenants/{company["id"]}/onboarding')
    assert state["status"] == 200 and state["data"]["can_edit"] is False
    result = h.api(f'/api/v1/tenants/{company["id"]}/onboarding/complete', method="POST", data={"expected_version":0,"idempotency_key":str(uuid4()),"expected_snapshot":state["data"]["snapshot"],"activate_catalog":True,"acknowledged":True})
    assert result["status"] == 403
    assert h.scalar("SELECT count(*) FROM business_onboarding_operations WHERE tenant_id=%s", (company["id"],)) == 0


def test_12_pending_email_bound_invitation_cannot_silently_create_an_owner_business(journey):
    h = journey
    owner, invited = h.account(), h.account(verified=True)
    company = h.tenant(owner, configured=True)
    h.invitation(owner, company, invited)
    h.login(invited)
    expect(h.page.locator("#orionSetup")).to_contain_text("Você tem um convite de acesso")
    expect(h.page.locator('[data-ob-form="create"]')).to_have_count(0)
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (invited["id"],)) == 0
    assert h.scalar("SELECT count(*) FROM business_onboarding_creations WHERE account_id=%s", (invited["id"],)) == 0


def test_13_real_invitation_requires_acceptance_then_explicit_owner_review(journey):
    h = journey
    owner, invited = h.account(), h.account(verified=True)
    company = h.tenant(owner, configured=True)
    invitation = h.invitation(owner, company, invited)
    h.login(invited)
    h.page.goto(h.server.origin + "/convite#token=" + invitation["token"])
    expect(h.page.locator("#inviteAccept")).to_be_visible()
    with h.page.expect_response(lambda r: urlsplit(r.url).path == "/api/v1/team-invitations/accept") as accepted:
        h.page.locator("#inviteAccept").click()
    assert accepted.value.status == 200 and accepted.value.json()["status"] == "claimed"
    expect(h.page.locator("#inviteMessage")).to_contain_text("Aguarde a aprovação do proprietário")
    assert h.scalar("SELECT status FROM management_team_invitations WHERE id=%s", (invitation["id"],)) == "claimed"
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (invited["id"],)) == 0
    h.page.goto(h.server.origin + "/panel")
    expect(h.page.locator("#orionSetup")).to_contain_text("Aguardando aprovação")
    owner_context = h.context.browser.new_context(ignore_https_errors=True)
    owner_context.set_default_timeout(15_000)
    owner_context.route("**/*", h._guard_network)
    owner_context.on("page", h._observe)
    try:
        owner_page = owner_context.new_page()
        h.login(owner, page=owner_page)
        owner_page.goto(h.server.origin + "/panel?aba=funcionarios&empresa=" + company["id"])
        owner_page.locator(f'[data-os-action="approve-invitation"][data-invitation="{invitation["id"]}"]').click()
        expect(owner_page.locator('#osDialog [data-os-form="confirm"]')).to_be_visible()
        assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (invited["id"],)) == 0
        with owner_page.expect_response(lambda r: urlsplit(r.url).path.endswith("/team/invitations/" + invitation["id"]) and r.request.method == "PATCH") as approved:
            owner_page.locator('#osDialog [data-os-form="confirm"] button[type="submit"]').click()
        assert approved.value.status == 200
        assert h.query("SELECT role,active FROM tenant_memberships WHERE tenant_id=%s AND account_id=%s", (company["id"], invited["id"]))[0] == {"role":"editor","active":True}
        assert h.scalar("SELECT status FROM management_team_invitations WHERE id=%s", (invitation["id"],)) == "approved"
    finally:
        owner_context.close()
    h.page.reload()
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    expect(h.page.locator('[data-os-action="business-setup"]')).to_have_count(0)
    assert h.scalar("SELECT count(*) FROM business_onboarding_creations WHERE account_id=%s", (invited["id"],)) == 0


def test_14_logout_drops_the_real_cookie_session_and_never_creates_business_on_reload(journey):
    h = journey
    account = fresh(h)
    expect(h.page.locator('[data-ob-form="create"]')).to_be_visible()
    with h.page.expect_response(lambda r: urlsplit(r.url).path == "/auth/logout") as logged_out:
        h.page.locator("#logout").click()
    assert logged_out.value.status == 200
    h.page.reload()
    expect(h.page.locator("#loginForm")).to_be_visible()
    expect(h.page.locator("#appView")).not_to_be_visible()
    assert not any(cookie["name"] == "__Host-orion_session" for cookie in h.context.cookies())
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 0


def test_15_two_real_tabs_detect_cas_conflict_without_overwriting_saved_business(journey):
    h = journey
    fresh(h)
    state = h.create()
    h.step("business")
    other = h.context.new_page()
    other.goto(h.server.origin + "/panel")
    other.locator('[data-ob-step="business"]').click()
    expect(other.locator('[data-ob-form="business"]')).to_be_visible()
    h.page.locator('#orionSetup [name="name"]').fill("Nome vencedor E2E")
    saved = h.submit("business", "/steps/business")
    other.locator('#orionSetup [name="name"]').fill("Nome conflitante E2E")
    h.submit("business", "/steps/business", status=409, page=other)
    expect(other.locator('#orionSetup [name="name"]')).to_have_value("Nome conflitante E2E")
    expect(other.locator('[data-ob-action="refresh"]')).to_be_visible()
    assert h.scalar("SELECT name FROM tenants WHERE id=%s", (state["tenant_id"],)) == "Nome vencedor E2E"
    assert h.scalar("SELECT version FROM business_onboarding WHERE tenant_id=%s", (state["tenant_id"],)) == saved["version"]


def test_16_double_submit_creates_only_one_business_one_owner_and_one_creation_identity(journey):
    h = journey
    account = fresh(h)
    form = h.page.locator('[data-ob-form="create"]')
    expect(form).to_be_visible()
    form.locator('[name="name"]').fill("Duplo clique sintético")
    form.locator('[name="niche"]').select_option("barbearia")
    form.locator('[name="activity"]').select_option("services")
    form.locator('[name="timezone"]').select_option("America/Cuiaba")
    with h.page.expect_response(lambda r: urlsplit(r.url).path.endswith("/onboarding/businesses") and r.request.method == "POST") as saved:
        form.evaluate("form=>{form.requestSubmit();form.requestSubmit();}")
    assert saved.value.status == 201
    expect(h.page.locator('[data-ob-form="location"]')).to_be_visible()
    assert h.scalar("SELECT count(*) FROM business_onboarding_creations WHERE account_id=%s", (account["id"],)) == 1
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s AND role='owner'", (account["id"],)) == 1
    assert len([event for event in h.events if event["path"].endswith("/onboarding/businesses") and event["method"] == "POST"]) == 1


def test_17_committed_create_with_lost_transport_response_recovers_without_duplicate_post(journey):
    h = journey
    account = fresh(h)
    form = h.page.locator('[data-ob-form="create"]')
    expect(form).to_be_visible()
    for field, value in {"name":"Resposta perdida E2E","niche":"barbearia","activity":"services","timezone":"America/Cuiaba"}.items():
        locator = form.locator(f'[name="{field}"]')
        locator.fill(value) if field == "name" else locator.select_option(value)
    commits = []
    def lose_only_response(route):
        # The HTTP request really reaches the API and commits to PostgreSQL;
        # discard only its transport response to the browser.
        response = route.fetch()
        assert response.status == 201
        commits.append(response.json()["tenant_id"])
        route.abort("connectionreset")
    h.page.route("**/api/v1/onboarding/businesses", lose_only_response)
    form.locator('button[type="submit"]').click()
    expect(h.page.locator('[data-ob-action="refresh"]')).to_be_visible()
    assert len(commits) == 1
    assert h.scalar("SELECT count(*) FROM business_onboarding_creations WHERE account_id=%s", (account["id"],)) == 1
    h.page.locator('[data-ob-action="refresh"]').click()
    expect(h.page.locator('[data-ob-form="location"]')).to_be_visible()
    expect(h.page.locator('[data-ob-action="retry-operation"]')).to_have_count(0)
    assert h.scalar("SELECT count(*) FROM tenant_memberships WHERE account_id=%s", (account["id"],)) == 1
    assert len(commits) == 1


def test_18_foreign_tenant_cannot_be_read_or_updated_from_the_authenticated_browser(journey):
    h = journey
    own, foreign_owner = h.account(), h.account()
    a = h.tenant(own, configured=True)
    b = h.tenant(foreign_owner, configured=True, name="DADO ESTRANGEIRO E2E")
    h.login(own)
    expect(h.page.locator("#appView")).not_to_have_class(re.compile(r"\bob-gated\b"))
    assert h.api(f'/api/v1/tenants/{b["id"]}/onboarding')["status"] == 403
    denied = h.api(f'/api/v1/tenants/{b["id"]}/onboarding/steps/business', method="PUT", data={"expected_version":0,"idempotency_key":str(uuid4()),"data":{"name":"Tentativa E2E","niche":"barbearia","activity":"services","timezone":"America/Cuiaba"}})
    assert denied["status"] == 403
    expect(h.page.locator("#appView")).not_to_contain_text(b["name"])
    assert h.scalar("SELECT name FROM tenants WHERE id=%s", (b["id"],)) == b["name"]
    assert h.scalar("SELECT count(*) FROM business_onboarding_operations WHERE tenant_id IN (%s,%s)", (a["id"], b["id"])) == 0


def test_19_owner_revoked_after_render_cannot_commit_a_stale_wizard_save(journey):
    h = journey
    account = fresh(h)
    state = h.create()
    tenant = state["tenant_id"]
    expect(h.page.locator('[data-ob-form="location"]')).to_be_visible()
    h.page.locator('#orionSetup [name="modality"]').select_option("online")
    h.query("UPDATE tenant_memberships SET active=false WHERE tenant_id=%s AND account_id=%s RETURNING id", (tenant, account["id"]))
    h.submit("location", "/steps/location", status=403)
    expect(h.page.locator('#orionSetup [name="modality"]')).to_have_value("online")
    assert h.scalar("SELECT configuration->>'modality' FROM business_onboarding WHERE tenant_id=%s", (tenant,)) is None
    assert h.scalar("SELECT count(*) FROM business_onboarding_operations WHERE tenant_id=%s AND step='location'", (tenant,)) == 0


def test_20_completed_business_channels_remain_disabled_without_touching_any_provider(journey):
    h = journey
    fresh(h)
    state = h.create(activity="products")
    h.location()
    h.product()
    h.complete()
    h.page.locator('[data-ob-action="open-dashboard"]').click()
    h.page.locator('[data-os-route="configuracoes"]').first.click()
    with h.page.expect_response(lambda r: urlsplit(r.url).path.endswith("/qr-channels")) as channels:
        h.page.locator('[data-os-settings="whatsapp"]').click()
    assert channels.value.status == 423
    expect(h.page.locator("#osManagedChannels")).to_contain_text("Os canais gerenciados ainda não estão disponíveis neste ambiente")
    assert not any(event["path"].endswith(("/start", "/stop", "/connect", "/publish")) and event["method"] != "GET" for event in h.events)
    h.no_activation(state["tenant_id"])
