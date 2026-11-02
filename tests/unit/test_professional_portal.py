"""Professional-only identities, routes and fail-closed contracts.

These tests never use production data, send email, or call a payment provider.
PostgreSQL-backed invitation and isolation tests run in CI separately.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest
from pydantic import ValidationError

from app.catalog_api import Actor, authorize
from app.management_team import InvitationCreate, MemberChange
from app.professional_invites import (
    InviteProfessional, SetProfessionalEmail, ChangeProfessionalAccess,
)
from app.professional_portal import (
    individual_access, BlockRequest, ProfessionalReschedule,
)
from app.main import app

BASE = Path(__file__).resolve().parents[2]


def test_professional_invite_requires_verified_email_and_idempotency():
    data = InviteProfessional(email="  BARBER@EXAMPLE.INVALID ", idempotency_key="synthetic-token-00001")
    assert data.email == "barber@example.invalid"
    for payload in (
        {"email": "", "idempotency_key": "synthetic-token-00001"},
        {"email": "example.com", "idempotency_key": "synthetic-token-00001"},
        {"email": "barber@example.invalid", "idempotency_key": "short"},
        {"email": "barber@example.invalid", "idempotency_key": "synthetic-token-00001", "role": "owner"},
    ):
        with pytest.raises(ValidationError):
            InviteProfessional(**payload)
    assert SetProfessionalEmail(email=None).email is None


def test_editor_cannot_be_converted_into_professional_via_generic_invite():
    with pytest.raises(ValidationError):
        InvitationCreate(email="barber@example.invalid", role="professional",
                         idempotency_key="synthetic-token-00001")
    with pytest.raises(ValidationError):
        MemberChange(role="professional", active=True,
                     expected_updated_at=datetime.now(timezone.utc))


def test_professional_membership_cannot_read_or_edit_legacy_company_routes():
    tenant=uuid4()
    professional=Actor(role="member", account_id=uuid4(),
                       tenant_id=tenant, membership_role="professional")
    for write in (False, True):
        with pytest.raises(HTTPException) as error:
            authorize(professional, tenant, write=write)
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_individual_portal_requires_real_verified_member_not_support_or_maintenance():
    for actor in (
        Actor(role="support"),
        Actor(role="admin", account_id=uuid4()),
        Actor(role="viewer", account_id=uuid4()),
        Actor(role="member", account_id=None),
        Actor(role="member", account_id=uuid4(), maintenance=True),
    ):
        with pytest.raises(HTTPException) as error:
            async with individual_access(actor):
                raise AssertionError("Unauthorised portal context")
        assert error.value.status_code == 403


def test_professional_mutation_requires_aware_version_and_valid_block_reason():
    with pytest.raises(ValidationError):
        ProfessionalReschedule(day="2026-11-10",clock="12:30:00",
                               idempotency_key="synthetic-key-001",
                               expected_updated_at="2026-11-10T12:30:00")
    assert ProfessionalReschedule(
        day="2026-11-10",clock="12:30:00",
        idempotency_key="synthetic-key-001",
        expected_updated_at="2026-11-10T12:30:00Z",
    ).clock.hour == 12
    with pytest.raises(ValidationError):
        BlockRequest(day="2026-11-10",start_time="10:00",end_time="11:00",
                     reason="x",idempotency_key="synthetic-key-001")
    assert ChangeProfessionalAccess(action="suspend",expected_updated_at=datetime.now(timezone.utc)).action == "suspend"


@pytest.mark.asyncio
async def test_public_portal_assets_and_unauthenticated_data_are_separated(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN", "x"*64)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.invalid") as client:
        landing=await client.get("/profissional")
        assert landing.status_code == 200
        assert "no-store" in landing.headers["cache-control"]
        assert "/api/v1/professional" not in landing.text
        for asset in ("orion-professional.js","orion-professional.css"):
            response=await client.get("/panel-assets/"+asset)
            assert response.status_code == 200
            assert "nosniff" == response.headers.get("x-content-type-options")
        for endpoint in ("/api/v1/professional/me","/api/v1/professional/dashboard",
                         "/api/v1/professional/agenda","/api/v1/professional/finance",
                         "/api/v1/professional/goals"):
            response=await client.get(endpoint)
            assert response.status_code == 401, (endpoint, response.text)


def test_portal_has_no_broad_data_endpoints():
    source=(BASE/"app"/"professional_portal.py").read_text(encoding="utf-8")
    ui=(BASE/"app"/"static"/"orion-professional.js").read_text(encoding="utf-8")
    assert "professional_portal_links" in source
    assert "m.role='professional'" in source
    assert "FOR SHARE OF l,m" in source
    assert "authorized_professional_id=professional" in source
    assert "SELECT id FROM appointments" in source
    assert "WHERE tenant_id=:t AND professional_id=:p" in source
    assert "/management/customers" not in ui
    assert "/tenants/" not in ui
    assert "data-page=" in ui
    assert "professional_portal_links" in (BASE/"alembic"/"versions"/"0057_professional_portal.py").read_text(encoding="utf-8")
