from datetime import datetime, timezone
import json

import pytest

from app.communication_events import envelope,event_reply,needs_review,invalidate_proposal,safe_body
from app.messaging import extract_messages
from app.worker import bounded_answer


@pytest.mark.asyncio
async def test_panel_really_serves_the_new_communication_asset():
    import httpx
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/panel-assets/orion-communication.js')
    assert response.status_code==200 and 'window.OrionCommunication=' in response.text
    assert 'javascript' in response.headers['content-type']


def meta(kind='text', body='Oi', **extra):
    item={'from':'5567999999901','id':'synthetic-inbound','type':kind,kind:body if isinstance(body,dict) else {'body':body},**extra}
    return {'object':'whatsapp_business_account','entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'test'},'messages':[item]}}]}]}


def test_meta_preserves_complete_caption_without_claiming_vision():
    for kind in ('image','video','document'):
        event=extract_messages(meta(kind,{'caption':'Quero só saber o valor do corte'}))[0]
        assert event['text']=='Quero só saber o valor do corte'
        assert event['envelope']['kind']==kind
        assert event_reply(event['envelope'],event['text']) is None


def test_meta_never_executes_prefix_without_late_negation():
    value='A'*1499+' NÃO CONFIRMO'
    event=extract_messages(meta(body=value))[0]
    assert event['text']=='' and event['envelope']['oversized'] is True
    assert 'partes menores' in event_reply(event['envelope'])
    assert needs_review(event['envelope'])


def test_meta_rejects_the_entire_large_batch_instead_of_acknowledging_a_prefix():
    payload=meta()
    messages=payload['entry'][0]['changes'][0]['value']['messages']
    original=messages[0]
    messages[:]=[{**original,'id':'synthetic-'+str(index)} for index in range(101)]
    with pytest.raises(ValueError,match='too_many_events'):extract_messages(payload)


def test_oversized_meta_optout_retains_only_a_finite_control_without_executing_text():
    event=extract_messages(meta(body='Texto longo '*160+' Não quero mais promoções'))[0]
    assert not event['text'] and event['envelope']['control']=='marketing'


@pytest.mark.asyncio
async def test_signed_large_batch_rejected_before_any_persistence(monkeypatch):
    import hashlib,hmac,httpx
    from app import whatsapp_api
    from app.main import app
    secret='synthetic-meta-batch-test'
    monkeypatch.setenv('META_APP_SECRET',secret)
    monkeypatch.setattr(whatsapp_api,'support_session_factory',lambda:pytest.fail('A rejected prefix reached the database'))
    payload=meta();messages=payload['entry'][0]['changes'][0]['value']['messages'];original=messages[0]
    messages[:]=[{**original,'id':str(i)} for i in range(101)]
    raw=json.dumps(payload).encode();signature=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/webhooks/whatsapp',content=raw,headers={'X-Hub-Signature-256':'sha256='+signature})
    assert response.status_code==413


@pytest.mark.parametrize('kind',['image','video','document'])
def test_unread_media_caption_cannot_confirm_an_operation(kind):
    assert 'responda por texto' in event_reply(envelope(kind),'pode confirmar')


def test_reply_reference_is_adapter_metadata_not_llm_authority():
    event=extract_messages(meta(body='sim',context={'id':'old-provider-id'}))[0]
    assert event['text']=='sim' and event['envelope']['reply_to']=='old-provider-id'
    assert needs_review(event['envelope'])
    assert 'conferir o pedido' in event_reply(event['envelope'],event['text'])


@pytest.mark.parametrize('kind',['sticker','reaction'])
def test_decorative_events_do_not_trigger_llm_or_domain(kind):
    assert event_reply(envelope(kind))==''


@pytest.mark.parametrize('kind',['document','poll','unknown','audio'])
def test_unsupported_content_has_honest_repair(kind):
    assert 'não consigo analisar' in event_reply(envelope(kind))


def test_unversioned_interactive_id_is_not_confirmation():
    event=extract_messages(meta('interactive',{'button_reply':{'id':'sim'}}))[0]
    assert event_reply(event['envelope'],event['text']) is not None


@pytest.mark.asyncio
async def test_reference_invalidates_consent_without_touching_reservation():
    class Redis:
        state=json.dumps({'step':'confirm','hold':'real-hold-id','services':['s'],
            'summary_message_id':'old','last_message_id':'old','expected_snapshot':'old'})
        async def get(self,key):return self.state
        async def set(self,key,value,**kw):self.state=value
    redis=Redis();await invalidate_proposal(redis,'test')
    saved=json.loads(redis.state)
    assert saved['hold']=='real-hold-id' and saved['services']==['s']
    assert 'summary_message_id' not in saved and 'expected_snapshot' not in saved
    assert saved['confirmation_requires_review'] is True


@pytest.mark.parametrize('body',['a'*1501,'A\x00B','\ud800'])
def test_bad_input_is_rejected_whole(body):
    result,issue=safe_body(body)
    assert result=='' and issue is not None


def test_output_utf16_guard_never_cuts_critical_summary():
    assert bounded_answer('😀'*600)=='😀'*600
    result=bounded_answer('😀'*601+' PROTOCOLO')
    assert 'revisar o resumo completo' in result and 'PROTOCOLO' not in result
