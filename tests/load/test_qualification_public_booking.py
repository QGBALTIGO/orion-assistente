"""Safety and accounting of the harness; never evidence of app capacity."""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest

from scripts import qualification_public_booking as load


def test_output_requires_explicit_local_authorization_before_mutation(tmp_path):
    with pytest.raises(SystemExit,match='confirm-disposable-local'):
        load.main(['--output',str(tmp_path/'result.json')])
    assert not (tmp_path/'result.json').exists()


def test_non_loopback_owner_is_rejected_before_report_or_subprocess(tmp_path,monkeypatch):
    monkeypatch.setenv('ORION_QUALIFICATION_OWNER_URL','postgresql://postgres:p@production.invalid/postgres')
    for key,role in load.ROLE_ENV.items():monkeypatch.setenv(key,f'postgresql://{role}:p@127.0.0.1/synthetic_e2e')
    monkeypatch.setenv('REDIS_URL','redis://127.0.0.1/15')
    monkeypatch.setattr(load.subprocess,'check_output',lambda *a,**k:pytest.fail('No subprocess before target validation'))
    with pytest.raises(ValueError,match='loopback'):
        load.main(['--confirm-disposable-local','--output',str(tmp_path/'result.json')])
    assert not (tmp_path/'result.json').exists()


@pytest.mark.asyncio
async def test_burst_releases_all_1000_operations_and_drains_them():
    active=maximum=completed=0
    release=asyncio.Event()
    async def operation(index,item):
        nonlocal active,maximum,completed
        active+=1;maximum=max(maximum,active)
        if active==1000:release.set()
        await release.wait()
        active-=1;completed+=1
        return index
    values,elapsed=await asyncio.wait_for(load.burst(list(range(1000)),operation),3)
    assert values==list(range(1000)) and maximum==completed==1000 and active==0 and elapsed>0


@pytest.mark.asyncio
async def test_burst_cancellation_drains_every_request():
    active=0;ready=asyncio.Event()
    async def operation(index,item):
        nonlocal active
        active+=1
        if active==10:ready.set()
        try:await asyncio.Event().wait()
        finally:active-=1
    task=asyncio.create_task(load.burst(list(range(10)),operation))
    await ready.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert active==0


def test_cookie_capture_preserves_independent_public_clients():
    first=httpx.Response(200,headers={'set-cookie':'orion_public=first; Path=/; HttpOnly; SameSite=Lax'})
    second=httpx.Response(200,headers={'set-cookie':'orion_public=second; Path=/; HttpOnly; SameSite=Lax'})
    assert load.cookie_from(first)=='orion_public=first'
    assert load.cookie_from(second)=='orion_public=second'
    assert load.cookie_from(httpx.Response(200),'orion_public=first')=='orion_public=first'


def test_same_slot_pass_requires_one_success_and_999_conflicts():
    metrics=load.Metrics()
    metrics.record(201,.1)
    for _ in range(999):metrics.record(409,.2)
    result=metrics.report(.3,write=True,expected={201:1,409:999})
    assert result['pass'] and result['statuses']=={'201':1,'409':999}
    metrics.rows[-1]['status']=503
    result=metrics.report(.3,write=True,expected={201:1,409:999})
    assert not result['pass'] and not result['status_pass']


def test_transport_failure_is_never_hidden_as_expected_conflict():
    metrics=load.Metrics();metrics.record('transport_error',1)
    result=metrics.report(1,expected={409:1})
    assert not result['status_pass'] and not result['pass']


def test_report_does_not_include_private_replay_payloads(tmp_path):
    path=tmp_path/'result.json'
    load.write_report(path,{'pass':False,'_winner':{'quote':'private','phone':'private'},'scenarios':{}})
    assert json.loads(path.read_text())=={'pass':False,'scenarios':{}}


def test_contact_is_synthetic_and_distinct():
    quoted={'quote':'synthetic','slots':['2030-01-01T08:00:00-03:00']}
    one,two=load.body(quoted,1),load.body(quoted,2)
    assert one['customer_phone']!=two['customer_phone']
    assert one['customer_phone'].startswith('+5511000') and one['confirmed'] is True
    assert set(one)=={'quote','start_at','customer_name','customer_phone','confirmed'}


def test_expired_clock_fixture_preserves_authority_and_valid_signature():
    secret='synthetic-server-only-signing-key-32-characters'
    original={'nonce':'a'*32,'professional':'synthetic-professional','expires':int(time.time())+600,'fingerprint':'b'*64}
    raw=base64.urlsafe_b64encode(json.dumps(original).encode()).decode().rstrip('=')
    token=load.expired_quote(raw+'.unused-original-signature',secret)
    encoded,signature=token.encode().split(b'.')
    current=json.loads(base64.urlsafe_b64decode(encoded+b'='*((-len(encoded))%4)))
    assert current['expires']<time.time() and current['nonce']==original['nonce']
    assert current['fingerprint']==original['fingerprint'] and current['professional']==original['professional']
    key=hmac.new(secret.encode(),b'orion-public-quotes:v1',hashlib.sha256).digest()
    assert signature==base64.urlsafe_b64encode(hmac.new(key,encoded,hashlib.sha256).digest()).rstrip(b'=')
    assert original['expires']>time.time()


@pytest.mark.asyncio
async def test_partitioned_client_keeps_real_cookie_sticky_and_closes_every_pool():
    made=[]
    class Client:
        def __init__(self,**kwargs):self.calls=[];self.closed=0;made.append(self)
        async def request(self,method,path,**kwargs):self.calls.append((method,path,kwargs));return httpx.Response(200)
        async def aclose(self):self.closed+=1
    user={'Cookie':'orion_public_browser=synthetic-first-cookie'}
    index=load.PublicHttpClient.pool_index(user)
    assert index==load.PublicHttpClient.pool_index(user)
    async with load.PublicHttpClient('http://127.0.0.1',1000,60,factory=Client) as client:
        await client.request('GET','/one',headers=user)
        await client.request('GET','/two',headers=user)
        assert len(made[index].calls)==2 and sum(len(item.calls) for item in made)==2
        await client.recycle()
        assert len(made)==32 and all(item.closed==1 for item in made[:16])
        assert client.configuration()['actual_global_inflight_limit']==1000
    assert all(item.closed==1 for item in made)
