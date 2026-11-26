"""Exercise the real auth routes, session generation and migration on synthetic PG/Redis only."""
import asyncio
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
import httpx
import psycopg
import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import account_auth, panel_auth, legacy_admin_identity as identity, legacy_admin_password as own

ACCOUNT=UUID("91a00000-0000-4000-8000-000000000001")
OLD="synthetic-old-test-secret"
NEW="synthetic-new-test-secret"
NEXT="synthetic-next-test-secret"
PG=os.getenv("ORION_TEST_ADMIN_POSTGRES","")
REDIS=os.getenv("ORION_TEST_ADMIN_REDIS","")
pytestmark=pytest.mark.skipif(not PG or not REDIS,reason="Requires isolated synthetic PostgreSQL and Redis")

@pytest_asyncio.fixture
async def app(monkeypatch):
    # These names and credentials exist only in the CI service containers.
    assert "127.0.0.1" in PG and "127.0.0.1" in REDIS
    salt=b"synthetic-ci-salt"
    digest=hashlib.pbkdf2_hmac("sha256",OLD.encode(),salt,200000)
    b64=lambda v:base64.urlsafe_b64encode(v).decode()
    monkeypatch.setenv("PANEL_ADMIN_USERNAME","ci-admin")
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH","pbkdf2_sha256$200000$"+b64(salt)+"$"+b64(digest))
    monkeypatch.setenv("PANEL_LEGACY_ADMIN_IDENTITY_ENABLED","1")
    monkeypatch.setenv("REDIS_URL",REDIS)
    username,binding=identity.configuration_binding()
    with psycopg.connect(PG,autocommit=True) as db:
        db.execute("DROP TABLE IF EXISTS public.orion_legacy_admin_passwords")
        db.execute("DROP TABLE IF EXISTS public.panel_accounts")
        db.execute("DROP ROLE IF EXISTS app_support")
        db.execute("DROP ROLE IF EXISTS app_runtime")
        db.execute("CREATE ROLE app_support LOGIN PASSWORD 'ci-role-password-only'")
        db.execute("CREATE ROLE app_runtime NOLOGIN")
        db.execute("""CREATE TABLE public.panel_accounts (
            id uuid PRIMARY KEY,name text NOT NULL,role text NOT NULL,
            email text,password_hash text,auth_provider text NOT NULL,
            legacy_username text,legacy_credential_digest char(64),
            auth_version integer NOT NULL
        )""")
        db.execute("GRANT SELECT,UPDATE ON public.panel_accounts TO app_support")
        db.execute("""INSERT INTO panel_accounts VALUES
                    (%s,'Administrador de teste','admin',NULL,NULL,'legacy_admin',%s,%s,1)""",
                   (ACCOUNT,username,binding))
        path=Path("/srv/app/alembic/versions/0099_admin_personal_password.py")
        spec=importlib.util.spec_from_file_location("admin_test_migration",path)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        class Operations:
            @staticmethod
            def execute(sql):
                db.execute(sql)
        mod.op=Operations
        mod.upgrade()
        assert db.execute("SELECT count(*) FROM orion_legacy_admin_passwords").fetchone()==(0,)
        assert db.execute("SELECT has_table_privilege('app_runtime','orion_legacy_admin_passwords','SELECT')").fetchone()==(False,)
        assert db.execute("SELECT has_table_privilege('app_support','orion_legacy_admin_passwords','DELETE')").fetchone()==(False,)
    engine=create_async_engine("postgresql+asyncpg://app_support:ci-role-password-only@127.0.0.1:5432/postgres",hide_parameters=True)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(own,"support_session_factory",lambda:factory)
    monkeypatch.setattr(identity,"_storage",lambda:factory)
    monkeypatch.setattr(account_auth,"_storage",lambda:factory)
    redis=from_url(REDIS)
    await redis.flushdb()
    await redis.aclose()
    api=FastAPI()
    api.include_router(panel_auth.router)
    api.include_router(account_auth.router)
    yield api
    await engine.dispose()

def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://orion.test")

async def login(c,password):
    return await c.post("/auth/login",json={"username":"ci-admin","password":password})

