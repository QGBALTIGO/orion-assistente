"""Public publication, catalogue isolation and real package booking on PostgreSQL."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from redis.asyncio import Redis

from app.catalog_api import Actor, authenticate, router as catalog_router, support_session_factory
from app.database import get_engine
from app.management_api import router as management_router
from app.public_site_api import router as public_router
from app.public_site_api import public_limit
from app.service_packages_api import router as packages_router

pytestmark = pytest.mark.integration


class TestQuotaCache:
    """Deterministic Redis boundary; PostgreSQL and booking engine stay real."""
    __test__ = False
    def __init__(self): self.counts = {}
    async def ping(self): return True
    async def eval(self, script, count, key, window):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]
    async def aclose(self): pass


def async_url(value): return value.replace('postgresql+psycopg://', 'postgresql+asyncpg://', 1)


@pytest_asyncio.fixture
async def public_db(monkeypatch):
    if not all(os.getenv(k) for k in ('APP_DATABASE_URL','MIGRATION_DATABASE_URL','SUPPORT_DATABASE_URL')):
        if os.getenv('REQUIRE_POSTGRES_TESTS') == '1': pytest.fail('Public catalogue requires PostgreSQL roles and migrations')
        pytest.skip('Requires local PostgreSQL')
    monkeypatch.setenv('SUPPORT_API_TOKEN', 'public-test-support-signing-key-at-least-32-characters')
    monkeypatch.setenv('BUSINESS_PROFILES_ENABLED', '1')
    monkeypatch.delenv('PUBLIC_SITE_SIGNING_KEY', raising=False)
    cache = TestQuotaCache()
    monkeypatch.setattr('app.public_site_api._redis', lambda: cache)
    app = FastAPI()
    # Static management paths must precede the generic resource/item catalogue.
    for router in (management_router, packages_router, public_router, catalog_router): app.include_router(router)
    ids = {k:uuid4() for k in ('a','b','pro','pro2','other_pro','cut','beard','wash','foreign','owner','editor','viewer','private_customer')}
    async def actor(request:Request):
        role=request.headers.get('x-test-role','owner')
        if role=='support': return Actor('support')
        return Actor('member',tenant_id=ids['a'],account_id=ids[role],membership_role=role)
    app.dependency_overrides[authenticate]=actor
    get_engine.cache_clear();support_session_factory.cache_clear()
    migrator=create_async_engine(async_url(os.environ['MIGRATION_DATABASE_URL']))
    runtime=create_async_engine(async_url(os.environ['APP_DATABASE_URL']))
    support=create_async_engine(async_url(os.environ['SUPPORT_DATABASE_URL']))
    day=(datetime.now(ZoneInfo('America/Sao_Paulo'))+timedelta(days=7)).date()
    slug='site-'+ids['a'].hex[:20]
    async with migrator.begin() as db:
        for tenant in (ids['a'],ids['b']):
            await db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:id,'Nome interno privado',:slug,'America/Sao_Paulo')"),{'id':tenant,'slug':'tenant-'+tenant.hex})
        for key in ('owner','editor','viewer'):
            await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES(:id,'Conta privada',:email,'test-only-not-a-hash')"),{'id':ids[key],'email':ids[key].hex+'@example.invalid'})
            await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:tenant,:id,:role)"),{'tenant':ids['a'],'id':ids[key],'role':key})
        for tenant,pro in ((ids['a'],ids['pro']),(ids['a'],ids['pro2']),(ids['b'],ids['other_pro'])):
            await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Profissional sintético')"),{'id':pro,'tenant':tenant})
            await db.execute(text("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(:tenant,:pro,:weekday,'08:00','18:00')"),{'tenant':tenant,'pro':pro,'weekday':day.weekday()})
        for key,name,minutes,price in (('cut','Corte',30,3000),('beard','Barba',20,2000),('wash','Lavagem',10,1000),('foreign','Outro estabelecimento',30,9000)):
            tenant=ids['b'] if key=='foreign' else ids['a']
            await db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:tenant,:name,:minutes,:price)"),{'id':ids[key],'tenant':tenant,'name':name,'minutes':minutes,'price':price})
            people=[ids['other_pro']] if key=='foreign' else [ids['pro']]+([ids['pro2']] if key!='beard' else [])
            for pro in people:
                await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:tenant,:pro,:service)"),{'tenant':tenant,'pro':pro,'service':ids[key]})
        await db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164,notes) VALUES(:id,:tenant,'Nome de cliente privado','+5511999990000','Nota clínica privada de teste')"),{'id':ids['private_customer'],'tenant':ids['a']})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            yield {**ids,'client':client,'app':app,'migrator':migrator,'runtime':runtime,'support':support,'cache':cache,'day':day,'slug':slug,
                   'base':f"/api/v1/tenants/{ids['a']}/management",'public':f'/api/v1/public/sites/{slug}'}
    finally:
        async with migrator.begin() as db:
            for table in ('public_site_publications','public_site_slugs','public_booking_receipts','public_site_daily_metrics','loyalty_redemptions','loyalty_appointment_awards','loyalty_transactions','loyalty_coupons','commission_advance_allocations','commission_settlements','financial_entries',
                          'calendar_outbox','appointment_history','appointment_items','appointments','customers',
                          'service_package_items','service_addons','service_packages','management_site_preferences','management_preferences','working_breaks','working_hours','schedule_blocks','time_off','professional_services','services','professionals','usage_counters','tenant_audit','tenant_memberships'):
                if (await db.execute(text('SELECT to_regclass(:table)'),{'table':table})).scalar_one():
                    await db.execute(text(f'DELETE FROM {table} WHERE tenant_id IN (:a,:b)'),{'a':ids['a'],'b':ids['b']})
            await db.execute(text('DELETE FROM tenants WHERE id IN (:a,:b)'),{'a':ids['a'],'b':ids['b']})
            await db.execute(text('DELETE FROM panel_accounts WHERE id=ANY(CAST(:ids AS uuid[]))'),{'ids':[ids[x] for x in ('owner','editor','viewer')]})
        await migrator.dispose();await runtime.dispose();await support.dispose()
        await get_engine().dispose();get_engine.cache_clear();support_session_factory.cache_clear()


async def save_site(d,**overrides):
    current=(await d['client'].get(d['base']+'/site')).json()
    payload={'expected_version':current.get('version',0),'title':'Barbearia sintética pública','slug':d['slug'],'theme_color':'#123456',
             'description':'Descrição pública aprovada','address':'Endereço sintético público','contact_phone':'11999990000',**overrides}
    result=await d['client'].put(d['base']+'/site',json=payload)
    assert result.status_code==200,result.text
    return result.json()


async def publish(d,**overrides):
    current=(await d['client'].get(d['base']+'/site')).json()
    if not current.get('version'): current=await save_site(d)
    result=await d['client'].post(d['base']+'/site/publication',json={'expected_version':current['version'],'enabled':True,**overrides})
    assert result.status_code==200,result.text
    return result.json()


async def combo(d,**overrides):
    payload={'name':'Corte + barba','service_ids':[str(d['cut']),str(d['beard'])],'price_cents':4200,'idempotency_key':'combo-test-'+uuid4().hex,**overrides}
    result=await d['client'].post(d['base']+'/service-packages',json=payload)
    assert result.status_code==201,result.text
    return result.json(),payload


async def availability(d,service=None,addons=(),pro=None):
    params=[('service_id',str(service or d['cut'])),('professional_id',str(pro or d['pro'])),('day',str(d['day']))]+[('addon_ids',str(x)) for x in addons]
    result=await d['client'].get(d['public']+'/availability',params=params)
    assert result.status_code==200,result.text
    assert result.json()['slots'],result.text
    return result.json()


def book_body(available,phone='+5511987654321'):
    return {'quote':available['quote'],'start_at':available['slots'][0],'customer_name':'Cliente público sintético','customer_phone':phone,'confirmed':True}


@pytest.mark.asyncio
async def test_package_contract_sync_and_professional_intersection(public_db):
    d=public_db;c=d['client'];base=d['base']
    item,payload=await combo(d)
    assert item['duration_min']==50 and item['price_cents']==4200 and item['original_price_cents']==5000 and item['available']
    replay=await c.post(base+'/service-packages',json=payload)
    assert replay.json()['id']==item['id']
    assert (await c.post(base+'/service-packages',json={**payload,'price_cents':4100})).status_code==409
    assert (await c.post(base+'/service-packages',json={**payload,'idempotency_key':uuid4().hex,'service_ids':[str(d['cut']),str(d['foreign'])]})).status_code==422
    assert (await c.post(base+'/service-packages',json={**payload,'idempotency_key':uuid4().hex,'price_cents':5001})).status_code==422
    async with d['migrator'].begin() as db:
        pro_ids=(await db.execute(text('SELECT professional_id FROM professional_services WHERE service_id=:id'),{'id':UUID(item['synthetic_service_id'])})).scalars().all()
        assert pro_ids==[d['pro']]
        await db.execute(text('UPDATE services SET duration_min=45 WHERE id=:id'),{'id':d['cut']})
    current=(await c.get(base+'/service-packages')).json()['items'][0]
    assert current['duration_min']==65
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE services SET price_cents=100 WHERE id=:id'),{'id':d['beard']})
    assert (await c.get(base+'/service-packages')).json()['items'][0]['available'] is False
    save={k:v for k,v in payload.items() if k!='idempotency_key'}
    changed=await c.patch(base+'/service-packages/'+item['id'],json={**save,'price_cents':3000,'expected_version':1})
    assert changed.status_code==200 and changed.json()['available'],changed.text
    assert (await c.patch(base+'/service-packages/'+item['id'],json={**save,'expected_version':1})).status_code==409
    # Moving an existing mapping must invalidate packages that relied on its OLD service_id.
    async with d['migrator'].begin() as db:
        await db.execute(text('DELETE FROM professional_services WHERE professional_id=:pro AND service_id=:service'),{'pro':d['pro'],'service':d['wash']})
        await db.execute(text('UPDATE professional_services SET service_id=:new WHERE professional_id=:pro AND service_id=:old'),{'new':d['wash'],'pro':d['pro'],'old':d['beard']})
        assert not (await db.execute(text('SELECT 1 FROM professional_services WHERE service_id=:id'),{'id':UUID(item['synthetic_service_id'])})).all()


@pytest.mark.asyncio
async def test_addons_time_variants_private_and_source_changes(public_db):
    d=public_db;c=d['client'];path=d['base']+'/services/'+str(d['cut'])+'/addons'
    result=await c.put(path,json={'expected_version':0,'items':[{'addon_service_id':str(d['wash']),'add_duration':False},{'addon_service_id':str(d['beard']),'add_duration':True}]})
    assert result.status_code==200 and result.json()['version']==1,result.text
    assert (await c.put(path,json={'expected_version':0,'items':[]})).status_code==409
    body={'service_id':str(d['cut']),'professional_id':str(d['pro']),'addon_ids':[str(d['wash'])]}
    selection=await c.post(d['base']+'/booking-selection',json=body)
    assert selection.status_code==200,selection.text
    value=selection.json();assert value['price_cents']==4000 and value['duration_min']==30
    repeated=await c.post(d['base']+'/booking-selection',json=body)
    assert repeated.json()['engine_service_id']==value['engine_service_id']
    normal=(await c.get(f"/api/v1/tenants/{d['a']}/services")).json()
    assert value['engine_service_id'] not in str(normal)
    await publish(d)
    assert value['engine_service_id'] not in (await c.get(d['public'])).text
    full=await availability(d,addons=[d['wash'],d['beard']])
    assert full['price_cents']==6000 and full['duration_min']==50
    bad=await c.post(d['base']+'/booking-selection',json={**body,'addon_ids':[str(d['foreign'])]})
    assert bad.status_code==422
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE services SET price_cents=1500 WHERE id=:id'),{'id':d['wash']})
        row=(await db.execute(text('SELECT price_cents,duration_min FROM services WHERE id=:id'),{'id':UUID(value['engine_service_id'])})).one()
        assert tuple(row)==(4500,30)
    changed=await c.put(path,json={'expected_version':1,'items':[{'addon_service_id':str(d['wash']),'add_duration':True}]})
    assert changed.status_code==200
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT active FROM services WHERE id=:id'),{'id':UUID(value['engine_service_id'])})).scalar_one() is False


@pytest.mark.asyncio
async def test_owner_snapshot_publication_draft_isolation_and_no_private_fields(public_db):
    d=public_db;c=d['client'];base=d['base']
    assert (await c.get(d['public'])).status_code==404
    draft=await save_site(d)
    for role in ('editor','viewer','support'):
        response=await c.post(base+'/site/publication',json={'expected_version':draft['version'],'enabled':True},headers={'x-test-role':role})
        assert response.status_code==403,(role,response.text)
    published=await publish(d)
    assert published['public_url']=='/s/'+d['slug']
    public=(await c.get(d['public'])).json()
    raw=str(public)
    for secret in ('tenant_id','customer_id','account_id','email','notes','private_customer','Nome interno privado','Nome de cliente privado','Nota clínica privada',str(d['a']),str(d['owner'])): assert secret not in raw
    assert public['site']['title']=='Barbearia sintética pública'
    new_slug=d['slug']+'-draft'
    response=await c.put(base+'/site',json={'expected_version':published['version'],'title':'Rascunho ainda privado','slug':new_slug,'theme_color':'#abcdef'},headers={'x-test-role':'editor'})
    assert response.status_code==200,response.text
    assert response.json()['has_unpublished_changes']
    assert (await c.get(d['public'])).json()['site']['title']=='Barbearia sintética pública'
    assert (await c.get(base+'/site/preview')).json()['site']['title']=='Rascunho ainda privado'
    assert (await c.get('/api/v1/public/sites/'+new_slug)).status_code==404
    renewed=await publish(d)
    assert renewed['public_url']=='/s/'+new_slug
    # Previously shared addresses remain this company's aliases after a rename.
    assert (await c.get(d['public'])).json()['site']['title']=='Rascunho ainda privado'
    assert (await c.get('/api/v1/public/sites/'+new_slug)).json()['site']['title']=='Rascunho ainda privado'
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE tenant_memberships SET active=false WHERE account_id=:id'),{'id':d['owner']})
    denied=await c.post(base+'/site/publication',json={'expected_version':renewed['version'],'enabled':False})
    assert denied.status_code==403


@pytest.mark.asyncio
async def test_public_booking_real_snapshot_consent_replay_conflict_and_metrics(public_db):
    d=public_db;c=d['client'];await publish(d)
    page=await c.get('/s/'+d['slug']);assert page.status_code==200 and 'no-store' in page.headers['cache-control']
    assert (await c.get(f"/app/site-preview/{d['a']}")).headers['x-robots-tag']=='noindex, nofollow'
    quoted=await availability(d);payload=book_body(quoted)
    assert (await c.post(d['public']+'/appointments',json={**payload,'confirmed':False})).status_code==422
    assert (await c.post(d['public']+'/appointments',json=payload,headers={'Origin':'https://foreign.invalid'})).status_code==403
    result=await asyncio.wait_for(c.post(d['public']+'/appointments',json=payload),20)
    assert result.status_code==201,result.text
    item=result.json();assert item['status']=='confirmed' and item['price_cents']==3000
    assert set(item)=={'status','protocol','start_at','end_at','price_cents','price_mode','timezone','professional','services'}
    assert item['price_mode']=='fixed'
    replay=await c.post(d['public']+'/appointments',json=payload)
    assert replay.status_code==201 and replay.json()['protocol']==item['protocol'],replay.text
    # Same quote binds idempotency to the contact and selected start.
    conflict=await c.post(d['public']+'/appointments',json={**payload,'customer_phone':'+5511976543210'})
    assert conflict.status_code==409,conflict.text
    after=await availability(d)
    assert payload['start_at'] not in after['slots']
    metric=(await c.get(d['base']+'/site/metrics')).json()
    assert metric['visits']==1 and metric['clients_booked']==1 and metric['site_bookings']==1 and metric['conversion_percent']==100
    async with d['migrator'].connect() as db:
        row=(await db.execute(text('SELECT status,total_cents FROM appointments WHERE tenant_id=:tenant'),{'tenant':d['a']})).one()
        assert tuple(row)==('confirmed',3000)
    # A lost-response retry returns the committed snapshot after catalogue edits.
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE services SET price_cents=9900,name=\'Nome novo\' WHERE id=:id'),{'id':d['cut']})
    recovered=await c.post(d['public']+'/appointments',json=payload)
    assert recovered.status_code==201 and recovered.json()['price_cents']==3000 and recovered.json()['services']==['Corte'],recovered.text


@pytest.mark.asyncio
async def test_concurrent_public_combo_addon_single_slot_and_immutable_composition(public_db):
    d=public_db;c=d['client'];item,_=await combo(d);service=UUID(item['synthetic_service_id'])
    added=await c.put(d['base']+'/services/'+str(service)+'/addons',json={'expected_version':0,'items':[{'addon_service_id':str(d['wash']),'add_duration':False}]})
    assert added.status_code==200,added.text
    await publish(d)
    quotes=await asyncio.gather(*(availability(d,service,addons=[d['wash']]) for _ in range(6)))
    bodies=[book_body(quote,f'+55119876543{i:02d}') for i,quote in enumerate(quotes)]
    responses=await asyncio.wait_for(asyncio.gather(*(c.post(d['public']+'/appointments',json=body) for body in bodies)),25)
    assert sorted(r.status_code for r in responses)==[201,409,409,409,409,409],[r.text for r in responses]
    winner=next(i for i,r in enumerate(responses) if r.status_code==201);result=responses[winner].json()
    assert result['price_cents']==5200
    assert datetime.fromisoformat(result['end_at'])-datetime.fromisoformat(result['start_at'])==timedelta(minutes=50)
    async with d['migrator'].begin() as db:
        stored=(await db.execute(text('SELECT catalog_snapshot FROM appointment_items WHERE tenant_id=:tenant'),{'tenant':d['a']})).scalar_one()
        assert stored['kind']=='variant'
        assert [x['name'] for x in stored['items']]==['Corte + barba','Lavagem']
        assert [x['name'] for x in stored['items'][0]['components']]==['Corte','Barba']
        assert stored['items'][1]['add_duration'] is False
        await db.execute(text('UPDATE services SET name=\'Lavagem alterada\',price_cents=1200 WHERE id=:id'),{'id':d['wash']})
        after=(await db.execute(text('SELECT catalog_snapshot FROM appointment_items WHERE tenant_id=:tenant'),{'tenant':d['a']})).scalar_one()
        assert after==stored
    replays=await asyncio.gather(*(c.post(d['public']+'/appointments',json=bodies[winner]) for _ in range(2)))
    assert all(r.status_code==201 and r.json()['price_cents']==5200 and r.json()['services']==['Corte + barba','Lavagem'] for r in replays),[r.text for r in replays]
    assert (await c.get(d['base']+'/site/metrics')).json()['site_bookings']==1


@pytest.mark.asyncio
async def test_stale_quote_disabled_site_and_unavailable_professional_fail_closed(public_db):
    d=public_db;c=d['client'];publication=await publish(d)
    item,_=await combo(d)
    publication=await publish(d)  # A new public catalogue item requires owner publication.
    quoted=await availability(d,UUID(item['synthetic_service_id']))
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE services SET name=\'Nome alterado\' WHERE id=:id'),{'id':d['beard']})
    stale=await c.post(d['public']+'/appointments',json=book_body(quoted))
    assert stale.status_code==409,stale.text
    quoted=await availability(d)
    async with d['migrator'].begin() as db:
        await db.execute(text('UPDATE professionals SET accepts_appointments=false WHERE id=:id'),{'id':d['pro']})
    assert (await c.post(d['public']+'/appointments',json=book_body(quoted))).status_code in {404,422}
    disabled=await c.post(d['base']+'/site/publication',json={'expected_version':publication['version'],'enabled':False})
    assert disabled.status_code==200,disabled.text
    assert (await c.get(d['public'])).status_code==404
    assert (await c.post(d['public']+'/appointments',json=book_body(quoted))).status_code==404
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT count(*) FROM appointments WHERE tenant_id=:tenant'),{'tenant':d['a']})).scalar_one()==0


@pytest.mark.asyncio
async def test_public_tables_forced_rls_scoped_fks_and_resolver_boundary(public_db):
    d=public_db;item,_=await combo(d);await publish(d)
    async with d['runtime'].begin() as db:
        assert (await db.execute(text('SELECT count(*) FROM service_packages'))).scalar_one()==0
        assert (await db.execute(text('SELECT public.resolve_published_site(:slug)'),{'slug':d['slug']})).scalar_one()==d['a']
        await db.execute(text("SELECT set_config('app.tenant_id',:scope,true)"),{'scope':str(d['b'])})
        assert (await db.execute(text('SELECT count(*) FROM service_packages WHERE id=:id'),{'id':UUID(item['id'])})).scalar_one()==0
    with pytest.raises(DBAPIError):
        async with d['runtime'].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:scope,true)"),{'scope':str(d['a'])})
            await db.execute(text('INSERT INTO service_addons(tenant_id,base_service_id,addon_service_id) VALUES(:tenant,:base,:addon)'),{'tenant':d['a'],'base':d['cut'],'addon':d['foreign']})
    with pytest.raises(DBAPIError):
        async with d['support'].begin() as db: await db.execute(text('SELECT * FROM service_packages'))
    with pytest.raises(DBAPIError):
        async with d['support'].begin() as db: await db.execute(text('SELECT public.resolve_published_site(:slug)'),{'slug':d['slug']})
    async with d['migrator'].connect() as db:
        flags=(await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('service_packages','service_package_items','service_addons','public_site_daily_metrics','public_booking_receipts')"))).all()
        assert len(flags)==5 and all(x and y for x,y in flags)


@pytest.mark.asyncio
async def test_real_redis_atomic_quota_publication_and_booking(public_db,monkeypatch):
    url=os.getenv('REDIS_URL')
    if not url:
        if os.getenv('REQUIRE_REDIS_TESTS')=='1':pytest.fail('Real Redis URL required')
        pytest.skip('Requires Redis for the atomic quota boundary')
    d=public_db;c=d['client']
    monkeypatch.setattr('app.public_site_api._redis',lambda:Redis.from_url(url,socket_connect_timeout=3,socket_timeout=3))
    cache=Redis.from_url(url)
    try:
        assert await cache.ping()
        request=Request({'type':'http','headers':[],'client':('127.0.0.1',2345)})
        async def attempt():
            try:await public_limit(request,d['slug'],'atomic-test',7,300);return 200
            except HTTPException as exc:return exc.status_code
        results=await asyncio.gather(*(attempt() for _ in range(25)))
        assert results.count(200)==7 and results.count(429)==18
        await publish(d)
        quoted=await availability(d)
        result=await c.post(d['public']+'/appointments',json=book_body(quoted))
        assert result.status_code==201 and result.json()['status']=='confirmed',result.text
        # Every counter sets its expiration atomically; no lasting unbounded key.
        import hashlib
        key='orion:public-limit:atomic-test:'+hashlib.sha256((d['slug']+'|127.0.0.1').encode()).hexdigest()
        assert 1<=await cache.ttl(key)<=300
    finally:
        # Delete only this synthetic tenant's finite, known test counters.
        import hashlib
        for category,peer in (('atomic-test','127.0.0.1'),('availability','127.0.0.1'),('booking-ip','127.0.0.1'),('booking-contact','+5511987654321')):
            for scope in (d['slug'],'tenant:'+str(d['a'])):
                key='orion:public-limit:'+category+':'+hashlib.sha256((scope+'|'+peer).encode()).hexdigest()
                await cache.delete(key)
        await cache.aclose()


@pytest.mark.asyncio
async def test_public_amenity_flags_follow_preview_published_snapshot_and_legacy_defaults(public_db):
    """Only explicitly enabled amenities are public; unpublished edits stay private."""
    d=public_db
    client=d['client']
    keys=('amenity_wifi','amenity_parking','amenity_accessibility','amenity_children')

    # Completely new businesses must advertise no amenities by default.
    first=(await client.get(d['base']+'/site')).json()
    assert all(first.get(key) is False for key in keys)

    await save_site(d,amenity_wifi=True,amenity_parking=False,
                    amenity_accessibility=True,amenity_children=False)
    preview=(await client.get(d['base']+'/site/preview')).json()
    assert [preview['site'][key] for key in keys]==[True,False,True,False]

    await publish(d)
    published=(await client.get(d['public'])).json()
    assert [published['site'][key] for key in keys]==[True,False,True,False]

    # A saved draft cannot mutate a published snapshot until republished.
    revised=await save_site(d,amenity_wifi=False,amenity_parking=True,
                            amenity_accessibility=False,amenity_children=True)
    assert revised['has_unpublished_changes'] is True
    assert [(await client.get(d['public'])).json()['site'][key] for key in keys]==[
        True,False,True,False]
    assert [(await client.get(d['base']+'/site/preview')).json()['site'][key]
            for key in keys]==[False,True,False,True]

    await publish(d)
    assert [(await client.get(d['public'])).json()['site'][key] for key in keys]==[
        False,True,False,True]

    # Old snapshots have no amenities fields at all; each remains disabled.
    await save_site(d)
    await publish(d)
    async with d['migrator'].begin() as db:
        await db.execute(text("""
            UPDATE management_site_preferences
            SET public_snapshot = public_snapshot - 'amenity_wifi' - 'amenity_parking'
                               - 'amenity_accessibility' - 'amenity_children'
            WHERE tenant_id = :tenant
        """),{'tenant':d['a']})
    older=(await client.get(d['public'])).json()
    assert all(older['site'][key] is False for key in keys)
    status=(await client.get(d['base']+'/site')).json()
    assert status['has_unpublished_changes'] is False


@pytest.mark.asyncio
async def test_public_custom_selection_sums_price_duration_and_creates_one_booking(public_db):
    d=public_db;c=d['client']
    await publish(d)
    params=[('service_ids',str(d['cut'])),('service_ids',str(d['wash'])),
            ('professional_id',str(d['pro'])),('day',str(d['day']))]
    response=await c.get(d['public']+'/availability',params=params)
    assert response.status_code==200,response.text
    quoted=response.json()
    assert quoted['slots'] and quoted['quote'] and quoted['price_cents']==4000
    assert quoted['price_mode']=='fixed' and quoted['duration_min']==40
    assert quoted['services']==['Corte','Lavagem']
    result=await c.post(d['public']+'/appointments',json=book_body(quoted))
    assert result.status_code==201,result.text
    item=result.json()
    assert item['status']=='confirmed' and item['price_cents']==4000
    assert item['services']==['Corte','Lavagem']
    assert (datetime.fromisoformat(item['end_at'])-datetime.fromisoformat(item['start_at'])).total_seconds()==2400
    # Two real appointment line items, not two appointments and not a
    # persistent synthetic package. Accounting uses the existing engine.
    async with d['migrator'].connect() as db:
        count=(await db.execute(text('SELECT count(*) FROM appointments WHERE tenant_id=:tenant'),
                                {'tenant':d['a']})).scalar_one()
        stored=(await db.execute(text("""SELECT service_id,price_snapshot_cents,duration_snapshot_min,
            price_mode_snapshot FROM appointment_items WHERE tenant_id=:tenant ORDER BY item_order"""),
            {'tenant':d['a']})).all()
        synthetic=(await db.execute(text("""SELECT count(*) FROM service_packages
            WHERE tenant_id=:tenant AND kind='variant'"""),{'tenant':d['a']})).scalar_one()
    assert count==1 and synthetic==0
    assert [(row[0],row[1],row[2]) for row in stored]==[
        (d['cut'],3000,30),(d['wash'],1000,10)]
    # Identical signed quote recovers the same booking; it cannot create a second.
    replay=await c.post(d['public']+'/appointments',json=book_body(quoted))
    assert replay.status_code==201 and replay.json()['protocol']==item['protocol']
    assert replay.json()['services']==['Corte','Lavagem']
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT count(*) FROM appointments WHERE tenant_id=:tenant'),
                                 {'tenant':d['a']})).scalar_one()==1


@pytest.mark.asyncio
async def test_public_custom_selection_rejects_duplicates_incompatible_staff_and_combination_overlap(public_db):
    d=public_db;c=d['client']
    await publish(d)
    root=[('professional_id',str(d['pro'])),('day',str(d['day']))]
    async def query(ids,pro=None,addons=()):
        return await c.get(d['public']+'/availability',params=[
            ('professional_id',str(pro or d['pro'])),('day',str(d['day']))]+[
            ('service_ids',str(x)) for x in ids]+[
            ('addon_ids',str(x)) for x in addons])
    for values in ((d['cut'],d['cut']),(d['cut'],d['foreign'])):
        response=await query(values)
        assert response.status_code==422,response.text
    assert (await query([d['cut'],d['beard']],pro=d['pro2'])).status_code==422
    assert (await query([d['cut'],d['wash']],addons=[d['beard']])).status_code==422
    assert (await query([d['cut'],d['wash']],pro=d['pro'])).json()['slots']
    registered,_=await combo(d)
    await publish(d)
    combo_id=UUID(registered['synthetic_service_id'])
    assert (await query([combo_id,d['cut']])).status_code==422
    mixed=await query([combo_id,d['wash']])
    assert mixed.status_code==200,mixed.text
    assert mixed.json()['price_cents']==5200 and mixed.json()['duration_min']==60
    assert mixed.json()['services']==['Corte + barba','Lavagem']


@pytest.mark.asyncio
async def test_public_custom_quote_price_change_or_professional_revocation_fails_closed(public_db):
    d=public_db;c=d['client'];await publish(d)
    def params():
        return [('service_ids',str(d['cut'])),('service_ids',str(d['wash'])),
                ('professional_id',str(d['pro'])),('day',str(d['day']))]
    first=(await c.get(d['public']+'/availability',params=params())).json()
    assert first['slots']
    async with d['migrator'].begin() as db:
        await db.execute(text("""UPDATE services SET price_cents=2200,price_mode='from'
            WHERE tenant_id=:tenant AND id=:service"""),
            {'tenant':d['a'],'service':d['wash']})
    assert (await c.post(d['public']+'/appointments',json=book_body(first))).status_code==409
    second=(await c.get(d['public']+'/availability',params=params())).json()
    assert second['price_cents']==5200 and second['duration_min']==40
    assert second['price_mode']=='from'
    async with d['migrator'].begin() as db:
        await db.execute(text("""DELETE FROM professional_services
            WHERE tenant_id=:tenant AND professional_id=:professional AND service_id=:service"""),
            {'tenant':d['a'],'professional':d['pro'],'service':d['wash']})
    assert (await c.post(d['public']+'/appointments',json=book_body(second))).status_code in (409,422)
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT count(*) FROM appointments WHERE tenant_id=:tenant'),
                                 {'tenant':d['a']})).scalar_one()==0
