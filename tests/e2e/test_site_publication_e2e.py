"""Real tenant owner UI → HTTPS → restricted PostgreSQL publication lifecycle.

Runs only with the explicitly configured loopback E2E infrastructure. No route
is fulfilled with a fixture and no provider, message or AI operation is invoked.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import Journey, redact_text


@pytest.fixture(params=["chromium", "webkit"])
def publication_journey(request, qualification_server):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch(headless=True)
        h = Journey(browser, qualification_server, request.node.name)
        h.browser = browser
        h.engine = request.param
        try:
            yield h
        finally:
            folder = qualification_server.artifacts / request.node.name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "http.json").write_text(redact_text(json.dumps({
                "engine": h.engine, "http": h.events,
                "page_errors": h.errors, "external_blocked": h.external,
            }, indent=2)), encoding="utf-8")
            try:
                h.page.screenshot(path=str(folder / "panel-final.png"), full_page=True,
                                  mask=[h.page.locator('input[type="password"]')])
            finally:
                h.context.tracing.stop()
                h.context.close()
                browser.close()
            assert not h.external, "External requests were blocked: " + repr(h.external)
            assert not h.errors, "Real browser errors: " + repr(h.errors)


def save_preferences(h, title, slug):
    page = h.page
    page.locator('[data-os-site-tab="settings"]').click()
    form = page.locator("#osSiteForm")
    form.locator('[name="title"]').fill(title)
    form.locator('[name="slug"]').fill(slug)
    form.locator('[name="description"]').fill("Serviços e agenda sintéticos para a publicação E2E")
    expect(page.locator("#osSiteSlugStatus")).to_contain_text("Endereço disponível")
    with page.expect_response(lambda r: r.request.method == "PUT" and
                              urlsplit(r.url).path.endswith("/management/site")) as saved:
        form.locator('button[type="submit"]').click()
    assert saved.value.status == 200, saved.value.text()
    return saved.value.json()


def change_publication(h, action):
    page = h.page
    page.locator(f'[data-os-action="{action}"]').click()
    form = page.locator('[data-os-form="site-publication"]')
    expect(form).to_be_visible()
    with page.expect_response(lambda r: r.request.method == "POST" and
                              urlsplit(r.url).path.endswith("/site/publication")) as changed:
        form.locator('button[type="submit"]').click()
    assert changed.value.status == 200, changed.value.text()
    expect(form).to_have_count(0)
    return changed.value.json()


def assert_responsive(page, folder, phase):
    measurements = []
    for width in (320, 768, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("scrollTo(0, 0)")
        page.wait_for_timeout(100)  # allow CSS transitions to finish before measuring
        actual = page.evaluate("""() => ({width:innerWidth,
            scroll:document.documentElement.scrollWidth,
            body:document.body.scrollWidth})""")
        assert actual["scroll"] <= width + 2, actual
        assert actual["body"] <= width + 2, actual
        page.screenshot(path=str(folder / f"{phase}-{width}.png"), full_page=True)
        measurements.append(actual)
    return measurements


def reservation_snapshot(h, tenant):
    """Stable PostgreSQL JSON text, including immutable item/history fields."""
    return {table: h.scalar(f"""SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)::text
            FROM {table} t WHERE tenant_id=%s""", (tenant,))
            for table in ("appointments", "appointment_items", "appointment_history")}


def book_synthetic_appointment(h, public, tenant, slug, folder):
    """Confirm exactly once through the public UI, with non-routable test contact."""
    public.locator("#hero-book").click()
    service = public.locator("#booking-service-list [data-service-id]").first
    if service.get_attribute("aria-pressed") != "true":
        service.click()
    public.locator("#booking-next").click()
    day = public.locator("#booking-day-input")
    today = date.fromisoformat(day.get_attribute("min"))
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    with public.expect_response(lambda r: urlsplit(r.url).path ==
                                f"/api/v1/public/sites/{slug}/availability" and
                                f"day={next_monday.isoformat()}" in r.url) as available:
        day.fill(next_monday.isoformat())
        day.dispatch_event("change")
    assert available.value.status == 200, available.value.text()
    availability = available.value.json()
    assert availability["services"] == ["Serviço cadastrado E2E"]
    assert availability["price_cents"] == 3500
    public.locator("#time-slots .slot").first.click()
    public.locator("#booking-next").click()
    public.locator('[name="customer_name"]').fill("Cliente inteiramente sintético E2E")
    # +55 11 with a zero subscriber prefix: fixture only, never a real send target.
    public.locator('[name="customer_phone"]').fill("+5511000000000")
    public.locator('[name="confirmed"]').check()
    fit = assert_responsive(public, folder, "booking-confirmation")
    with public.expect_response(lambda r: r.request.method == "POST" and
                                urlsplit(r.url).path == f"/api/v1/public/sites/{slug}/appointments") as booked:
        public.locator("#booking-next").click()
    assert booked.value.status == 201, booked.value.text()
    result = booked.value.json()
    assert result["status"] == "confirmed" and result["price_cents"] == 3500
    assert result["services"] == ["Serviço cadastrado E2E"]
    assert result["professional"] == "Profissional cadastrado E2E"
    expect(public.get_by_text("Agendamento confirmado!", exact=True)).to_be_visible()
    assert h.scalar("SELECT count(*) FROM appointments WHERE tenant_id=%s", (tenant,)) == 1
    stored = h.query("SELECT tenant_id::text,status,total_cents,protocol FROM appointments WHERE tenant_id=%s", (tenant,))[0]
    assert stored == {"tenant_id": tenant, "status": "confirmed", "total_cents": 3500, "protocol": result["protocol"]}
    item = h.query("SELECT name_snapshot,price_snapshot_cents,price_mode_snapshot,duration_snapshot_min FROM appointment_items WHERE tenant_id=%s", (tenant,))[0]
    assert item == {"name_snapshot": "Serviço cadastrado E2E", "price_snapshot_cents": 3500,
                    "price_mode_snapshot": "fixed", "duration_snapshot_min": 40}
    public.locator("#booking-next").click()
    return reservation_snapshot(h, tenant), fit


@pytest.mark.integration
def test_owner_publishes_and_controls_real_public_site_without_admin(publication_journey):
    h, page = publication_journey, publication_journey.page
    folder = h.server.artifacts / h.name
    folder.mkdir(parents=True, exist_ok=True)
    account = h.account(verified=True)
    h.login(account)
    h.create(name="Empresa autônoma de publicação E2E")
    h.location()
    h.professional()
    h.service()
    h.hours()
    business = h.complete()
    tenant = business["tenant_id"]
    assert h.scalar("SELECT role FROM panel_accounts WHERE id=%s", (account["id"],)) == "member"
    assert h.scalar("SELECT role FROM tenant_memberships WHERE tenant_id=%s AND account_id=%s",
                    (tenant, account["id"])) == "owner"
    h.no_activation(tenant)
    page.locator('[data-ob-action="open-dashboard"]').click()
    page.locator('#osNav [data-os-route="site"]').click()
    expect(page.locator("#osSitePublicationState")).to_have_text("Não publicado")
    slug = "publication-e2e-" + uuid4().hex[:12]
    title = "Empresa publicada pelo proprietário E2E"
    saved = save_preferences(h, title, slug)
    assert saved["published"] is False
    expect(page.locator("#osSitePublicationState")).to_have_text("Pronto para publicar")
    public_url = h.server.origin + "/s/" + slug
    assert h.context.request.get(public_url).status == 404

    with page.expect_popup() as popup:
        page.get_by_role("link", name="Visualizar prévia").first.click()
    preview = popup.value
    expect(preview.locator("#site-title")).to_have_text(title)
    assert "noindex" in preview.request.get(preview.url).headers.get("x-robots-tag", "")
    preview.close()
    measurements = {"panel_draft": assert_responsive(page, folder, "panel-draft")}
    first = change_publication(h, "publish-site")
    assert first["published"] is True
    expect(page.locator("#osSitePublicationState")).to_have_text("Publicado")
    link = page.get_by_role("link", name="Abrir site").first
    expect(link).to_have_attribute("href", public_url)
    assert h.scalar("SELECT count(*) FROM public_site_publications WHERE tenant_id=%s AND action='publish'", (tenant,)) == 1

    anonymous = h.browser.new_context(ignore_https_errors=True)
    anonymous.route("**/*", h._guard_network)
    anonymous.on("page", h._observe)
    try:
        public = anonymous.new_page()
        response = public.goto(public_url)
        assert response.status == 200
        expect(public.locator("#site-title")).to_have_text(title)
        expect(public).to_have_title(title + " • Agendamento")
        expect(public.locator('link[rel="canonical"]')).to_have_attribute("href", public_url)
        assert account["email"] not in public.content()
        measurements["public_published"] = assert_responsive(public, folder, "public-published")
        original_reservation, measurements["booking_confirmation"] = book_synthetic_appointment(
            h, public, tenant, slug, folder)
        revised_title = "Empresa com alteração pendente E2E"
        save_preferences(h, revised_title, slug)
        expect(page.locator("#osSitePublicationState")).to_have_text("Alterações pendentes")
        public.reload()
        expect(public.locator("#site-title")).to_have_text(title)
        change_publication(h, "publish-site")
        expect(page.locator("#osSitePublicationState")).to_have_text("Publicado")
        public.reload()
        expect(public.locator("#site-title")).to_have_text(revised_title)
        assert reservation_snapshot(h, tenant) == original_reservation
        before = h.query("SELECT bot_enabled,status FROM tenants WHERE id=%s", (tenant,))[0]
        change_publication(h, "unpublish-site")
        expect(page.locator("#osSitePublicationState")).to_have_text("Retirado do ar")
        assert anonymous.request.get(public_url).status == 404
        assert h.query("SELECT bot_enabled,status FROM tenants WHERE id=%s", (tenant,))[0] == before
        assert h.scalar("SELECT count(*) FROM professionals WHERE tenant_id=%s", (tenant,)) == 1
        assert reservation_snapshot(h, tenant) == original_reservation
        change_publication(h, "restore-site")
        expect(page.locator("#osSitePublicationState")).to_have_text("Publicado")
        public.reload()
        expect(public.locator("#site-title")).to_have_text(revised_title)
        assert reservation_snapshot(h, tenant) == original_reservation
        history = h.query("SELECT action FROM public_site_publications WHERE tenant_id=%s ORDER BY id", (tenant,))
        assert [row["action"] for row in history] == ["publish", "publish", "unpublish", "restore"]
    finally:
        anonymous.close()
    assert not any(event["method"] == "POST" and ("support" in event["path"] or "admin" in event["path"])
                   for event in h.events)
    for table in ("authorized_staff", "whatsapp_channels", "qr_channels", "google_connections", "automation_jobs"):
        assert h.scalar(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)) == 0, table
    assert h.scalar("SELECT bot_enabled FROM tenants WHERE id=%s", (tenant,)) is False
    (folder / "acceptance.json").write_text(json.dumps({
        "engine": h.engine, "member_owner_without_admin": True,
        "lifecycle": ["draft", "preview", "publish", "pending_changes", "republish", "unpublish", "restore"],
        "responsive": measurements, "real_messages_or_campaigns": False,
        "synthetic_reservations": 1, "reservation_preserved_byte_for_byte": True,
    }, indent=2), encoding="utf-8")