async def change(c,csrf,current,new,confirm=None):
    return await c.post("/auth/admin/password",headers={"X-Orion-CSRF":csrf},
        json={"current_password":current,"new_password":new,"confirm_password":new if confirm is None else confirm})

@pytest.mark.asyncio
async def test_initial_login_rotation_and_session_invalidation(app):
    async with client(app) as c,client(app) as previous:
        first=await login(c,OLD)
        assert first.status_code==200 and first.json()["role"]=="admin"
        old_session=await login(previous,OLD)
        assert old_session.status_code==200
        csrf=first.json()["csrf"]
        result=await change(c,csrf,OLD,NEW)
        assert result.status_code==200 and result.json()["requires_login"]
        assert (await c.get("/auth/me")).status_code==401
        assert (await previous.get("/auth/me")).status_code==401
        assert (await login(c,OLD)).status_code==401
        new=await login(c,NEW)
        assert new.status_code==200 and new.json()["role"]=="admin"
        assert not await own.verify_authorization(OLD,panel_auth._password_valid)
        assert await own.verify_authorization(NEW,panel_auth._password_valid)
        second=await change(c,new.json()["csrf"],NEW,NEXT)
        assert second.status_code==200
        assert (await login(c,NEW)).status_code==401
        assert (await login(c,NEXT)).status_code==200
    with psycopg.connect(PG) as db:
        row=db.execute("SELECT password_hash FROM orion_legacy_admin_passwords").fetchone()
        assert row[0].startswith("$argon2id$")
        assert NEXT not in row[0]
        assert db.execute("SELECT auth_version FROM panel_accounts WHERE id=%s",(ACCOUNT,)).fetchone()==(3,)

@pytest.mark.asyncio
async def test_requires_session_csrf_and_current_password(app):
    async with client(app) as c:
        assert (await change(c,"not-a-session",OLD,NEW)).status_code==401
        csrf=(await login(c,OLD)).json()["csrf"]
        assert (await change(c,"incorrect-csrf",OLD,NEW)).status_code==403
        assert (await change(c,csrf,"incorrect-current",NEW)).status_code==403
        assert (await change(c,csrf,OLD,NEW,NEXT)).status_code==422
        assert (await change(c,csrf,OLD,OLD)).status_code==422
        response=await change(c,csrf,OLD,"too-short"[:3])
        assert response.status_code==422 and OLD not in response.text
        assert (await c.get("/auth/me")).status_code==200
    with psycopg.connect(PG) as db:
        assert db.execute("SELECT count(*) FROM orion_legacy_admin_passwords").fetchone()==(0,)

@pytest.mark.asyncio
async def test_wrong_roles_and_maintenance(app):
    for role,maintenance in [("member",None),("support",None),("admin",{"active":True})]:
        app.dependency_overrides[panel_auth.panel_actor]=lambda:{"role":role,"account_id":str(ACCOUNT),"maintenance":maintenance}
        async with client(app) as c:
            assert (await change(c,"fake",OLD,NEW)).status_code==403
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_simultaneous_rotations_only_one_commits(app):
    async with client(app) as a,client(app) as b:
        ac=(await login(a,OLD)).json()["csrf"]
        bc=(await login(b,OLD)).json()["csrf"]
        results=await asyncio.gather(change(a,ac,OLD,NEW),change(b,bc,OLD,NEXT))
        assert sorted(r.status_code for r in results)==[200,401]
        winner=NEW if results[0].status_code==200 else NEXT
        loser=NEXT if winner==NEW else NEW
        assert (await login(a,OLD)).status_code==401
        assert (await login(a,loser)).status_code==401
        assert (await login(a,winner)).status_code==200

@pytest.mark.asyncio
async def test_disabling_identity_does_not_revive_old_password(app,monkeypatch):
    async with client(app) as c:
        csrf=(await login(c,OLD)).json()["csrf"]
        assert (await change(c,csrf,OLD,NEW)).status_code==200
        monkeypatch.setenv("PANEL_LEGACY_ADMIN_IDENTITY_ENABLED","0")
        assert (await login(c,OLD)).status_code==401
        assert (await login(c,NEW)).status_code==401
