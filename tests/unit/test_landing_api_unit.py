"""Public asset routing and optional contact link, without network or databases."""
from html import unescape
import json
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
import httpx
import pytest

from app import landing_api as api


def application():
    app = FastAPI()
    app.include_router(api.router)
    return app


@pytest.mark.parametrize("phone", ["", "+5511999999999\" onclick=\"alert(1)", "5511999999999", "+123456789", "+5500999999999"])
def test_invalid_or_absent_sales_phone_never_becomes_a_contact_link(monkeypatch, phone):
    monkeypatch.setenv("SALES_WHATSAPP_E164", phone)
    assert api.sales_contact_link() == ""


def test_configured_contact_link_preserves_exact_destination_and_business_intent(monkeypatch):
    monkeypatch.setenv("SALES_WHATSAPP_E164", "+5511912345678")
    link = api.sales_contact_link()
    href = unescape(link.split('href="', 1)[1].split('"', 1)[0])
    parsed = urlsplit(href)
    assert parsed.scheme == "https" and parsed.netloc == "wa.me" and parsed.path == "/5511912345678"
    assert parse_qs(parsed.query) == {"text": ["Olá! Quero conhecer o Orion para o meu negócio."]}
    assert 'rel="noopener noreferrer"' in link


async def test_html_render_does_not_depend_on_process_cwd_or_invent_contact(tmp_path, monkeypatch):
    root = api.LANDING_DIR
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SALES_WHATSAPP_E164", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="https://test.invalid") as c:
        response = await c.get("/")
    assert response.status_code == 200
    assert root.joinpath("index.html").is_file()
    assert "__SALES_CONTACT_LINK__" not in response.text and "https://wa.me/" not in response.text
    assert "text/html" in response.headers["content-type"]
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_landing_routes_serve_only_reviewed_media_and_support_video_ranges():
    manifest = json.loads((api.LANDING_DIR / "assets/media-sources.json").read_text())
    media = {item["path"].rsplit("/", 1)[-1]: item for item in manifest["assets"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="https://test.invalid") as c:
        for filename, item in media.items():
            response = await c.get("/landing-assets/" + filename)
            assert response.status_code == 200, filename
            assert response.headers["content-type"] == item["mime"]
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert len(response.content) == item["bytes"]
        video = api.LANDING_DIR / "assets/rotina-negocio.mp4"
        response = await c.get("/landing-assets/rotina-negocio.mp4", headers={"Range": "bytes=0-31"})
        assert response.status_code == 206 and response.content == video.read_bytes()[:32]
        assert response.headers["content-range"] == "bytes 0-31/" + str(video.stat().st_size)
        for forbidden in ("index.html", "media-sources.json", "media-sources.md", "sales_requests_api.py", "not-approved.webp", "..%2Fapp%2Fmain.py"):
            assert (await c.get("/landing-assets/" + forbidden)).status_code in {404, 405}


async def test_css_js_mimes_are_exact_and_unknown_assets_fail_closed(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "orion-landing.css").write_text("body{color:navy}")
    (assets / "orion-landing.js").write_text("'use strict';")
    monkeypatch.setattr(api, "LANDING_DIR", tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="https://test.invalid") as c:
        for filename, expected in (("orion-landing.css", "text/css"), ("orion-landing.js", "text/javascript")):
            response = await c.get("/landing-assets/" + filename)
            assert response.status_code == 200 and response.headers["content-type"].startswith(expected)
            assert response.headers["x-content-type-options"] == "nosniff"
        assert (await c.get("/landing-assets/negocio-barbearia.webp")).status_code == 404
