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
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
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
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
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


@pytest.mark.asyncio
async def test_real_public_api_rejects_early_review_and_duplicate_review(booking_db,monkeypatch):
    import httpx
    from app.main import app
    from app import appointment_access_api as api
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
    monkeypatch.setattr(api,'get_session_factory',lambda:booking_db['factory'])
    d=booking_db;a=await prepare(d)
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            link=await access.issue(db,d['a'],a['id'])
    token=link.split('#')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://orionagenda.pro') as client:
        view=await client.post('/api/appointment-access',json={'token':token})
        assert view.status_code==200 and view.json()['protocol']==a['protocol']
        assert view.headers['referrer-policy']=='no-referrer' and view.headers['cache-control']=='no-store'
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':5})).status_code==409
        assert (await client.post('/api/appointment-access',headers={'Origin':'https://evil.example'},json={'token':token})).status_code==403
        # A synthetic team-recorded completion. Automated completion alone is
        # deliberately insufficient for a review even if status says completed.
        async with d['migrator'].begin() as db:
            await db.execute(text("UPDATE appointments SET status='completed' WHERE tenant_id=:t AND id=:id"),{'t':d['a'],'id':a['id']})
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':5})).status_code==409
        async with d['migrator'].begin() as db:
            await db.execute(text("""INSERT INTO appointment_history(tenant_id,appointment_id,event,operator,details)
                VALUES(:t,:id,'completed','owner',CAST(:details AS jsonb))"""),
                {'t':d['a'],'id':a['id'],'details':'{"v":1,"source":"panel","actor":{"role":"owner"}}'})
        valid=await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':5,'comment':'<script>alert(1)</script>'})
        assert valid.status_code==200 and valid.json()['review']['rating']==5
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':1})).status_code==409
        assert (await client.post('/api/appointment-access',json={'token':token,'action':'review','rating':0})).status_code==422


@pytest.mark.asyncio
async def test_revocation_cannot_be_bypassed_by_requesting_a_new_token(booking_db,monkeypatch):
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED','1')
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_PILOT_ONLY','0')
    d=booking_db;a=await prepare(d)
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE appointments SET customer_access_blocked=true WHERE tenant_id=:t AND id=:id'),{'t':d['a'],'id':a['id']})
    async with d['factory']() as db:
        async with tenant_transaction(db,d['a']):
            with pytest.raises(HTTPException):await access.issue(db,d['a'],a['id'])


@pytest.mark.asyncio
async def test_release_verifier_checks_real_additive_schema(booking_db):
    import asyncio
    import os
    from scripts.backup_and_migrate_appointment_communications import schema_ready
    assert await asyncio.to_thread(schema_ready,os.environ['MIGRATION_DATABASE_URL'])
