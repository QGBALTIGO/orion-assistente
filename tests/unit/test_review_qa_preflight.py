"""No-PII preflight for the authorized review evaluation test."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest

from app import review_qa_preflight as pre


class Response:
    def __init__(self, records):
        self.records = records
    def mappings(self):
        return self
    def first(self):
        return self.records[0] if self.records else None
    def all(self):
        return self.records


class FakeSession:
    def __init__(self, rows, consent):
        self.rows, self.consent = rows, consent
        self.reads = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def execute(self, query, params=None):
        q = str(query)
        assert q.strip().upper().startswith("SELECT")
        if params:
            assert params["t"] == pre.TENANT
            if "phone" in params:
                assert params["phone"] == pre.PHONE
        self.reads.append(q)
        if "appointment_communication_settings" in q:
            return Response([])
        if "FROM appointments a JOIN customers c" in q:
            return Response(self.rows)
        if "FROM contact_consents" in q:
            return Response([self.consent] if self.consent else [])
        raise AssertionError("Unanticipated query")


@asynccontextmanager
async def scope(db, tenant):
    assert tenant == pre.TENANT
    yield db


@pytest.mark.asyncio
async def test_preflight_counts_only_completed_unreviewed_by_team(monkeypatch):
    rows=[
        dict(status="completed", completed_by_team=True,
             reviewed=False, review_noticed=False,
             customer_access_blocked=False),
        dict(status="completed", completed_by_team=True,
             reviewed=True, review_noticed=True,
             customer_access_blocked=False),
        dict(status="completed", completed_by_team=False,
             reviewed=False, review_noticed=False,
             customer_access_blocked=False),
        dict(status="cancelled", completed_by_team=False,
             reviewed=False, review_noticed=False,
             customer_access_blocked=False)
    ]
    db=FakeSession(rows,{"operational_opt_in":True,"marketing_opt_in":True})
    monkeypatch.setattr(pre, "central_enabled",lambda:True)
    monkeypatch.setattr(pre, "central_channel",lambda:(pre.TENANT,uuid4()))
    monkeypatch.setattr(pre, "get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(pre, "tenant_transaction",scope)
    async def enabled(db,tenant,feature):
        assert tenant==pre.TENANT and feature=="notifications.basic"
        return True
    monkeypatch.setattr(pre, "has_feature",enabled)
    report=await pre.inspect()
    assert report["matching_bookings"]==4
    assert report["eligible_unreviewed_team_completed"]==1
    assert report["completed_with_prior_review"]==1
    assert report["states"]=={"completed":3,"cancelled":1}
    assert report["review_enabled"] is True
    assert report["review_delay_minutes"]==15
    assert report["consent_marketing"] is True
    assert len(db.reads)==3
    assert "phone" not in report and "customer" not in report


@pytest.mark.asyncio
async def test_non_consent_does_not_imply_approval(monkeypatch):
    db=FakeSession([],None)
    monkeypatch.setattr(pre,"central_enabled",lambda:True)
    monkeypatch.setattr(pre,"central_channel",lambda:(pre.TENANT,uuid4()))
    monkeypatch.setattr(pre,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(pre,"tenant_transaction",scope)
    async def entitlement(db,tenant,feature):
        return False
    monkeypatch.setattr(pre,"has_feature",entitlement)
    result=await pre.inspect()
    assert result["matching_bookings"]==0
    assert result["plan_allows_notifications"] is False
    assert result["consent_marketing"] is False
