"""No live customers or external channels are involved in these tests."""
from uuid import uuid4
from fastapi import HTTPException
from starlette.requests import Request
import pytest
from app import public_customer_area as area
from app.public_site_api import version_public_assets, STATIC, PUBLIC_ASSETS

def test_link_origin_and_public_identifier_are_not_credentials(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://orionagenda.pro")
    monkeypatch.delenv("PANEL_LEGACY_PUBLIC_ORIGINS",raising=False)
    tenant = uuid4()
    token = str(tenant) + "." + "a"*43
    parsed = area.parse_booking_link("https://orionagenda.pro/a#" + token)
    assert parsed[0] == "legacy" and parsed[1] == tenant
    assert area.parse_booking_link("https://orionagenda.pro/AG-ABCDEF#" + "a"*27)[0] == "direct"
    for link in ("https://orionagenda.pro/AG-ABCDEF",
                 "https://evil.example/a#" + token,
                 "https://orionagenda.pro/p/AG-ABCDEF#" + "a"*27,
                 "https://orionagenda.pro/a?foo=bar#" + token,
                 "https://orionagenda.pro@evil.example/a#" + token):
        with pytest.raises(HTTPException) as exc:
            area.parse_booking_link(link)
        assert exc.value.status_code == 403

def test_explicit_public_whitelist_and_assets(monkeypatch):
    row = {"name":"Synthetic","whatsapp_e164":"+5567999999999","birth_date":"1990-01-01",
           "notes":"do not disclose","id":uuid4(),"version":5}
    assert set(area.profile_data(row)) == {"name","phone","birth_date"}
    assert area.NEEDS_LINK == {"authenticated":False,"proof_required":True}
    assert "no-store" in area.reply(area.NEEDS_LINK).headers["cache-control"]
    assert area.cookie_name(uuid4()) != area.cookie_name(uuid4())
    html = version_public_assets((STATIC/"orion-public.html").read_text(encoding="utf-8"))
    for name in ("orion-customer-area.css","orion-customer-area.js"):
        assert name in PUBLIC_ASSETS
        assert "/public-assets/"+name+"?v=" in html
    assert version_public_assets(html) == html
