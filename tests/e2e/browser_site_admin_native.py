"""Existing synthetic support session obtains native admin identity by fresh login.

No dependency override, forged Actor or real credential is used. Run this file
against its own loopback *_e2e database: the legacy identity is a singleton.
"""
import hashlib
import json
import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright
from redis import Redis

from conftest import Journey, redact_text
from browser_site_publication import change_publication


def seed_business(h, tenant, title):
    pro, service = uuid4(), uuid4()
    slug = "native-admin-site-" + str(tenant).replace("-", "")[:16]
    # Exact synthetic fixture tenant only; no shared or production data.
    for table in ("public_site_publications", "public_site_slugs", "management_site_preferences",
                  "working_hours", "professional_services", "services", "professionals"):
        h.query(f"DELETE FROM {table} WHERE tenant_id=%s RETURNING tenant_id", (tenant,))
    h.query("""INSERT INTO tenants(id,name,slug,timezone,status,bot_enabled)
        VALUES(%s,%s,%s,'America/Cuiaba','active',false) ON CONFLICT(id) DO NOTHING RETURNING id""",
        (tenant, title, "native-admin-" + str(tenant).replace("-", "")))
    h.query("INSERT INTO professionals(id,tenant_id,name) VALUES(%s,%s,'Profissional sintético admin') RETURNING id", (pro, tenant))
    h.query("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(%s,%s,'Serviço sintético admin',30,3000) RETURNING id", (service, tenant))
    h.query("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(%s,%s,%s) RETURNING id", (tenant, pro, service))
    h.query("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(%s,%s,0,'09:00','18:00') RETURNING id", (tenant, pro))
    h.query("""INSERT INTO management_site_preferences(tenant_id,title,slug,version)
        VALUES(%s,%s,%s,20) RETURNING tenant_id""", (tenant, title, slug))
    h.query("INSERT INTO public_site_slugs(slug,tenant_id,kind) VALUES(%s,%s,'draft') RETURNING slug", (slug, tenant))
    return slug


