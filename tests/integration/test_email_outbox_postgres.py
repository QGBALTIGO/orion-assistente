"""Synthetic-only PostgreSQL/Redis email acceptance; no external provider calls."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app import account_auth, panel_auth, management_team, email_outbox as outbox, email_transport as transport
from app.catalog_api import support_session_factory

pytestmark = pytest.mark.integration


@asynccontextmanager
async def signup_case(d):
    address=str(uuid4())+'@example.test'
    payload={'name':'Synthetic signup','email':address,'password':d['password']}
    try:
        yield payload
    finally:
        async with d['engine'].begin() as db:
            identity=(await db.execute(text('SELECT id FROM panel_accounts WHERE email=:e'),{'e':address})).scalar_one_or_none()
            if identity:
                await db.execute(text('DELETE FROM panel_email_outbox WHERE account_id=:a'),{'a':identity})
                await db.execute(text('DELETE FROM panel_email_tokens WHERE account_id=:a'),{'a':identity})
                await db.execute(text('DELETE FROM panel_accounts WHERE id=:a'),{'a':identity})


async def signup_state(d,address):
    async with d['engine'].connect() as db:
        account=(await db.execute(text('SELECT * FROM panel_accounts WHERE email=:e'),{'e':address})).mappings().one_or_none()
        messages=(await db.execute(text('SELECT * FROM panel_email_outbox WHERE account_id=:a ORDER BY created_at,id'),
                                  {'a':account['id'] if account else None})).mappings().all()
    return account,messages


def signup_code(messages):
    return __import__('re').search(r'\b([0-9]{6})\b',json.loads(transport.cipher().decrypt(messages[-1]['payload_enc'].encode()))['text'])[1]


async def test_new_signup_cannot_login_or_use_forged_session_until_confirmed(mail_db):
    d=mail_db;c=d['client'];origin={'Origin':'https://orion.example.test'}
    async with signup_case(d) as payload:
        created=await c.post('/auth/register',json=payload,headers=origin)
        assert created.status_code==201 and created.json()['verificationRequired']
        account,messages=await signup_state(d,payload['email'])
        assert account['registration_pending'] and account['verified_at'] is None and account['role']=='member'
        assert len(messages)==1 and messages[0]['kind']=='verify' and messages[0]['status']=='pending'
        code=signup_code(messages)
        assert code not in created.text
        wrong=await c.post('/auth/login',json={'username':payload['email'],'password':'wrong-password'},headers=origin)
        assert wrong.status_code==401 and 'set-cookie' not in wrong.headers
        login=await c.post('/auth/login',json={'username':payload['email'],'password':payload['password']},headers=origin)
        assert login.status_code==403 and login.json()['detail']['code']=='email_confirmation_required'
        assert 'set-cookie' not in login.headers
        with pytest.raises(HTTPException):await account_auth.current_account(str(account['id']),account['auth_version'])
        with pytest.raises(HTTPException):await account_auth.current_account_and_membership(str(account['id']),account['auth_version'],d['tenant'])
        finished=await c.post('/auth/verify',json={'email':payload['email'],'code':code},headers=origin)
        assert finished.status_code==200 and finished.json()['registrationCompleted'] is True
        assert 'set-cookie' not in finished.headers
        account,_=await signup_state(d,payload['email'])
        assert account['registration_pending'] is False and account['verified_at'] is not None
        assert (await c.post('/auth/verify',json={'email':payload['email'],'code':code},headers=origin)).status_code==400
        login=await c.post('/auth/login',json={'username':payload['email'],'password':payload['password']},headers=origin)
        assert login.status_code==200 and login.json()['role']=='member'
        assert (await c.get('/auth/me')).json()['email_verified'] is True


async def test_repeating_pending_signup_preserves_password_name_and_single_email(mail_db):
    d=mail_db;c=d['client'];origin={'Origin':'https://orion.example.test'}
    async with signup_case(d) as payload:
        assert (await c.post('/auth/register',json=payload,headers=origin)).status_code==201
        before,messages=await signup_state(d,payload['email'])
        assert (await c.post('/auth/register',json={**payload,'name':'A different name'},headers=origin)).status_code==201
        assert (await c.post('/auth/register',json={**payload,'password':'ChangedWithoutProof123'},headers=origin)).status_code==409
        after,new_messages=await signup_state(d,payload['email'])
        assert before==after and len(messages)==len(new_messages)==1


@pytest.mark.parametrize('failure',['expired','five_wrong_attempts'])
async def test_invalid_registration_proof_never_finishes_signup(mail_db,failure):
    d=mail_db;c=d['client'];origin={'Origin':'https://orion.example.test'}
    async with signup_case(d) as payload:
        await c.post('/auth/register',json=payload,headers=origin)
        account,messages=await signup_state(d,payload['email']);code=signup_code(messages)
        if failure=='expired':
            async with d['engine'].begin() as db:
                await db.execute(text("UPDATE panel_email_tokens SET expires_at=now()-interval '1 second' WHERE account_id=:a"),{'a':account['id']})
        else:
            wrong='000000' if code!='000000' else '000001'
            for _ in range(5):
                assert (await c.post('/auth/verify',json={'email':payload['email'],'code':wrong},headers=origin)).status_code==400
        assert (await c.post('/auth/verify',json={'email':payload['email'],'code':code},headers=origin)).status_code==400
        account,_=await signup_state(d,payload['email'])
        assert account['registration_pending'] and account['verified_at'] is None


async def test_signup_and_token_roll_back_when_enqueue_fails(mail_db,monkeypatch):
    d=mail_db
    async def broken(*args,**kwargs):raise RuntimeError('synthetic rollback')
    monkeypatch.setattr(outbox,'enqueue',broken)
    async with signup_case(d) as payload:
        with pytest.raises(RuntimeError,match='synthetic rollback'):
            await d['client'].post('/auth/register',json=payload,headers={'Origin':'https://orion.example.test'})
        assert await signup_state(d,payload['email'])==(None,[])


async def test_existing_unverified_account_and_session_stay_usable(mail_db):
    d=mail_db;c=d['client'];origin={'Origin':'https://orion.example.test'}
    async with d['engine'].begin() as db:
        await db.execute(text('UPDATE panel_accounts SET verified_at=NULL WHERE id=:id'),{'id':d['guest']})
    account,_=await signup_state(d,d['guest_email'])
    assert account['verified_at'] is None and account['registration_pending'] is False
    login=await c.post('/auth/login',json={'username':d['guest_email'],'password':d['password']},headers=origin)
    assert login.status_code==200
    assert (await c.get('/auth/me')).status_code==200
    assert (await c.post('/auth/register',json={'name':'Other','email':d['guest_email'],'password':d['password']},headers=origin)).status_code==409
    assert (await c.get('/auth/me')).status_code==200


async def test_resend_replaces_pending_signup_code_without_creating_another_account(mail_db):
    d=mail_db;c=d['client'];origin={'Origin':'https://orion.example.test'}
    async with signup_case(d) as payload:
        await c.post('/auth/register',json=payload,headers=origin)
        original,messages=await signup_state(d,payload['email']);old_code=signup_code(messages)
        assert (await c.post('/auth/verify/resend',json={'email':payload['email']},headers=origin)).status_code==200
        account,messages=await signup_state(d,payload['email'])
        assert account['id']==original['id'] and len(messages)==2
        assert (await c.post('/auth/verify',json={'email':payload['email'],'code':old_code},headers=origin)).status_code==400
        assert (await c.post('/auth/verify',json={'email':payload['email'],'code':signup_code(messages)},headers=origin)).status_code==200


@pytest_asyncio.fixture
async def mail_db(monkeypatch):
    required = ("MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL", "APP_DATABASE_URL", "REDIS_URL")
    if not all(os.getenv(key) for key in required):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Isolated email PostgreSQL and Redis are required")
        pytest.skip("Requires isolated migration0038 PostgreSQL and Redis")
    for key in required[:3]:
        url = make_url(os.environ[key])
        assert url.host in {"127.0.0.1", "localhost"} and (url.database or "").endswith("_e2e"), "Refusing non-isolated database"
    assert make_url(os.environ["REDIS_URL"]).host in {"127.0.0.1", "localhost"}
    for name, value in {
        "PANEL_PUBLIC_URL": "https://orion.example.test", "PANEL_EMAIL_TRANSPORT": "resend_https",
        "PANEL_EMAIL_ENABLED": "1", "PANEL_EMAIL_LIVE_SEND": "1", "PANEL_TEST_MODE": "0",
        "PANEL_SMTP_FROM": "Orion <acesso@orion.example.test>", "PANEL_SMTP_PASSWORD": "synthetic-never-a-live-key",
        "PANEL_EMAIL_FERNET_KEY": Fernet.generate_key().decode(),
        "PANEL_OTP_SECRET": "synthetic-stable-email-otp-secret-more-than-thirty-two-characters",
        "SUPPORT_API_TOKEN": "synthetic-support-token-over-thirty-two-characters",
        "PANEL_ADMIN_USERNAME": "synthetic-legacy-admin", "PANEL_ADMIN_PASSWORD_HASH": "synthetic-unused-hash",
        "PANEL_REGISTRATION_ENABLED": "1", "BUSINESS_PROFILES_ENABLED": "1",
    }.items():
        monkeypatch.setenv(name, value)
    async def forbidden(*args, **kwargs):
        raise AssertionError("External email provider is forbidden in integration tests")
    monkeypatch.setattr(transport, "send_message", forbidden)
    original_quota = outbox._send_quota
    async def quota(*args):
        return True
    monkeypatch.setattr(outbox, "_send_quota", quota)
    support_session_factory.cache_clear()
    engine = create_async_engine(os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    owner, guest, tenant, other = (uuid4() for _ in range(4))
    owner_email, guest_email = str(owner) + "@example.test", str(guest) + "@example.test"
    password = "Synthetic#Password123"
    async with engine.begin() as db:
        for identity, email in ((owner, owner_email), (guest, guest_email)):
            await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,verified_at) VALUES(:id,'Synthetic account',:email,:password,now())"),
                {"id": identity, "email": email, "password": account_auth._hasher.hash(password)})
        for identity in (tenant, other):
            await db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:id,'Synthetic <company>',:slug,'UTC')"),
                             {"id": identity, "slug": "email-" + str(identity)})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:tenant,:owner,'owner')"),
                         {"tenant": tenant, "owner": owner})
    cache = Redis.from_url(os.environ["REDIS_URL"], socket_timeout=3, socket_connect_timeout=3)
    app = FastAPI()
    for router in (account_auth.router, panel_auth.router, management_team.router, management_team.invitation_router):
        app.include_router(router)
    app.state.panel_session_redis = cache
    sessions = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://orion.example.test") as client:
        try:
            yield {"engine": engine, "owner": owner, "guest": guest, "tenant": tenant, "other": other,
                   "email": owner_email, "guest_email": guest_email, "password": password,
                   "redis": cache, "client": client, "ctx": {"redis": cache}, "raw_quota": original_quota, "sessions": sessions}
        finally:
            cookie = client.cookies.get(panel_auth.COOKIE)
            for token in set(sessions + ([cookie] if cookie else [])):
                await cache.delete(panel_auth._hash_cookie(token))
            async with engine.begin() as db:
                await db.execute(text("DELETE FROM panel_email_outbox WHERE account_id IN (:a,:b) OR tenant_id IN (:t,:o)"),
                                 {"a": owner, "b": guest, "t": tenant, "o": other})
                await db.execute(text("DELETE FROM management_team_invitations WHERE tenant_id IN (:t,:o)"), {"t": tenant, "o": other})
                for table in ("tenant_membership_events", "tenant_memberships"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:t,:o)"), {"t": tenant, "o": other})
                await db.execute(text("DELETE FROM tenants WHERE id IN (:t,:o)"), {"t": tenant, "o": other})
                await db.execute(text("DELETE FROM panel_email_tokens WHERE account_id IN (:a,:b)"), {"a": owner, "b": guest})
                await db.execute(text("DELETE FROM panel_accounts WHERE id IN (:a,:b)"), {"a": owner, "b": guest})
    await cache.aclose()
    await engine.dispose()
    await support_session_factory().kw["bind"].dispose()
    support_session_factory.cache_clear()


async def rows(d):
    async with d["engine"].connect() as db:
        return (await db.execute(text("SELECT * FROM panel_email_outbox WHERE account_id IN (:a,:b) OR tenant_id=:t ORDER BY created_at,id"),
                                 {"a": d["owner"], "b": d["guest"], "t": d["tenant"]})).mappings().all()


async def queued_notification(d, *, key=None):
    key = key or str(uuid4())
    async with support_session_factory()() as db:
        async with db.begin():
            result = await outbox.enqueue(db, kind="password_changed", recipient=d["email"],
                                         account_id=d["owner"], dedupe_key=key)
    return result


async def login(d, *, guest=False):
    result = await d["client"].post("/auth/login", headers={"Origin": "https://orion.example.test"},
        json={"username": d["guest_email"] if guest else d["email"], "password": d["password"]})
    assert result.status_code == 200, result.text
    d["sessions"].append(d["client"].cookies.get(panel_auth.COOKIE))
    return {"Origin": "https://orion.example.test", "X-Orion-CSRF": result.json()["csrf"]}


@pytest.mark.asyncio
async def test_token_and_ciphertext_outbox_commit_or_rollback_together(mail_db):
    d = mail_db
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        async with support_session_factory()() as db:
            async with db.begin():
                code = await account_auth._issue_token(db, d["owner"], "reset", d["email"])
                assert code.isdigit()
                raise RuntimeError("synthetic rollback")
    assert not await rows(d)
    async with d["engine"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM panel_email_tokens WHERE account_id=:id"), {"id": d["owner"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_concurrent_issuance_leaves_one_live_code_and_suppresses_superseded_mail(mail_db, monkeypatch):
    d = mail_db
    async def issue():
        async with support_session_factory()() as db:
            async with db.begin():
                return await account_auth._issue_token(db, d["owner"], "reset", d["email"])
    codes = await asyncio.gather(*(issue() for _ in range(3)))
    assert len(set(codes)) == 3
    items = await rows(d)
    assert len(items) == 3
    async with d["engine"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM panel_email_tokens WHERE account_id=:id AND used_at IS NULL"), {"id": d["owner"]})).scalar_one() == 1
    calls = []
    async def accepted(payload, item):
        calls.append((payload, item))
        return "synthetic-provider-id"
    monkeypatch.setattr(transport, "send_message", accepted)
    results = [await outbox.process_email_outbox(d["ctx"], str(item["id"])) for item in items]
    assert sorted(results) == ["accepted", "suppressed", "suppressed"]
    assert len(calls) == 1
    assert all(code not in item["payload_enc"] for item in items for code in codes)


@pytest.mark.asyncio
async def test_historical_six_digit_collision_is_regenerated_without_losing_transaction(mail_db, monkeypatch):
    d = mail_db
    async with d["engine"].begin() as db:
        await db.execute(text("INSERT INTO panel_email_tokens(account_id,token_digest,purpose,expires_at,used_at) VALUES(:a,:digest,'reset',now()+interval '1 hour',now())"),
            {"a": d["owner"], "digest": account_auth._digest_code(d["email"], "reset", "000001")})
    values = iter([1, 2])
    monkeypatch.setattr(account_auth.secrets, "randbelow", lambda maximum: next(values))
    async with support_session_factory()() as db:
        async with db.begin():
            code = await account_auth._issue_token(db, d["owner"], "reset", d["email"])
    assert code == "000002" and len(await rows(d)) == 1


@pytest.mark.asyncio
async def test_parallel_workers_accept_one_row_once_and_clear_secrets(mail_db, monkeypatch):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def accepted(payload, identity):
        calls.append(identity)
        started.set()
        await release.wait()
        return "synthetic-provider-id"
    monkeypatch.setattr(transport, "send_message", accepted)
    first = asyncio.create_task(outbox.process_email_outbox(d["ctx"], str(item["id"])))
    await asyncio.wait_for(started.wait(), 3)
    try:
        second = await asyncio.wait_for(outbox.process_email_outbox(d["ctx"], str(item["id"])), 3)
        assert second == "not_pending"
    finally:
        release.set()
    assert await first == "accepted" and calls == [item["id"]]
    stored = (await rows(d))[0]
    assert stored["payload_enc"] is None and stored["provider_message_id"] == "synthetic-provider-id" and stored["attempts"] == 1


@pytest.mark.asyncio
async def test_ambiguous_retry_keeps_exact_payload_and_stable_outbox_id(mail_db, monkeypatch):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    calls = []
    async def accepted(payload, identity):
        calls.append((payload, identity))
        if len(calls) == 1:
            raise transport.EmailProviderError("email_transport_ambiguous", retryable=True, ambiguous=True)
        return "synthetic-provider-id"
    monkeypatch.setattr(transport, "send_message", accepted)
    assert await outbox.process_email_outbox(d["ctx"], str(item["id"])) == "retry"
    retry = (await rows(d))[0]
    assert retry["status"] == "pending" and retry["attempts"] == 1
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_email_outbox SET next_attempt_at=now() WHERE id=:id"), {"id": item["id"]})
    assert await outbox.process_email_outbox(d["ctx"], str(item["id"])) == "accepted"
    assert calls[0] == calls[1]


@pytest.mark.asyncio
async def test_ambiguous_retry_limit_becomes_uncertain_and_cannot_send_again(mail_db, monkeypatch):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_email_outbox SET attempts=7,first_attempt_at=now() WHERE id=:id"), {"id": item["id"]})
    async def ambiguous(*args):
        raise transport.EmailProviderError("email_transport_ambiguous", retryable=True, ambiguous=True)
    monkeypatch.setattr(transport, "send_message", ambiguous)
    assert await outbox.process_email_outbox(d["ctx"], str(item["id"])) == "failed"
    stored = (await rows(d))[0]
    assert stored["status"] == "uncertain" and stored["attempts"] == 8 and stored["payload_enc"] is None
    assert await outbox.process_email_outbox(d["ctx"], str(item["id"])) == "not_pending"


@pytest.mark.asyncio
async def test_egress_flag_off_does_not_enqueue_or_process_existing_mail(mail_db, monkeypatch):
    d = mail_db
    await queued_notification(d)
    original = (await rows(d))[0]
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", "0")
    assert (await queued_notification(d))["status"] == "disabled"
    assert await outbox.process_email_outbox(d["ctx"], str(original["id"])) == "disabled"
    assert dict((await rows(d))[0]) == dict(original)


@pytest.mark.asyncio
async def test_expired_and_unreadable_payloads_never_leave_the_worker(mail_db):
    d = mail_db
    await queued_notification(d)
    await queued_notification(d)
    first, second = await rows(d)
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_email_outbox SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE id=:id"), {"id": first["id"]})
        await db.execute(text("UPDATE panel_email_outbox SET payload_enc='not-valid-ciphertext' WHERE id=:id"), {"id": second["id"]})
    assert await outbox.process_email_outbox(d["ctx"], str(first["id"])) == "expired"
    assert await outbox.process_email_outbox(d["ctx"], str(second["id"])) == "failed"
    assert all(item["payload_enc"] is None for item in await rows(d))


@pytest.mark.asyncio
async def test_scanner_disposes_expired_secrets_even_when_delivery_is_disabled(mail_db, monkeypatch):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_email_outbox SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE id=:id"), {"id": item["id"]})
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND", "0")
    assert await outbox.scan_email_outbox(d["ctx"]) == 0
    stored = (await rows(d))[0]
    assert stored["status"] == "expired" and stored["payload_enc"] is None and stored["attempts"] == 0


@pytest.mark.asyncio
async def test_scanner_resumes_normally_after_outbox_table_exists(mail_db):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    jobs = []
    class Cache:
        async def set(self,*args,**kwargs):pass
        async def enqueue_job(self,function,*args,**kwargs):
            jobs.append((function,args,kwargs))
            return object()
    assert await outbox.scan_email_outbox({"redis":Cache()}) >= 1
    assert any(function == "process_email_outbox" and args == (str(item["id"]),) for function,args,kwargs in jobs)
    assert (await rows(d))[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_ciphertext_recipient_must_match_its_private_row_scope(mail_db):
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    forged = transport.render_message(d["guest_email"], "password_changed")
    encrypted = transport.cipher().encrypt(json.dumps(forged).encode()).decode()
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_email_outbox SET payload_enc=:payload WHERE id=:id"), {"id": item["id"], "payload": encrypted})
    assert await outbox.process_email_outbox(d["ctx"], str(item["id"])) == "failed"
    assert (await rows(d))[0]["last_error_code"] == "email_payload_unreadable"


@pytest.mark.asyncio
async def test_deadline_is_rechecked_after_waiting_for_source_row_lock(mail_db, monkeypatch):
    d = mail_db
    async with support_session_factory()() as db:
        async with db.begin():
            await account_auth._issue_token(db, d["owner"], "reset", d["email"])
    item = (await rows(d))[0]
    async with d["engine"].begin() as editor:
        await editor.execute(text("SELECT id FROM panel_email_tokens WHERE id=:id FOR UPDATE"), {"id": item["token_id"]})
        await editor.execute(text("UPDATE panel_email_outbox SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id=:id"), {"id": item["id"]})
    # The expiry update must commit before the worker reads its outbox row.
    entered = asyncio.Event()
    original = outbox._source_is_current
    async def source(db, row):
        entered.set()
        return await original(db, row)
    monkeypatch.setattr(outbox, "_source_is_current", source)
    task = None
    try:
        async with d["engine"].begin() as editor:
            await editor.execute(text("SELECT id FROM panel_email_tokens WHERE id=:id FOR UPDATE"), {"id": item["token_id"]})
            task = asyncio.create_task(outbox.process_email_outbox(d["ctx"], str(item["id"])))
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.sleep(2.1)
        assert await asyncio.wait_for(task, 3) == "expired"
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (await rows(d))[0]["attempts"] == 0


@pytest.mark.asyncio
async def test_outbox_rls_force_and_runtime_denial(mail_db):
    d = mail_db
    await queued_notification(d)
    async with d["engine"].connect() as db:
        info = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.panel_email_outbox'::regclass"))).one()
        assert info == (True, True)
    runtime = create_async_engine(os.environ["APP_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        for sql in ("SELECT * FROM panel_email_outbox", "UPDATE panel_email_outbox SET status='accepted'", "DELETE FROM panel_email_outbox"):
            with pytest.raises(DBAPIError):
                async with runtime.begin() as db:
                    await db.execute(text(sql))
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_recipient_and_provider_quotas_use_real_atomic_redis(mail_db, monkeypatch):
    d = mail_db
    monkeypatch.setenv("PANEL_EMAIL_REQUESTS_PER_RECIPIENT_HOUR", "1")
    assert await outbox.request_quota(d["redis"], d["email"])
    assert not await outbox.request_quota(d["redis"], d["email"])
    assert await outbox.request_quota(d["redis"], d["guest_email"])
    monkeypatch.setenv("PANEL_EMAIL_RATE_PER_SECOND", "100")
    monkeypatch.setenv("PANEL_EMAIL_RECIPIENT_DAILY_LIMIT", "1")
    digest = outbox.recipient_digest(d["email"])
    assert await d["raw_quota"](d["redis"], digest)
    assert not await d["raw_quota"](d["redis"], digest)


@pytest.mark.asyncio
async def test_real_owner_invites_replay_manual_foreign_denial_and_revoke_suppression(mail_db, monkeypatch):
    d = mail_db
    headers = await login(d)
    base = f"/api/v1/tenants/{d['tenant']}/management/team"
    request = {"email": d["guest_email"], "role": "viewer", "idempotency_key": str(uuid4())}
    result = await d["client"].post(base + "/invitations", json=request, headers=headers)
    assert result.status_code == 201, result.text
    invite = result.json()
    assert invite["email_delivery"]["status"] == "queued" and "/convite#token=" in invite["link"]
    repeated = await d["client"].post(base + "/invitations", json=request, headers=headers)
    assert repeated.status_code == 201 and repeated.json()["link"] is None
    assert repeated.json()["email_delivery"]["status"] == "queued" and len(await rows(d)) == 1
    foreign = await d["client"].post(f"/api/v1/tenants/{d['other']}/management/team/invitations", json=request, headers=headers)
    assert foreign.status_code == 403 and len(await rows(d)) == 1
    manual = await d["client"].post(base + "/invitations", json={"role": "viewer", "idempotency_key": str(uuid4())}, headers=headers)
    assert manual.status_code == 201 and manual.json()["email_delivery"]["status"] == "manual"
    revoked = await d["client"].patch(base + "/invitations/" + invite["id"], json={"action": "revoke", "expected_version": 1}, headers=headers)
    assert revoked.status_code == 200, revoked.text
    items = await rows(d)
    invitation = next(item for item in items if item["kind"] == "team_invite")
    assert await outbox.process_email_outbox(d["ctx"], str(invitation["id"])) == "suppressed"
    calls = []
    async def accepted(payload, identity):
        calls.append(payload)
        return "synthetic-revoked-id"
    monkeypatch.setattr(transport, "send_message", accepted)
    notification = next(item for item in items if item["kind"] == "team_access_revoked")
    assert await outbox.process_email_outbox(d["ctx"], str(notification["id"])) == "accepted"
    assert calls[0]["to"] == [d["guest_email"]]


@pytest.mark.asyncio
async def test_forgot_known_unknown_generic_reset_invalidates_cookie_and_notifies(mail_db):
    d = mail_db
    await login(d)
    headers = {"Origin": "https://orion.example.test"}
    known = await d["client"].post("/auth/password/forgot", json={"email": d["email"]}, headers=headers)
    unknown = await d["client"].post("/auth/password/forgot", json={"email": str(uuid4()) + "@example.test"}, headers=headers)
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    item = (await rows(d))[0]
    payload = json.loads(transport.cipher().decrypt(item["payload_enc"].encode()))
    import re
    code = re.search(r"\b[0-9]{6}\b", payload["text"])[0]
    reset = await d["client"].post("/auth/password/reset", json={"email": d["email"], "code": code, "password": "New#SyntheticPassword123"}, headers=headers)
    assert reset.status_code == 200, reset.text
    assert (await d["client"].get("/auth/me")).status_code == 401
    assert sorted(item["kind"] for item in await rows(d)) == ["password_changed", "reset"]
    replay = await d["client"].post("/auth/password/reset", json={"email": d["email"], "code": code, "password": "Another#Synthetic123"}, headers=headers)
    assert replay.status_code == 400 and len(await rows(d)) == 2


@pytest.mark.asyncio
async def test_verified_invitation_claim_review_and_access_notifications_keep_authority(mail_db, monkeypatch):
    d = mail_db
    owner_headers = await login(d)
    base = f"/api/v1/tenants/{d['tenant']}/management/team"
    created = await d["client"].post(base + "/invitations", headers=owner_headers,
        json={"email": d["guest_email"], "role": "viewer", "idempotency_key": str(uuid4())})
    invitation = created.json()
    token = invitation["link"].split("#token=", 1)[1]
    premature = await d["client"].patch(base + "/invitations/" + invitation["id"], headers=owner_headers,
                                       json={"action": "approve", "expected_version": 1})
    assert premature.status_code == 409
    guest_headers = await login(d, guest=True)
    accepted = await d["client"].post("/api/v1/team-invitations/accept", headers=guest_headers, json={"token": token})
    assert accepted.status_code == 200 and accepted.json()["status"] == "claimed"
    owner_headers = await login(d)
    approved = await d["client"].patch(base + "/invitations/" + invitation["id"], headers=owner_headers,
                                      json={"action": "approve", "expected_version": 2})
    assert approved.status_code == 200, approved.text
    calls = []
    async def provider(payload, identity):
        calls.append(payload)
        return "synthetic-access-id"
    monkeypatch.setattr(transport, "send_message", provider)
    items = await rows(d)
    notification = next(item for item in items if item["kind"] == "team_access_approved")
    assert await outbox.process_email_outbox(d["ctx"], str(notification["id"])) == "accepted"
    assert calls[-1]["to"] == [d["guest_email"]]
    assert "&lt;company&gt;" in calls[-1]["html"]
    team = (await d["client"].get(base)).json()
    guest = next(member for member in team["members"] if member["account_id"] == str(d["guest"]))
    revoked = await d["client"].patch(base + "/members/" + str(d["guest"]), headers=owner_headers,
        json={"role": "viewer", "active": False, "expected_updated_at": guest["updated_at"]})
    assert revoked.status_code == 200, revoked.text
    notification = next(item for item in await rows(d) if item["kind"] == "team_access_revoked")
    assert await outbox.process_email_outbox(d["ctx"], str(notification["id"])) == "accepted"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_email_confirmation_uses_durable_code_without_changing_password_or_session(mail_db, monkeypatch):
    d = mail_db
    async with d["engine"].begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=NULL WHERE id=:id"), {"id": d["guest"]})
    await login(d, guest=True)
    assert (await d["client"].get("/auth/me")).json()["email_verified"] is False
    monkeypatch.setenv("PANEL_REGISTRATION_ENABLED", "0")
    origin = {"Origin": "https://orion.example.test"}
    sent = await d["client"].post("/auth/verify/resend", headers=origin, json={"email": d["guest_email"]})
    assert sent.status_code == 200
    item = (await rows(d))[0]
    assert item["kind"] == "verify"
    payload = json.loads(transport.cipher().decrypt(item["payload_enc"].encode()))
    import re
    code = re.search(r"\b[0-9]{6}\b", payload["text"])[0]
    verified = await d["client"].post("/auth/verify", headers=origin, json={"email": d["guest_email"], "code": code})
    assert verified.status_code == 200
    current = await d["client"].get("/auth/me")
    assert current.status_code == 200 and current.json()["email_verified"] is True
    async with d["engine"].connect() as db:
        row = (await db.execute(text("SELECT auth_version,verified_at FROM panel_accounts WHERE id=:id"), {"id": d["guest"]})).one()
        assert row.auth_version == 1 and row.verified_at is not None


@pytest.mark.asyncio
async def test_arq_failed_result_cannot_block_the_next_scanner_bucket(mail_db):
    from arq.connections import create_pool, RedisSettings
    from arq.constants import job_key_prefix, result_key_prefix, default_queue_name
    d = mail_db
    await queued_notification(d)
    item = (await rows(d))[0]
    now = datetime.now(timezone.utc)
    previous = outbox.email_job_id(item["id"], item["next_attempt_at"], now)
    next_bucket = outbox.email_job_id(item["id"], item["next_attempt_at"], now + timedelta(seconds=15))
    assert previous != next_bucket
    cache = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    try:
        await cache.set(result_key_prefix + previous, b"synthetic-failed-result", ex=3600)
        assert await cache.enqueue_job("process_email_outbox", str(item["id"]), _job_id=previous, _defer_by=3600) is None
        # Future defer guarantees another test worker cannot run this proof.
        assert await cache.enqueue_job("process_email_outbox", str(item["id"]), _job_id=next_bucket, _defer_by=3600) is not None
        assert await cache.enqueue_job("process_email_outbox", str(item["id"]), _job_id=next_bucket, _defer_by=3600) is None
    finally:
        await cache.delete(result_key_prefix + previous, job_key_prefix + next_bucket)
        await cache.zrem(default_queue_name, next_bucket)
        await cache.aclose()
