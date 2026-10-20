from uuid import uuid4
import pytest
from pydantic import ValidationError
import httpx

from app.inbox_conversations import Message
from app.inbox_runtime import fingerprint,process_inbox_message
from app.messaging import extract_statuses,extract_messages
from app.main import app
from app.catalog_api import Actor,authenticate


@pytest.mark.parametrize('body',['', ' ', '\x00', '\ud800', '😀'*601])
def test_manual_message_rejects_unusable_or_oversize_text(body):
    with pytest.raises(ValidationError):
        Message(contact_e164='+5567999999901',expected_revision=2,operation_id=uuid4(),text=body)


def test_manual_fingerprint_matches_bridge_json_and_preserves_text():
    import hashlib
    assert fingerprint('+5567999999901','Olá\nAna')==hashlib.sha256('["5567999999901","Olá\\nAna"]'.encode()).hexdigest()


def test_signed_meta_payload_parser_retains_timestamp_and_bounds_statuses():
    value={'metadata':{'phone_number_id':'synthetic'},'messages':[{'from':'5567999999901','id':'wamid-synthetic','type':'text','text':{'body':'Olá'},'timestamp':'1792497600'}],
           'statuses':[{'id':'wamid-synthetic','recipient_id':'5567999999901','status':'read'},{'id':'bad','recipient_id':'wrong','status':'read'}]}
    payload={'object':'whatsapp_business_account','entry':[{'changes':[{'value':value}]}]}
    assert extract_messages(payload)[0]['provider_at'].tzinfo is not None
    assert extract_statuses(payload)==[{'phone_number_id':'synthetic','id':'wamid-synthetic','phone':'+5567999999901','status':'read'}]


@pytest.mark.asyncio
async def test_new_central_routes_are_authenticated_and_disabled_without_database(monkeypatch):
    monkeypatch.delenv('INBOX_CONTROL_ENABLED',raising=False)
    monkeypatch.setenv('SUPPORT_API_TOKEN','synthetic-token-'+'x'*40)
    path=f'/api/v1/tenants/{uuid4()}/management/inbox'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(path+'/conversations')).status_code==401
        app.dependency_overrides[authenticate]=lambda:Actor('support')
        try:assert (await client.get(path+'/channels')).status_code==423
        finally:app.dependency_overrides.pop(authenticate,None)
    assert await process_inbox_message({},str(uuid4()),str(uuid4()))=='not_enabled'
