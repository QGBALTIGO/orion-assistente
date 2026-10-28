"""Irreversible account-login erasure after the 30-day recovery window.

The personal login identity and all its recoverable credentials are eliminated.
Only non-login relational tombstones and legally necessary tenant/transaction
records survive; these retain their own independent retention obligations.
A legal hold pauses erasure. No messages or customer automations are sent.
"""
from sqlalchemy import text
from app.account_auth import _storage


async def erase_due_account_logins(_ctx=None, *, batch_size=50):
    """Bounded, idempotent and concurrency-safe cleanup for the Orion worker.

    A separate account row and archived-state lock are acquired atomically.
    A restored account or new legal hold cannot be erased by a stale scan.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("Invalid erasure batch size")
    erased=0
    async with _storage()() as db:
        # A worker can restart ahead of an API migration. Do nothing until the
        # reviewed 0042 schema is present; it must not run DDL itself.
        ready=(await db.execute(text("""
            SELECT to_regclass('public.admin_account_controls') IS NOT NULL
        """))).scalar_one()
        await db.rollback()
        if not ready:
            return 0
        ids=(await db.execute(text("""
            SELECT account_id FROM admin_account_controls
            WHERE state='archived' AND legal_hold=false AND purge_after<=now()
            ORDER BY purge_after,account_id LIMIT :limit
        """),{"limit":batch_size})).scalars().all()
        await db.rollback()

    for account in ids:
        async with _storage()() as session:
            async with session.begin():
                row=(await session.execute(text("""
                    SELECT a.id,c.purge_after,c.state,c.legal_hold
                    FROM panel_accounts a
                    JOIN admin_account_controls c ON c.account_id=a.id
                    WHERE a.id=:account AND a.role='member'
                      AND c.state='archived' AND c.legal_hold=false
                      AND c.purge_after<=now()
                    FOR UPDATE OF a,c SKIP LOCKED
                """),{"account":account})).mappings().first()
                if not row:continue

                # Login identifiers and the old password are irreversibly
                # replaced, while referenced UUIDs keep FK/audit integrity.
                await session.execute(text("""
                    UPDATE panel_accounts
                    SET name='Conta excluída', email='deleted+'||replace(id::text,'-','')||'@accounts.invalid',
                        password_hash='!deleted:'||id::text,
                        verified_at=NULL,registration_pending=false,
                        auth_version=auth_version+1
                    WHERE id=:account
                """),{"account":account})
                await session.execute(text("""
                    UPDATE panel_email_tokens SET used_at=COALESCE(used_at,now())
                    WHERE account_id=:account AND used_at IS NULL
                """),{"account":account})
                await session.execute(text("""
                    UPDATE tenant_memberships SET active=false
                    WHERE account_id=:account AND active
                """),{"account":account})
                await session.execute(text("""
                    UPDATE admin_account_controls SET
                        state='erased',archived_at=NULL,purge_after=NULL,
                        legal_hold=false,purged_at=now(),updated_at=now(),
                        reason='Credenciais e identidade de login excluídas após 30 dias'
                    WHERE account_id=:account AND state='archived'
                """),{"account":account})
                await session.execute(text("""
                    INSERT INTO admin_account_audit(actor_id,account_id,action,reason)
                    VALUES(NULL,:account,'erase','Exclusão irreversível dos identificadores de login ao fim do prazo de recuperação')
                """),{"account":account})
                erased+=1
    return erased
