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
        listing=await admin_accounts.list_customer_accounts(q='',status='all',page=1,page_size=20,admin=actor)
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


@pytest.mark.asyncio
async def test_legal_hold_blocks_due_erasure_and_release_triggers_irreversible_login_erasure(onboarding_db):
    from app.account_erasure import erase_due_account_logins
    ctx=onboarding_db
    account=ctx.actor.account_id
    administrator=uuid4()
    async with ctx.migration.connect() as db:
        before=(await db.execute(text("SELECT auth_version,email FROM panel_accounts WHERE id=:a"),
                                 {"a":account})).mappings().one()
    token_id, outbox_id = uuid4(), uuid4()
    original_token_digest, original_recipient_digest = "f"*64, "e"*64
    async with ctx.migration.begin() as db:
        await db.execute(text("""
            INSERT INTO panel_email_tokens(id,account_id,token_digest,purpose,expires_at)
            VALUES(:token,:account,:digest,'reset',now()+interval '1 day')
        """), {"token":token_id,"account":account,"digest":original_token_digest})
        await db.execute(text("""
            INSERT INTO panel_email_outbox(id,kind,recipient_digest,account_id,token_id,
                                           dedupe_key,payload_enc,expires_at)
            VALUES(:outbox,'reset',:digest,:account,:token,:key,'synthetic-encrypted-payload',
                   now()+interval '1 day')
        """), {"outbox":outbox_id,"account":account,"token":token_id,
                "digest":original_recipient_digest,"key":"erasure-test:"+str(outbox_id)})
    try:
        await admin_accounts.manage_account(
            account,"archive",
            admin_accounts.AccountAction(expected_version=before["auth_version"],
                reason="Pedido de exclusão de conta",confirmation="EXCLUIR"),administrator)
        hold=await admin_accounts.manage_account(account,"hold",
            admin_accounts.AccountAction(expected_version=before["auth_version"]+1,
                reason="Obrigação fiscal pendente"),administrator)
        assert hold["legal_hold"] is True
        # Emulate the elapsed recovery period only in this disposable database.
        async with ctx.migration.begin() as db:
            await db.execute(text("""
                UPDATE admin_account_controls SET purge_after=now()-interval '1 day'
                WHERE account_id=:a
            """),{"a":account})
        assert await erase_due_account_logins(batch_size=50)==0
        async with ctx.migration.connect() as db:
            still=(await db.execute(text("SELECT email FROM panel_accounts WHERE id=:a"),
                                    {"a":account})).scalar_one()
            assert still==before["email"]
        released=await admin_accounts.manage_account(account,"release_hold",
            admin_accounts.AccountAction(expected_version=before["auth_version"]+2,
                reason="Retenção legal encerrada"),administrator)
        assert released["legal_hold"] is False
        assert await erase_due_account_logins(batch_size=50)==1
        assert await erase_due_account_logins(batch_size=50)==0
        async with ctx.migration.connect() as db:
            erased=(await db.execute(text("""
                SELECT a.email,a.name,a.password_hash,a.verified_at,a.auth_version,c.state,c.purged_at,
                       c.archived_at,c.purge_after,m.active
                FROM panel_accounts a JOIN admin_account_controls c ON c.account_id=a.id
                JOIN tenant_memberships m ON m.account_id=a.id
                WHERE a.id=:a
            """),{"a":account})).mappings().one()
            assert erased["email"]!=before["email"]
            assert erased["email"].endswith("@accounts.invalid")
            assert erased["name"]=="Conta excluída"
            assert erased["password_hash"].startswith("!deleted:")
            assert erased["verified_at"] is None
            assert erased["state"]=="erased"
            assert erased["purged_at"] is not None
            assert erased["archived_at"] is None and erased["purge_after"] is None
            assert erased["active"] is False
            assert erased["auth_version"]==before["auth_version"]+4
        async with ctx.migration.connect() as db:
            token=(await db.execute(text("""
                SELECT used_at,token_digest FROM panel_email_tokens WHERE id=:id
            """),{"id":token_id})).mappings().one()
            mail=(await db.execute(text("""
                SELECT account_id,token_id,recipient_digest,payload_enc,status
                FROM panel_email_outbox WHERE id=:id
            """),{"id":outbox_id})).mappings().one()
        assert token["used_at"] is not None
        assert token["token_digest"] != original_token_digest
        assert mail["account_id"] is None and mail["token_id"] is None
        assert mail["recipient_digest"] == "0"*64
        assert mail["payload_enc"] is None and mail["status"] == "suppressed"
        with pytest.raises(HTTPException) as blocked:
            await account_auth.current_account(str(account),before["auth_version"]+4)
        assert blocked.value.status_code==401
        with pytest.raises(HTTPException) as immutable:
            await admin_accounts.manage_account(account,"restore",
                admin_accounts.AccountAction(expected_version=before["auth_version"]+4,
                    reason="Tentativa após prazo"),administrator)
        assert immutable.value.status_code==409
        detail=await admin_accounts.account_detail(account,administrator)
        assert detail["account"]["state"]=="erased"
        assert detail["history"][0]["action"]=="erase"
        # The tenant itself remains for its independent legal retention rules.
        async with ctx.migration.connect() as db:
            assert (await db.execute(text("SELECT count(*) FROM tenants WHERE id=:t"),
                                     {"t":ctx.a})).scalar_one()==1
    finally:
        async with ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM panel_email_outbox WHERE id=:id"),{"id":outbox_id})
            await db.execute(text("DELETE FROM panel_email_tokens WHERE id=:id"),{"id":token_id})
            await db.execute(text("DELETE FROM admin_account_audit WHERE account_id=:a"),{"a":account})
            await db.execute(text("DELETE FROM admin_account_controls WHERE account_id=:a"),{"a":account})
