"""Real PostgreSQL: global aliases, OTP proofs, tenant and staff boundaries.

No real WhatsApp sends. The managed transport is replaced by a receipt fake.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.integration.test_appointment_notifications_pg import comm_db, booking, phone, PRO_PHONE
from app import appointment_short_auth as auth
from app import appointment_short_links as links
from app import qr_channels, automation_transport
from app.database import tenant_transaction

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_aliases_are_unique_global_stable_and_tenant_fenced(comm_db,monkeypatch):
    d=comm_db
    monkeypatch.setenv('PANEL_PUBLIC_URL','https://orionagenda.pro')
    a=await booking(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            code=await links.get_or_create(db,d['a'],a['id'],a['protocol'])
            assert code==links.natural_code(a['protocol'])
            assert await links.get_or_create(db,d['a'],a['id'],a['protocol'])==code
    assert links.customer_url(code).endswith('/'+code)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['b']):
            assert not await links.belongs(db,d['b'],a['id'],code)
            assert (await db.execute(text('SELECT count(*) FROM appointment_short_codes WHERE code=:code'),
                {'code':code})).scalar_one()==0
    async with d['factory']() as db:
        held=await booking(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            fallback=await links.get_or_create(db,d['a'],held['id'],a['protocol'])
            assert fallback!=code
            assert len(fallback)==12


@pytest.mark.asyncio
async def test_otp_is_encrypted_durable_one_use_and_staff_cannot_cross_assignment(comm_db,monkeypatch):
    d=comm_db
    monkeypatch.setenv('APPOINTMENT_SHORT_LINKS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
    monkeypatch.setenv('PANEL_PUBLIC_URL','https://orionagenda.pro')
    monkeypatch.setattr(auth,'get_session_factory',lambda:d['factory'])
    async def canonical(db,tenant):
        assert tenant==d['a']
        return ({'id':d['channel'],'allowed_senders':[phone(920)[1:],PRO_PHONE[1:]]},'ready')
    monkeypatch.setattr(qr_channels,'_canonical',canonical)
    monkeypatch.setattr(__import__('secrets'),'randbelow',lambda top:123456)
    async def fake_transport(tenant,channel,operation_id,*,message):
        assert tenant==d['a'] and channel==d['channel']
        assert message['text'].count('*123456*')==1
        assert message['to'] in (phone(920)[1:],PRO_PHONE[1:])
        return {'operation_id':str(operation_id),'accepted':True,'state':'accepted'}
    monkeypatch.setattr(automation_transport,'_request',fake_transport)
    a=await booking(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            code=await links.get_or_create(db,d['a'],a['id'],a['protocol'])
    assert await auth.request_otp(d['a'],a['id'],'customer')==links.GENERIC_REQUEST
    async with d['migrator'].connect() as db:
        challenge=(await db.execute(text("""
            SELECT * FROM appointment_short_challenges WHERE tenant_id=:t
              AND appointment_id=:a AND recipient_kind='customer'
        """),{'t':d['a'],'a':a['id']})).mappings().one()
    assert challenge['status']=='pending'
    assert '123456' not in challenge['otp_hash'] and '123456' not in challenge['body_encrypted']
    assert await auth.process_short_otp({},str(d['a']),str(challenge['id']))=='accepted'
    assert await auth.process_short_otp({},str(d['a']),str(challenge['id']))=='already_processed'
    with pytest.raises(HTTPException):
        await auth.redeem_otp(d['a'],a['id'],'customer','000000')
    cookie=await auth.redeem_otp(d['a'],a['id'],'customer','123456')
    assert links.CUSTOMER_COOKIE.startswith('__Host-')
    assert len(cookie)>60 and '/a#' not in cookie
    with pytest.raises(HTTPException):
        await auth.redeem_otp(d['a'],a['id'],'customer','123456')
    # A professional receives an independent proof, not the customer's cookie.
    assert await auth.request_otp(d['a'],a['id'],'professional')==links.GENERIC_REQUEST
    async with d['migrator'].connect() as db:
        pro_challenge=(await db.execute(text("""
            SELECT id FROM appointment_short_challenges WHERE tenant_id=:t
              AND appointment_id=:a AND recipient_kind='professional'
        """),{'t':d['a'],'a':a['id']})).scalar_one()
    assert await auth.process_short_otp({},str(d['a']),str(pro_challenge))=='accepted'
    staff_cookie=await auth.redeem_otp(d['a'],a['id'],'professional','123456')
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            assert await auth.verify_staff(db,d['a'],a['id'],staff_cookie)==d['pro']
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE professionals SET phone_e164=:phone WHERE tenant_id=:t AND id=:pro'),
            {'t':d['a'],'pro':d['pro'],'phone':phone(923)})
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            with pytest.raises(HTTPException):
                await auth.verify_staff(db,d['a'],a['id'],staff_cookie)
