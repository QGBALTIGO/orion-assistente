from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import inbox_conversations as api, qr_api, qr_legacy


@pytest.fixture
def history(monkeypatch):
    tenant,account=uuid4(),uuid4();channel=qr_legacy.channel_id(tenant);events=[]
    actor=SimpleNamespace(role='member',account_id=account)
    monkeypatch.setenv('INBOX_CONTROL_ENABLED','1');monkeypatch.setenv('QR_AI_TENANT_ID',str(tenant))
    db=SimpleNamespace(info={'management_actor_role':'owner'})
    async def owner(*_):return SimpleNamespace(scalar_one_or_none=lambda:account)
    db.execute=owner
    @asynccontextmanager
    async def session(t,a):
        assert (t,a)==(tenant,actor);events.append('membership');yield db
    async def check(*_):events.append('allowlist')
    monkeypatch.setattr(api,'management_session',session);monkeypatch.setattr(api.control,'check_channel',check)
    async def bridge(path,*,query):
        assert path=='/internal/messages' and query=={'phone':'5567999999901'}
        events.append('local-read')
        return {'items':[{'phone':'5567999999901','direction':'out','body':'<synthetic>','at':10000,'delivery':'pending'}]}
    monkeypatch.setattr(qr_api,'_bridge',bridge)
    return SimpleNamespace(tenant=tenant,channel=channel,actor=actor,db=db,events=events,bridge=bridge)


async def test_history_is_owner_scoped_rechecked_and_does_not_assert_delivery(history):
    h=history;result=await api.preserved_history(h.tenant,h.channel,'+5567999999901',h.actor)
    assert h.events==['membership','allowlist','local-read','membership','allowlist']
    assert result=={'items':[{'direction':'out','body':'<synthetic>','at':10000,'delivery':None}],
        'origin':'preserved_device','read_receipts_sent':False}


async def test_reader_and_another_channel_cannot_query_the_primary_device(history):
    h=history;h.db.info['management_actor_role']='reader'
    with pytest.raises(HTTPException) as error:await api.preserved_history(h.tenant,h.channel,'+5567999999901',h.actor)
    assert error.value.status_code==403 and 'local-read' not in h.events
    with pytest.raises(HTTPException) as error:await api.preserved_history(h.tenant,uuid4(),'+5567999999901',h.actor)
    assert error.value.status_code==404 and 'local-read' not in h.events


async def test_late_revocation_discards_the_local_result(history,monkeypatch):
    h=history
    async def revoked(path,*,query):
        result=await h.bridge(path,query=query);h.db.info['management_actor_role']='reader';return result
    monkeypatch.setattr(qr_api,'_bridge',revoked)
    with pytest.raises(HTTPException) as error:await api.preserved_history(h.tenant,h.channel,'+5567999999901',h.actor)
    assert error.value.status_code==403 and h.events.count('local-read')==1


async def test_foreign_contact_in_a_bridge_response_is_never_returned(history,monkeypatch):
    h=history
    async def wrong(*_,**__):return {'items':[{'phone':'5567999999902','direction':'in','body':'private','at':10000}]}
    monkeypatch.setattr(qr_api,'_bridge',wrong)
    with pytest.raises(HTTPException) as error:await api.preserved_history(h.tenant,h.channel,'+5567999999901',h.actor)
    assert error.value.status_code==503
