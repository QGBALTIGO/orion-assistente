"""Disposable PostgreSQL and fake Redis maintenance lifecycle tests."""
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from starlette.requests import Request

from app import legacy_admin_identity,maintenance_access,panel_auth
from tests.integration.test_onboarding_postgres import onboarding_db

pytestmark=pytest.mark.integration
COOKIE="t"*43


class RedisStub:
    def __init__(self):self.values={};self.ttls={}
    async def get(self,k):return self.values.get(k)
    async def exists(self,k):return int(k in self.values)
    async def ttl(self,k):return self.ttls.get(k,3600)
    async def set(self,k,v,*,ex=None,nx=False):
        if nx and k in self.values:return False
        self.values[k]=v;self.ttls[k]=ex;return True
    async def delete(self,*keys):
        removed=0
        for k in keys:
            if k in self.values:
                removed+=1;del self.values[k]
            self.ttls.pop(k,None)
        return removed


def req(path,method="GET",tenant=None):
    headers=[(b"cookie",(panel_auth.COOKIE+"="+COOKIE).encode()),
             (b"host",b"orion.test")]
    if method not in ("GET","HEAD","OPTIONS"):
        headers.extend([(b"origin",b"https://orion.test"),
                        (b"x-orion-csrf",b"admin-csrf")])
    return Request({"type":"http","method":method,"path":path,
                    "scheme":"https","server":("orion.test",443),
                    "headers":headers,
                    "path_params":{"tenant_id":str(tenant)} if tenant is not None else {}})


def session(monkeypatch,admin):
    redis=RedisStub()
    monkeypatch.setattr(maintenance_access,"_request_redis",lambda request:(redis,False))
    monkeypatch.setattr(panel_auth,"_request_redis",lambda request:(redis,False))
    async def current_identity(_id,_v):return {"name":"Administrador de teste"}
    monkeypatch.setattr(legacy_admin_identity,"current_identity",current_identity)
    redis.values[panel_auth._hash_cookie(COOKIE)]=json.dumps({
        "role":"admin","auth_provider":"legacy_admin","account_id":str(admin),
        "auth_version":1,"csrf":"admin-csrf"})
    return redis


async def start(ctx,admin):
    account=ctx.actor.account_id
    async with ctx.migration.connect() as db:
        version=(await db.execute(text(
            "SELECT auth_version FROM panel_accounts WHERE id=:a"),{"a":account})).scalar_one()
    await maintenance_access.start_maintenance(
        account,maintenance_access.MaintenanceStart(
            expected_version=version,
            reason="Manutenção solicitada para validação do cadastro do negócio",
            confirmation="ACESSAR"),
        req("/api/v1/admin/accounts/"+str(account)+"/maintenance","POST"),admin)
    return version


async def cleanup(ctx):
    async with ctx.migration.begin() as db:
        await db.execute(text("DELETE FROM admin_account_audit WHERE account_id=:a"),
                         {"a":ctx.actor.account_id})
        await db.execute(text("DELETE FROM admin_account_controls WHERE account_id=:a"),
                         {"a":ctx.actor.account_id})


@pytest.mark.asyncio
async def test_maintenance_preserves_customer_credentials_and_audits_every_attempt(onboarding_db,monkeypatch):
    ctx=onboarding_db;account=ctx.actor.account_id;admin=uuid4()
    redis=session(monkeypatch,admin)
    async with ctx.migration.connect() as db:
        before=(await db.execute(text(
            "SELECT email,password_hash,auth_version FROM panel_accounts WHERE id=:a"),
            {"a":account})).mappings().one()
    try:
        await start(ctx,admin)
        assert maintenance_access.maintenance_key(COOKIE) in redis.values
        assert maintenance_access.maintenance_guard_key(COOKIE) in redis.values
        with pytest.raises(HTTPException) as duplicate:
            await start(ctx,admin)
        assert duplicate.value.status_code==409
        route="/api/v1/tenants/"+str(ctx.a)+"/management/customers"
        claims=await panel_auth.panel_actor(req(route,tenant=ctx.a))
        assert claims["role"]=="member"
        assert claims["account_id"]==str(account)
        assert claims["maintenance_actor_id"]==str(admin)
        write=await panel_auth.panel_actor(req(route,"POST",tenant=ctx.a))
        assert write["maintenance"]["active"]
        async with ctx.migration.connect() as db:
            history=(await db.execute(text("""
                SELECT actor_id,action,metadata FROM admin_account_audit
                WHERE account_id=:a ORDER BY id
            """),{"a":account})).mappings().all()
        assert [x["action"] for x in history]==["maintenance_start","maintenance_request"]
        assert all(x["actor_id"]==admin for x in history)
        assert history[-1]["metadata"]["path"]==route
        final_claims=await panel_auth.panel_actor(req("/auth/maintenance/end","POST"))
        result=await maintenance_access.end_maintenance(
            req("/auth/maintenance/end","POST"),final_claims)
        assert result["ok"] and maintenance_access.maintenance_key(COOKIE) not in redis.values
        assert maintenance_access.maintenance_guard_key(COOKIE) not in redis.values
        assert (await panel_auth.panel_actor(req("/auth/me")))["role"]=="admin"
        async with ctx.migration.connect() as db:
            after=(await db.execute(text(
                "SELECT email,password_hash,auth_version FROM panel_accounts WHERE id=:a"),
                {"a":account})).mappings().one()
            actions=(await db.execute(text(
                "SELECT action FROM admin_account_audit WHERE account_id=:a ORDER BY id"),
                {"a":account})).scalars().all()
        assert dict(after)==dict(before)
        assert actions==["maintenance_start","maintenance_request","maintenance_end"]
    finally:await cleanup(ctx)


@pytest.mark.asyncio
async def test_expired_maintenance_cannot_escalate_to_admin(onboarding_db,monkeypatch):
    ctx=onboarding_db;admin=uuid4();redis=session(monkeypatch,admin)
    try:
        await start(ctx,admin)
        assert (await panel_auth.panel_actor(req("/auth/me")))["role"]=="member"
        redis.values.pop(maintenance_access.maintenance_key(COOKIE))
        with pytest.raises(HTTPException) as expired:
            await panel_auth.panel_actor(req("/api/v1/tenants/"+str(ctx.a)+"/management/customers",
                                             "POST",tenant=ctx.a))
        assert expired.value.status_code==401
        original=await panel_auth.panel_actor(req("/auth/me"))
        assert original["role"]=="admin" and original["account_id"]==str(admin)
        assert maintenance_access.maintenance_guard_key(COOKIE) not in redis.values
    finally:await cleanup(ctx)


@pytest.mark.asyncio
async def test_revoked_target_cannot_continue_support_access(onboarding_db,monkeypatch):
    from app import admin_accounts
    ctx=onboarding_db;account=ctx.actor.account_id;admin=uuid4()
    redis=session(monkeypatch,admin)
    try:
        version=await start(ctx,admin)
        await admin_accounts.manage_account(account,"suspend",
            admin_accounts.AccountAction(expected_version=version,
                                         reason="Teste de revogação imediata"),admin)
        with pytest.raises(HTTPException) as revoked:
            await panel_auth.panel_actor(req("/api/v1/tenants/"+str(ctx.a)+"/management/customers",
                                             tenant=ctx.a))
        assert revoked.value.status_code==401
        assert maintenance_access.maintenance_guard_key(COOKIE) in redis.values
        assert (await panel_auth.panel_actor(req("/auth/me")))["role"]=="admin"
    finally:await cleanup(ctx)
