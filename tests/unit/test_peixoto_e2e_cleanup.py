"""E2E cleanup is strict, idempotent, and disabled until explicitly enabled."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import peixoto_e2e_cleanup as cleanup

PHONE = "+5567999999922"
PROTOCOL = "AG-3SVM6N"


def booking(**overrides):
    now = datetime.now(timezone.utc)
    return {
        "id": uuid4(), "status": "confirmed",
        "updated_at": now, "customer_id": uuid4(),
        "professional_id": cleanup.PROFESSIONAL,
        "start_at": now + timedelta(days=4),
        "created_at": now,
        "customer_name": "TESTE ORION HOMOLOGACAO NAO COMPARECER",
        "customer_phone": PHONE,
        "public_slug": "barbearia-peixoto", **overrides
    }


def test_booking_guard_refuses_other_company_person_pro_or_expiry():
    now = datetime.now(timezone.utc)
    assert cleanup.candidate_error(booking(), PHONE, now) is None
    assert cleanup.candidate_error(booking(status="cancelled"), PHONE, now) == "already_cancelled"
    assert cleanup.candidate_error(booking(customer_phone="+5567999999923"), PHONE, now) == "contact_changed"
    assert cleanup.candidate_error(booking(customer_name="Real customer"), PHONE, now) == "not_a_labeled_test"
    assert cleanup.candidate_error(booking(professional_id=uuid4()), PHONE, now) == "professional_changed"
    assert cleanup.candidate_error(booking(start_at=now+timedelta(minutes=5)), PHONE, now) == "booking_too_close_to_cancel"
    assert cleanup.candidate_error(booking(created_at=now-timedelta(days=1)), PHONE, now) == "test_window_expired"
    assert cleanup.candidate_error(booking(public_slug="outro-site"), PHONE, now) == "site_changed"


@pytest.mark.asyncio
async def test_default_never_touches_database(monkeypatch):
    monkeypatch.delenv("ORION_E2E_PEIXOTO_CLEANUP", raising=False)
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: (_ for _ in ()).throw(AssertionError("database must not be accessed")))
    assert await cleanup.cancel_one() == "disabled"


@pytest.mark.asyncio
async def test_malformed_operator_input_fails_without_database(monkeypatch):
    monkeypatch.setenv("ORION_E2E_PEIXOTO_CLEANUP", "1")
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PROTOCOL", "UNKNOWN")
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PHONE", PHONE)
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: (_ for _ in ()).throw(AssertionError("database must not be accessed")))
    assert await cleanup.cancel_one() == "invalid_operator_scope"


class Result:
    def __init__(self, value):
        self.value = value
    def mappings(self):
        return self
    def all(self):
        return self.value
    def scalar_one(self):
        return self.value
    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, row, status):
        self.row, self.status = row, status
        self.statements = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def execute(self, stmt, params=None):
        s = str(stmt)
        self.statements.append(s)
        if "FROM appointments a" in s:
            assert params["t"] == cleanup.TENANT and params["protocol"] == PROTOCOL
            return Result([self.row])
        if "SELECT clock_timestamp()" in s:
            return Result(datetime.now(timezone.utc))
        if "FROM appointment_notifications" in s:
            return Result(self.status)
        raise AssertionError("Unexpected query: " + s[:100])


@asynccontextmanager
async def tenant_scope(_db, tenant):
    assert tenant == cleanup.TENANT
    yield _db


@pytest.mark.asyncio
async def test_delivered_test_can_only_cancel_exact_customer_row(monkeypatch):
    monkeypatch.setenv("ORION_E2E_PEIXOTO_CLEANUP", "1")
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PROTOCOL", PROTOCOL)
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PHONE", PHONE)
    instance = Session(booking(), "delivered")
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: lambda: instance)
    monkeypatch.setattr(cleanup, "tenant_transaction", tenant_scope)
    invoked = []
    async def fake_cancel(_db, tenant, ident, **kwargs):
        invoked.append((tenant,ident,kwargs))
        return {"status":"cancelled"}
    monkeypatch.setattr(cleanup, "cancel_appointment", fake_cancel)
    assert await cleanup.cancel_one() == "cancelled"
    assert len(invoked) == 1
    tenant, appointment, args = invoked[0]
    assert tenant == cleanup.TENANT and appointment == instance.row["id"]
    assert args["customer_phone"] == PHONE
    assert args["expected_updated_at"] == instance.row["updated_at"]
    assert args["future_only"] is True and args["context"].role == "customer"


@pytest.mark.asyncio
async def test_not_yet_sent_cannot_cancel_without_explicit_force(monkeypatch):
    monkeypatch.setenv("ORION_E2E_PEIXOTO_CLEANUP", "1")
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PROTOCOL", PROTOCOL)
    monkeypatch.setenv("ORION_E2E_PEIXOTO_PHONE", PHONE)
    monkeypatch.delenv("ORION_E2E_PEIXOTO_FORCE_CLEANUP", raising=False)
    instance = Session(booking(), "pending")
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: lambda: instance)
    monkeypatch.setattr(cleanup, "tenant_transaction", tenant_scope)
    async def no_cancel(*_args, **_kwargs):
        pytest.fail("must not cancel test with unconfirmed notice")
    monkeypatch.setattr(cleanup, "cancel_appointment", no_cancel)
    assert await cleanup.cancel_one() == "professional_delivery_not_confirmed"
