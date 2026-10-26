"""Untrusted public quote inputs, explicit consent, and fail-closed quotas."""
import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.public_site_api import PublicBooking, checked_slug, issue_quote, public_limit, read_quote
from app.service_packages_api import AddonsSave, PackageCreate


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setenv('PUBLIC_SITE_SIGNING_KEY','unit-only-public-signing-key-of-at-least-32-characters')
    now=datetime.now(timezone.utc)
    selection={'base_id':uuid4(),'addon_ids':[uuid4()],'fingerprint':'f'*64}
    quote=issue_quote('public-site',uuid4(),now.date(),selection,now)
    return quote,now,selection


def test_quote_signature_scope_expiry_and_no_private_authority(signed):
    token,now,selection=signed
    data=read_quote(token,'public-site',now+timedelta(seconds=599))
    assert data['service']==selection['base_id'] and data['addons']==selection['addon_ids']
    raw=token.split('.')[0]
    body=json.loads(base64.urlsafe_b64decode(raw+'='*((-len(raw))%4)))
    assert set(body)=={'v','slug','professional','service','addons','day','fingerprint','expires','nonce'}
    for candidate,slug,stamp,expected in ((token+'x','public-site',now,422),(token,'other-site',now,422),
                                        (token,'public-site',now+timedelta(seconds=600),409),('not-a-token','public-site',now,422)):
        with pytest.raises(HTTPException) as exc: read_quote(candidate,slug,stamp)
        assert exc.value.status_code==expected


def test_customer_must_confirm_and_cannot_submit_price_or_tenant(signed):
    token,now,_=signed
    payload={'quote':token,'start_at':now.isoformat(),'customer_name':'Cliente sintético','customer_phone':'(11) 98765-4321','confirmed':True}
    assert PublicBooking(**payload).customer_phone=='+5511987654321'
    for changes in ({'confirmed':False},{'confirmed':'true'},{'price_cents':1},{'tenant_id':str(uuid4())},{'start_at':'2030-01-01T12:00:00'},{'customer_phone':'123'}):
        with pytest.raises(ValidationError): PublicBooking(**{**payload,**changes})


def test_public_slug_and_package_selections_reject_bad_inputs():
    for slug in ('a','../site','SITE','site_name','<script>','a'*81):
        with pytest.raises(HTTPException):checked_slug(slug)
    service=uuid4()
    with pytest.raises(ValidationError): PackageCreate(name='Combo',service_ids=[service,service],price_cents=100,idempotency_key='test-idem')
    with pytest.raises(ValidationError): AddonsSave(expected_version=0,items=[{'addon_service_id':uuid4()} for _ in range(6)])


@pytest.mark.asyncio
async def test_public_rate_limit_fails_closed_and_does_not_trust_forwarded_headers(monkeypatch):
    class Cache:
        def __init__(self): self.keys=[];self.count=0;self.closed=0
        async def eval(self,script,n,key,window):
            assert "EXPIRE" in script and n==1 and window==300
            self.keys.append(key);self.count+=1;return self.count
        async def aclose(self):self.closed+=1
    cache=Cache();monkeypatch.setattr('app.public_site_api._redis',lambda:cache)
    def request(forwarded):return Request({'type':'http','headers':[(b'x-forwarded-for',forwarded.encode())],'client':('127.0.0.1',1234)})
    await public_limit(request('198.51.100.1'),'site','unit',1,300)
    with pytest.raises(HTTPException) as exc:await public_limit(request('203.0.113.2'),'site','unit',1,300)
    assert exc.value.status_code==429 and exc.value.headers['Retry-After']=='300'
    assert cache.keys[0]==cache.keys[1] and cache.closed==2
    class Broken:
        async def eval(self,*args):raise ConnectionError('local test cache unavailable')
        async def aclose(self):pass
    monkeypatch.setattr('app.public_site_api._redis',lambda:Broken())
    with pytest.raises(HTTPException) as exc:await public_limit(request('x'),'site','unit')
    assert exc.value.status_code==503


def test_multiservice_signed_quote_binds_every_service_and_expires(monkeypatch):
    monkeypatch.setenv('PUBLIC_SITE_SIGNING_KEY','unit-only-public-signing-key-of-at-least-32-characters')
    day=datetime.now(timezone.utc)
    services=[uuid4() for _ in range(3)]
    professional=uuid4()
    quote=issue_quote('public-site',professional,day.date(),
                      {'service_ids':services,'fingerprint':'a'*64},day)
    payload=read_quote(quote,'public-site',day)
    assert payload['v']==2
    assert payload['services']==services
    assert payload['professional']==professional
    assert payload['fingerprint']=='a'*64
    assert payload['day']==day.date()
    assert 'service' not in payload and 'addons' not in payload
    for value,name,clock,status in [
        (quote+'x','public-site',day,422),
        (quote,'another-site',day,422),
        (quote,'public-site',day+timedelta(seconds=601),409),
    ]:
        with pytest.raises(HTTPException) as exc:
            read_quote(value,name,clock)
        assert exc.value.status_code==status
    # The signed transport accepts only bounded, unique, type-safe IDs.
    for invalid in [services[:1],services+[uuid4() for _ in range(4)],services[:2]+[services[1]]]:
        malformed=issue_quote('public-site',professional,day.date(),
                              {'service_ids':invalid,'fingerprint':'a'*64},day)
        with pytest.raises(HTTPException) as exc:
            read_quote(malformed,'public-site',day)
        assert exc.value.status_code==422
