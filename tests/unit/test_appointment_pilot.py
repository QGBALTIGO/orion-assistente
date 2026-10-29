from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import appointment_access as access,qr_api,qr_channels,qr_legacy,inbox_conversations
from app.catalog_api import Actor


def test_pilot_fails_closed_and_restricts_tenant_and_recipient(monkeypatch):
    tenant=uuid4();monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.delenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY',raising=False)
    monkeypatch.delenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',raising=False)
    assert not access.tenant_enabled(tenant) and not access.recipient_allowed('+5567999999901')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(tenant))
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','5567999999901')
    assert access.tenant_enabled(tenant) and not access.tenant_enabled(uuid4())
    assert access.recipient_allowed('+5567999999901') and not access.recipient_allowed('+5567999999902')


@pytest.mark.asyncio
async def test_legacy_manual_send_uses_server_owned_channel_and_durable_adapter(monkeypatch):
    tenant,account,operation=uuid4(),uuid4(),uuid4();calls=[]
    monkeypatch.setenv('INBOX_CONTROL_ENABLED','1');monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant))
    async def owned(t,c,a):calls.append(('owner',t,c,a.account_id))
    async def enqueue(t,c,phone,body,op,a):calls.append(('enqueue',t,c,phone,body,op));return {'managed':True,'accepted':False,'status':'queued'}
    async def forbidden(*a,**k):pytest.fail('Old direct bridge must not be called')
    monkeypatch.setattr(qr_channels,'_owned_channel',owned)
    monkeypatch.setattr(inbox_conversations,'enqueue_preserved_manual',enqueue)
    monkeypatch.setattr(qr_api,'_bridge',forbidden)
    result=await qr_api.qr_send(qr_api.SendPayload(phone='5567999999901',text='Teste',operation_id=operation),Actor('admin',account_id=account))
    assert result['status']=='queued' and not result['accepted']
    assert calls==[('owner',tenant,qr_legacy.channel_id(tenant),account),('enqueue',tenant,qr_legacy.channel_id(tenant),'+5567999999901','Teste',operation)]


@pytest.mark.asyncio
async def test_managed_manual_send_denies_generic_support_and_requires_operation(monkeypatch):
    monkeypatch.setenv('INBOX_CONTROL_ENABLED','1')
    for actor,payload,code in [(Actor('support'),qr_api.SendPayload(phone='5567999999901',text='Teste',operation_id=uuid4()),403),
        (Actor('admin',account_id=uuid4()),qr_api.SendPayload(phone='5567999999901',text='Teste'),422)]:
        with pytest.raises(HTTPException) as error:await qr_api.qr_send(payload,actor)
        assert error.value.status_code==code


def test_old_self_pn_needs_preserved_session_hmac_and_never_invents_ninth_digit(monkeypatch):
    import hmac,hashlib
    tenant=uuid4();number='556781234567';key='synthetic-preserved-key-'+'x'*32
    monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant));monkeypatch.setenv('QR_BRIDGE_TOKEN',key)
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE',number)
    row={'tenant_id':tenant,'id':qr_legacy.channel_id(tenant),'request_fingerprint':hmac.new(key.encode(),('orion:preserved-primary-session:v1\0'+number).encode(),hashlib.sha256).hexdigest()}
    assert qr_legacy.verified_self_phone(row)==number
    with pytest.raises(ValueError):qr_channels.normalize_sender(number)
    assert qr_legacy.verified_self_phone({**row,'request_fingerprint':'a'*64}) is None
    assert qr_legacy.verified_self_phone({**row,'tenant_id':uuid4()}) is None
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','5567981234567')
    assert qr_legacy.verified_self_phone(row) is None


def test_worker_registration_uses_the_exact_persistent_queue_function_name():
    from app.worker import WorkerSettings
    from arq import func
    names=[func(f).name for f in WorkerSettings.functions]
    assert names.count('process_appointment_notification')==1


def test_old_self_notice_requires_current_hmac_and_preserved_identity(monkeypatch):
    import hmac,hashlib
    from dataclasses import replace
    from app import automation_runtime
    tenant=uuid4();phone='556781234567';key='synthetic-session-key-'+'x'*40
    monkeypatch.setenv('QR_CHANNELS_ENABLED','1');monkeypatch.setenv('QR_LEGACY_CENTRAL_ENABLED','1')
    monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant));monkeypatch.setenv('QR_BRIDGE_TOKEN',key)
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE',phone)
    proof=hmac.new(key.encode(),('orion:preserved-primary-session:v1\0'+phone).encode(),hashlib.sha256).hexdigest()
    identity=qr_channels.ChannelIdentity(qr_legacy.channel_id(tenant),tenant,uuid4(),'baileys',2,uuid4(),True,
        False,True,True,phone,proof)
    values=automation_runtime._notice_identity(tenant,'+'+phone,identity)
    assert values['sender']==phone and values['session_proof']==proof
    for candidate in (replace(identity,verified_self_phone=None),replace(identity,preserved_session_fingerprint='a'*64),
        replace(identity,legacy_transport=False),replace(identity,central_adopted=False),replace(identity,channel_id=uuid4()),
        replace(identity,allowed_sender=False)):
        with pytest.raises(HTTPException):automation_runtime._notice_identity(tenant,phone,candidate)
    with pytest.raises(HTTPException):automation_runtime._notice_identity(tenant,'556781234568',identity)
    monkeypatch.setenv('QR_BRIDGE_TOKEN','rotated-synthetic-key-'+'y'*40)
    with pytest.raises(HTTPException):automation_runtime._notice_identity(tenant,phone,identity)
