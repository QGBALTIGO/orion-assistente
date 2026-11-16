"""A receipt/link verifier never issues new messages or leaks the bearer URL."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
from cryptography.fernet import Fernet
from app import review_qa_verify_once as qa


class Response:
    def __init__(self,rows):
        self.rows=rows
    def mappings(self):return self
    def first(self):return self.rows


class Db:
    def __init__(self,notice,app_id):
        self.notice=notice
        self.app_id=app_id
        self.queries=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def execute(self,q,params):
        q=str(q)
        assert q.strip().upper().startswith("SELECT")
        assert params["t"]==qa.TENANT
        self.queries.append(q)
        if "FROM appointment_notifications" in q:return Response(self.notice)
        if "FROM appointments a JOIN customers c" in q:
            assert params["id"]==self.app_id and params["phone"]==qa.PHONE
            return Response(dict(status="completed",reviewed=False,completed_by_staff=True))
        if "FROM contact_consents" in q:
            return Response(dict(operational_opt_in=False,marketing_opt_in=False))
        raise AssertionError("Unsupported read")


@asynccontextmanager
async def scope(db,tenant):
    assert tenant==qa.TENANT
    yield db


@pytest.mark.asyncio
async def test_existing_delivered_review_has_real_link_and_rating_form(monkeypatch):
    key=Fernet.generate_key()
    cipher=Fernet(key)
    app_id=uuid4()
    link='https://orionagenda.pro/a#'+str(qa.TENANT)+'.'+'A'*43
    body='🧪 *TESTE ORION — AVALIAÇÃO*\n\n⭐ *Que tal avaliar sua experiência?*\n'+link
    notice={"id":uuid4(),"status":"accepted",
            "appointment_id":app_id,"body_encrypted":cipher.encrypt(body.encode()).decode()}
    db=Db(notice,app_id)
    monkeypatch.setenv("ORION_REVIEW_QA_VERIFY_7364","1")
    monkeypatch.setattr(qa,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(qa,"tenant_transaction",scope)
    monkeypatch.setattr(qa,"cipher",lambda:cipher)
    parse=AsyncMock()
    monkeypatch.setattr(qa.access,"parse_token",lambda raw:(qa.TENANT,"hash"))
    monkeypatch.setattr(qa.access,"verify",AsyncMock(return_value=SimpleNamespace(appointment_id=app_id)))
    receipt=AsyncMock(return_value="delivered")
    monkeypatch.setattr(qa,"reconcile_notification",receipt)
    result=await qa.inspect_once({})
    assert result=={
        "state":"inspected","receipt":"delivered",
        "contains_review_link":True,"link_token_valid":True,
        "rating_form_available":True,"consent_automatically_revoked":True,
        "published_review_message":True,
    }
    assert receipt.await_count==1
    assert link not in str(result)
    assert len(db.queries)==3


@pytest.mark.asyncio
async def test_no_original_notification_does_not_send(monkeypatch):
    monkeypatch.setenv("ORION_REVIEW_QA_VERIFY_7364","1")
    db=Db(None,uuid4())
    monkeypatch.setattr(qa,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(qa,"tenant_transaction",scope)
    monkeypatch.setattr(qa,"reconcile_notification",AsyncMock(side_effect=AssertionError("must not call")))
    assert await qa.inspect_once({})=={"state":"notice_not_found"}


@pytest.mark.asyncio
async def test_disabled_never_reads_or_sends(monkeypatch):
    monkeypatch.delenv("ORION_REVIEW_QA_VERIFY_7364",raising=False)
    monkeypatch.setattr(qa,"get_session_factory",lambda:(_ for _ in ()).throw(AssertionError()))
    assert await qa.inspect_once({})=={"state":"disabled"}
