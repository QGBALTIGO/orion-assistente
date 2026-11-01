"""Synthetic only: one historical booking can be recovered without a second send."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import one_time_peixoto_recovery as repair


def appointment(**overrides):
    now = datetime.now(timezone.utc)
    value = {
        "id": uuid4(), "updated_at": now,
        "professional_id": uuid4(), "status": "confirmed",
        "end_at": now + timedelta(days=2),
        "phone_e164": "+5567999999922",
        "professional_active": True, "tenant_status": "active",
        "public_slug": repair.SITE, "public_enabled": True,
    }
    return {**value, **overrides}


def test_only_correct_company_professional_and_future_booking_allowed():
    good = appointment()
    assert repair.eligible_booking(good, datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(status="cancelled"), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(end_at=datetime(2025,1,1,tzinfo=timezone.utc)), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(public_slug="other-site"), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(professional_active=False), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(tenant_status="suspended"), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(phone_e164="+5511123"), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(phone_e164=None), datetime.now(timezone.utc))
    assert not repair.eligible_booking(appointment(public_enabled=False), datetime.now(timezone.utc))
    assert repair.booking_blocker(appointment(), datetime.now(timezone.utc)) is None
    assert repair.booking_blocker(appointment(status="completed"), datetime.now(timezone.utc)) == "booking_not_confirmed"
    assert repair.booking_blocker(appointment(phone_e164=None), datetime.now(timezone.utc)) == "professional_phone_missing"
    assert repair.booking_blocker(appointment(phone_e164="5567999999922"), datetime.now(timezone.utc)) == "professional_phone_invalid"
    assert repair.booking_blocker(appointment(end_at=datetime(2025,1,1,tzinfo=timezone.utc)), datetime.now(timezone.utc)) == "booking_already_finished"



class QueryResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.value

    def first(self):
        return self.value


class Session:
    def __init__(self, candidates, old_notice=None):
        self.candidates = candidates
        self.old_notice = old_notice
        self.calls = []
        self.notice = uuid4()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, sql, params=None):
        query = str(sql)
        self.calls.append((query, params))
        if "FROM public_booking_receipts" in query:
            return QueryResult(self.candidates)
        if "SELECT clock_timestamp()" in query:
            return QueryResult(datetime.now(timezone.utc))
        if "FROM appointment_notifications" in query:
            return QueryResult(self.old_notice)
        if "FROM appointment_communication_settings" in query:
            return QueryResult(None)
        if "INSERT INTO appointment_notifications" in query:
            assert params["pro"] == self.candidates[0]["professional_id"]
            assert params["appointment"] == self.candidates[0]["id"]
            assert params["tenant"] == repair.TENANT
            assert params["details"] == '{"orion_test_recovery": true}'
            assert params["channel"] is not None
            return QueryResult(self.notice)
        assert "pg_advisory_xact_lock" in query
        return QueryResult(None)


@asynccontextmanager
async def scope(session, tenant):
    assert tenant == repair.TENANT
    yield session


def fixture(monkeypatch, candidates, allowed=True, old_notice=None):
    from app import plan_permissions
    session = Session(candidates, old_notice)
    monkeypatch.setattr(repair, "central_enabled", lambda: True)
    monkeypatch.setattr(repair, "central_channel", lambda: (uuid4(), uuid4()))
    monkeypatch.setattr(repair, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(repair, "tenant_transaction", scope)
    async def feature(_session, _tenant, name):
        assert name == "notifications.basic"
        return allowed
    monkeypatch.setattr(repair, "has_feature", feature)
    return session


@pytest.mark.asyncio
async def test_one_match_enqueues_exactly_one_test_professional_notice(monkeypatch):
    row = appointment()
    db = fixture(monkeypatch, [row])
    state, job_id = await repair.recover_once()
    assert (state,job_id) == ("enqueued", db.notice)
    assert sum("INSERT INTO appointment_notifications" in query for query,_ in db.calls) == 1


@pytest.mark.asyncio
async def test_no_plan_never_reads_customer_or_books_outbound(monkeypatch):
    db = fixture(monkeypatch, [appointment()], allowed=False)
    assert await repair.recover_once() == ("plan_not_eligible", None)
    assert not any("public_booking_receipts" in q or "INSERT INTO appointment_notifications" in q for q,_ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("candidates", [[], [appointment(), appointment()]])
async def test_zero_or_ambiguous_bookings_never_send(monkeypatch,candidates):
    db = fixture(monkeypatch, candidates)
    assert await repair.recover_once() == ("booking_not_unique_or_missing", None)
    assert not any("INSERT INTO appointment_notifications" in q for q,_ in db.calls)


@pytest.mark.asyncio
async def test_any_previous_notice_refuses_second_message(monkeypatch):
    db = fixture(monkeypatch, [appointment()], old_notice=uuid4())
    assert await repair.recover_once() == ("notice_already_exists", None)
    assert not any("INSERT INTO appointment_notifications" in q for q,_ in db.calls)


@pytest.mark.asyncio
async def test_disabled_company_never_receives_recovery(monkeypatch):
    db = fixture(monkeypatch, [appointment()])
    monkeypatch.setattr(repair, "notification_settings",
                        lambda *_args: {"enabled":False, "professional_notice":True})
    assert await repair.recover_once() == ("company_notice_disabled", None)
    assert not any("INSERT INTO appointment_notifications" in q for q,_ in db.calls)
