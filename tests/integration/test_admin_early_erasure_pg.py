"""Immediate account erasure: no live customer accounts or production services."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import admin_accounts, account_auth
from tests.integration.test_onboarding_postgres import onboarding_db

pytestmark=pytest.mark.integration


@pytest.mark.asyncio
async def test_archived_account_can_be_permanently_erased_and_email_reused(onboarding_db):
    ctx=onboarding_db
    account=ctx.actor.account_id
    actor=uuid4()
    async with ctx.migration.connect() as db:
        original=(await db.execute(text("""
            SELECT email,password_hash,auth_version FROM panel_accounts WHERE id=:id
        """),{"id":account})).mappings().one()
    new_id=uuid4()
    try:
        archived=await admin_accounts.manage_account(account,"archive",
            admin_accounts.AccountAction(expected_version=original["auth_version"],
                reason="Solicitação de encerramento da conta",confirmation="EXCLUIR"),actor)
        assert archived["state"]=="archived"
        with pytest.raises(HTTPException) as no_consent:
            await admin_accounts.manage_account(account,"erase_now",
                admin_accounts.AccountAction(expected_version=original["auth_version"]+1,
                    reason="Exclusão definitiva solicitada",confirmation="EXCLUIR"),actor)
        assert no_consent.value.status_code==422
        with pytest.raises(HTTPException) as stale:
            await admin_accounts.manage_account(account,"erase_now",
                admin_accounts.AccountAction(expected_version=original["auth_version"],
                    reason="Exclusão definitiva solicitada",
                    confirmation="EXCLUIR PERMANENTEMENTE"),actor)
        assert stale.value.status_code==409
        erased=await admin_accounts.manage_account(account,"erase_now",
            admin_accounts.AccountAction(expected_version=original["auth_version"]+1,
                reason="Exclusão definitiva solicitada",
                confirmation="EXCLUIR PERMANENTEMENTE"),actor)
        assert erased=={"ok":True,"state":"erased","email_reusable":True,"reversible":False}
        async with ctx.migration.connect() as db:
            row=(await db.execute(text("""
                SELECT a.email,a.name,a.password_hash,a.verified_at,a.auth_version,
                       c.state,c.purged_at,c.purge_after
                FROM panel_accounts a JOIN admin_account_controls c ON c.account_id=a.id
                WHERE a.id=:id
            """),{"id":account})).mappings().one()
        assert row["email"]!=original["email"]
        assert row["email"].endswith("@accounts.invalid")
        assert row["name"]=="Conta excluída"
        assert row["password_hash"].startswith("!deleted:")
        assert row["verified_at"] is None
        assert row["state"]=="erased" and row["purged_at"] is not None
        assert row["purge_after"] is None
        assert row["auth_version"]==original["auth_version"]+2
        with pytest.raises(HTTPException) as revoked:
            await account_auth.current_account(str(account),row["auth_version"])
        assert revoked.value.status_code==401
        with pytest.raises(HTTPException) as immutable:
            await admin_accounts.manage_account(account,"restore",
                admin_accounts.AccountAction(expected_version=row["auth_version"],
                    reason="Tentativa de restaurar conta eliminada"),actor)
        assert immutable.value.status_code==409
        # The old email is free to register a NEW independent account.
        async with ctx.migration.begin() as db:
            await db.execute(text("""
                INSERT INTO panel_accounts(id,name,email,password_hash,role,registration_pending)
                VALUES(:id,'Novo cadastro',:email,:hash,'member',true)
            """),{"id":new_id,"email":original["email"],"hash":original["password_hash"]})
        async with ctx.migration.connect() as db:
            assert (await db.execute(text("""
                SELECT count(*) FROM panel_accounts WHERE email=:email AND id=:id
            """),{"email":original["email"],"id":new_id})).scalar_one()==1
            assert (await db.execute(text("""
                SELECT count(*) FROM tenant_memberships WHERE account_id=:id AND active
            """),{"id":account})).scalar_one()==0
            assert (await db.execute(text("""
                SELECT count(*) FROM tenants WHERE id=:id
            """),{"id":ctx.a})).scalar_one()==1
    finally:
        async with ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:id"),{"id":new_id})
            await db.execute(text("DELETE FROM admin_account_audit WHERE account_id=:id"),{"id":account})
            await db.execute(text("DELETE FROM admin_account_controls WHERE account_id=:id"),{"id":account})


@pytest.mark.asyncio
async def test_legal_hold_prevents_early_erasure(onboarding_db):
    ctx=onboarding_db
    account=ctx.actor.account_id
    actor=uuid4()
    async with ctx.migration.connect() as db:
        original=(await db.execute(text("""
            SELECT email,auth_version FROM panel_accounts WHERE id=:id
        """),{"id":account})).mappings().one()
    try:
        await admin_accounts.manage_account(account,"archive",
            admin_accounts.AccountAction(expected_version=original["auth_version"],
                reason="Arquivamento administrativo",confirmation="EXCLUIR"),actor)
        await admin_accounts.manage_account(account,"hold",
            admin_accounts.AccountAction(expected_version=original["auth_version"]+1,
                reason="Retenção por obrigação legal"),actor)
        with pytest.raises(HTTPException) as blocked:
            await admin_accounts.manage_account(account,"erase_now",
                admin_accounts.AccountAction(expected_version=original["auth_version"]+2,
                    reason="Solicitação de exclusão permanente",
                    confirmation="EXCLUIR PERMANENTEMENTE"),actor)
        assert blocked.value.status_code==409
        async with ctx.migration.connect() as db:
            assert (await db.execute(text("""
                SELECT email FROM panel_accounts WHERE id=:id
            """),{"id":account})).scalar_one()==original["email"]
    finally:
        async with ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM admin_account_audit WHERE account_id=:id"),{"id":account})
            await db.execute(text("DELETE FROM admin_account_controls WHERE account_id=:id"),{"id":account})
