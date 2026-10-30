"""iOS Home Screen icon and admin-only Web Push pilot security regression."""
import base64
from io import BytesIO
import json
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from PIL import Image

from app.main import app
from app.panel_auth import panel_actor
from app import orion_pwa_api as pwa
from app.orion_pwa_icon import icon_png, NAVY


def vapid(monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    raw = private.private_bytes(serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv("ORION_PUSH_VAPID_PRIVATE_KEY", base64.b64encode(raw).decode())
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ORION_ADMIN_PUSH_ENABLED", "1")


def subscription():
    encode=lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    private = ec.generate_private_key(ec.SECP256R1())
    browser_key = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        "endpoint": "https://web.push.apple.com/web-push/test-0123456789abcdef",
        "keys": {
            "p256dh": encode(browser_key),
            "auth": encode(os.urandom(16)),
        },
    }


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.rate = 0
    async def hlen(self, key):
        return len(self.hashes.get(key, {}))
    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)
    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)
    async def eval(self, script, _key_count, *args):
        if "HEXISTS" in script:
            key, digest, cipher, maximum, _ttl = args
            row = self.hashes.setdefault(key, {})
            if digest not in row and len(row) >= maximum:
                return 0
            row[digest] = cipher
            return 1
        self.rate += 1
        return self.rate
    async def aclose(self):
        pass


def test_orion_png_icons_are_square_real_brand_artwork():
    for size in (180, 192, 512):
        png = icon_png(size)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(BytesIO(png)) as image:
            assert image.size == (size, size)
            assert image.getpixel((0, 0)) == (11, 16, 32)
            assert any(image.getpixel((x, y))[2] > 100
                for x in range(0, size, max(1, size // 12))
                for y in range(0, size, max(1, size // 12)))
    with pytest.raises(ValueError):
        icon_png(1024)


def test_vapid_uses_p256_key_and_admin_isolated(monkeypatch):
    vapid(monkeypatch)
    public = base64.urlsafe_b64decode(pwa._vapid_public() + "=")
    assert len(public) == 65 and public[0] == 4
    account = uuid4()
    admin = {"role":"admin","auth_provider":"legacy_admin","account_id":str(account)}
    assert pwa._admin(admin) == account
    for identity in (
        {"role":"member","auth_provider":"password","account_id":str(account)},
        {"role":"support","account_id":str(account)},
        {**admin, "maintenance":{"active":True}},
    ):
        with pytest.raises(HTTPException) as error:
            pwa._admin(identity)
        assert error.value.status_code == 403


@pytest.mark.parametrize("endpoint", [
    "http://web.push.apple.com/x"*3,
    "https://push.apple.com.evil.example/01234567891234567890",
    "https://127.0.0.1/a-very-long-apple-push-looking-endpoint",
    "https://example.com/a-very-long-apple-push-looking-endpoint",
    "https://web.push.apple.com:444/bogus-subscription123",
    "https://user@web.push.apple.com/push-test-abcde",
    "https://web.push.apple.com/push-test-abcde#secret",
])
def test_no_arbitrary_push_urls_or_ssrf(endpoint):
    with pytest.raises(HTTPException):
        pwa._valid_endpoint(endpoint)


@pytest.mark.asyncio
async def test_only_registered_admin_device_receives_generic_push(monkeypatch):
    vapid(monkeypatch)
    store = FakeRedis()
    monkeypatch.setattr(pwa, "_redis", lambda: store)
    identity = {"role":"admin","auth_provider":"legacy_admin","account_id":str(uuid4())}
    app.dependency_overrides[panel_actor] = lambda: identity
    captured = []
    def fake_webpush(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(status_code=201)
    monkeypatch.setitem(sys.modules, "pywebpush", SimpleNamespace(webpush=fake_webpush))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://orionagenda.pro") as client:
            app_icon = await client.get("/pwa/icon-180.png")
            assert app_icon.status_code == 200 and app_icon.headers["content-type"] == "image/png"
            page = await client.get("/panel")
            assert page.status_code == 200
            assert 'rel="apple-touch-icon"' in page.text
            assert 'href="/manifest.webmanifest"' in page.text
            manifest = await client.get("/manifest.webmanifest")
            assert manifest.json()["display"] == "standalone"
            assert manifest.json()["id"] == "/panel"
            sw = await client.get("/pwa-sw.js")
            assert sw.status_code == 200 and "showNotification" in sw.text
            cfg = await client.get("/api/pwa/admin/config")
            assert cfg.status_code == 200 and cfg.json()["enabled"]
            assert cfg.json()["devices"] == 0
            s = subscription()
            denied = await client.post("/api/pwa/admin/test", json={"endpoint":s["endpoint"]})
            assert denied.status_code == 409 and captured == []
            started = await client.post("/api/pwa/admin/subscribe", json=s)
            assert started.status_code == 201
            internal = next(iter(next(iter(store.hashes.values())).values()))
            assert s["endpoint"] not in internal
            assert json.loads(Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode())
                .decrypt(internal.encode())) == s
            sent = await client.post("/api/pwa/admin/test", json={"endpoint":s["endpoint"]})
            assert sent.status_code == 202 and sent.json()["accepted_by_push_service"]
            assert len(captured) == 1
            assert captured[0]["subscription_info"]["endpoint"] == s["endpoint"]
            assert captured[0]["content_encoding"] == "aes128gcm"
            assert "cliente" not in captured[0]["data"].lower()
            stopped = await client.post("/api/pwa/admin/unsubscribe",
                                         json={"endpoint":s["endpoint"]})
            assert stopped.status_code == 200
            again = await client.post("/api/pwa/admin/test", json={"endpoint":s["endpoint"]})
            assert again.status_code == 409 and len(captured) == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_non_admin_cannot_read_config_or_register(monkeypatch):
    vapid(monkeypatch)
    app.dependency_overrides[panel_actor] = lambda: {
        "role":"member","auth_provider":"password","account_id":str(uuid4())}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://orionagenda.pro") as client:
            assert (await client.get("/api/pwa/admin/config")).status_code == 403
            assert (await client.post("/api/pwa/admin/subscribe",
                 json=subscription())).status_code == 403
    finally:
        app.dependency_overrides.clear()

def test_real_pywebpush_builds_vapid_and_encrypted_payload_without_network(monkeypatch):
    vapid(monkeypatch)
    from pywebpush import webpush
    command = webpush(
        subscription_info=subscription(),
        data=json.dumps({"type": "orion-admin-test"}),
        vapid_private_key=os.environ["ORION_PUSH_VAPID_PRIVATE_KEY"],
        vapid_claims={"sub": "https://orionagenda.pro"},
        content_encoding="aes128gcm", curl=True, ttl=120,
    )
    assert isinstance(command, str)
    assert "https://web.push.apple.com" in command
    assert "authorization" in command.lower()
    assert "aes128gcm" in command.lower()
