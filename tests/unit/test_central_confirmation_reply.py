"""Protocol replies are operational, not AI: never trust tenant or sender text."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import central_appointment_reply as api
from app import appointment_access as access


class Lookup:
    def __init__(self,row):
        self.row=row
        self.calls=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def execute(self,stmt,params):
        self.calls.append((str(stmt),params))
        return SimpleNamespace(mappings=lambda:SimpleNamespace(first=lambda:self.row))


def enable(monkeypatch):
    monkeypatch.setenv('QR_BRIDGE_TOKEN','central-protocol-test-key-'+'x'*40)
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED','1')
    monkeypatch.setenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED','1')
    monkeypatch.setenv('QR_AI_TENANT_ID',str(uuid4()))
    async def no_network_rate_limit(_request, _phone):
        return None
    monkeypatch.setattr(api, '_limit_protocol_lookup', no_network_rate_limit)


def message(phone='5567998888777'):
    return api.IncomingProtocol(phone=phone,message_id='pn-valid-1',
        text='*Olá, Orion!* 👋\n\n🧾 Protocolo: *AG-3SVM6N*')


def request(authorized=True):
    return SimpleNamespace(headers={'X-Orion-Bridge-Key':
        os.environ['QR_BRIDGE_TOKEN'] if authorized else 'wrong'})


@pytest.mark.asyncio
async def test_only_approved_bridge_can_query_and_reply(monkeypatch):
    enable(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await api.confirm_inbound_protocol(message(),request(False))
    assert exc.value.status_code==401


@pytest.mark.asyncio
async def test_all_numbers_with_matching_booking_are_eligible_without_ai(monkeypatch):
    enable(monkeypatch)
    company,appointment=uuid4(),uuid4()
    lookup=Lookup({'tenant_id':company,'appointment_id':appointment})
    monkeypatch.setattr(api,'support_session_factory',lambda:lambda:lookup)
    calls=[]
    async def reply(tenant,phone,body,channel,message_id,*,central):
        calls.append((tenant,phone,channel,message_id,central))
        return 'Confirmação segura enviada'
    monkeypatch.setattr(api,'confirmation_reply',reply)
    result=await api.confirm_inbound_protocol(message(),request())
    assert result=={'state':'confirmed','answer':'Confirmação segura enviada'}
    assert lookup.calls[0][1]=={'protocol':'AG-3SVM6N','phone':'+5567998888777'}
    assert calls==[(company,'5567998888777','central-admin-qr','pn-valid-1',True)]
    assert access.recipient_allowed('+5567998888777') is False or access.pilot_only() is False


@pytest.mark.asyncio
async def test_mismatched_phone_does_not_reveal_booking(monkeypatch):
    enable(monkeypatch)
    lookup=Lookup(None)
    monkeypatch.setattr(api,'support_session_factory',lambda:lambda:lookup)
    async def unexpected(*args,**kwargs):
        pytest.fail('cannot disclose appointment without phone match')
    monkeypatch.setattr(api,'confirmation_reply',unexpected)
    result=await api.confirm_inbound_protocol(message('5567998888778'),request())
    assert result['state']=='unrecognized'
    assert 'confirmar' in result['answer'].lower()
    assert lookup.calls[0][1]['phone']=='+5567998888778'


@pytest.mark.asyncio
async def test_other_text_never_invokes_ai_or_database(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(api,'support_session_factory',lambda:pytest.fail('not a protocol'))
    result=await api.confirm_inbound_protocol(
        api.IncomingProtocol(phone='5567998888777',message_id='test',
            text='Bom dia! Quanto custa o corte?'),request())
    assert result=={'state':'not_protocol','answer':None}
    monkeypatch.setenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED','0')
    assert (await api.confirm_inbound_protocol(message(),request()))['state']=='disabled'


def test_tenant_and_destination_cannot_be_supplied_by_whatsapp(monkeypatch):
    enable(monkeypatch)
    with pytest.raises(ValidationError):
        api.IncomingProtocol(**message().model_dump(),tenant_id=str(uuid4()))
    with pytest.raises(ValidationError):
        api.IncomingProtocol(**{**message().model_dump(),'phone':'552199999999999999'})
    # Pilot restrictions remain intact for AI/automatic notification routing.
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','556781234567')
    assert access.recipient_allowed('+5567998888777') is False
    assert not access.tenant_enabled(uuid4())


def test_central_token_access_still_requires_global_activation(monkeypatch):
    enable(monkeypatch)
    access.require_tenant(uuid4())
    monkeypatch.setenv('ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED','0')
    with pytest.raises(HTTPException) as error:access.require_tenant(uuid4())
    assert error.value.status_code==404


@pytest.mark.asyncio
async def test_rate_limit_is_enforced_before_cross_tenant_database_lookup(monkeypatch):
    enable(monkeypatch)
    called=[]
    async def limited(_request, phone):
        called.append(phone)
        raise HTTPException(429,'Muitas tentativas')
    monkeypatch.setattr(api,'_limit_protocol_lookup',limited)
    monkeypatch.setattr(api,'support_session_factory',
                        lambda:pytest.fail('rate-limited caller must not access database'))
    with pytest.raises(HTTPException) as exc:
        await api.confirm_inbound_protocol(message('595992820210'),request())
    assert exc.value.status_code==429
    assert called==['595992820210']


@pytest.mark.asyncio
async def test_empty_non_protocol_input_never_consumes_rate_limit(monkeypatch):
    enable(monkeypatch)
    async def unexpected(*args):
        pytest.fail('invalid protocol cannot consume database or limiter')
    monkeypatch.setattr(api,'_limit_protocol_lookup',unexpected)
    monkeypatch.setattr(api,'support_session_factory',unexpected)
    payload=api.IncomingProtocol(phone='595992820210',message_id='M-1',text='olá!')
    assert (await api.confirm_inbound_protocol(payload,request()))['state']=='not_protocol'


@pytest.mark.asyncio
async def test_quotas_have_separate_phone_and_global_keys_and_are_hashed(monkeypatch):
    from app import public_site_api
    captured=[]
    async def fake_limit(_request,slug,category,limit,window,subject):
        captured.append((slug,category,limit,window,subject))
    monkeypatch.setattr(public_site_api,'public_limit',fake_limit)
    await api._limit_protocol_lookup(request(),'595992820210')
    assert captured==[
        ('orion-central-qr','qr-protocol-phone',8,300,'595992820210'),
        ('orion-central-qr','qr-protocol-global',120,60,'all')
    ]
