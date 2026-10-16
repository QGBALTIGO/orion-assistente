"""Auth UI contracts and privileged account gating, without a browser dependency."""
from pathlib import Path
import re
import pytest
import httpx

from app.main import app
from app.account_auth import Register, EmailMixin


def test_auth_panel_has_all_screens_without_google_or_hardcoded_owner():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for identifier in (
        "loginForm","registerForm","forgotForm","resetForm","rememberMe",
        "authTabLogin","authTabRegister","authGlobalMessage",
        "ownerClaimForm","memberView","authSubmitRegister",
    ):
        assert re.search(r'\bid="'+identifier+'"',html),identifier
    assert "Continuar com o Google" not in html
    assert "kaykys468@gmail.com" not in html
    assert "prefers-reduced-motion" in html
    assert "prefers-color-scheme:light" in html
    assert "max-width:900px" in html and "max-width:560px" in html
    assert "orion-brand-dark.svg" in html and "orion-brand-light.svg" in html


def test_account_email_and_password_validation():
    with pytest.raises(ValueError):
        Register(name="A",email="bad@",password="short")
    value=Register(name="  Pessoa   Teste  ",email="  Person@Example.COM  ",
                   password="Twelve#Chars123")
    assert value.email=="person@example.com"
    assert value.name=="Pessoa Teste"
    with pytest.raises(ValueError):
        Register(name="Valid Name",email="person@invalid",password="Twelve#Chars123")


@pytest.mark.asyncio
async def test_assets_are_allowlisted_and_svg(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        for asset in ("orion-symbol.svg","orion-brand-dark.svg","orion-brand-light.svg"):
            r=await client.get("/brand/"+asset)
            assert r.status_code==200
            assert "image/svg+xml" in r.headers["content-type"]
            assert b"<svg" in r.content
        assert (await client.get("/brand/../panel.html")).status_code in (404,405)
        assert (await client.get("/brand/untrusted.svg")).status_code==404
