"""Origin migration is configured on the server, without real DNS, mail or OAuth."""
import json

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
import httpx
import pytest
from starlette.requests import Request

from app import landing_api
from app.google_oauth import authorization_origin, configuration_checklist, oauth_config
from app.public_origins import canonical_public_origin, checked_public_origin, legacy_public_origins
from app.public_site_rendering import public_site_url
from app.sales_requests_api import require_public_origin

NEW = "https://orionagenda.pro"
OLD = "https://orion-assistente-production.up.railway.app"
FACADE = "https://orion-painel-iota.vercel.app"


def request(origin, host="orionagenda.pro", **extra):
    headers = {"origin": origin, "host": host, "sec-fetch-site": "same-origin", **extra}
    return Request({"type": "http", "scheme": "https", "method": "POST", "path": "/",
                    "query_string": b"", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})


@pytest.mark.parametrize("value", ["", "http://orionagenda.pro", "https://user:secret@orionagenda.pro",
    "https://orionagenda.pro/path", "https://orionagenda.pro?secret=private", "https://orionagenda.pro#private",
    "https://*.orionagenda.pro", "https://orionagenda.pro:bad", "https://bad\nhost", 'https://bad">host'])
def test_public_origin_rejects_invalid_configuration_without_echo(value):
    with pytest.raises(ValueError) as exc:
        checked_public_origin(value)
    assert "private" not in str(exc.value) and "secret" not in str(exc.value)


def test_canonical_and_legacy_origin_configuration_are_exact(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", NEW + "/")
    monkeypatch.setenv("PANEL_LEGACY_PUBLIC_ORIGINS", OLD + ", " + FACADE)
    assert canonical_public_origin() == NEW
    assert legacy_public_origins() == {OLD, FACADE}
    assert public_site_url("empresa-sintetica") == NEW + "/s/empresa-sintetica"
    monkeypatch.setenv("PANEL_LEGACY_PUBLIC_ORIGINS", "https://*.vercel.app")
    with pytest.raises(ValueError): legacy_public_origins()


def test_sales_accepts_only_explicit_origins_and_ignores_forwarded_host(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", NEW)
    monkeypatch.setenv("PANEL_LEGACY_PUBLIC_ORIGINS", OLD + "," + FACADE)
    for origin in (NEW, OLD, FACADE): require_public_origin(request(origin))
    for origin in ("https://foreign.invalid", "https://orionagenda.pro.evil.invalid", "null"):
        with pytest.raises(HTTPException) as exc:
            require_public_origin(request(origin, **{"x-forwarded-host": "orionagenda.pro"}))
        assert exc.value.status_code == 403
    monkeypatch.delenv("PANEL_LEGACY_PUBLIC_ORIGINS")
    with pytest.raises(HTTPException) as exc: require_public_origin(request(OLD))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_landing_canonical_uses_configuration_on_every_served_host(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", NEW)
    app = FastAPI(); app.include_router(landing_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=NEW) as client:
        for headers in ({}, {"host": "untrusted.invalid"}, {"x-forwarded-host": "untrusted.invalid"}, {"x-orion-facade": "vercel-iota"}):
            page = await client.get("/", headers=headers)
            assert page.status_code == 200
            assert f'rel="canonical" href="{NEW}/"' in page.text
            assert f'property="og:url" content="{NEW}/"' in page.text
            assert f'property="og:image" content="{NEW}/landing-assets/orion-social.png"' in page.text
            assert "untrusted.invalid" not in page.text


def configure_google(monkeypatch):
    for name, value in {"GOOGLE_CLIENT_ID": "synthetic-client", "GOOGLE_CLIENT_SECRET": "synthetic-secret",
                        "INTEGRATION_FERNET_KEY": Fernet.generate_key().decode(), "GOOGLE_REDIRECT_URI": OLD + "/integrations/google/callback",
                        "PANEL_PUBLIC_URL": NEW}.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv("GOOGLE_OAUTH_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("PANEL_LEGACY_PUBLIC_ORIGINS", raising=False)


def test_explicit_legacy_callback_preserves_google_without_widening_trust(monkeypatch):
    configure_google(monkeypatch)
    assert not configuration_checklist()["configured"]
    monkeypatch.setenv("PANEL_LEGACY_PUBLIC_ORIGINS", OLD + "," + FACADE)
    assert configuration_checklist()["configured"]
    assert oauth_config()[2] == OLD + "/integrations/google/callback"
    assert authorization_origin() == OLD
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://foreign.invalid/integrations/google/callback")
    assert not configuration_checklist()["configured"] and authorization_origin() is None


def test_separate_oauth_origin_requires_exact_secure_callback(monkeypatch):
    configure_google(monkeypatch)
    monkeypatch.setenv("GOOGLE_OAUTH_PUBLIC_ORIGIN", OLD)
    assert oauth_config()[2] == OLD + "/integrations/google/callback"
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", OLD + "/integrations/google/callback?secret=private")
    report = configuration_checklist()
    assert not report["configured"] and "private" not in json.dumps(report)
