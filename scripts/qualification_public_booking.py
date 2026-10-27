"""Finite public-site HTTP qualification on disposable loopback-only data.

No target URL or production mode exists. This process creates a random local
database, migrates it, starts its own API and drains it before reconciliation.
Importing this module does not touch infrastructure. Requests use real runtime
roles, PostgreSQL exclusion/RLS constraints, Redis and the real SQLAlchemy pool.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.cookies import SimpleCookie
import importlib.util
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from scripts.qualification_load import DisposableDatabase, ROLE_ENV, local_url, percentile

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
TARGETS = {"read_p95_ms": 1500, "read_p99_ms": 3000,
           "write_p95_ms": 4000, "write_p99_ms": 8000}
PHASES = ("navigation", "availability", "same_slot", "different_days", "distributed_bookings", "unknown_slugs", "tenants", "replay", "combos", "dependencies")


class PublicHttpClient:
    """Partition socket bookkeeping while preserving 1,000 offered requests.

    Public browser cookies stay on a deterministic pool. A shared semaphore
    caps actual in-flight requests, so partitioning never increases offered
    concurrency. No automatic retries are performed. Phase recycling prevents
    reusing sockets left idle during the preceding large burst.
    """
    POOLS=16
    def __init__(self,base_url,users,timeout,*,factory=None):
        self.base_url=base_url;self.users=users;self.timeout=timeout
        self.factory=factory or httpx.AsyncClient;self.clients=[]
        self.capacity=asyncio.Semaphore(users)
    @staticmethod
    def pool_index(headers):
        cookie=(headers or {}).get('Cookie','')
        parsed=SimpleCookie();parsed.load(cookie)
        token=parsed.get('orion_public_browser')
        return int.from_bytes(hashlib.sha256(token.value.encode()).digest()[:8],'big')%PublicHttpClient.POOLS if token else 0
    async def __aenter__(self):
        self.clients=[self.factory(base_url=self.base_url,timeout=self.timeout,trust_env=False,
            limits=httpx.Limits(max_connections=self.users,max_keepalive_connections=(self.users+15)//16+8,keepalive_expiry=2))
            for _ in range(self.POOLS)]
        return self
    async def __aexit__(self,*_):
        clients,self.clients=self.clients,[]
        await asyncio.gather(*(client.aclose() for client in clients))
    async def recycle(self):
        await self.__aexit__();await self.__aenter__()
    async def request(self,method,path,**kwargs):
        async with self.capacity:
            return await self.clients[self.pool_index(kwargs.get('headers'))].request(method,path,**kwargs)
    async def get(self,path,**kwargs):return await self.request('GET',path,**kwargs)
    def configuration(self):
        return {'pools':self.POOLS,'actual_global_inflight_limit':self.users,
                'sticky_routing':'sha256 of real server-issued public cookie','max_keepalive_per_pool':(self.users+15)//16+8,
                'keepalive_expiry_seconds':2,'recycled_between_scenarios':True,'automatic_retries':0}


def parser():
    result=argparse.ArgumentParser(description=__doc__)
    result.add_argument('--confirm-disposable-local',action='store_true')
    result.add_argument('--output',type=Path,required=True)
    result.add_argument('--users',type=int,choices=(10,100,1000),default=1000)
    result.add_argument('--phases',nargs='+',choices=PHASES,default=list(PHASES))
    result.add_argument('--workers',type=int,choices=(1,),default=1)
    result.add_argument('--db-pool-size',type=int,choices=range(1,21),default=5)
    result.add_argument('--db-max-overflow',type=int,choices=range(0,21),default=10)
    result.add_argument('--timeout',type=int,choices=range(10,121),default=60)
    result.add_argument('--readiness-path',choices=('/health/ready',),default='/health/ready')
    result.add_argument('--api-cpus',type=int,choices=(1,2),default=None)
    result.add_argument('--api-memory-mb',type=int,choices=(128,256,512,1024),default=None)
    return result


def fixture(sandbox):
    """Published synthetic sites and immutable historical reservations."""
    tenants=[]; day=(datetime.now(UTC)+timedelta(days=2)).date()
    with sandbox.engine.begin() as db:
        db.execute(text("SELECT set_config('app.site_publication_v2','on',true)"))
        for n in range(20):
            tenant,cut,beard,historical=uuid4(),uuid4(),uuid4(),uuid4()
            slug='public-qualification-'+tenant.hex
            db.execute(text("INSERT INTO tenants(id,name,slug,timezone,booking_horizon_days) VALUES(:t,:name,:slug,'America/Sao_Paulo',60)"),
                {'t':tenant,'name':f'Synthetic public company {n}','slug':slug})
            for service,name,duration,price,mode in ((cut,'Synthetic fixed service',15,3000,'fixed'),
                                                  (beard,'Synthetic minimum price service',10,1500,'from')):
                db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents,price_mode,prep_min,cleanup_min) VALUES(:s,:t,:name,:duration,:price,:mode,:buffer,:buffer)"),
                    {'s':service,'t':tenant,'name':name,'duration':duration,'price':price,'mode':mode,'buffer':5 if mode=='from' else 0})
            people=[]
            for j in range(5):
                professional=uuid4();people.append(str(professional))
                db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:p,:t,:name)"),
                    {'p':professional,'t':tenant,'name':f'Synthetic professional {j}'})
                for service in (cut,beard):
                    db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)"),{'t':tenant,'p':professional,'s':service})
                for weekday in range(7):
                    db.execute(text("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(:t,:p,:day,'08:00','20:00')"),
                        {'t':tenant,'p':professional,'day':weekday})
            snapshot={'title':f'Synthetic public company {n}','description':'Isolated public qualification',
                      'slug':slug,'show_prices':True,'theme_color':'#123456'}
            db.execute(text("""INSERT INTO management_site_preferences(tenant_id,title,slug,version,public_enabled,
                public_enabled_at,public_snapshot,public_slug,public_version)
                VALUES(:t,:title,:slug,1,true,now(),CAST(:snapshot AS jsonb),:slug,1)"""),
                {'t':tenant,'title':snapshot['title'],'slug':slug,'snapshot':json.dumps(snapshot)})
            db.execute(text("INSERT INTO public_site_slugs(slug,tenant_id,kind) VALUES(:slug,:t,'canonical')"),{'t':tenant,'slug':slug})
            db.execute(text("INSERT INTO public_site_publications(tenant_id,version,action,snapshot,slug) VALUES(:t,1,'publish',CAST(:snapshot AS jsonb),:slug)"),
                {'t':tenant,'slug':slug,'snapshot':json.dumps(snapshot)})
            customer=uuid4();start=datetime.now(UTC).replace(hour=11,minute=0,second=0,microsecond=0)-timedelta(days=1)
            db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164) VALUES(:c,:t,'Historical synthetic customer',:phone)"),
                {'c':customer,'t':tenant,'phone':'+55110000'+f'{n:05d}'})
            db.execute(text("""INSERT INTO appointments(id,tenant_id,professional_id,customer_id,protocol,idempotency_key,
                request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents)
                VALUES(:a,:t,:p,:c,:protocol,:key,:fp,:start,:end,:start,:end,'confirmed',3000)"""),
                {'a':historical,'t':tenant,'p':people[0],'c':customer,'protocol':f'Q-{n:06d}',
                 'key':'synthetic-history-'+historical.hex,'fp':'0'*64,'start':start,'end':start+timedelta(minutes=15)})
            db.execute(text("""INSERT INTO appointment_items(tenant_id,appointment_id,service_id,name_snapshot,
                duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order)
                VALUES(:t,:a,:s,'Historical synthetic service',15,3000,0,0,0)"""),{'t':tenant,'a':historical,'s':cut})
            db.execute(text("INSERT INTO appointment_history(tenant_id,appointment_id,event,operator) VALUES(:t,:a,'confirmed','system')"),{'t':tenant,'a':historical})
            tenants.append({'tenant':str(tenant),'slug':slug,'professional':people[0],'professionals':people,
                            'service':str(cut),'variable_service':str(beard),'day':day.isoformat(),'historical':str(historical)})
    return tenants


def history_digest(sandbox,tenants):
    result={}; identifiers=[t['historical'] for t in tenants]
    with sandbox.engine.connect() as db:
        for table,column in (('appointments','id'),('appointment_items','appointment_id'),('appointment_history','appointment_id')):
            rows=db.execute(text(f"SELECT to_jsonb(t)::text FROM {table} t WHERE {column}=ANY(CAST(:ids AS uuid[])) ORDER BY t.id"),{'ids':identifiers}).scalars().all()
            result[table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return result


def cookie_from(response,existing=''):
    cookies=SimpleCookie();cookies.load(existing)
    for header in response.headers.get_list('set-cookie'):
        incoming=SimpleCookie();incoming.load(header)
        for key,value in incoming.items():cookies[key]=value.value
    return '; '.join(key+'='+value.value for key,value in cookies.items())


class Metrics:
    def __init__(self):self.rows=[]
    def record(self,status,elapsed,body=None):self.rows.append({'status':status,'ms':elapsed*1000,'body':body})
    def report(self,elapsed,*,write=False,expected=None):
        statuses=Counter(str(row['status']) for row in self.rows);times=[row.get('offered_ms',row['ms']) for row in self.rows]
        requests=[row['ms'] for row in self.rows]
        p95,p99=percentile(times,.95),percentile(times,.99)
        target95,target99=(TARGETS['write_p95_ms'],TARGETS['write_p99_ms']) if write else (TARGETS['read_p95_ms'],TARGETS['read_p99_ms'])
        actual={int(k):v for k,v in statuses.items() if k.isdecimal()}
        status_ok=(actual==expected and sum(actual.values())==len(self.rows)) if expected is not None else not any(k in statuses for k in ('429','503','500','transport_error'))
        return {'requests':len(self.rows),'elapsed_seconds':round(elapsed,3),'requests_per_second':round(len(self.rows)/max(elapsed,.001),3),
                'latency_ms':{'p50':percentile(times,.5),'p95':p95,'p99':p99,'max':round(max(times),3) if times else None},
                'latency_scope':'from common burst release to response; includes generator scheduling and prior sequential journey requests',
                'request_latency_ms':{'p50':percentile(requests,.5),'p95':percentile(requests,.95),'p99':percentile(requests,.99)},
                'statuses':dict(statuses),'expected_statuses':expected,'status_pass':status_ok,
                'transport_error_types':dict(Counter(row['body'].get('error_type','unknown') for row in self.rows
                    if row['status']=='transport_error' and isinstance(row.get('body'),dict))),
                'latency_pass':bool(times) and p95<=target95 and p99<=target99,
                'pass':status_ok and bool(times) and p95<=target95 and p99<=target99}


async def request(client,user,method,path,*,body=None,metrics=None):
    started=time.perf_counter()
    try:
        response=await client.request(method,path,json=body,headers={'Cookie':user.get('cookie','')})
        user['cookie']=cookie_from(response,user.get('cookie',''))
        try:value=response.json()
        except ValueError:value=None
        status=response.status_code
    except httpx.HTTPError as exc:
        status='transport_error';value={'error_type':type(exc).__name__}
    if metrics is not None:
        finished=time.perf_counter();metrics.record(status,finished-started,value)
        if '_offered_at' in user:metrics.rows[-1]['offered_ms']=(finished-user['_offered_at'])*1000
    return status,value


def availability_path(user,*,multi=False):
    path=f"/api/v1/public/sites/{user['slug']}/availability?professional_id={user['professional']}&day={user['day']}"
    return path+('&service_ids='+user['service']+'&service_ids='+user['variable_service'] if multi else '&service_id='+user['service'])


def body(quoted,index,*,slot=None):
    return {'quote':quoted['quote'],'start_at':slot or quoted['slots'][0],
            'customer_name':'Synthetic public qualification customer',
            'customer_phone':'+5511000'+f'{index:06d}','confirmed':True}


async def burst(items,operation):
    gate=asyncio.Event();ready=0
    async def execute(index,item):
        nonlocal ready
        ready+=1
        await gate.wait()
        return await operation(index,item)
    tasks=[asyncio.create_task(execute(i,item)) for i,item in enumerate(items)]
    try:
        while ready<len(tasks):await asyncio.sleep(0)
        started=time.perf_counter()
        for item in items:
            if isinstance(item,dict):item['_offered_at']=started
        gate.set()
        values=await asyncio.gather(*tasks)
        return values,time.perf_counter()-started
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def prefetch(client,users,*,multi=False):
    # Preparation is bounded and excluded from the measured simultaneous burst.
    semaphore=asyncio.Semaphore(20)
    async def one(user):
        async with semaphore:
            status,value=await request(client,user,'GET',availability_path(user,multi=multi))
            if status!=200 or not value or not value.get('slots') or not value.get('quote'):
                raise RuntimeError(f"Synthetic quote preparation failed: status={status}, error_type={(value or {}).get('error_type','none')}")
            return value
    return await asyncio.gather(*(one(user) for user in users))


def integrity(sandbox,tenants,before):
    with sandbox.engine.connect() as db:
        overlap=db.execute(text("""SELECT count(*) FROM appointments a JOIN appointments b ON a.tenant_id=b.tenant_id
            AND a.professional_id=b.professional_id AND a.id<b.id AND tstzrange(a.occupied_from,a.occupied_until,'[)') && tstzrange(b.occupied_from,b.occupied_until,'[)')
            WHERE a.status IN ('hold','confirmed','in_progress') AND b.status IN ('hold','confirmed','in_progress')""")).scalar_one()
        orphan_items=db.execute(text("SELECT count(*) FROM appointment_items i LEFT JOIN appointments a ON a.id=i.appointment_id AND a.tenant_id=i.tenant_id WHERE a.id IS NULL")).scalar_one()
        duplicates=db.execute(text("SELECT count(*) FROM (SELECT tenant_id,idempotency_key FROM appointments GROUP BY tenant_id,idempotency_key HAVING count(*)>1) x")).scalar_one()
        confirmations=db.execute(text("SELECT count(*) FROM appointments WHERE status='confirmed' AND idempotency_key LIKE 'public:%' ")).scalar_one()
        receipts=db.execute(text("SELECT count(*) FROM public_booking_receipts")).scalar_one()
        statuses=dict(db.execute(text("SELECT status,count(*) FROM appointments WHERE idempotency_key LIKE 'public:%' GROUP BY status")).all())
        constraints=db.execute(text("SELECT conname FROM pg_constraint WHERE contype='x' AND conrelid='appointments'::regclass")).scalars().all()
        metric_bookings=db.execute(text('SELECT COALESCE(sum(bookings),0) FROM public_site_daily_metrics')).scalar_one()
        if db.execute(text("SELECT to_regclass('public.public_site_metric_shards')")).scalar_one():
            metric_bookings+=db.execute(text('SELECT COALESCE(sum(bookings),0) FROM public_site_metric_shards')).scalar_one()
        metric_bookings=int(metric_bookings)
    after=history_digest(sandbox,tenants)
    return {'overlapping_active_intervals':overlap,'orphan_items':orphan_items,'duplicate_idempotency_keys':duplicates,
            'public_confirmed':confirmations,'public_receipts':receipts,'public_statuses':statuses,
            'metric_bookings_legacy_plus_shards':metric_bookings,
            'postgres_exclusion_constraints':constraints,'historical_before':before,'historical_after':after,
            'historical_byte_preservation':before==after,
            'pass':overlap==orphan_items==duplicates==0 and confirmations==receipts==metric_bookings and before==after and bool(constraints)}


async def monitor(sandbox,cache,api,samples,stop):
    try:import psutil
    except ImportError:psutil=None
    process=psutil.Process(api.pid) if psutil else None
    generator=psutil.Process() if psutil else None
    previous_cpu=previous_stamp=previous_pg=previous_redis=previous_generator=None
    while not stop.is_set():
        stamp=time.perf_counter();row={'monotonic_seconds':stamp}
        try:
            def pg_sample():
                with sandbox.engine.connect() as db:
                    return dict(db.execute(text("""SELECT count(*) AS database_connections,
                        count(*) FILTER(WHERE state='active') AS active_connections,
                        count(*) FILTER(WHERE wait_event_type='Lock') AS lock_waiters,
                        COALESCE(max(extract(epoch FROM clock_timestamp()-query_start)) FILTER(WHERE wait_event_type='Lock'),0) AS longest_lock_wait_seconds,
                        array_agg(pid) AS backend_pids
                        FROM pg_stat_activity WHERE datname=current_database()""")).mappings().one())
            row.update(await asyncio.to_thread(pg_sample))
            row['longest_lock_wait_seconds']=float(row['longest_lock_wait_seconds'])
            info=await cache.info('memory');row['redis_used_memory_bytes']=info['used_memory']
            redis_cpu=await cache.info('cpu')
            row['redis_cpu_seconds']=float(redis_cpu['used_cpu_sys'])+float(redis_cpu['used_cpu_user'])
            row['redis_cpu_scope']='dedicated synthetic Redis server across logical DBs; excludes production'
            if previous_redis is not None and previous_stamp is not None:
                row['redis_cpu_percent_one_core']=(row['redis_cpu_seconds']-previous_redis)/(stamp-previous_stamp)*100
            previous_redis=row['redis_cpu_seconds']
            if process:
                cpu=generator.cpu_times();row['generator_cpu_seconds']=cpu.user+cpu.system
                row['generator_rss_bytes']=generator.memory_info().rss
                if previous_generator is not None and previous_stamp is not None:
                    row['generator_cpu_percent_one_core']=(row['generator_cpu_seconds']-previous_generator)/(stamp-previous_stamp)*100
                previous_generator=row['generator_cpu_seconds']
                pg={};pg_rss=0
                for pid in row.pop('backend_pids',[]):
                    try:
                        backend=psutil.Process(pid);cpu=backend.cpu_times()
                        pg[(pid,backend.create_time())]=cpu.user+cpu.system
                        pg_rss+=backend.memory_info().rss
                    except (psutil.NoSuchProcess,psutil.AccessDenied):continue
                row['postgres_backend_rss_bytes']=pg_rss
                row['postgres_cpu_scope']='database backend processes observed in pg_stat_activity; excludes postmaster/background'
                row['postgres_backend_processes_observed']=len(pg)
                if previous_pg is not None and previous_stamp is not None:
                    shared=pg.keys() & previous_pg.keys()
                    row['postgres_cpu_interval_complete']=pg.keys()==previous_pg.keys()
                    row['postgres_cpu_lower_bound_percent_one_core']=sum(pg[key]-previous_pg[key] for key in shared)/(stamp-previous_stamp)*100
                previous_pg=pg
                processes=[process,*process.children(recursive=True)]
                row['api_cpu_seconds']=sum(p.cpu_times().user+p.cpu_times().system for p in processes if p.is_running())
                if previous_cpu is not None:row['api_cpu_percent_one_core']=(row['api_cpu_seconds']-previous_cpu)/(stamp-previous_stamp)*100
                previous_cpu=row['api_cpu_seconds'];previous_stamp=stamp
                row['api_rss_bytes']=sum(p.memory_info().rss for p in processes if p.is_running())
                row['host_available_memory_bytes']=psutil.virtual_memory().available
            else:row.pop('backend_pids',None)
        except Exception as exc:row['sample_error_type']=type(exc).__name__
        samples.append(row)
        try:await asyncio.wait_for(stop.wait(),.2)
        except TimeoutError:pass


async def run_phases(args,sandbox,tenants,client,report):
    users=[{**tenants[i%len(tenants)],'professional':tenants[i%len(tenants)]['professionals'][(i//len(tenants))%5],
            'day':(datetime.fromisoformat(tenants[0]['day'])+timedelta(days=(i//100)%20)).date().isoformat(), 'cookie':''} for i in range(args.users)]
    # Independent cookies are obtained from the real server, never fabricated.
    report['phase']='public_cookie_and_quote_preparation';write_report(args.output,report)
    await prefetch(client,users)
    for phase in args.phases:
        report['phase']=phase;write_report(args.output,report);metrics=Metrics()
        if phase=='navigation':
            async def navigate(index,user):
                for path in (f"/s/{user['slug']}",f"/api/v1/public/sites/{user['slug']}"):
                    await request(client,user,'GET',path,metrics=metrics)
            _,elapsed=await burst(users,navigate)
            result=metrics.report(elapsed,expected={200:args.users*2})
        elif phase=='availability':
            async def available(index,user):return await request(client,user,'GET',availability_path(user),metrics=metrics)
            _,elapsed=await burst(users,available)
            result=metrics.report(elapsed,expected={200:args.users})
        elif phase=='same_slot':
            candidates=[{**user,**tenants[0],'professional':tenants[0]['professional'],'day':tenants[0]['day']} for user in users]
            quotes=await prefetch(client,candidates)
            async def compete(index,user):
                return await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",body=body(quotes[index],index),metrics=metrics)
            responses,elapsed=await burst(candidates,compete)
            result=metrics.report(elapsed,write=True,expected={201:1,409:args.users-1})
            with sandbox.engine.connect() as db:
                actual=db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=CAST(:t AS uuid) AND professional_id=CAST(:p AS uuid) AND start_at=CAST(:start AS timestamptz) AND status='confirmed'"),
                    {'t':tenants[0]['tenant'],'p':tenants[0]['professional'],'start':quotes[0]['slots'][0]}).scalar_one()
            result['persisted_exact_slot_confirmations']=actual;result['pass'] &= actual==1
            winner=next((i for i,(code,_) in enumerate(responses) if code==201),None)
            if winner is not None:report['_winner']=(candidates[winner],body(quotes[winner],winner),responses[winner][1])
        elif phase=='different_days':
            # One professional on distinct dates: no legitimate same-day lock collision.
            candidates=[{**user,**tenants[1],'professional':tenants[1]['professional'],
                'day':(datetime.fromisoformat(tenants[1]['day'])+timedelta(days=i)).date().isoformat()} for i,user in enumerate(users[:30])]
            quotes=await prefetch(client,candidates)
            async def book_day(index,user):
                return await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",
                    body=body(quotes[index],1000+index),metrics=metrics)
            _,elapsed=await burst(candidates,book_day)
            result=metrics.report(elapsed,write=True,expected={201:len(candidates)})
            result['single_professional']=True;result['distinct_days']=min(30,args.users)
        elif phase=='distributed_bookings':
            candidates=[{**user,'day':(datetime.fromisoformat(tenants[0]['day'])+timedelta(days=32+i//100)).date().isoformat()}
                        for i,user in enumerate(users)]
            quotes=await prefetch(client,candidates)
            async def distributed(index,user):
                return await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",
                    body=body(quotes[index],2000+index),metrics=metrics)
            _,elapsed=await burst(candidates,distributed)
            result=metrics.report(elapsed,write=True,expected={201:args.users})
            result['independent_professional_dates']=len(candidates);result['companies']=min(20,args.users)
        elif phase=='tenants':
            user={**tenants[2],'cookie':''};foreign=tenants[3]
            path=availability_path({**user,'service':foreign['service']})
            code,_=await request(client,user,'GET',path,metrics=metrics)
            result={'cross_tenant_service_status':code,'pass':code in (404,422)}
            with sandbox.engine.connect() as db:
                counts={}
                for role,key in (('runtime','APP_DATABASE_URL'),('support','SUPPORT_DATABASE_URL')):
                    from sqlalchemy import create_engine
                    engine=create_engine(sandbox.roles[key].set(database=sandbox.name,drivername='postgresql+psycopg'))
                    try:
                        with engine.connect() as limited:counts[role]=limited.execute(text('SELECT count(*) FROM appointments')).scalar_one()
                    except DBAPIError as exc:
                        if getattr(exc.orig,'sqlstate',None)!='42501':raise
                        counts[role]='permission_denied'
                    finally:engine.dispose()
            result['unscoped_rls_appointment_counts']=counts;result['pass'] &= all(value in (0,'permission_denied') for value in counts.values())
        elif phase=='unknown_slugs':
            cache=redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
            try:
                before=await cache.dbsize()
                async def missing(index,user):return await request(client,user,'GET','/api/v1/public/sites/missing-qualification-'+str(index),metrics=metrics)
                _,elapsed=await burst(users,missing)
                after=await cache.dbsize()
            finally:await cache.aclose()
            result=metrics.report(elapsed,expected={404:args.users})
            result['redis_key_growth']=after-before;result['maximum_expected_key_growth']=2
            result['pass'] &= after-before<=2
        elif phase=='replay':
            if '_winner' not in report:
                user={**tenants[4],'cookie':''};quoted=(await prefetch(client,[user]))[0]
                payload=body(quoted,900000);code,original=await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",body=payload)
                if code!=201:raise RuntimeError('Replay setup confirmation failed')
            else:user,payload,original=report['_winner']
            copies=[{**user} for _ in range(10)]
            async def replay(index,current):return await request(client,current,'POST',f"/api/v1/public/sites/{current['slug']}/appointments",body=payload,metrics=metrics)
            responses,elapsed=await burst(copies,replay)
            result=metrics.report(elapsed,write=True,expected={201:10})
            result['same_response']=all(value==original for code,value in responses);result['pass'] &= result['same_response']
            # A valid signed nonce issued 11 minutes ago, with identical authority.
            # This clock fixture tests expiry recovery without sleeping ten minutes.
            old=expired_quote(payload['quote'],os.environ['_ORION_SYNTHETIC_SIGNING_KEY'])
            old_status,old_value=await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",body={**payload,'quote':old})
            result['expired_signed_recovery_status']=old_status
            result['expired_signed_recovery_same_response']=old_value==original
            result['expiry_clock_fixture_seconds']=660
            result['pass'] &= old_status==201 and old_value==original
            recovery_body={key:value for key,value in {**payload,'quote':old}.items() if key in ('quote','start_at','customer_phone')}
            recovered_status,recovered=await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments/recover",body=recovery_body)
            wrong_status,_=await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments/recover",
                body={**recovery_body,'customer_phone':'+5511000999999'})
            result['recovery_endpoint_status']=recovered_status
            result['recovery_endpoint_same_response']=recovered==original
            result['wrong_phone_recovery_status']=wrong_status
            result['pass'] &= recovered_status==200 and recovered==original and wrong_status in (404,409)
            fresh_user={**tenants[7],'cookie':''};fresh=(await prefetch(client,[fresh_user]))[0]
            expired_payload=body(fresh,930000)
            expired_payload['quote']=expired_quote(fresh['quote'],os.environ['_ORION_SYNTHETIC_SIGNING_KEY'])
            expired_status,_=await request(client,fresh_user,'POST',f"/api/v1/public/sites/{fresh_user['slug']}/appointments",body=expired_payload)
            missing_status,_=await request(client,fresh_user,'POST',f"/api/v1/public/sites/{fresh_user['slug']}/appointments/recover",
                body={key:value for key,value in expired_payload.items() if key in ('quote','start_at','customer_phone')})
            result['uncommitted_expired_quote_creation_status']=expired_status
            result['uncommitted_expired_quote_recovery_status']=missing_status
            result['pass'] &= expired_status==409 and missing_status==404
        elif phase=='combos':
            user={**tenants[5],'cookie':''};quoted=(await prefetch(client,[user],multi=True))[0]
            payload=body(quoted,910000);code,value=await request(client,user,'POST',f"/api/v1/public/sites/{user['slug']}/appointments",body=payload)
            result={'status':code,'minimum_price_cents':quoted.get('price_cents'),'duration_min':quoted.get('duration_min'),
                    'price_mode':quoted.get('price_mode'),'confirmed_price_cents':(value or {}).get('price_cents'),
                    'pass':code==201 and quoted.get('price_cents')==4500 and quoted.get('duration_min')==25 and quoted.get('price_mode')=='from' and (value or {}).get('price_mode')=='from'}
            if code==201:
                with sandbox.engine.connect() as db:
                    persisted=db.execute(text("""SELECT id,status,extract(epoch FROM end_at-start_at)/60 AS service_minutes,
                        extract(epoch FROM occupied_until-occupied_from)/60 AS occupied_minutes,commission_cents
                        FROM appointments WHERE tenant_id=CAST(:t AS uuid) AND protocol=:protocol"""),
                        {'t':user['tenant'],'protocol':value['protocol']}).mappings().one()
                    financial=db.execute(text('SELECT count(*) FROM financial_entries WHERE appointment_id=:id'),{'id':persisted['id']}).scalar_one()
                    items=db.execute(text('SELECT price_mode_snapshot,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min FROM appointment_items WHERE appointment_id=:id ORDER BY item_order'),{'id':persisted['id']}).mappings().all()
                result.update(persisted_service_minutes=int(persisted['service_minutes']),
                    persisted_occupied_minutes=int(persisted['occupied_minutes']),financial_entries_before_completion=financial,
                    commission_calculated_before_completion=persisted['commission_cents'] is not None,
                    immutable_item_snapshots=[dict(item) for item in items])
                result['pass'] &= (persisted['status']=='confirmed' and int(persisted['service_minutes'])==25
                    and int(persisted['occupied_minutes'])==35 and financial==0 and persisted['commission_cents'] is None
                    and len(items)==2 and {item['price_mode_snapshot'] for item in items}=={'fixed','from'})
        elif phase=='dependencies':continue  # Performed after the primary API drains.
        report['scenarios'][phase]=result
        write_report(args.output,report)
        await client.recycle()
    report.pop('_winner',None)


def write_report(path,report):
    safe={key:value for key,value in report.items() if not key.startswith('_')}
    temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(safe,indent=2,default=str),encoding='utf-8');temporary.replace(path)


def source_manifest():
    files=[]
    for folder in ('app','alembic'):
        files.extend(path for path in (ROOT/folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts)
    files.append(Path(__file__).resolve())
    files.append(ROOT/'scripts/qualification_process_limits.py')
    return {str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}


def expired_quote(token,signing_secret):
    raw,_=token.split('.')
    value=json.loads(base64.urlsafe_b64decode(raw+'='*((-len(raw))%4)))
    value['expires']=int(datetime.now(UTC).timestamp())-60
    encoded=base64.urlsafe_b64encode(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).rstrip(b'=')
    key=hmac.new(signing_secret.encode(),b'orion-public-quotes:v1',hashlib.sha256).digest()
    signature=base64.urlsafe_b64encode(hmac.new(key,encoded,hashlib.sha256).digest()).rstrip(b'=')
    return (encoded+b'.'+signature).decode()


async def dependency_checks(args,environment,tenant):
    """Real refused loopback sockets, isolated to this owned API's configuration.

    Shared PostgreSQL/Redis processes keep running, so parallel synthetic test
    databases are undisturbed. Restored configuration is checked in a new child.
    """
    results=[]
    for dependency in ('postgresql','redis'):
        for unavailable in (True,False):
            with socket.socket() as blocked:
                blocked.bind(('127.0.0.1',0));blocked_port=blocked.getsockname()[1]
                with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
                env=dict(environment);label=dependency+('-unavailable' if unavailable else '-recovered')
                stop=args.output.with_suffix('.'+label+'.stop');stats=args.output.with_suffix('.'+label+'.stats.json')
                env.update({'ORION_PUBLIC_QUALIFICATION_PORT':str(port),'ORION_PUBLIC_QUALIFICATION_STOP':str(stop),
                            'ORION_PUBLIC_QUALIFICATION_STATS':str(stats),'PANEL_PUBLIC_URL':f'https://127.0.0.1:{port}'})
                if unavailable:
                    for key in (ROLE_ENV if dependency=='postgresql' else ('REDIS_URL',)):
                        env[key]=make_url(env[key]).set(port=blocked_port).render_as_string(hide_password=False)
                log_path=args.output.with_suffix('.'+label+'.api.log')
                with log_path.open('wb') as output:
                    process=subprocess.Popen([sys.executable,'-m','scripts.qualification_public_booking','--owned-server'],cwd=ROOT,
                        env=env,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    try:
                        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=10,trust_env=False) as client:
                            for attempt in range(120):
                                if process.poll() is not None:raise RuntimeError('Dependency diagnostic API exited')
                                try:
                                    if (await client.get('/health')).status_code==200:break
                                except httpx.HTTPError:pass
                                await asyncio.sleep(.1)
                            else:raise RuntimeError('Dependency diagnostic startup timeout')
                            started=time.perf_counter();ready=await client.get(args.readiness_path)
                            user={**tenant,'cookie':''};code,_=await request(client,user,'GET',availability_path(user))
                            expected=503 if unavailable else 200
                            results.append({'dependency':dependency,'state':'unavailable' if unavailable else 'recovered',
                                'liveness_status':200,'readiness_status':ready.status_code,'availability_status':code,
                                'elapsed_seconds':round(time.perf_counter()-started,3),
                                'failure_method':'refused dedicated loopback socket' if unavailable else 'restored original local dependency URL',
                                'pass':ready.status_code==expected and code==expected})
                    finally:
                        stop.write_text('Stop only owned diagnostic server',encoding='utf-8')
                        try:await asyncio.to_thread(process.wait,90)
                        except subprocess.TimeoutExpired:process.terminate();await asyncio.to_thread(process.wait,10)
                        stop.unlink(missing_ok=True)
                raw=log_path.read_text(encoding='utf-8',errors='replace')
                for value in env.values():
                    if '://' in value or len(value)>35:raw=raw.replace(value,'[redacted-qualification-value]')
                log_path.write_text(raw,encoding='utf-8')
    return {'cases':results,'shared_infrastructure_stopped':False,'pass':all(result['pass'] for result in results)}


async def execute(args,sandbox,report):
    cache_url=local_url(os.environ['REDIS_URL'],database='15',redis_url=True)
    cache=redis.from_url(cache_url.render_as_string(hide_password=False),decode_responses=True)
    lock='orion:public-qualification:exclusive';owner=secrets.token_urlsafe(32)
    if await cache.dbsize()!=0 or not await cache.set(lock,owner,nx=True,ex=3600):
        raise RuntimeError('Qualification requires an empty, dedicated Redis DB15')
    api=None;api_limits=None;api_resumed=False;monitor_task=None;stop=asyncio.Event();env={}
    try:
        report['phase']='fixture';write_report(args.output,report)
        tenants=await asyncio.to_thread(fixture,sandbox)
        before=await asyncio.to_thread(history_digest,sandbox,tenants)
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        env=sandbox.environment();env.update({'REDIS_URL':cache_url.render_as_string(hide_password=False),
            'SUPPORT_API_TOKEN':secrets.token_urlsafe(48),'PUBLIC_SITE_SIGNING_KEY':secrets.token_urlsafe(48),
            'BUSINESS_PROFILES_ENABLED':'1','ONBOARDING_ENABLED':'1','PANEL_EMAIL_ENABLED':'0','META_LIVE_SEND':'0',
            'GOOGLE_LIVE_SYNC':'0','MARKETING_EXECUTION_ENABLED':'0','MARKETING_RECONCILIATION_ENABLED':'0',
            'GROQ_ENABLED':'0','GROQ_PROBE_ON_START':'0','QR_CHANNELS_ENABLED':'0','QR_AI_ENABLED':'0',
            'ORION_DB_POOL_SIZE':str(args.db_pool_size),'ORION_DB_MAX_OVERFLOW':str(args.db_max_overflow),
            'PANEL_PUBLIC_URL':f'https://127.0.0.1:{port}','PYTHONUNBUFFERED':'1'})
        assert 'PYTEST_CURRENT_TEST' not in env
        os.environ['_ORION_SYNTHETIC_SIGNING_KEY']=env['PUBLIC_SITE_SIGNING_KEY']
        stop_file=args.output.with_suffix('.stop-'+uuid4().hex)
        stats_file=args.output.with_suffix('.server-stats.json')
        env.update({'ORION_PUBLIC_QUALIFICATION_PORT':str(port),
            'ORION_PUBLIC_QUALIFICATION_STOP':str(stop_file),'ORION_PUBLIC_QUALIFICATION_STATS':str(stats_file)})
        with args.output.with_suffix('.api.log').open('wb') as output:
            report['phase']='owned_api_start';write_report(args.output,report)
            limited=args.api_cpus is not None or args.api_memory_mb is not None
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            if limited and os.name=='nt':
                from scripts.qualification_process_limits import CREATE_SUSPENDED
                flags|=CREATE_SUSPENDED
            api=subprocess.Popen([sys.executable,'-m','scripts.qualification_public_booking','--owned-server'],cwd=ROOT,env=env,
                stdout=output,stderr=subprocess.STDOUT,creationflags=flags)
            api_resumed=not(limited and os.name=='nt')
            if limited:
                from scripts.qualification_process_limits import OwnedApiLimits,resume_owned_child
                api_limits=OwnedApiLimits(api.pid,cpus=args.api_cpus,memory_mb=args.api_memory_mb)
                report['api_resource_envelope']=api_limits.snapshot()
                if os.name=='nt':resume_owned_child(api.pid);api_resumed=True
            async with PublicHttpClient(origin,args.users,args.timeout) as client:
                for attempt in range(100):
                    if api.poll() is not None:raise RuntimeError('Owned API exited during startup')
                    try:
                        response=await client.get('/health')
                        if response.status_code==200:break
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.25)
                else:raise RuntimeError('Owned API readiness timeout')
                if api_limits:report['api_resource_envelope']=api_limits.verify_running_tree()
                report['api']={'host':'127.0.0.1','port':port,'pid':api.pid,'workers':args.workers,
                    'runtime_pool_size':args.db_pool_size,'runtime_max_overflow':args.db_max_overflow,'pytest_nullpool':False,
                    'http_max_connections':args.users,'http_automatic_retries':0,'forwarded_ips_fabricated':False,
                    'http_generator_pools':client.configuration(),
                    'external_provider_credentials_inherited':False,'external_workers_started':False}
                monitor_task=asyncio.create_task(monitor(sandbox,cache,api,report['resource_samples'],stop))
                await run_phases(args,sandbox,tenants,client,report)
            report['phase']='drain';stop_file.write_text('Stop only this owned synthetic API',encoding='utf-8')
            code=await asyncio.to_thread(api.wait,90)
            if code!=0:raise RuntimeError('Owned API failed clean drain; no integrity PASS')
            api=None;stop_file.unlink(missing_ok=True)
            if api_limits:report['api_resource_envelope']=api_limits.snapshot()
            if stats_file.exists():report['actual_pool']=json.loads(stats_file.read_text(encoding='utf-8'))
            stop.set();await monitor_task;monitor_task=None
        if 'dependencies' in args.phases:
            report['phase']='dependencies';report['scenarios']['dependencies']=await dependency_checks(args,env,tenants[10])
        report['integrity']=await asyncio.to_thread(integrity,sandbox,tenants,before)
        pool=report.get('actual_pool',{})
        report['pool_contract_pass']=(pool.get('pool_class')=='AsyncAdaptedQueuePool'
            and pool.get('checked_out_after_drain')==0
            and pool.get('peak_checked_out',0)<=args.db_pool_size+args.db_max_overflow)
        report['resource_measurement_pass']=any('api_rss_bytes' in sample and 'database_connections' in sample
            and 'redis_used_memory_bytes' in sample for sample in report['resource_samples'])
        report['pass']=(all(result['pass'] for result in report['scenarios'].values()) and report['integrity']['pass']
            and report['pool_contract_pass'] and (args.users<1000 or report['resource_measurement_pass']))
        report['phase']='complete'
    finally:
        stop.set()
        if monitor_task:await monitor_task
        if api is not None and api.poll() is None:
            if not api_resumed:api.terminate()
            stop_file.write_text('Stop only this owned synthetic API',encoding='utf-8')
            try:await asyncio.to_thread(api.wait,90)
            except subprocess.TimeoutExpired:
                api.terminate();await asyncio.to_thread(api.wait,10)
            stop_file.unlink(missing_ok=True)
        # Preserve integrity/pool evidence even when a measured scenario or its
        # later quote preparation fails. Failed HTTP status/latency stays failed.
        if 'tenants' in locals() and 'before' in locals():
            report['integrity']=await asyncio.to_thread(integrity,sandbox,tenants,before)
        if 'stats_file' in locals() and stats_file.exists():
            report['actual_pool']=json.loads(stats_file.read_text(encoding='utf-8'))
        if api_limits:
            report['api_resource_envelope']=api_limits.snapshot();api_limits.close()
        # Only keys created inside this dedicated DB by this run are removed.
        # This DB must be dedicated: no sessions, existing bookings or remote data.
        if await cache.get(lock)==owner:
            keys=[key async for key in cache.scan_iter(match='orion:public*')]
            if keys:await cache.delete(*keys)
        await cache.aclose()
        os.environ.pop('_ORION_SYNTHETIC_SIGNING_KEY',None)
        for path in args.output.parent.glob(args.output.stem+'*.api.log'):
            raw=path.read_text(encoding='utf-8',errors='replace')
            for value in env.values():
                if '://' in value or len(value)>35:raw=raw.replace(value,'[redacted-qualification-value]')
            path.write_text(raw,encoding='utf-8')


def main(argv=None):
    args=parser().parse_args(argv)
    if not args.confirm_disposable_local:raise SystemExit('Explicit --confirm-disposable-local is required')
    if args.output.exists():raise SystemExit('Refuse to overwrite an evidence report')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    roles={key:os.environ[key] for key in ROLE_ENV};owner=os.environ['ORION_QUALIFICATION_OWNER_URL']
    # Validate every URL before any subprocess or database mutation.
    local_url(owner,role='postgres',database='postgres')
    for key,role in ROLE_ENV.items():local_url(roles[key],role=role)
    local_url(os.environ['REDIS_URL'],database='15',redis_url=True)
    if (args.users==1000 or args.api_cpus is not None or args.api_memory_mb is not None) and importlib.util.find_spec('psutil') is None:
        raise SystemExit('Qualification resource measurement/limits require psutil')
    complete_scope=set(args.phases)==set(PHASES)
    report={'schema':1,'run_kind':('1000_simultaneous_public_burst' if complete_scope else 'partial_1000_diagnostic') if args.users==1000 else 'smoke_only',
        'qualification_scope_complete':complete_scope,'qualified':False,
        'api_resource_envelope':{'requested_cpus':args.api_cpus,'requested_memory_mb':args.api_memory_mb,
            'applied':False,'railway_or_container_equivalence':False},
        'latency_clock':'time.perf_counter (high resolution, monotonic)',
        'started_utc':datetime.now(UTC).isoformat(),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
        'users':args.users,'targets_declared_before_run':TARGETS,'scenarios':{},'resource_samples':[],
        'machine':{'platform':platform.platform(),'python':platform.python_version(),'logical_cpus':os.cpu_count(),
            'production_equivalence':False,'limitation':'Host resources differ from Railway 2vCPU/1GB; finite burst is not sustained production capacity'},
        'real_customer_data_used':False,'provider_messages_sent':0,'production_requests_sent':0,'pass':False,'phase':'setup'}
    report['source_manifest_before']=source_manifest()
    write_report(args.output,report)
    try:
        with DisposableDatabase(owner,roles) as sandbox:
            report['phase']='migrations';report['disposable_database']=sandbox.name;write_report(args.output,report)
            sandbox.migrate()
            asyncio.run(execute(args,sandbox,report))
    except Exception as exc:
        report.update(pass_=False,error_type=type(exc).__name__,error=str(exc) if '://' not in str(exc) else 'Local operation failed; credentials omitted')
        report['pass']=False
    report['source_manifest_after']=source_manifest()
    report['source_unchanged_during_run']=report['source_manifest_before']==report['source_manifest_after']
    report['pass'] &= report['source_unchanged_during_run']
    report['qualified']=bool(report['pass'] and args.users==1000 and complete_scope)
    report['finished_utc']=datetime.now(UTC).isoformat();write_report(args.output,report)
    print(json.dumps({'phase':report['phase'],'pass':report['pass'],'output':str(args.output)}))
    return 0 if report['pass'] else 1


def owned_server():
    """Private child entry point; file shutdown permits graceful Windows drain."""
    for key,role in ROLE_ENV.items():
        url=local_url(os.environ[key],role=role)
        if not (url.database or '').startswith('orion_qualification_'):
            raise SystemExit('Owned API requires disposable qualification database')
    local_url(os.environ['REDIS_URL'],database='15',redis_url=True)
    if 'PYTEST_CURRENT_TEST' in os.environ:raise SystemExit('Real runtime pool required')
    import uvicorn
    from app.main import app
    from app.database import get_engine
    from sqlalchemy import event
    stop_file=Path(os.environ['ORION_PUBLIC_QUALIFICATION_STOP'])
    stats_file=Path(os.environ['ORION_PUBLIC_QUALIFICATION_STATS'])
    config=uvicorn.Config(app,host='127.0.0.1',port=int(os.environ['ORION_PUBLIC_QUALIFICATION_PORT']),
                          access_log=False,timeout_graceful_shutdown=60)
    server=uvicorn.Server(config)
    pool=get_engine().sync_engine.pool
    observations={'checkout_count':0,'checkin_count':0,'peak_checked_out':0,'current_checked_out':0}
    @event.listens_for(pool,'checkout')
    def checkout(*_):
        observations['checkout_count']+=1;observations['current_checked_out']+=1
        observations['peak_checked_out']=max(observations['peak_checked_out'],observations['current_checked_out'])
    @event.listens_for(pool,'checkin')
    def checkin(*_):observations['checkin_count']+=1;observations['current_checked_out']-=1
    async def serve():
        task=asyncio.create_task(server.serve())
        while not task.done():
            if stop_file.exists():server.should_exit=True
            await asyncio.sleep(.1)
        await task
        engine=get_engine();pool=engine.pool
        from app.public_observability import runtime_snapshot
        runtime=runtime_snapshot()
        stats={'pool_class':type(pool).__name__,'pool_size':pool.size(),
               'checked_out_after_drain':pool.checkedout(),'overflow_after_drain':pool.overflow(),
               'status_after_drain':pool.status(),'graceful_shutdown':True,**observations,
               'pool_wait_latency_directly_instrumented':runtime.get('pool_wait',{}).get('available',False),
               'public_runtime':runtime,'event_loop':type(asyncio.get_running_loop()).__module__+'.'+type(asyncio.get_running_loop()).__name__}
        stats_file.write_text(json.dumps(stats,indent=2),encoding='utf-8')
        await engine.dispose()
    if sys.platform!='win32' and importlib.util.find_spec('uvloop') is not None:
        import uvloop
        uvloop.run(serve())
    else:asyncio.run(serve())


if __name__=='__main__':
    if sys.argv[1:]==['--owned-server']:owned_server()
    else:raise SystemExit(main())
