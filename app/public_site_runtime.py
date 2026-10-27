"""Bounded public browsing identities, quotas and short-lived catalogue cache.

An anonymous browser ID is an abuse budget, never account/tenant authority.
Only verified server-resolved tenants may create tenant-specific Redis keys.
"""
import asyncio
import copy
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import OrderedDict
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from redis.exceptions import RedisError

COOKIE = 'orion_public_browser'
_caches = WeakKeyDictionary()
_flights = WeakKeyDictionary()
CACHE_TTL = 60.0
CACHE_SIZE = 128


def _signature(raw, key):
    return hmac.new(key, b'orion-public-browser:v1:' + raw.encode(), hashlib.sha256).hexdigest()


def browser_identity(request, key, response=None, *, required=False):
    """Only server-signed IDs split NAT budgets; none grants booking authority."""
    if response is not None:
        response.headers['Cache-Control']='no-store'
    remembered = request.scope.get('orion.public_browser')
    if remembered:
        token=request.scope.get('orion.public_browser_cookie')
        if token and response is not None:
            response.set_cookie(COOKIE,token,max_age=7*24*3600,httponly=True,
                                secure=request.url.scheme=='https',samesite='lax',path='/')
        return remembered
    token = request.cookies.get(COOKIE, '')
    valid = False
    if re.fullmatch(r'[0-9a-f]{48}\.[0-9a-f]{64}', token):
        raw, signature = token.split('.')
        valid = hmac.compare_digest(_signature(raw, key), signature)
    if not valid:
        if required:
            raise HTTPException(428, {'code': 'browser_session_required',
                'message': 'Abra novamente a página da barbearia para continuar com segurança.', 'retryable': False})
        raw = secrets.token_hex(24)
        token = raw + '.' + _signature(raw, key)
        request.scope['orion.public_browser_cookie']=token
        if response is not None:
            response.set_cookie(COOKIE, token, max_age=7 * 24 * 3600, httponly=True,
                                secure=request.url.scheme == 'https', samesite='lax', path='/')
    request.scope['orion.public_browser'] = raw
    return raw


def request_cache(request, factory):
    state = getattr(request.scope.get('app'), 'state', None)
    shared = getattr(state, 'panel_session_redis', None)
    return (shared, False) if shared is not None else (factory(), True)


def quota_key(scope, category, subject):
    digest = hashlib.sha256((scope + '|' + subject).encode()).hexdigest()
    return 'orion:public-limit:' + category + ':' + digest


# Check every budget before writing any of them. A replay of the same signed
# operation is not a new booking. No global budget is keyed by an unverified phone.
CREATION_QUOTA_LUA = """
local now=tonumber(ARGV[1]); local window=tonumber(ARGV[2]); local member=ARGV[3]
for i,key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE',key,'-inf',now-window)
  if not redis.call('ZSCORE',key,member) and redis.call('ZCARD',key)>=tonumber(ARGV[3+i]) then
    return 0
  end
end
for _,key in ipairs(KEYS) do
  redis.call('ZADD',key,'NX',now,member); redis.call('EXPIRE',key,window)
end
return 1
"""


async def creation_limit(request, tenant, actor, phone, nonce, factory):
    cache, owned = request_cache(request, factory)
    scope = 'tenant:' + str(tenant)
    keys = [quota_key(scope, 'new-browser', actor),
            quota_key(scope, 'new-contact-browser', actor + '|' + phone)]
    try:
        member = hashlib.sha256((nonce + '|' + phone).encode()).hexdigest()
        allowed = await cache.eval(CREATION_QUOTA_LUA, 2, *keys,
                                   int(time.time()), 3600, member, 12, 4)
        if not allowed:
            raise HTTPException(429, {'code': 'booking_rate_limited',
                'message': 'Este navegador realizou muitas tentativas. Aguarde ou fale com a barbearia.',
                'retryable': True}, headers={'Retry-After': '3600'})
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(503, {'code': 'dependency_unavailable',
            'message': 'Agendamento temporariamente indisponível. Sua tentativa foi preservada.',
            'retryable': True}) from None
    finally:
        if owned:
            await cache.aclose()


class _AbandonedCatalogueBuild(Exception):
    """The owning request ended; independent waiters may build in their context."""


async def cached_catalogue(key, build, *, kind='catalogue'):
    """Versioned, bounded per-worker cache; caller always checks publication.

    Authority, availability and every write are never cached. The short visual
    cache is partitioned by verified tenant, publication version, timezone and
    the database-maintained operational version. Its TTL bounds retention;
    committed catalogue changes choose a new key immediately on every worker.
    """
    loop = asyncio.get_running_loop()
    cache = _caches.setdefault(loop, OrderedDict())
    flights = _flights.setdefault(loop, {})
    entry = cache.get(key)
    if entry and entry[0] > loop.time():
        from app.public_observability import observe_cache
        observe_cache(True,kind=kind)
        cache.move_to_end(key)
        return copy.deepcopy(entry[1])
    future = flights.get(key)
    if future is not None:
        from app.public_observability import observe_cache
        observe_cache(True,kind=kind)
        try:
            return copy.deepcopy(await asyncio.shield(future))
        except _AbandonedCatalogueBuild:
            # Never propagate another request's cancellation into this one.
            # Its own deadline/cancellation still applies to the fresh build.
            return await cached_catalogue(key,build,kind=kind)
    from app.public_observability import observe_cache
    observe_cache(False,kind=kind)
    future = loop.create_future()
    # A cancelled caller must not leave an unobserved exception in a future.
    future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
    flights[key] = future
    try:
        result = await build()
        cache[key] = (loop.time() + CACHE_TTL, copy.deepcopy(result))
        cache.move_to_end(key)
        while len(cache) > CACHE_SIZE:
            cache.popitem(last=False)
        future.set_result(copy.deepcopy(result))
        return result
    except BaseException as exc:
        if not future.done():
            if isinstance(exc, asyncio.CancelledError):
                future.set_exception(_AbandonedCatalogueBuild())
            else:
                future.set_exception(exc)
        raise
    finally:
        flights.pop(key, None)


def clear_catalogue_cache(tenant=None):
    """Local invalidation is a test/operations helper, not publication authority."""
    for cache in list(_caches.values()):
        for key in list(cache):
            if tenant is None or key[0] == str(tenant):
                cache.pop(key, None)
