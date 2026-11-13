"""Every business controls its own notification settings; sender stays admin-only."""
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import appointment_communications_api as api
from app.catalog_api import Actor


class Result:
    def __init__(self,value): self.value=value
    def scalar_one_or_none(self):return self.value


class Db:
    def __init__(self):self.actions=[]
    async def execute(self,query,params):
        sql=str(query)
        self.actions.append((sql,params))
        if 'SELECT version FROM appointment_communication_settings' in sql:
            return Result(None)
        return Result(None)


@pytest.mark.asyncio
async def test_non_admin_company_can_save_preferences_but_never_select_sender(monkeypatch):
    admin,company,hub,account=uuid4(),uuid4(),uuid4(),uuid4()
    for key in ('APPOINTMENT_COMMUNICATIONS_ENABLED','ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED',
                'ORION_CENTRAL_NOTIFICATIONS_ENABLED'):
        monkeypatch.setenv(key,'1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('QR_AI_TENANT_ID',str(admin))
    async def access(*_args):return None
    monkeypatch.setattr(api,'resolve_tenant_access',access)
    actor=Actor('owner',account_id=account)
    fake=Db()
    @asynccontextmanager
    async def management(*_args,**_kwargs):
        yield fake
    monkeypatch.setattr(api,'management_session',management)
    class Registry:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(api,'support_session_factory',lambda:Registry)
    from app import qr_channels
    async def canonical(_,tenant):
        assert tenant==admin,'Never let a source company select its own WhatsApp'
        return ({'id':hub,'enabled':True,'allowed_senders':[]},'ready')
    monkeypatch.setattr(qr_channels,'_canonical',canonical)
    from app import appointment_notifications as notifications
    monkeypatch.setattr(notifications,'central_channel',lambda:(admin,hub))
    async def audit(*_args):return None
    monkeypatch.setattr(api,'audit',audit)

    malicious=api.Settings(expected_version=0,enabled=True,channel_id=uuid4(),acknowledged=True)
    with pytest.raises(HTTPException) as denied:
        await api.save_configuration(company,malicious,actor)
    assert denied.value.status_code==422 and fake.actions==[]

    approved=api.Settings(expected_version=0,enabled=True,channel_id=hub,
                          professional_notice=True,reminder=True,acknowledged=True)
    assert (await api.save_configuration(company,approved,actor))=={'saved':True,'version':1}
    row=next(params for sql,params in fake.actions if
             'INSERT INTO appointment_communication_settings' in sql)
    assert row['tenant']==company and row['channel_id'] is None
    assert row['enabled'] is True and row['professional_notice'] is True
