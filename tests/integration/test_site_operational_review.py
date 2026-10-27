"""Owner review and pending variable prices against isolated PostgreSQL roles."""
from datetime import datetime,time,timedelta,timezone
from contextlib import asynccontextmanager
import asyncio
import os
import socket
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
import uvicorn

from app.booking_service import book_appointment,confirm_hold,public_booking_transaction
from test_public_catalog import public_db,publish,save_site
from scripts import correct_public_site_timezone as timezone_correction

pytestmark=pytest.mark.integration


async def preserved_records(d):
    result={}
    async with d['migrator'].connect() as db:
        for table in ('appointments','appointment_items','appointment_history','calendar_outbox','financial_entries'):
            result[table]=(await db.execute(text(f'''SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]'::jsonb)::text
                FROM {table} r WHERE tenant_id IN (:a,:b)'''),{'a':d['a'],'b':d['b']})).scalar_one()
    return result


async def confirmed_future(d):
    begin=datetime.combine(d['day'],time(10),tzinfo=ZoneInfo('America/Sao_Paulo'))
    factory=async_sessionmaker(d['runtime'],expire_on_commit=False)
    async with factory() as db:
        async with public_booking_transaction(db,d['a'],d['pro'],d['day']):
            hold=await book_appointment(db,d['a'],d['pro'],[d['cut']],'+5511987654321',
                'Cliente exclusivamente sintético',begin,'review-'+uuid4().hex,source='public_site',_public_transaction=True)
            result=await confirm_hold(db,d['a'],hold['id'],customer_phone='+5511987654321',
                source='public_site',_public_transaction=True)
    return UUID(str(result['id'])),begin


async def seed_appointment(d,*,tenant=None,at=None,status='confirmed',variable=True):
    tenant=tenant or d['a'];foreign=tenant==d['b'];identity=uuid4();customer=d['private_customer']
    at=at or datetime.now(timezone.utc)-timedelta(hours=3)
    async with d['migrator'].begin() as db:
        if foreign:
            customer=uuid4()
            await db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164) VALUES(:id,:tenant,'Cliente sintético externo','+5511987654322')"),{'id':customer,'tenant':tenant})
        await db.execute(text('''INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,
            idempotency_key,request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents,hold_expires_at)
            VALUES(:id,:tenant,:customer,:pro,:protocol,:key,:fingerprint,:start,:end,:start,:end,:status,1100,:expiry)'''),
            {'id':identity,'tenant':tenant,'customer':customer,'pro':d['other_pro'] if foreign else d['pro'],
             'protocol':'S'+identity.hex[:8],'key':'synthetic-review-'+identity.hex,'fingerprint':'0'*64,
             'start':at,'end':at+timedelta(minutes=30),'status':status,
             'expiry':datetime.now(timezone.utc)+timedelta(minutes=5) if status=='hold' else None})
        await db.execute(text('''INSERT INTO appointment_items(tenant_id,appointment_id,service_id,name_snapshot,
            duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order,price_mode_snapshot)
            VALUES(:tenant,:id,:service,'Serviço sintético',30,1100,0,0,0,:mode)'''),
            {'tenant':tenant,'id':identity,'service':d['foreign'] if foreign else d['cut'],'mode':'from' if variable else 'fixed'})
    return identity,at


async def seed_review(d,identity,begin,tenant=None):
    async with d['migrator'].begin() as db:
        await db.execute(text('''INSERT INTO public_site_timezone_reviews(tenant_id,appointment_id,from_timezone,to_timezone,original_start_at)
            VALUES(:tenant,:id,'America/Campo_Grande','America/Sao_Paulo',:at)'''),
            {'tenant':tenant or d['a'],'id':identity,'at':begin})


