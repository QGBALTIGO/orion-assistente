"""Public field assets resolve through exact allowlists before their consumers."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import httpx

from app.main import app, landing_router, public_site_router


ROOT = Path(__file__).resolve().parents[2]


class Assets(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.scripts = []
        self.styles = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(urlsplit(attrs["src"]).path)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(urlsplit(attrs.get("href", "")).path)


def asset_route(namespace):
    # Included routers can remain nested in recent FastAPI versions. Inspect the
    # same router object imported by app.main rather than assuming a flat tree.
    owner = {"panel-assets": app, "public-assets": public_site_router, "landing-assets": landing_router}[namespace]
    return next(route for route in owner.routes if getattr(route, "path", None) == "/" + namespace + "/{asset}")


@pytest.mark.parametrize("namespace", ["panel-assets", "public-assets", "landing-assets"])
@pytest.mark.parametrize("extension,mime", [("js", "text/javascript"), ("css", "text/css")])
async def test_shared_field_asset_has_an_explicit_public_route_and_the_real_source(namespace, extension, mime):
    name = "orion-fields." + extension
    route = asset_route(namespace)
    assert route.dependant.dependencies == []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        result = await client.get("/" + namespace + "/" + name)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith(mime)
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["cache-control"] == "no-cache"
    assert result.content == (ROOT / "app" / "static" / name).read_bytes()


@pytest.mark.parametrize("namespace", ["panel-assets", "public-assets", "landing-assets"])
@pytest.mark.parametrize("name", ["orion-fields.map", "private.py", "unknown.js"])
async def test_field_asset_addition_does_not_widen_the_allowlist(namespace, name):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        result = await client.get("/" + namespace + "/" + name)
    assert result.status_code == 404


@pytest.mark.parametrize("relative,prefix,consumers", [
    ("app/static/panel.html", "panel-assets", ["orion-suite-live.js", "orion-onboarding.js", "orion-channels.js", "orion-conversations.js"]),
    ("app/static/orion-public.html", "public-assets", ["orion-public.js"]),
    ("app/static/orion-invite.html", "panel-assets", ["orion-invite.js"]),
    ("landing/index.html", "landing-assets", ["orion-landing.js"]),
])
def test_templates_load_one_shared_core_before_every_relevant_consumer(relative, prefix, consumers):
    assets = Assets((ROOT / relative).read_text(encoding="utf-8"))
    core = "/" + prefix + "/orion-fields.js"
    assert assets.scripts.count(core) == 1
    assert assets.styles.count("/" + prefix + "/orion-fields.css") == 1
    for name in consumers:
        assert assets.scripts.index(core) < assets.scripts.index("/" + prefix + "/" + name)


async def test_invitation_self_only_csp_allows_the_local_fields_core():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        result = await client.get("/convite")
    assert result.status_code == 200
    assert "script-src 'self'" in result.headers["content-security-policy"]
    assert "style-src 'self'" in result.headers["content-security-policy"]
    assert result.headers["referrer-policy"] == "no-referrer"
