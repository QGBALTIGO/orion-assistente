"""Synthetic, no-send WhatsApp click-to-chat and QR verification."""
import base64
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
from uuid import UUID

from fastapi import HTTPException, Response
from PIL import Image
from pydantic import ValidationError
import pytest

from app import subscription_whatsapp_link as api
from app import subscription_whatsapp_link_schemas as schemas
from app import plan_catalog, plan_routing, workspace_policy

TENANT = UUID("00000000-0000-4000-8000-000000000119")
PHONE = "+5567999999901"
ACTOR = SimpleNamespace(account_id=UUID("00000000-0000-4000-8000-000000000123"))


def payload(message=""):
    return schemas.LinkRequest.model_validate({"phone": PHONE, "message": message})


def test_official_link_format_and_local_png_decode():
    result = api.generate(payload("Olá, gostaria de agendar 😀"))
    url = urlsplit(result["url"])
    assert url.scheme == "https"
    assert url.netloc == "wa.me"
    assert url.path == "/5567999999901"
    assert parse_qs(url.query)["text"] == ["Olá, gostaria de agendar 😀"]
    assert "%20" in result["url"] and "%F0%9F%98%80" in result["url"]
    assert result["format"] == "png"
    assert result["mime_type"] == "image/png"
    image = base64.b64decode(result["image_base64"], validate=True)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(image)) as qr:
        assert qr.format == "PNG"
        assert 100 <= qr.width <= 1200 and qr.width == qr.height


def test_short_link_without_greeting():
    assert api.generate(payload())["url"] == "https://wa.me/5567999999901"


@pytest.mark.parametrize("phone", [
    "5567999999901", "+0123456", "abc", "+55 (67) 9999-9999",
    "https://evil.example/123", "+", "+5567\n9999999",
])
def test_bad_phone_denied_before_render(phone):
    with pytest.raises(ValidationError):
        schemas.LinkRequest.model_validate({"phone": phone, "message": "Oi"})


@pytest.mark.parametrize("message", [
    "x" * 361, "\u202e controle", "\0", "\ud800", "\x01", "🎉" * 301
])
def test_control_characters_and_unbounded_text_denied(message):
    with pytest.raises((ValidationError, UnicodeEncodeError)):
        schemas.LinkRequest.model_validate({"phone": PHONE, "message": message})


def test_emojis_are_normalized_without_html_injection():
    link = api.generate(payload("Contato <script>alert(1)</script>"))
    assert "<script>" not in link["url"]
    assert "%3Cscript%3E" in link["url"]


def test_dense_encoded_text_fails_instead_of_creating_oversized_qr():
    with pytest.raises(HTTPException) as exc:
        api.generate(payload("😀" * 300))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_authenticated_owner_only_and_no_external_send(monkeypatch):
    called = []
    @asynccontextmanager
    async def owner(tenant, actor, feature=None):
        assert tenant == TENANT and actor is ACTOR and feature == "whatsapp_link.manage"
        called.append("authorized")
        yield None
    monkeypatch.setattr(api.shared, "owner_transaction", owner)
    response = Response()
    result = await api.generate_link(TENANT, payload("Olá"), response, ACTOR)
    assert result["url"].startswith("https://wa.me/")
    assert called == ["authorized"]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_denied_owner_does_not_generate_qr(monkeypatch):
    @asynccontextmanager
    async def owner(*args, **kwargs):
        raise HTTPException(403, "owner_only")
        yield None
    monkeypatch.setattr(api.shared, "owner_transaction", owner)
    with pytest.raises(HTTPException) as exc:
        await api.generate_link(TENANT, payload("Oi"), Response(), ACTOR)
    assert exc.value.status_code == 403


def test_only_active_subscriptions_receive_feature():
    assert "whatsapp_link.manage" in plan_catalog.ALL_FEATURES
    assert "whatsapp_link.manage" in workspace_policy.SUBSCRIPTION_WORKSPACE_FEATURES
    assert "whatsapp_link.manage" not in plan_catalog.ELITE_FEATURES
    assert plan_routing.feature_for_path("/subscription-whatsapp/whatsapp-link/generate","POST") == "whatsapp_link.manage"
    assert plan_routing.subscription_workspace_route("/subscription-whatsapp/whatsapp-link/generate","POST",{}) is True