async def test_timezone_review_keeps_original_instant_and_owner_ack_is_idempotent(public_db):
    d=public_db;await publish(d);identity,begin=await confirmed_future(d);await seed_review(d,identity,begin)
    before=await preserved_records(d)
    site=await d['client'].get(d['base']+'/site');assert site.status_code==200,site.text
    review=site.json()['operational_review'];assert review['timezone_review_count']==1
    row=review['timezone_reviews'][0]
    old=datetime.fromisoformat(row['previous_local_start']);new=datetime.fromisoformat(row['current_local_start'])
    assert old==new==begin and old.utcoffset()==timedelta(hours=-4) and new.utcoffset()==timedelta(hours=-3)
    assert new.hour==10 and old.hour==9
    path=d['base']+'/site/timezone-reviews/'+str(identity)+'/acknowledge'
    first=await d['client'].post(path);second=await d['client'].post(path)
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()==second.json(),'repeated acknowledgement cannot rewrite its timestamp'
    after=(await d['client'].get(d['base']+'/site')).json()['operational_review']
    assert after['timezone_review_count']==0 and after['timezone_reviews']==[]
    assert await preserved_records(d)==before,'review and acknowledgement cannot edit booking/items/history/calendar/financial rows'


@pytest.mark.parametrize('role',['editor','viewer','support'])
async def test_timezone_review_nonowners_cannot_acknowledge_or_change_bookings(public_db,role):
    d=public_db;identity,begin=await confirmed_future(d);await seed_review(d,identity,begin)
    before=await preserved_records(d)
    response=await d['client'].post(d['base']+'/site/timezone-reviews/'+str(identity)+'/acknowledge',headers={'x-test-role':role})
    assert response.status_code==403,response.text
    async with d['migrator'].connect() as db:
        assert (await db.execute(text('SELECT acknowledged_at FROM public_site_timezone_reviews WHERE tenant_id=:tenant AND appointment_id=:id'),{'tenant':d['a'],'id':identity})).scalar_one() is None
    assert await preserved_records(d)==before


async def test_timezone_reviews_do_not_leak_or_acknowledge_another_company(public_db):
    d=public_db;identity,begin=await seed_appointment(d,tenant=d['b']);await seed_review(d,identity,begin,d['b'])
    before=await preserved_records(d)
    review=(await d['client'].get(d['base']+'/site')).json()['operational_review']
    assert review['timezone_review_count']==0 and review['timezone_reviews']==[]
    assert str(identity) not in str(review)
    wrong_path=d['base']+'/site/timezone-reviews/'+str(identity)+'/acknowledge'
    assert (await d['client'].post(wrong_path)).status_code==404
    foreign=f"/api/v1/tenants/{d['b']}/management/site"
    assert (await d['client'].get(foreign)).status_code in (403,404)
    assert (await d['client'].post(foreign+'/timezone-reviews/'+str(identity)+'/acknowledge')).status_code in (403,404)
    assert await preserved_records(d)==before


async def test_finance_pending_final_prices_are_scoped_elapsed_and_do_not_create_revenue(public_db):
    d=public_db;now=datetime.now(timezone.utc).replace(microsecond=0)
    included,_=await seed_appointment(d,at=now-timedelta(hours=2))
    for at,status,variable,tenant in (
        (now+timedelta(hours=2),'confirmed',True,d['a']),
        (now-timedelta(hours=4),'completed',True,d['a']),
        (now-timedelta(hours=6),'confirmed',False,d['a']),
        (now-timedelta(days=90),'confirmed',True,d['a']),
        (now-timedelta(hours=2),'confirmed',True,d['b'])):
        await seed_appointment(d,at=at,status=status,variable=variable,tenant=tenant)
    before=await preserved_records(d)
    params={'start_date':str((now-timedelta(days=2)).date()),'end_date':str((now+timedelta(days=2)).date())}
    response=await d['client'].get(d['base']+'/finance/summary',params=params)
    assert response.status_code==200,response.text
    summary=response.json()['summary']
    assert summary['awaiting_final_price_count']==1
    assert [item['id'] for item in summary['awaiting_final_price']]==[str(included)]
    assert summary['income_cents']==summary['pending_income_cents']==0
    assert await preserved_records(d)==before,'report reads never finalize work or invent financial entries'


async def prepare_correction(d):
    await save_site(d,city='Montes Claros',state='MG');await publish(d)
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE tenants SET timezone='America/Campo_Grande' WHERE id=:tenant"),{'tenant':d['a']})


def run_correction(db,d,**overrides):
    return timezone_correction.correct_timezone(db,tenant=d['a'],slug=d['slug'],
        expected_zone=overrides.pop('expected_zone','America/Campo_Grande'),
        target_zone=overrides.pop('target_zone','America/Sao_Paulo'),**overrides)


