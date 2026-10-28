"""Account administration: isolation and irreversible-session-revocation tests."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from app import admin_accounts,account_auth
from tests.integration.test_onboarding_postgres import onboarding_db

pytestmark=pytest.mark.integration

@pytest.mark.asyncio
async def test_platform_admin_can_suspend_archive_restore_without_deleting_customer(onboarding_db):
    ctx=onboarding_db
    actor=uuid4()
    account=ctx.actor.account_id
    async with ctx.migration.begin() as db:
        row=(await db.execute(text("SELECT auth_version FROM panel_accounts WHERE id=:id"),
                              {"id":account})).scalar_one()
    claims={"role":"admin","auth_provider":"legacy_admin","account_id":str(actor)}
    try:
        listing=await admin_accounts.list_customer_accounts(admin=actor)
        assert any(x["id"]==account for x in listing["items"])
        with pytest.raises(HTTPException) as forbidden:
            admin_accounts.require_platform_owner({"role":"member","account_id":str(account)})
        assert forbidden.value.status_code==403
        result=await admin_accounts.manage_account(account,"suspend",
            admin_accounts.AccountAction(expected_version=row,reason="Investigação de segurança"),actor)
        assert result["state"]=="suspended"
        with pytest.raises(HTTPException) as invalid:
            await account_auth.current_account(str(account),row)
        assert invalid.value.status_code==401
        with pytest.raises(HTTPException) as stale:
            await admin_accounts.manage_account(account,"reactivate",
                admin_accounts.AccountAction(expected_version=row,reason="Solicitação do cliente"),actor)
        assert stale.value.status_code==409
        result=await admin_accounts.manage_account(account,"archive",
            admin_accounts.AccountAction(expected_version=row+1,reason="Exclusão solicitada",
                                         confirmation="EXCLUIR"),actor)
        assert result["reversible_days"]==30
        async with ctx.migration.connect() as db:
            control=(await db.execute(text("""
                SELECT state,archived_at,purge_after FROM admin_account_controls WHERE account_id=:id
            """),{"id":account})).mappings().one()
            assert control["state"]=="archived"
            assert (control["purge_after"]-control["archived_at"]).days==30
            assert (await db.execute(text("SELECT count(*) FROM panel_accounts WHERE id=:id"),
                                     {"id":account})).scalar_one()==1
        restored=await admin_accounts.manage_account(account,"restore",
            admin_accounts.AccountAction(expected_version=row+2,reason="Solicitação de recuperação"),actor)
        assert restored["state"]=="active"
        detail=await admin_accounts.account_detail(account,actor)
        assert [e["action"] for e in detail["history"]][:3]==["restore","archive","suspend"]
    finally:
        async with ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM admin_account_audit WHERE account_id=:id"),{"id":account})
            await db.execute(text("DELETE FROM admin_account_controls WHERE account_id=:id"),{"id":account})
