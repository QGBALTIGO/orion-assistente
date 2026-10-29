"""Real PostgreSQL: credentials, RLS, revocation, reviews, engine-bound authorization."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.integration.test_booking import booking_db, phone, start_at
from app import appointment_access as access
from app.appointment_access_api import customer_view
from app.booking_service import book_appointment, confirm_hold, cancel_appointment
from app.appointment_lifecycle import customer_actor
from app.database import tenant_transaction

pytestmark = pytest.mark.integration


async def prepare(d):
    async with d['factory']() as db:
        held = await book_appointment(db,d['a'],d['pro'],[d['cut']],phone(920),'Cliente sintético',
            start_at(d['day'],10),'access-'+str(uuid4()))
        return await confirm_hold(db,d['a'],held['id'])


@pytest.mark.asyncio
async def test_link_only_hash_is_stored_and_other_tenant_cannot_read(booking_db,monkeypatch):
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    d=booking_db
    a=await prepare(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            link=await access.issue(db,d['a'],a['id'])
    raw=link.split('#')[1]
    tenant,digest=access.parse_token(raw)
    assert tenant==d['a']
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            identity=await access.verify(db,tenant,digest)
            stored=(await db.execute(text('SELECT token_hash FROM appointment_access WHERE tenant_id=:t'),{'t':d['a']})).scalar_one()
            assert stored==digest and raw not in stored
    async with d['factory']() as db:
        async with tenant_transaction(db,d['b']):
            assert (await db.execute(text('SELECT count(*) FROM appointment_access'))).scalar_one()==0
            with pytest.raises(HTTPException):
                await access.verify(db,d['b'],digest)
    with access.bind(identity):
        async with d['factory']() as db:
            async with tenant_transaction(db,tenant):
                view=await customer_view(db,identity)
                assert view['can_change'] and not view['can_review']
                assert 'customer_phone' not in view
        async with d['factory']() as db:
            await cancel_appointment(db,tenant,a['id'],context=customer_actor(identity.customer_id,'public_site'))
        async with d['factory']() as db:
            async with tenant_transaction(db,tenant):
                assert (await customer_view(db,identity))['status']=='cancelled'
    async with d['factory']() as db:
        async with tenant_transaction(db,tenant):
            await db.execute(text('UPDATE appointment_access SET revoked_at=clock_timestamp() WHERE tenant_id=:t'),{'t':tenant})
    with access.bind(identity):
        async with d['factory']() as db:
            with pytest.raises(HTTPException):
                async with tenant_transaction(db,tenant):
                    pytest.fail('revoked credential entered domain transaction')


@pytest.mark.asyncio
async def test_phone_edit_invalidates_link_and_future_review_is_unavailable(booking_db,monkeypatch):
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    d=booking_db;a=await prepare(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            link=await access.issue(db,d['a'],a['id'])
    _,digest=access.parse_token(link.split('#')[1])
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE customers SET version=version+1,whatsapp_e164=:phone WHERE tenant_id=:t AND id=:id'),
            {'phone':phone(921),'t':d['a'],'id':a['customer_id']})
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            with pytest.raises(HTTPException):
                await access.verify(db,d['a'],digest)