async def correction_state(d):
    async with d['migrator'].connect() as db:
        zone=(await db.execute(text('SELECT timezone FROM tenants WHERE id=:tenant'),{'tenant':d['a']})).scalar_one()
        reviews=(await db.execute(text('SELECT appointment_id,original_start_at FROM public_site_timezone_reviews WHERE tenant_id=:tenant ORDER BY appointment_id'),{'tenant':d['a']})).all()
    return zone,reviews


async def test_reviewed_timezone_correction_preserves_every_booking_and_only_flags_future_active_states(public_db):
    d=public_db;await prepare_correction(d);now=datetime.now(timezone.utc).replace(microsecond=0)
    confirmed,confirmed_at=await seed_appointment(d,at=now+timedelta(days=7))
    hold,hold_at=await seed_appointment(d,at=now+timedelta(days=8),status='hold')
    for offset,status in ((9,'cancelled'),(10,'completed'),(-1,'confirmed')):
        await seed_appointment(d,at=now+timedelta(days=offset),status=status)
    foreign,_=await seed_appointment(d,tenant=d['b'],at=now+timedelta(days=7))
    before=await preserved_records(d)
    async with d['migrator'].begin() as db:
        receipt=await db.run_sync(lambda connection:run_correction(connection,d,now=now))
        replay=await db.run_sync(lambda connection:run_correction(connection,d,now=now))
    assert receipt['changed'] and receipt['reviews_added']==2 and receipt['policy']=='preserve_instants'
    assert not replay['changed'] and replay['reviews_added']==0
    assert replay['appointments_digest']==receipt['appointments_digest']
    zone,reviews=await correction_state(d)
    assert zone=='America/Sao_Paulo'
    assert dict(reviews)=={confirmed:confirmed_at,hold:hold_at} and foreign not in dict(reviews)
    assert await preserved_records(d)==before


@pytest.mark.parametrize('invalid',['tenant','slug','city','previous_zone','target_zone'])
async def test_timezone_correction_rejects_changed_scope_without_partial_writes(public_db,invalid):
    d=public_db;await prepare_correction(d);await seed_appointment(d,at=datetime.now(timezone.utc)+timedelta(days=7))
    options={'tenant':d['a'],'slug':d['slug'],'expected_zone':'America/Campo_Grande','target_zone':'America/Sao_Paulo'}
    if invalid=='tenant':options['tenant']=d['b']
    if invalid=='slug':options['slug']='other-'+uuid4().hex
    if invalid=='previous_zone':options['expected_zone']='America/Manaus'
    if invalid=='target_zone':options['target_zone']='America/Rio_Branco'
    if invalid=='city':
        async with d['migrator'].begin() as db:
            await db.execute(text("UPDATE management_site_preferences SET city='Outra cidade' WHERE tenant_id=:tenant"),{'tenant':d['a']})
    before=await preserved_records(d)
    with pytest.raises(ValueError):
        async with d['migrator'].begin() as db:
            await db.run_sync(lambda connection:timezone_correction.correct_timezone(connection,**options))
    assert await correction_state(d)==('America/Campo_Grande',[])
    assert await preserved_records(d)==before


async def test_timezone_correction_can_be_rehearsed_and_rolled_back_by_caller(public_db):
    d=public_db;await prepare_correction(d);await seed_appointment(d,at=datetime.now(timezone.utc)+timedelta(days=7))
    before=await preserved_records(d)
    async with d['migrator'].connect() as db:
        transaction=await db.begin()
        receipt=await db.run_sync(lambda connection:run_correction(connection,d))
        assert receipt['changed'] and receipt['reviews_added']==1
        await transaction.rollback()
    assert await correction_state(d)==('America/Campo_Grande',[])
    assert await preserved_records(d)==before


async def test_timezone_digest_guard_requires_rollback_and_preserves_all_original_rows(public_db,monkeypatch):
    d=public_db;await prepare_correction(d);await seed_appointment(d,at=datetime.now(timezone.utc)+timedelta(days=7))
    before=await preserved_records(d);original=timezone_correction.appointment_digest;calls=0
    def changed_digest(db,tenant):
        nonlocal calls
        calls+=1;value=original(db,tenant)
        return value if calls==1 else (value[0],'synthetic-changed-digest')
    monkeypatch.setattr(timezone_correction,'appointment_digest',changed_digest)
    with pytest.raises(RuntimeError,match='preservation'):
        async with d['migrator'].begin() as db:
            await db.run_sync(lambda connection:run_correction(connection,d))
    assert await correction_state(d)==('America/Campo_Grande',[])
    assert await preserved_records(d)==before


