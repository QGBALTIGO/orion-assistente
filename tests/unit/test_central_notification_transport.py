"""Private admin sender admits only fully verified appointment outbox attempts."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import central_appointment_transport as transport
from app.inbox_control import SendSlot, FinishSlot
from app.qr_legacy import channel_id


class Scalar:
    def __init__(self,value):self.value=value
    def scalar_one(self):return self.value


class Db:
    def __init__(self,allowed):self.allowed=allowed;self.statements=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def execute(self,query,values):
        self.statements.append((str(query),values))
        return Scalar(self.allowed)


def slot(phone='5567999999922',text='📩 *NOVO AGENDAMENTO*'):
    body=json.dumps([phone,text],ensure_ascii=False,separators=(',',':'))
    return SendSlot(phone=phone,text=text,operation_id=uuid4(),
                    fingerprint=hashlib.sha256(body.encode()).hexdigest(),
                    source='appointment')


@pytest.mark.asyncio
async def test_admin_broker_verifies_exact_operation_and_body(monkeypatch):
    tenant=uuid4()
    channel=channel_id(tenant)
    monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant))
    for flag in ('APPOINTMENT_COMMUNICATIONS_ENABLED',
                 'ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED',
                 'ORION_CENTRAL_NOTIFICATIONS_ENABLED'):
        monkeypatch.setenv(flag,'1')
    db=Db(True)
    monkeypatch.setattr(transport,'support_session_factory',lambda:lambda:db)
    async def identity(_):
        return SimpleNamespace(tenant_id=tenant,channel_id=channel,
                               legacy_transport=True,central_adopted=True)
    monkeypatch.setattr(transport,'authenticated_channel',identity)
    payload=slot()
    approved=await transport.authorize(channel,payload,object())
    assert approved=={'authorized':True,'operation_id':payload.operation_id}
    assert len(db.statements)==1
    assert db.statements[0][1]['recipient']==hashlib.sha256(('+'+payload.phone).encode()).hexdigest()
    assert db.statements[0][1]['body']==hashlib.sha256(payload.text.encode()).hexdigest()
    completed=FinishSlot(**payload.model_dump(),accepted=True,provider_id='test-provider-id')
    assert (await transport.finish(channel,completed,object()))['released'] is True
    assert len(db.statements)==2


@pytest.mark.asyncio
async def test_admin_broker_denies_unapproved_foreign_or_spoofed_sends(monkeypatch):
    tenant=uuid4();channel=channel_id(tenant)
    monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant))
    for flag in ('APPOINTMENT_COMMUNICATIONS_ENABLED',
                 'ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED',
                 'ORION_CENTRAL_NOTIFICATIONS_ENABLED'):
        monkeypatch.setenv(flag,'1')
    db=Db(False)
    monkeypatch.setattr(transport,'support_session_factory',lambda:lambda:db)
    async def identity(_):
        return SimpleNamespace(tenant_id=tenant,channel_id=channel,
                               legacy_transport=True,central_adopted=True)
    monkeypatch.setattr(transport,'authenticated_channel',identity)
    with pytest.raises(HTTPException) as blocked:
        await transport.authorize(channel,slot(),object())
    assert blocked.value.status_code==403

    db.allowed=True
    forged=slot().model_copy(update={'fingerprint':'0'*64})
    with pytest.raises(HTTPException):
        await transport.authorize(channel,forged,object())
    assert len(db.statements)==1,'Wrong message never looks up cross-tenant operation'
    async def other(_):
        return SimpleNamespace(tenant_id=uuid4(),channel_id=channel,
                               legacy_transport=True,central_adopted=True)
    monkeypatch.setattr(transport,'authenticated_channel',other)
    with pytest.raises(HTTPException):
        await transport.authorize(channel,slot(),object())
    assert len(db.statements)==1
    monkeypatch.setenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED','0')
    with pytest.raises(HTTPException):
        await transport.authorize(channel,slot(),object())
    assert len(db.statements)==1
