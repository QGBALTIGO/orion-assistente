"""Anonymous browser identity and bounded request-local cache semantics."""
import asyncio
from http.cookies import SimpleCookie
from uuid import uuid4

from fastapi import HTTPException, Response
import pytest
from starlette.requests import Request

from app import public_site_runtime as runtime
from app import public_site_api

KEY = b"synthetic-browser-hmac-key-at-least-32-characters"


def request(cookie=None):
    headers = [(b"cookie", (runtime.COOKIE + "=" + cookie).encode())] if cookie else []
    return Request({"type": "http", "scheme": "https", "path": "/s/synthetic", "headers": headers,
                    "client": ("127.0.0.1", 1234), "server": ("test", 443)})


def cookie_from(response):
    parsed = SimpleCookie(response.headers["set-cookie"])
    return parsed[runtime.COOKIE].value, parsed[runtime.COOKIE]


def test_signed_browser_survives_reopening_and_bad_tokens_cannot_authorize_creation():
    response = Response()
    first = runtime.browser_identity(request(), KEY, response)
    token, cookie = cookie_from(response)
    assert runtime.browser_identity(request(token), KEY, required=True) == first
    assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "lax"
    for invalid in (None, "a" * 48 + "." + "b" * 64, token[:-1] + "x"):
        with pytest.raises(HTTPException) as error:
            runtime.browser_identity(request(invalid), KEY, required=True)
        assert error.value.status_code == 428
    with pytest.raises(HTTPException):
        runtime.browser_identity(request(token), b"other-independent-synthetic-key", required=True)
    replacement = Response()
    replaced = runtime.browser_identity(request(token[:-1] + "x"), KEY, replacement)
    assert replaced != first and cookie_from(replacement)[0] != token


async def test_creation_budgets_partition_unverified_contact_by_browser_and_resolved_tenant(monkeypatch):
    class Cache:
        def __init__(self): self.calls = []; self.allowed = 1
        async def eval(self, script, numkeys, *args):
            self.calls.append((script, numkeys, args))
            return self.allowed
    cache = Cache()
    monkeypatch.setattr(runtime, "request_cache", lambda request, factory: (cache, False))
    tenant = uuid4()
    phone = "+5511987654321"
    for actor in ("first-browser", "other-browser"):
        await runtime.creation_limit(request(), tenant, actor, phone, "a" * 32, lambda: cache)
    first, second = cache.calls
    assert first[1] == second[1] == 2
    assert not set(first[2][:2]) & set(second[2][:2])
    assert first[2][0] == runtime.quota_key("tenant:" + str(tenant), "new-browser", "first-browser")
    assert first[2][1] == runtime.quota_key("tenant:" + str(tenant), "new-contact-browser", "first-browser|" + phone)
    assert "ZADD" in first[0] and "NX" in first[0] and "EXPIRE" in first[0]
    cache.allowed = 0
    with pytest.raises(HTTPException) as error:
        await runtime.creation_limit(request(), tenant, "first-browser", phone, "b" * 32, lambda: cache)
    assert error.value.status_code == 429


async def test_catalogue_singleflight_and_deep_copies_preserve_cached_value():
    tenant = uuid4()
    key = (str(tenant), 1, "America/Sao_Paulo", 0)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def build():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"services": [{"name": "Publicado"}]}
    tasks = [asyncio.create_task(runtime.cached_catalogue(key, build)) for _ in range(30)]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        release.set()
        values = await asyncio.gather(*tasks)
        assert calls == 1
        values[0]["services"][0]["name"] = "Mudança do consumidor"
        again = await runtime.cached_catalogue(key, build)
        assert again == {"services": [{"name": "Publicado"}]}
        # The operational version participates in the key; mutation is visible
        # immediately without waiting for the visual cache retention TTL.
        release.set()
        await runtime.cached_catalogue((*key[:3], 1), build)
        assert calls == 2
    finally:
        release.set()
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        runtime.clear_catalogue_cache(tenant)


async def test_cancelled_cache_builder_does_not_leave_poisoned_singleflight():
    tenant = uuid4()
    key = (str(tenant), 1, "America/Sao_Paulo", 0)
    entered = asyncio.Event()
    async def blocked():
        entered.set()
        await asyncio.Event().wait()
    creator = asyncio.create_task(runtime.cached_catalogue(key, blocked))
    await asyncio.wait_for(entered.wait(), timeout=1)
    creator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creator
    async def recovered(): return {"ok": True}
    try:
        assert await asyncio.wait_for(runtime.cached_catalogue(key, recovered), timeout=1) == {"ok": True}
    finally:
        runtime.clear_catalogue_cache(tenant)


async def test_cache_capacity_is_bounded_and_failures_are_not_cached(monkeypatch):
    tenant = uuid4()
    monkeypatch.setattr(runtime, "CACHE_SIZE", 3)
    async def build(): return {"ok": True}
    for version in range(10):
        await runtime.cached_catalogue((str(tenant), version, "UTC", 0), build)
    cache = runtime._caches[asyncio.get_running_loop()]
    assert len(cache) <= 3
    key = (str(tenant), 99, "UTC", 0)
    async def broken(): raise ValueError("synthetic failed build")
    with pytest.raises(ValueError):
        await runtime.cached_catalogue(key, broken)
    try:
        assert await runtime.cached_catalogue(key, build) == {"ok": True}
    finally:
        runtime.clear_catalogue_cache(tenant)


async def test_builder_deadline_cannot_cancel_independent_existing_waiter():
    tenant=uuid4();key=(str(tenant),1,'UTC',0)
    entered,waiting=asyncio.Event(),asyncio.Event()
    async def abandoned():
        entered.set()
        await asyncio.Event().wait()
    async def replacement():
        return {'services':[{'name':'Published'}]}
    async def creator():
        async with asyncio.timeout(.1):
            return await runtime.cached_catalogue(key,abandoned)
    async def follower():
        waiting.set()
        async with asyncio.timeout(1):
            return await runtime.cached_catalogue(key,replacement)
    owner=asyncio.create_task(creator())
    await entered.wait()
    waiter=asyncio.create_task(follower())
    await waiting.wait()
    try:
        with pytest.raises(TimeoutError):await owner
        assert await waiter=={'services':[{'name':'Published'}]}
        assert await runtime.cached_catalogue(key,replacement)=={'services':[{'name':'Published'}]}
    finally:
        runtime.clear_catalogue_cache(tenant)


async def test_shared_network_fuses_are_separate_from_browser_limits_and_configurable(monkeypatch):
    calls=[]
    async def limit(*args,**kwargs):calls.append((args,kwargs))
    monkeypatch.setattr(public_site_api,'public_limit',limit)
    monkeypatch.delenv('PUBLIC_NETWORK_READ_LIMIT',raising=False)
    monkeypatch.delenv('PUBLIC_NETWORK_WRITE_LIMIT',raising=False)
    await public_site_api.public_network_limit(request())
    await public_site_api.public_network_limit(request(),write=True)
    assert calls[0][0][1:]==('network','read',120000,300)
    assert calls[1][0][1:]==('network','write',12000,300)
    monkeypatch.setenv('PUBLIC_NETWORK_READ_LIMIT','200000')
    await public_site_api.public_network_limit(request())
    assert calls[-1][0][3]==200000
    for bad in ('-1','0','999','1000001',' 120000','1.2','abc'):
        monkeypatch.setenv('PUBLIC_NETWORK_READ_LIMIT',bad)
        with pytest.raises(RuntimeError):await public_site_api.public_network_limit(request())
