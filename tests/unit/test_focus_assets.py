"""Serve only reviewed focus assets to every Orion surface."""
from pathlib import Path

import httpx
import pytest

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_focus_assets_are_served_to_public_panel_landing_and_receipt(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://orion.test"
    ) as client:
        for prefix in ("panel-assets", "public-assets",
                       "appointment-assets", "landing-assets"):
            for filename, media_type in (
                ("orion-focus.js", "javascript"),
                ("orion-focus.css", "text/css"),
            ):
                response = await client.get(f"/{prefix}/{filename}")
                assert response.status_code == 200, (prefix,filename,response.text[:200])
                assert media_type in response.headers["content-type"]
                assert response.content.decode() == (
                    ROOT / "app" / "static" / filename
                ).read_text(encoding="utf-8")
        for page in ("/painel", "/a", "/"):
            response = await client.get(page)
            assert response.status_code == 200, (page,response.text[:100])
            assert "orion-focus.js" in response.text
            assert "orion-focus.css" in response.text


@pytest.mark.asyncio
async def test_non_allowlisted_focus_adjacent_paths_are_denied():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://orion.test"
    ) as client:
        for prefix in ("panel-assets", "public-assets", "landing-assets"):
            response=await client.get(f"/{prefix}/orion-focus.js.map")
            assert response.status_code == 404