@pytest.mark.integration
@pytest.mark.parametrize("qualification_server", ["synthetic_native_admin"], indirect=True)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_existing_support_cookie_reauthenticates_native_admin_owner(qualification_server, engine, request):
    server = qualification_server
    native = server.native_admin
    assert native is not None
    tenant, foreign, account = native.tenant_id, str(uuid4()), uuid4()
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        h = Journey(browser, server, request.node.name)
        folder = server.artifacts / request.node.name
        folder.mkdir(parents=True, exist_ok=True)
        cache = Redis.from_url(os.environ["ORION_E2E_REDIS_URL"], decode_responses=True)
        try:
            assert h.scalar("SELECT count(*) FROM panel_accounts WHERE auth_provider='legacy_admin'") == 0
            slug = seed_business(h, tenant, "Demonstração sintética do próprio admin")
            seed_business(h, foreign, "Empresa sintética sem vínculo do admin")
            h.login({"email": native.username})
            initial = h.api("/auth/me")["data"]
            assert initial["role"] == "support" and initial["account"] is False
            old_cookie = next(c["value"] for c in h.context.cookies() if c["name"] == "__Host-orion_session")
            old_key = "orion:session:" + hashlib.sha256(old_cookie.encode()).hexdigest()
            old_claims = cache.get(old_key)
            assert json.loads(old_claims)["role"] == "support"
            assert "account_id" not in json.loads(old_claims)

            # Provision only a synthetic fixture identity after the old session
            # exists. The running API's configuration does not change.
            from app.legacy_admin_identity import configuration_binding
            with patch.dict(os.environ, {"PANEL_ADMIN_USERNAME": native.username,
                                        "PANEL_ADMIN_PASSWORD_HASH": native.password_hash}):
                username, digest = configuration_binding()
            h.query("""INSERT INTO panel_accounts(id,name,email,password_hash,role,auth_provider,
                legacy_username,legacy_credential_digest) VALUES(%s,'Admin nativo sintético',NULL,NULL,
                'admin','legacy_admin',%s,%s) RETURNING id""", (account, username, digest))
            h.query("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(%s,%s,'owner') RETURNING id", (tenant, account))
            h.page.reload()
            h.page.locator('#osNav [data-os-route="site"]').click()
            status = h.api(f"/api/v1/tenants/{tenant}/management/site/publication")["data"]
            assert status["ready"] is True and status["can_publish"] is False
            assert status["draft_version"] == 20
            assert status["reauthentication_required"] is True
            assert status["access_reason"] == "administrator_reauthentication_required"
            reauth = h.page.locator('[data-os-action="site-reauthenticate"]')
            expect(reauth).to_have_text("Atualizar acesso de admin")
            h.page.locator("#osSitePublication").screenshot(path=str(folder / "before-reauthentication.png"))
            reauth.click()
            expect(h.page.locator("#authSubmitLogin")).to_be_visible()
            assert "aba=site" in h.page.url
            assert next(c["value"] for c in h.context.cookies() if c["name"] == "__Host-orion_session") == old_cookie
            assert cache.get(old_key) == old_claims
            assert not any(e["path"] == "/auth/logout" for e in h.events)
            h.page.locator("#username").fill(native.username)
            h.page.locator("#password").fill(native.password)
            with h.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/auth/login")) as logged:
                h.page.locator("#authSubmitLogin").click()
            assert logged.value.status == 200 and logged.value.json()["role"] == "admin"
            expect(h.page.locator("#appView")).to_be_visible()
            expect(h.page.locator('[data-os-action="publish-site"]')).to_be_enabled()
            current = h.api("/auth/me")["data"]
            assert current["role"] == "admin" and current["account"] is True
            after = h.api(f"/api/v1/tenants/{tenant}/management/site/publication")["data"]
            assert after["can_publish"] is True and after["reauthentication_required"] is False
            assert cache.get(old_key) == old_claims  # previous session was preserved, not silently upgraded
            foreign_status = h.api(f"/api/v1/tenants/{foreign}/management/site/publication")
            assert foreign_status["status"] == 200 and foreign_status["data"]["can_publish"] is False
            denied = h.api(f"/api/v1/tenants/{foreign}/management/site/publication", method="POST",
                           data={"enabled": True, "expected_version": 20})
            assert denied["status"] == 403
            result = change_publication(h, "publish-site")
            assert result["published"] is True
            expect(h.page.locator("#osSitePublicationState")).to_have_text("Publicado")
            assert h.scalar("SELECT actor_account_id FROM public_site_publications WHERE tenant_id=%s ORDER BY id DESC LIMIT 1", (tenant,)) == account
            assert h.context.request.get(server.origin + "/s/" + slug).status == 200
            assert h.scalar("SELECT public_enabled FROM management_site_preferences WHERE tenant_id=%s", (foreign,)) is False
            assert h.scalar("SELECT bot_enabled FROM tenants WHERE id=%s", (tenant,)) is False
            for table in ("appointments", "whatsapp_channels", "qr_channels", "automation_jobs"):
                assert h.scalar(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)) == 0
            assert not any(e["path"] == "/auth/logout" for e in h.events)
            (folder / "acceptance.json").write_text(json.dumps({
                "engine": engine, "old_support_cookie_preserved": True,
                "fresh_login_native_admin_owner": True, "foreign_publication_denied": True,
                "real_browser_https_postgres": True, "real_messages": False,
            }, indent=2), encoding="utf-8")
        finally:
            (folder / "http.json").write_text(redact_text(json.dumps({"http": h.events,
                "page_errors": h.errors, "external_blocked": h.external}, indent=2)), encoding="utf-8")
            h.page.screenshot(path=str(folder / "final.png"), full_page=True,
                              mask=[h.page.locator('input[type="password"]')])
            h.query("DELETE FROM tenant_memberships WHERE account_id=%s RETURNING id", (account,))
            h.query("DELETE FROM panel_accounts WHERE id=%s RETURNING id", (account,))
            cache.close()
            h.context.tracing.stop()
            h.context.close()
            browser.close()
        assert not h.external and not h.errors