@asynccontextmanager
async def live_public_app(app):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(64)
    listener.setblocking(False);port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',
                                       log_level='error',access_log=False,proxy_headers=False))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():await task
                await asyncio.sleep(.02)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit=True
        await asyncio.wait_for(task,timeout=8)
        listener.close()


@pytest.mark.parametrize('engine',['chromium','webkit','firefox'])
async def test_real_ssr_remains_readable_without_js_and_recovers_slow_failed_catalogue(public_db,engine):
    try:from playwright.async_api import async_playwright
    except ImportError:
        if os.environ.get('ORION_E2E_ENABLED')=='1':pytest.fail('Browser qualification requires the Playwright runner')
        pytest.skip('SSR browser integration requires the optional Playwright runner')
    d=public_db
    await save_site(d,address='Rua exclusivamente sintética, 123',city='Montes Claros',state='MG',
                    theme_color='#fdc700',whatsapp_phone='+5538999999999',contact_phone='+5567999999999')
    await publish(d);before=await preserved_records(d)
    async with live_public_app(d['app']) as origin,async_playwright() as playwright:
        browser=await getattr(playwright,engine).launch()
        page_path='/s/'+d['slug'];api_path=d['public'];calls=[]
        async def only_local(route):
            calls.append((route.request.method,route.request.url))
            if route.request.url.startswith(origin+'/'):await route.continue_()
            else:await route.abort()
        try:
            context=await browser.new_context(java_script_enabled=False,viewport={'width':320,'height':900},locale='pt-BR')
            await context.route('**/*',only_local);page=await context.new_page()
            response=await page.goto(origin+page_path,wait_until='domcontentloaded')
            assert response.status==200
            await page.locator('#site-content').wait_for(state='visible',timeout=5000)
            assert await page.locator('#services-grid article.service-card').count()>=3
            assert '30,00' in await page.locator('#services-grid').inner_text()
            assert 'Rua exclusivamente sintética, 123' in await page.locator('#full-address').inner_text()
            assert await page.locator('#contact-links a[href^="https://wa.me/"]').count()==1
            assert await page.locator('#hero-book').is_disabled()
            assert not any(url==origin+api_path for _,url in calls),'catalogue HTML exists before any JavaScript API read'
            assert 'Nome de cliente privado' not in await page.content()
            assert 'Nota clínica privada' not in await page.content()
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            await context.close()

            context=await browser.new_context(viewport={'width':320,'height':900},locale='pt-BR')
            started=asyncio.Event();release=asyncio.Event();attempts=0
            async def slow_first_catalogue(route):
                nonlocal attempts
                calls.append((route.request.method,route.request.url))
                if not route.request.url.startswith(origin+'/'):await route.abort();return
                if route.request.url==origin+api_path:
                    attempts+=1
                    if attempts==1:
                        started.set();await release.wait()
                        await route.fulfill(status=503,content_type='application/json',body='{"detail":"Falha sintética temporária"}');return
                await route.continue_()
            await context.route('**/*',slow_first_catalogue);page=await context.new_page()
            await page.goto(origin+page_path,wait_until='domcontentloaded')
            await asyncio.wait_for(started.wait(),timeout=5)
            assert await page.locator('#services-grid article.service-card').count()>=3
            for selector in ('#hero-book','#share-site','#service-search'):assert await page.locator(selector).is_disabled()
            release.set();await page.locator('#page-message button').wait_for(state='visible')
            assert await page.locator('#site-content').is_visible()
            assert await page.locator('#hero-book').is_disabled()
            assert await page.locator('#services-grid article.service-card').count()>=3
            await page.locator('#page-message button').click()
            await page.locator('#services-grid button.service-card').first.wait_for(state='visible')
            for selector in ('#hero-book','#share-site','#service-search'):assert await page.locator(selector).is_enabled()
            assert await page.locator('#page-message').is_hidden()
            assert await page.evaluate('document.body.style.getPropertyValue("--primary")===""')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            assert not any(method!='GET' for method,_ in calls),'SSR/catalogue recovery is read-only'
            await context.close()
        finally:await browser.close()
    assert await preserved_records(d)==before,'browser reads cannot change any existing reservation or financial record'
