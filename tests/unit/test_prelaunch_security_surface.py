"""Security release boundaries that must hold without a live database."""
import pytest
from fastapi import HTTPException
from app.main import app
from app.public_site_api import signing_key, issue_quote


def test_public_api_has_no_discovery_endpoints():
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None
    assert not any(route.path in {"/docs", "/redoc", "/openapi.json"} for route in app.routes)


def test_public_signing_never_falls_back_to_platform_support_token(monkeypatch):
    monkeypatch.delenv("PUBLIC_SITE_SIGNING_KEY", raising=False)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-support-token-" + "A" * 60)
    with pytest.raises(HTTPException) as error:
        signing_key()
    assert error.value.status_code == 503
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY", "short")
    with pytest.raises(HTTPException):
        signing_key()


def test_dedicated_signing_key_still_signs_quotes(monkeypatch):
    from datetime import datetime, timezone
    from uuid import uuid4
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY", "synthetic-public-signing-secret-" + "Q" * 48)
    monkeypatch.delenv("SUPPORT_API_TOKEN", raising=False)
    stamp=datetime(2026,10,30,18,0,tzinfo=timezone.utc)
    selection={"fingerprint":"a"*64,"base_id":uuid4(),"addon_ids":[]}
    quote=issue_quote("test-business",uuid4(),stamp.date(),selection,stamp)
    assert isinstance(quote,str) and len(quote)>90
