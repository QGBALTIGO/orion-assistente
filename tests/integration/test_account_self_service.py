"""Self-service account and business identity on real PostgreSQL, never production."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import account_auth, account_settings
from tests.integration.test_onboarding_postgres import onboarding_db

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_owner_business_identity_updates_legacy_name_and_phone_atomically(onboarding_db):
    ctx=onboarding_db
    claims={"role":"member","account_id":str(ctx.actor.account_id)}
    initial=await account_settings.get_business_identity(claims)
    assert initial["name"]=="Legado sintético"
    assert initial["contact_phone"] is None
    payload=account_settings.BusinessIdentity(
        name="Barbearia de Teste",contact_phone="(67) 99999-0012",
        expected_name=initial["name"],expected_phone=initial["contact_phone"])
    try:
        updated=await account_settings.update_business_identity(payload,claims)
        assert updated["name"]=="Barbearia de Teste"
        assert updated["contact_phone"]=="+5567999990012"
        assert (await account_settings.get_business_identity(claims))["contact_phone"]=="+5567999990012"
        async with ctx.migration.connect() as db:
            assert (await db.execute(text("SELECT name FROM tenants WHERE id=:t"),
                                     {"t":ctx.a})).scalar_one()=="Barbearia de Teste"
            prefs=(await db.execute(text("SELECT preferences FROM management_preferences WHERE tenant_id=:t"),
                                    {"t":ctx.a})).scalar_one()
            assert prefs["business"]["contact_phone"]=="+5567999990012"
        with pytest.raises(HTTPException) as stale:
            await account_settings.update_business_identity(payload,claims)
        assert stale.value.status_code==409
        with pytest.raises(HTTPException) as forbidden:
            await account_settings.get_business_identity({"role":"support"})
        assert forbidden.value.status_code==403
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET role='editor' WHERE tenant_id=:t AND account_id=:a"),
                             {"t":ctx.a,"a":ctx.actor.account_id})
        with pytest.raises(HTTPException) as editor:
            await account_settings.update_business_identity(
                account_settings.BusinessIdentity(name="Não autorizado",contact_phone="+5567999990012",
                    expected_name="Barbearia de Teste",expected_phone="+5567999990012"),claims)
        assert editor.value.status_code==403
    finally:
        async with ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM management_preferences WHERE tenant_id=:t"),{"t":ctx.a})


@pytest.mark.asyncio
async def test_password_change_requires_current_secret_and_revokes_previous_sessions(onboarding_db,monkeypatch):
    ctx=onboarding_db
    old="Old#SyntheticSecret123"
    new="New#SyntheticSecret456"
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET password_hash=:hash WHERE id=:id"),
                         {"id":ctx.actor.account_id,"hash":account_auth._hasher.hash(old)})
    async with ctx.migration.connect() as db:
        before=(await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                                 {"id":ctx.actor.account_id})).scalar_one()
    async def no_quota(*args,**kwargs):return None
    monkeypatch.setattr(account_auth,"_throttle",no_quota)
    monkeypatch.setattr(account_auth,"mail_ready",lambda:False)
    claims={"role":"member","account_id":str(ctx.actor.account_id),"auth_version":before}
    request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    with pytest.raises(HTTPException) as mismatch:
        await account_auth.change_own_password(
            account_auth.ChangeOwnPassword(current_password=old,new_password=new,confirm_password="Wrong#Secret123"),
            request,claims)
    assert mismatch.value.status_code==422
    with pytest.raises(HTTPException) as incorrect:
        await account_auth.change_own_password(
            account_auth.ChangeOwnPassword(current_password="Wrong#Secret123",new_password=new,confirm_password=new),
            request,claims)
    assert incorrect.value.status_code==403
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                                 {"id":ctx.actor.account_id})).scalar_one()==before
    changed=await account_auth.change_own_password(
        account_auth.ChangeOwnPassword(current_password=old,new_password=new,confirm_password=new),
        request,claims)
    assert changed["requires_login"] is True
    async with ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                                 {"id":ctx.actor.account_id})).scalar_one()==before+1
    with pytest.raises(HTTPException) as revoked:
        await account_auth.current_account(str(ctx.actor.account_id),before)
    assert revoked.value.status_code==401
    assert await account_auth.authenticate_account(
        f"onboarding-{ctx.actor.account_id}@example.test",new)
    assert not await account_auth.authenticate_account(
        f"onboarding-{ctx.actor.account_id}@example.test",old)
