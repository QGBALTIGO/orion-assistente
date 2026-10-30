"""The public booking must always use the verified platform admin channel when central rollout is enabled."""
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from app import appointment_confirmation as confirmation


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class BookingSession:
    def __init__(self, appointment):
        self.appointment = appointment
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, values):
        sql = str(query)
        self.queries.append(sql)
        assert values["t"] == self.tenant
        assert values["protocol"] == "AG-G32XU5"
        assert values["phone"] == "+5567999999902"
        if "appointment_communication_settings" in sql:
            raise AssertionError("Per-company settings cannot choose platform destination")
        assert "appointments" in sql and "customers" in sql and "confirmed" in sql
        return ScalarResult(self.appointment)


@asynccontextmanager
async def tenant_scope(db, tenant):
    assert db.tenant == tenant
    yield db


@pytest.mark.asyncio
async def test_every_company_uses_only_the_admin_connected_whatsapp(monkeypatch):
    from app import public_site_api
    source_company, admin_company = uuid4(), uuid4()
    db = BookingSession(uuid4())
    db.tenant = source_company

    async def resolve(slug):
        assert slug == "barbearia-peixoto"
        return source_company

    seen = []

    async def admin_channel(tenant):
        seen.append(tenant)
        assert tenant == admin_company
        return "556781234567"

    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID", str(admin_company))
    monkeypatch.setenv("QR_AI_TENANT_ID", str(admin_company))
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "1")
    monkeypatch.setattr(public_site_api, "resolve_public", resolve)
    monkeypatch.setattr(confirmation, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(confirmation, "tenant_transaction", tenant_scope)
    monkeypatch.setattr(confirmation, "connected_channel_phone", admin_channel)

    payload = await confirmation.public_followup(
        "barbearia-peixoto", {"protocol": "AG-G32XU5"}, "+5567999999902"
    )
    url = urlsplit(payload["whatsapp_confirmation_url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "wa.me", "/556781234567")
    message = parse_qs(url.query)["text"][0]
    assert message == confirmation.compose_prefill("AG-G32XU5")
    assert "AG-G32XU5" in message
    assert seen == [admin_company]
    assert len(db.queries) == 1, "Never query another company's channel or preferences"
    assert "management_url" not in payload and "customer_access" not in payload


@pytest.mark.asyncio
async def test_central_redirect_fails_closed_without_admin_verified_connection(monkeypatch):
    from app import public_site_api
    tenant, admin = uuid4(), uuid4()
    db = BookingSession(uuid4())
    db.tenant = tenant

    async def resolve(_):
        return tenant

    async def unavailable(t):
        assert t == admin
        return None

    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(admin))
    monkeypatch.setattr(public_site_api, "resolve_public", resolve)
    monkeypatch.setattr(confirmation, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(confirmation, "tenant_transaction", tenant_scope)
    monkeypatch.setattr(confirmation, "connected_channel_phone", unavailable)

    result = await confirmation.public_followup("outro-site", {"protocol": "AG-G32XU5"}, "+5567999999902")
    assert result == {"communication_state": "channel_unavailable"}


@pytest.mark.asyncio
async def test_central_redirect_does_not_link_an_uncommitted_or_wrong_phone_booking(monkeypatch):
    from app import public_site_api
    tenant, admin = uuid4(), uuid4()
    db = BookingSession(None)
    db.tenant = tenant

    async def resolve(_):
        return tenant

    async def forbidden(_):
        raise AssertionError("Never resolve a channel for an unverified booking")

    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(admin))
    monkeypatch.setattr(public_site_api, "resolve_public", resolve)
    monkeypatch.setattr(confirmation, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(confirmation, "tenant_transaction", tenant_scope)
    monkeypatch.setattr(confirmation, "connected_channel_phone", forbidden)
    assert await confirmation.public_followup("outro-site", {"protocol": "AG-G32XU5"}, "+5567999999902") == {}


@pytest.mark.asyncio
async def test_central_number_never_comes_from_company_site_config(monkeypatch):
    from app import qr_legacy
    hub = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(hub))
    called = []

    async def from_verified_channel(tenant):
        called.append(tenant)
        return "556781234567"

    monkeypatch.setattr(confirmation, "connected_channel_phone", from_verified_channel)
    assert await confirmation.central_admin_phone() == "556781234567"
    assert called == [hub]
    monkeypatch.delenv("QR_AI_TENANT_ID")
    assert await confirmation.central_admin_phone() is None
    assert called == [hub]


@pytest.mark.asyncio
async def test_switching_admin_whatsapp_changes_all_next_links_without_hardcoded_number(monkeypatch):
    from app import public_site_api
    company_a, company_b, admin = uuid4(), uuid4(), uuid4()
    active = {"tenant": company_a, "number": "556781234567"}
    db = BookingSession(uuid4())

    async def resolve(_slug):
        db.tenant = active["tenant"]
        return db.tenant

    async def admin_channel(tenant):
        assert tenant == admin
        return active["number"]

    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(admin))
    monkeypatch.setattr(public_site_api, "resolve_public", resolve)
    monkeypatch.setattr(confirmation, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(confirmation, "tenant_transaction", tenant_scope)
    monkeypatch.setattr(confirmation, "connected_channel_phone", admin_channel)
    first = await confirmation.public_followup("site-a", {"protocol": "AG-G32XU5"}, "+5567999999902")
    assert urlsplit(first["whatsapp_confirmation_url"]).path == "/556781234567"

    # The administrator relinks a new verified number. No per-company change.
    active["tenant"], active["number"] = company_b, "5567912345678"
    second = await confirmation.public_followup("site-b", {"protocol": "AG-G32XU5"}, "+5567999999902")
    assert urlsplit(second["whatsapp_confirmation_url"]).path == "/5567912345678"
    active["tenant"] = company_a
    third = await confirmation.public_followup("site-a", {"protocol": "AG-G32XU5"}, "+5567999999902")
    assert urlsplit(third["whatsapp_confirmation_url"]).path == "/5567912345678"


@pytest.mark.asyncio
async def test_public_contact_also_tracks_only_the_active_admin_number(monkeypatch):
    from app import public_site_api as site_api
    admin,tenant=uuid4(),uuid4()
    db=BookingSession(uuid4())
    db.tenant=tenant
    active={"phone":"556781234567"}

    async def no_limit(*_args):return None
    async def resolve(_slug):return tenant
    async def published(*_args):return None
    async def site_catalogue(*_args):
        return {"site":{"whatsapp_phone":"+5567999999901"},"services":[]}
    async def admin_phone(connected_tenant):
        assert connected_tenant==admin
        return active["phone"]

    monkeypatch.setenv("QR_AI_TENANT_ID",str(admin))
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED","1")
    monkeypatch.setattr(site_api,"public_limit",no_limit)
    monkeypatch.setattr(site_api,"resolve_public",resolve)
    monkeypatch.setattr(site_api,"published_guard",published)
    monkeypatch.setattr(site_api,"catalogue",site_catalogue)
    monkeypatch.setattr(site_api,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(site_api,"tenant_transaction",tenant_scope)
    monkeypatch.setattr(confirmation,"connected_channel_phone",admin_phone)
    first=await site_api.public_catalogue("barbearia-peixoto",object())
    assert first["site"]["whatsapp_phone"]=="+556781234567"
    active["phone"]="5567912345678"
    second=await site_api.public_catalogue("barbearia-peixoto",object())
    assert second["site"]["whatsapp_phone"]=="+5567912345678"
    active["phone"]=None
    offline=await site_api.public_catalogue("barbearia-peixoto",object())
    assert offline["site"]["whatsapp_phone"] is None
