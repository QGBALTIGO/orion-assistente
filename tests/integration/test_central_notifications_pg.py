"""Prove one platform channel can send a company appointment without pilot access.

Isolated CI PostgreSQL only; no WhatsApp traffic and no production numbers.
"""
import hashlib
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.test_booking import phone
from tests.integration.test_appointment_notifications_pg import comm_db, booking, PRO_PHONE
from app import appointment_notifications as notifications

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_central_sender_authorizes_only_exact_committed_professional_notice(comm_db,monkeypatch):
    d=comm_db
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID',str(d['b']))
    monkeypatch.setenv('QR_AI_TENANT_ID',str(d['a']))
    monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED','1')
    monkeypatch.setenv('ORION_CENTRAL_NOTIFICATIONS_ENABLED','1')
    # This fixture owns a synthetic managed channel, not an admin legacy QR.
    # Inject only its fixed id; authorization still checks the real SQL rows.
    monkeypatch.setattr(notifications,'central_channel',lambda:(d['a'],d['channel']))
    assert not notifications.access.tenant_enabled(d['a'])
    await booking(d)
    async with d['migrator'].connect() as db:
        items=(await db.execute(text('SELECT * FROM appointment_notifications WHERE tenant_id=:t ORDER BY event'),{'t':d['a']})).mappings().all()
    assert {x['event'] for x in items}=={'professional_new','reminder'}
    staff=next(x for x in items if x['event']=='professional_new')
    assert staff['channel_id'] is None and staff['central_channel_id']==d['channel']
    assert staff['recipient_id']==d['pro'] and staff['recipient_id']!=d['pro2']

    support=create_async_engine(os.environ['SUPPORT_DATABASE_URL'])
    observed=[]
    async def sender(target_tenant,channel,number,body,operation):
        assert target_tenant==d['a'] and channel==d['channel']
        assert number==PRO_PHONE
        async def permitted(recipient=number,body_digest=None,tenant=None):
            async with support.connect() as db:
                return (await db.execute(text("""
                    SELECT public.orion_authorize_central_appointment_notification(
                        :operation,:admin,:channel,:phone,:recipient,:body)
                """),{'operation':staff['id'],'admin':tenant or d['a'],'channel':channel,
                       'phone':recipient.lstrip('+'),
                       'recipient':hashlib.sha256(recipient.encode()).hexdigest(),
                       'body':body_digest or hashlib.sha256(body.encode()).hexdigest()})).scalar_one()
        assert await permitted() is True
        assert await permitted(recipient=phone(999)) is False
        assert await permitted(body_digest='0'*64) is False
        assert await permitted(tenant=d['b']) is False
        observed.append(operation)
        return {'accepted':True,'state':'accepted','operation_id':operation}
    try:
        assert await notifications.process_notification({'appointment_sender':sender},
            str(d['a']),str(staff['id']))=='accepted'
        assert await notifications.process_notification({'appointment_sender':sender},
            str(d['a']),str(staff['id']))=='accepted'
        assert observed==[str(staff['id'])]
    finally:
        await support.dispose()


def test_central_default_never_uses_company_channel_and_preserves_explicit_opt_out():
    from uuid import uuid4
    admin_channel=uuid4()
    default=notifications.notification_settings(None,True,admin_channel)
    assert default['enabled'] and default['professional_notice']
    assert default['channel_id']==admin_channel and default['review'] is True
    own=notifications.notification_settings({'enabled':False,'channel_id':uuid4(),'version':2},True,admin_channel)
    assert own['enabled'] is False and own['channel_id']==admin_channel and own['version']==2
