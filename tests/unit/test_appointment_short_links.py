"""Short URLs are identifiers, never bearer tokens or personal-data previews."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import appointment_short_links as links
from app.appointment_message_templates import safe_link, compose
from app.main import app


def sample_row():
    return {
        "id": uuid4(), "protocol": "AG-ABCDEF", "customer_name": "Lucas Cliente",
        "professional_name": "Peixoto",
        "start_at": datetime(2026, 11, 3, 14, tzinfo=timezone.utc),
        "timezone": "America/Campo_Grande",
        "items": [{"name_snapshot": "Corte", "duration_snapshot_min": 40}],
    }


@pytest.mark.parametrize("value", ["AGABCDEF", "AGABCDEFGHJK", "AG23456789"])
def test_accepted_global_short_codes_have_no_uuid_or_token(value):
    assert links.checked_code(value) == value
    assert links.natural_code("AG-" + value[2:]) == value


@pytest.mark.parametrize("bad", [
    "admin", "login", "AG", "AG1", "AG123", "AG-ABCDEF", "AGABCDEF?redirect=/admin",
    "AGABCDEF/other", "AGABCDEF#token", "AGABCDEF@", "../AGABCDEF", "AGAAAAAAA%2F",
    "AGOOOOOOO", "AG000000", "AG" + "A" * 30
])
def test_invalid_short_codes_fail_closed(bad):
    with pytest.raises(HTTPException):
        links.checked_code(bad)


def test_global_short_code_generation_has_enough_entropy():
    generated = [links.random_code() for _ in range(500)]
    assert all(len(code) == 12 and links.CODE_RE.fullmatch(code) for code in generated)
    assert len(set(generated)) == 500


def test_client_and_staff_urls_are_scoped_and_have_no_hidden_capabilities(monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    code = "AGABCDEF"
    assert links.customer_url(code) == "https://orionagenda.pro/AGABCDEF"
    assert links.staff_url(code) == "https://orionagenda.pro/p/AGABCDEF"
    assert safe_link(links.customer_url(code)) == links.customer_url(code)
    assert safe_link(links.staff_url(code)) == links.staff_url(code)
    assert safe_link("https://attacker.example/AGABCDEF") is None
    assert safe_link("https://orionagenda.pro/AGABCDEF?token=x") is None
    staff = compose("professional_new", sample_row(), company="Barbearia", short_code=code)
    assert "https://orionagenda.pro/p/AGABCDEF" in staff
    assert str(sample_row()["id"]) not in staff
    client = compose("confirmation", sample_row(), company="Barbearia",
                     customer_link=links.customer_url(code), short_code=code)
    assert "https://orionagenda.pro/AGABCDEF" in client
    assert "/a#" not in client and "{" not in client
    assert "*Olá, Lucas!* 👋" in client


def test_get_shell_has_no_private_details_or_redirect(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_SHORT_LINKS_ENABLED", "1")
    with TestClient(app) as client:
        for url in ("/AGABCDEF", "/AGHJKLMN", "/p/AGABCDEF"):
            response = client.get(url, follow_redirects=False)
            assert response.status_code == 200
            assert "Confirme seu acesso" in response.text
            assert "UUID" not in response.text and "AGABCDEF" not in response.text
            assert "no-store" in response.headers["cache-control"]
            assert response.headers.get("referrer-policy") == "no-referrer"


def test_anonymous_unknown_request_is_indistinguishable_and_sends_nothing(monkeypatch):
    from app import appointment_short_api as api
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_SHORT_LINKS_ENABLED", "1")
    async def rate(*args, **kwargs):return None
    accepted = []
    async def queue_only(code, role):
        accepted.append((code, role))
    async def illegal(*args):
        raise AssertionError("Public API must not query appointment existence")
    monkeypatch.setattr(api, "public_limit", rate)
    monkeypatch.setattr(api, "resolve_global", illegal)
    monkeypatch.setattr(api, "enqueue_request", queue_only)
    with TestClient(app) as client:
        reply = client.post("/api/short-appointments/AGABCDEF/request-code",
                            json={"recipient_kind": "customer"})
        assert reply.status_code == 202
        assert reply.json() == links.GENERIC_REQUEST
        assert accepted == [("AGABCDEF", "customer")]
        assert "no-store" in reply.headers["cache-control"]
        failed = client.post("/api/short-appointments/AGABCDEF/verify-code",
                             json={"recipient_kind": "professional", "otp": "000001"})
        assert failed.status_code == 403
        assert "cliente" not in failed.text.lower() and "profissional" not in failed.text.lower()


def test_step_up_cookie_is_httponly_secure_samesite_and_bounded():
    reply = JSONResponse({"ok": True})
    links.response_cookie(reply, links.CUSTOMER_COOKIE, links.opaque_token())
    value = reply.headers["set-cookie"]
    assert "__Host-orion-short-customer=" in value
    assert "httponly" in value.lower()
    assert "secure" in value.lower()
    assert "samesite=lax" in value.lower()
    assert "max-age=7200" in value.lower()
    assert "path=/" in value.lower()
