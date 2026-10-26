"""The Orion HTML and both domains serve one continuously updated suite source."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.main import panel_asset


async def test_live_suite_alias_returns_the_same_source_and_no_cache():
    live = await panel_asset("orion-suite-live.js")
    original = await panel_asset("orion-suite.js")
    assert live.path == original.path
    assert Path(live.path).name == "orion-suite.js"
    assert live.media_type == "text/javascript"
    assert live.headers["cache-control"] == "no-cache"


async def test_disallowed_asset_still_fails_closed():
    with pytest.raises(HTTPException) as failure:
        await panel_asset("../.env")
    assert failure.value.status_code == 404


def test_panel_loads_the_live_unshadowed_suite():
    html = (Path(__file__).parents[2] / "app/static/panel.html").read_text(encoding="utf-8")
    assert '<script src="/panel-assets/orion-suite-live.js"></script>' in html
    assert '<script src="/panel-assets/orion-suite.js"></script>' not in html
