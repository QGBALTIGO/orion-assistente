"""Regression coverage of one-use 7364 review with no general consent grants."""
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from uuid import uuid4
from unittest.mock import AsyncMock
import json
import pytest

from app import review_qa_send_once as qa


class Result:
    def __init__(self,value=None):
        self.value=value
    def mappings(self):return self
    def first(self):return self.value
    def scalar_one(self):return self.value
    def scalar_one_or_none(self):return self.value


class Db:
    def __init__(self, prior=None, consent=None, candidate=None):
        self.prior=prior
        self.consent=consent
        self.candidate=candidate
        self.sql=[]
        self.notification=uuid4()
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def execute(self,sql,params=None):
        q=str(sql)
        self.sql.append((q,params))
        if params and "t" in params:assert params["t"]==qa.TENANT
        if params and "phone" in params:assert params["phone"]==qa.PHONE
        if "pg_advisory_xact_lock" in q:return Result(None)
        if "FROM appointments a" in q:
            for required in ("status='completed'","a.end_at<=clock_timestamp()",
                             "appointment_reviews","h.details->>'source'='panel'",
                             "NOT EXISTS(SELECT 1 FROM appointment_notifications"):
                assert required in q
            return Result(self.candidate)
        if "FROM appointment_notifications" in q:return Result(self.prior)
        if "FROM appointment_communication_settings" in q:return Result(None)
        if "FROM contact_consents" in q:return Result(self.consent)
        if "INSERT INTO contact_consents" in q:return Result(None)
        if "SELECT clock_timestamp()" in q:return Result(datetime.now(timezone.utc))
        if "INSERT INTO appointment_notifications" in q:
            assert "clock_timestamp(),clock_timestamp()+interval '60 minutes'" in q
            assert ":now" not in q
            assert "now" not in params
            assert params["key"]==qa.KEY
            assert params["channel"]
            assert params["customer"]==self.candidate["customer_id"]
            details=json.loads(params["details"])
            assert details[qa.MARKER] is True
            assert details["qa_before"]["missing"] is True
            return Result(self.notification)
        raise AssertionError("Unexpected SQL")


@asynccontextmanager
async def scope(db,tenant):
    assert tenant==qa.TENANT
    yield db


def factory(monkeypatch,db):
    monkeypatch.setenv("ORION_REVIEW_QA_SEND_7364","1")
    monkeypatch.setenv("ORION_REVIEW_QA_TEMP_CONSENT","1")
    monkeypatch.setattr(qa,"central_enabled",lambda:True)
    monkeypatch.setattr(qa,"central_channel",lambda:(qa.TENANT,uuid4()))
    monkeypatch.setattr(qa,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(qa,"tenant_transaction",scope)
    async def entitlement(session,tenant,feature):
        assert tenant==qa.TENANT and feature=="notifications.basic"
        return True
    monkeypatch.setattr(qa,"has_feature",entitlement)


@pytest.mark.asyncio
async def test_one_completed_review_no_preexisting_notice_can_enqueue(monkeypatch):
    db=Db(candidate={"id":uuid4(),"customer_id":uuid4(),"updated_at":datetime.now(timezone.utc)})
    factory(monkeypatch,db)
    state,notice,snap=await qa.prepare()
    assert (state,notice)==("enqueued",db.notification)
    assert snap=={"missing":True}
    assert sum("INSERT INTO appointment_notifications" in q for q,_ in db.sql)==1


@pytest.mark.asyncio
async def test_prior_attempt_never_queues_or_resends(monkeypatch):
    existing={"id":uuid4(),"status":"accepted","previous":{"qa_before":{"missing":True}}}
    db=Db(prior=existing)
    factory(monkeypatch,db)
    state,notice,snap=await qa.prepare()
    assert state=="already_attempted_never_resend" and notice is None
    assert snap=={"missing":True}
    assert not any("INSERT INTO" in q for q,_ in db.sql)


@pytest.mark.asyncio
async def test_explicit_customer_optout_is_not_overridden(monkeypatch):
    db=Db(candidate={"id":uuid4(),"customer_id":uuid4(),
                     "updated_at":datetime.now(timezone.utc)},
          consent={"operational_opt_in":False,"marketing_opt_in":False,"source":"customer_request"})
    factory(monkeypatch,db)
    assert await qa.prepare()==("customer_opted_out_no_override",None,None)
    assert not any("INSERT INTO" in q for q,_ in db.sql)


@pytest.mark.asyncio
async def test_no_eligible_booking_never_grants_consent(monkeypatch):
    db=Db()
    factory(monkeypatch,db)
    assert await qa.prepare()==("no_completed_unreviewed_test_booking",None,None)
    assert not any("INSERT INTO" in q for q,_ in db.sql)


@pytest.mark.asyncio
async def test_send_restores_consent_even_if_transport_throws(monkeypatch):
    monkeypatch.setenv("ORION_REVIEW_QA_SEND_7364","1")
    monkeypatch.setenv("ORION_REVIEW_QA_TEMP_CONSENT","1")
    notice=uuid4()
    monkeypatch.setattr(qa,"prepare",AsyncMock(return_value=("enqueued",notice,{"missing":True})))
    monkeypatch.setattr(qa,"process_notification",AsyncMock(side_effect=RuntimeError("synthetic send error")))
    cleanup=AsyncMock(return_value="deleted_new_qa_consent")
    monkeypatch.setattr(qa,"restore_consent",cleanup)
    result=await qa.run_once({})
    assert result["send_result"]=="transport_unknown_never_resend"
    assert cleanup.await_count==1


@pytest.mark.asyncio
async def test_disabled_never_reads_database(monkeypatch):
    monkeypatch.delenv("ORION_REVIEW_QA_SEND_7364",raising=False)
    monkeypatch.delenv("ORION_REVIEW_QA_TEMP_CONSENT",raising=False)
    monkeypatch.setattr(qa,"get_session_factory",
                        lambda: (_ for _ in ()).throw(AssertionError("must not query DB")))
    assert await qa.run_once({})=={"state":"disabled"}
