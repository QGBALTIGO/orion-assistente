"""Pure destination validation; no DNS, Redis or production connections."""
import pytest

from tests.redis_test_scope import communication_redis_url,MANDATORY_COMPOSE_FLAGS


def compose_environment():
    return {**{name:'1' for name in MANDATORY_COMPOSE_FLAGS},
        'ORION_COMPOSE_TEST_INFRA':'1','REDIS_URL':'redis://redis:6379/15'}


@pytest.mark.parametrize('url',['redis://127.0.0.1:55440/15','redis://localhost:6379/15','redis://[::1]:6379/15'])
def test_designated_loopback_works_without_compose_optin(url):
    assert communication_redis_url(url,{})==url


def test_exact_compose_service_requires_its_own_test_optin():
    url='redis://redis:6379/15'
    assert communication_redis_url(url,compose_environment())==url
    with pytest.raises(ValueError):communication_redis_url(url,{})


@pytest.mark.parametrize('flag',(*MANDATORY_COMPOSE_FLAGS,'ORION_COMPOSE_TEST_INFRA'))
@pytest.mark.parametrize('value',[None,'0'])
def test_compose_alias_refuses_missing_or_disabled_test_authority(flag,value):
    environment=compose_environment()
    if value is None:environment.pop(flag)
    else:environment[flag]=value
    with pytest.raises(ValueError):communication_redis_url(environment['REDIS_URL'],environment)


def test_compose_alias_cannot_override_the_process_redis_destination():
    environment=compose_environment();environment['REDIS_URL']='redis://redis:6379/0'
    with pytest.raises(ValueError):communication_redis_url('redis://redis:6379/15',environment)


@pytest.mark.parametrize('url',[
    'redis://cache.example.com:6379/15','redis://redis.internal:6379/15',
    'redis://redis.evil.example:6379/15','redis://postgres:6379/15',
    'rediss://redis:6379/15','https://localhost:6379/15',
    'redis://redis:6380/15','redis://redis/15',
    'redis://localhost:0/15','redis://localhost:65536/15','redis://localhost:bad/15',
    'redis://localhost:6379/0','redis://redis:6379/0',
    'redis://localhost:6379/15?db=0','redis://localhost:6379/15#fragment',
    'redis://user:secret@localhost:6379/15','redis://:secret@redis:6379/15',
    '\nredis://localhost:6379/15',
])
def test_unsafe_or_ambiguous_test_destinations_are_rejected(url):
    environment=compose_environment();environment['REDIS_URL']=url
    with pytest.raises(ValueError):communication_redis_url(url,environment)
