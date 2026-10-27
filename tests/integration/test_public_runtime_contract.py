"""Real PostgreSQL + isolated Redis DB12 public identity/quota/recovery contracts.

No flush is used. Each fixture tracks the exact Redis keys its own EVAL calls
touch and deletes only those keys; tenants and rows are synthetic fixture data.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import public_site_api, public_site_runtime
from app.booking_service import book_appointment
from app.management_schemas import normalize_phone
from app.public_site_runtime import COOKIE, quota_key
from test_public_catalog import public_db, publish, save_site, book_body

pytestmark = pytest.mark.integration
PHONE = "+5511987654321"


class TrackedRedis(Redis):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.used_keys = set()

    async def eval(self, script, numkeys, *args):
        self.used_keys.update(args[:numkeys])
        return await super().eval(script, numkeys, *args)


@pytest_asyncio.fixture
async def runtime_db(public_db, monkeypatch):
    url = os.getenv("REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1": pytest.fail("Real synthetic Redis is required")
        pytest.skip("Requires real local Redis")
    parts = urlsplit(url)
    assert parts.hostname in {"127.0.0.1", "localhost", "::1"}, "This suite only targets synthetic local Redis"
    isolated = urlunsplit((parts.scheme, parts.netloc, "/12", "", ""))
    redis = TrackedRedis.from_url(isolated, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    assert await redis.ping()
    d = public_db
    d["app"].state.panel_session_redis = redis
    monkeypatch.setattr(public_site_api, "_redis", lambda: redis)
    public_site_runtime.clear_catalogue_cache(d["a"])
    try:
        yield {**d, "redis": redis}
    finally:
        public_site_runtime.clear_catalogue_cache(d["a"])
        if redis.used_keys:
            assert all(str(key).startswith("orion:public-limit:") for key in redis.used_keys)
            await redis.delete(*redis.used_keys)
        await redis.aclose()
        del d["app"].state.panel_session_redis


def browser(d, cookies=None):
    # The same ASGI peer models every distinct browser sharing one NAT.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"], client=("127.0.0.1", 1234)),
                             base_url="http://test", cookies=cookies)


async def quote_for(d, client=None, slug=None):
    client = client or d["client"]
    path = "/api/v1/public/sites/" + (slug or d["slug"])
    response = await client.get(path + "/availability", params={"professional_id": str(d["pro"]),
                                "service_id": str(d["cut"]), "day": d["day"].isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["slots"]
    return response.json()


def actor(client):
    return client.cookies.get(COOKIE).split(".")[0]


def creation_keys(d, client, phone=PHONE):
    scope = "tenant:" + str(d["a"])
    return (quota_key(scope, "new-browser", actor(client)),
            quota_key(scope, "new-contact-browser", actor(client) + "|" + normalize_phone(phone)))


async def count(d, table="appointments"):
    async with d["migrator"].connect() as db:
        return (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).scalar_one()


def expire_api_clock(monkeypatch, quote, slug):
    expires = public_site_api.read_quote(quote, slug, allow_expired=True)["expires"]
    class LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(expires + 1, tz=tz or timezone.utc)
    monkeypatch.setattr(public_site_api, "datetime", LaterDatetime)


async def test_sixteen_distinct_browsers_book_from_one_nat_with_real_quotas(runtime_db):
    d = runtime_db
    await publish(d)
    protocols = set()
    identities = set()
    for index in range(16):
        async with browser(d) as client:
            quoted = await quote_for(d, client)
            identities.add(actor(client))
            phone = "+55119" + f"{index + 100:08d}"
            response = await client.post(d["public"] + "/appointments", json=book_body(quoted, phone))
            assert response.status_code == 201, (index, response.text)
            protocols.add(response.json()["protocol"])
            assert await d["redis"].zcard(creation_keys(d, client, phone)[0]) == 1
    assert len(identities) == len(protocols) == 16
    assert await count(d) == await count(d, "public_booking_receipts") == 16


async def test_invalid_day_cannot_drain_another_browser_phone_in_any_normalized_format(runtime_db):
    d = runtime_db
    await publish(d)
    formats = ["(11) 98765-4321", "+55 (11) 98765-4321", "5511987654321", "11987654321"]
    async with browser(d) as attacker, browser(d) as victim:
        quoted = await quote_for(d, attacker)
        wrong = datetime.fromisoformat(quoted["slots"][0]) + timedelta(days=1)
        for phone in formats:
            response = await attacker.post(d["public"] + "/appointments", json={**book_body(quoted, phone), "start_at": wrong.isoformat()})
            assert response.status_code == 422, response.text
        assert await d["redis"].zcard(creation_keys(d, attacker)[0]) == 0
        await victim.get(d["public"])
        result = await victim.post(d["public"] + "/appointments", json=book_body(quoted, formats[0]))
        assert result.status_code == 201, result.text
        assert await d["redis"].zcard(creation_keys(d, victim)[1]) == 1
        assert actor(attacker) != actor(victim)
    assert await count(d) == 1


async def test_forged_or_missing_browser_cookie_cannot_authorize_new_creation(runtime_db):
    d = runtime_db
    await publish(d)
    quoted = await quote_for(d)
    for token in (None, "a" * 48 + "." + "b" * 64, d["client"].cookies.get(COOKIE)[:-1] + "x"):
        async with browser(d, {COOKIE: token} if token else None) as client:
            response = await client.post(d["public"] + "/appointments", json=book_body(quoted))
            assert response.status_code == 428, response.text
            assert response.json()["detail"]["code"] == "browser_session_required"
    assert await count(d) == 0


async def test_aliases_and_reopened_browser_share_creation_limits_without_blocking_new_actor(runtime_db):
    d = runtime_db
    await publish(d)
    renamed = d["slug"] + "-renamed"
    await save_site(d, slug=renamed)
    await publish(d)
    client = d["client"]
    for index in range(4):
        slug = d["slug"] if index % 2 else renamed
        quoted = await quote_for(d, client, slug)
        response = await client.post("/api/v1/public/sites/" + slug + "/appointments", json=book_body(quoted))
        assert response.status_code == 201, response.text
    keys = creation_keys(d, client)
    assert [await d["redis"].zcard(key) for key in keys] == [4, 4]
    async with browser(d, httpx.Cookies(client.cookies)) as reopened:
        previous_actor = actor(reopened)
        quoted = await quote_for(d, reopened, renamed)
        assert actor(reopened) == previous_actor == actor(client)
        response = await reopened.post("/api/v1/public/sites/" + renamed + "/appointments", json=book_body(quoted))
        assert response.status_code == 429
        # Atomic multi-budget admission never partially charges the browser key.
        assert [await d["redis"].zcard(key) for key in keys] == [4, 4]
    async with browser(d) as independent:
        quoted = await quote_for(d, independent, renamed)
        response = await independent.post("/api/v1/public/sites/" + renamed + "/appointments", json=book_body(quoted))
        assert response.status_code == 201, response.text
    assert await count(d) == 5


async def test_expired_confirmed_replay_and_recovery_never_charge_a_new_operation(runtime_db, monkeypatch):
    d = runtime_db
    await publish(d)
    quoted = await quote_for(d)
    payload = book_body(quoted)
    first = await d["client"].post(d["public"] + "/appointments", json=payload)
    assert first.status_code == 201, first.text
    keys = creation_keys(d, d["client"])
    before = [await d["redis"].zcard(key) for key in keys]
    expire_api_clock(monkeypatch, quoted["quote"], d["slug"])
    for _ in range(8):
        replay = await d["client"].post(d["public"] + "/appointments", json=payload)
        assert replay.status_code == 201 and replay.json() == first.json(), replay.text
    async with browser(d) as reopened_without_identity:
        recovered = await reopened_without_identity.post(d["public"] + "/appointments/recover", json={
            "quote": payload["quote"], "start_at": payload["start_at"], "customer_phone": "(11) 98765-4321"})
        assert recovered.status_code == 200 and recovered.json() == first.json(), recovered.text
        for changed in ({"customer_phone": "+5511987654322"},
                        {"start_at": (datetime.fromisoformat(payload["start_at"]) + timedelta(minutes=1)).isoformat()}):
            invalid = await reopened_without_identity.post(d["public"] + "/appointments/recover", json={
                **{key: payload[key] for key in ("quote", "start_at", "customer_phone")}, **changed})
            assert invalid.status_code == 409
            assert first.json()["protocol"] not in invalid.text
    assert [await d["redis"].zcard(key) for key in keys] == before == [1, 1]
    assert await count(d) == await count(d, "public_booking_receipts") == 1


@pytest.mark.parametrize("state,recovery_status", [("none", 404), ("hold", 202), ("expired_hold", 404), ("cancelled", 409)])
async def test_expired_quote_never_creates_confirms_or_changes_unconfirmed_operation(runtime_db, monkeypatch, state, recovery_status):
    d = runtime_db
    await publish(d)
    quoted = await quote_for(d)
    payload = book_body(quoted)
    quote = public_site_api.read_quote(payload["quote"], d["slug"])
    if state != "none":
        async with async_sessionmaker(d["runtime"])() as db:
            await book_appointment(db, d["a"], d["pro"], [d["cut"]], PHONE, "Cliente legado sintético",
                datetime.fromisoformat(payload["start_at"]), "public:" + quote["nonce"], source="public_site")
        if state in {"expired_hold", "cancelled"}:
            async with d["migrator"].begin() as db:
                if state == "cancelled":
                    await db.execute(text("UPDATE appointments SET status='cancelled',hold_expires_at=NULL WHERE tenant_id=:tenant"), {"tenant": d["a"]})
                else:
                    await db.execute(text("UPDATE appointments SET hold_expires_at=now()-interval '1 second' WHERE tenant_id=:tenant"), {"tenant": d["a"]})
    async def snapshot():
        async with d["migrator"].connect() as db:
            return (await db.execute(text("SELECT to_jsonb(a) FROM appointments a WHERE tenant_id=:tenant ORDER BY id"),
                                     {"tenant": d["a"]})).scalars().all()
    before = await snapshot()
    expire_api_clock(monkeypatch, payload["quote"], d["slug"])
    response = await d["client"].post(d["public"] + "/appointments", json=payload)
    assert response.status_code == 409, response.text
    recovered = await d["client"].post(d["public"] + "/appointments/recover", json={key: payload[key] for key in ("quote", "start_at", "customer_phone")})
    assert recovered.status_code == recovery_status, recovered.text
    assert await snapshot() == before
    assert await d["redis"].zcard(creation_keys(d, d["client"])[0]) == 0
    assert await count(d, "public_booking_receipts") == 0


async def test_a_thousand_unknown_slugs_create_only_two_finite_network_buckets(runtime_db):
    d = runtime_db
    fake_selection = {"base_id": d["cut"], "addon_ids": [], "fingerprint": "f" * 64}
    limit = asyncio.Semaphore(8)
    async def probe(index):
        async with limit:
            slug = "unknown-" + d["a"].hex + "-" + str(index)
            if index % 2:
                quote = public_site_api.issue_quote(slug, d["pro"], d["day"], fake_selection)
                response = await d["client"].post("/api/v1/public/sites/" + slug + "/appointments", json={
                    "quote": quote, "start_at": datetime.now(timezone.utc).isoformat(),
                    "customer_name": "Cliente sintético", "customer_phone": PHONE, "confirmed": True})
            else:
                response = await d["client"].get("/api/v1/public/sites/" + slug)
            assert response.status_code == 404, response.text
    await asyncio.gather(*(probe(index) for index in range(1000)))
    assert len(d["redis"].used_keys) == 2
    assert {key.split(":")[2] for key in d["redis"].used_keys} == {"read", "write"}
    ttls = [await d["redis"].ttl(key) for key in d["redis"].used_keys]
    assert all(1 <= ttl <= 300 for ttl in ttls)
    assert await count(d) == 0


async def test_redis_failure_stays_closed_before_any_public_write(runtime_db):
    d = runtime_db
    await publish(d)
    quoted = await quote_for(d)
    class Unavailable:
        async def eval(self, *args):
            raise RedisConnectionError("redis://synthetic-private-secret@private.invalid")
    d["app"].state.panel_session_redis = Unavailable()
    for method, path, kwargs in (("get", d["public"], {}), ("post", d["public"] + "/appointments", {"json": book_body(quoted)})):
        response = await getattr(d["client"], method)(path, **kwargs)
        assert response.status_code == 503
        assert "private.invalid" not in response.text and "synthetic-private-secret" not in response.text
    assert await count(d) == 0


async def test_catalogue_cache_invalidates_operational_changes_and_rechecks_public_authority(runtime_db, monkeypatch):
    d = runtime_db
    publication = await publish(d)
    calls = 0
    original = public_site_api.catalogue
    async def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)
    monkeypatch.setattr(public_site_api, "catalogue", counted)
    first = (await d["client"].get(d["public"])).json()
    again = (await d["client"].get(d["public"])).json()
    assert first == again and calls == 1
    old_name = next(item["name"] for item in first["services"] if item["id"] == str(d["cut"]))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET price_cents=4700,name='Nome de rascunho privado' WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": d["cut"]})
    revised = (await d["client"].get(d["public"])).json()
    service = next(item for item in revised["services"] if item["id"] == str(d["cut"]))
    assert calls == 2 and service["price_cents"] == 4700 and service["name"] == old_name
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET accepts_appointments=false WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": d["pro"]})
    unavailable = (await d["client"].get(d["public"])).json()
    assert calls == 3 and str(d["pro"]) not in [p["id"] for p in unavailable["professionals"]]
    disabled = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": publication["version"], "enabled": False})
    assert disabled.status_code == 200, disabled.text
    before_offline_read = calls  # Publication diagnostics may render their own catalogue.
    assert (await d["client"].get(d["public"])).status_code == 404
    assert calls == before_offline_read  # Cached values never bypass current publication authority.


async def test_metric_shards_preserve_legacy_totals_and_both_new_tables_enforce_rls(runtime_db):
    d = runtime_db
    await publish(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO public_site_daily_metrics(tenant_id,day,visits,bookings) VALUES(:tenant,(now() AT TIME ZONE 'America/Sao_Paulo')::date,7,2)"), {"tenant": d["a"]})
        for tenant, visits, bookings in ((d["a"], 3, 1), (d["b"], 99, 99)):
            await db.execute(text("INSERT INTO public_site_metric_shards(tenant_id,day,shard,visits,bookings) VALUES(:tenant,(now() AT TIME ZONE 'America/Sao_Paulo')::date,0,:visits,:bookings)"),
                             {"tenant": tenant, "visits": visits, "bookings": bookings})
    before = (await d["client"].get(d["base"] + "/site/metrics")).json()
    assert before["visits"] == 10 and before["site_bookings"] == 3
    assert (await d["client"].get("/s/" + d["slug"])).status_code == 200
    quoted = await quote_for(d)
    payload = book_body(quoted)
    assert (await d["client"].post(d["public"] + "/appointments", json=payload)).status_code == 201
    assert (await d["client"].post(d["public"] + "/appointments", json=payload)).status_code == 201
    after = (await d["client"].get(d["base"] + "/site/metrics")).json()
    assert after["visits"] == 11 and after["site_bookings"] == 4 and after["clients_booked"] == 1
    async with d["migrator"].connect() as db:
        assert tuple((await db.execute(text("SELECT visits,bookings FROM public_site_daily_metrics WHERE tenant_id=:tenant"), {"tenant": d["a"]})).one()) == (7, 2)
        flags = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('public_site_metric_shards','public_site_catalogue_versions')"))).all()
        assert len(flags) == 2 and all(row[0] and row[1] for row in flags)
    for table in ("public_site_metric_shards", "public_site_catalogue_versions"):
        async with d["runtime"].begin() as db:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            tenants = (await db.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))).scalars().all()
            assert tenants == [d["a"]]
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            await db.execute(text("INSERT INTO public_site_metric_shards(tenant_id,day,shard) VALUES(:foreign,current_date,127)"), {"foreign": d["b"]})
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            await db.execute(text("UPDATE public_site_catalogue_versions SET version=version+1 WHERE tenant_id=:tenant"), {"tenant": d["a"]})


@pytest.mark.parametrize("wait_stage", ["admission", "selection"])
async def test_quote_expiring_while_waiting_never_reaches_new_engine_creation(runtime_db, monkeypatch, wait_stage):
    d = runtime_db
    await publish(d)
    quoted = await quote_for(d)
    payload = book_body(quoted)
    waiting, resume = asyncio.Event(), asyncio.Event()
    if wait_stage == "admission":
        async def delayed_capacity():
            waiting.set()
            await resume.wait()
            yield
        d["app"].dependency_overrides[public_site_api.booking_capacity] = delayed_capacity
    else:
        original = public_site_api.selection_contract
        async def delayed_selection(*args, **kwargs):
            result = await original(*args, **kwargs)
            waiting.set()
            await resume.wait()
            return result
        monkeypatch.setattr(public_site_api, "selection_contract", delayed_selection)
    task = asyncio.create_task(d["client"].post(d["public"] + "/appointments", json=payload))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=3)
        expire_api_clock(monkeypatch, quoted["quote"], d["slug"])
        resume.set()
        result = await asyncio.wait_for(task, timeout=3)
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["code"] == "quote_expired"
        assert await count(d) == await count(d, "public_booking_receipts") == 0
        if wait_stage == "admission":
            assert await d["redis"].zcard(creation_keys(d, d["client"])[0]) == 0
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
