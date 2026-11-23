"""Regression checks for immutable public assets and safe cache busting."""
import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.public_site_api import (
    PUBLIC_ASSETS,
    STATIC,
    public_asset,
    public_asset_versions,
    version_public_assets,
)


def request_for(version=""):
    query = ("v=" + version).encode() if version else b""
    return Request({
        "type": "http", "method": "GET",
        "path": "/public-assets/orion-public.js",
        "query_string": query, "headers": [],
    })


def test_fingerprinted_links_change_with_asset_contents():
    versions = public_asset_versions()
    assert set(versions) == set(PUBLIC_ASSETS)
    assert all(len(value) == 16 for value in versions.values())
    template = (STATIC / "orion-public.html").read_text(encoding="utf-8")
    versioned = version_public_assets(template)
    for name in ("orion-public.js", "orion-public.css",
                 "orion-fields.js", "orion-public-theme.js"):
        assert f'"/public-assets/{name}?v={versions[name]}"' in versioned
    assert version_public_assets(versioned) == versioned


def test_cache_is_immutable_only_for_the_correct_version():
    versions = public_asset_versions()
    good = asyncio.run(public_asset("orion-public.js",
                                   request_for(versions["orion-public.js"])))
    assert good.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    stale = asyncio.run(public_asset("orion-public.js", request_for("stale")))
    assert stale.headers["Cache-Control"] == "no-cache"
    legacy = asyncio.run(public_asset("orion-public.js", request_for()))
    assert legacy.headers["Cache-Control"] == "no-cache"


def test_arbitrary_files_are_not_exposed():
    with pytest.raises(HTTPException) as error:
        asyncio.run(public_asset("panel.html", request_for()))
    assert error.value.status_code == 404
