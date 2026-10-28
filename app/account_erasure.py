"""Irreversible erasure of a customer's login identity.

The original e-mail becomes available for a new registration immediately
after a committed erasure. Referenced tenant/transaction records are retained
according to their independent legal obligations.
"""
from uuid import UUID
from sqlalchemy import text
from app.account_auth import _storage


async def _erase_locked(session, account, actor, reason):
    # Login identifiers and the old password are irreversibly
    # replaced, while referenced UUIDs keep FK/audit integrity.
    await session.execute(text("""
        UPDATE panel_accounts
        SET name='Conta excluída', email='deleted+'||replace(id::text,'-','')||'@accounts.invalid',
            password_hash='!deleted:'||id::text,
            verified_at=NULL,registration_pending=false,
            auth_version=auth_version+1
        WHERE id=:account
    """),{"account":account,"actor":actor,"reason":reason})
    # Stop queued account e-mails and remove recoverable recipient
    # data before invalidating one-time codes. The outbox worker
    # also locks outbox rows before their token rows.
    await session.execute(text("""
        UPDATE panel_email_outbox SET
            status=CASE WHEN status='pending' THEN 'suppressed' ELSE status END,
            payload_enc=NULL,recipient_digest=repeat('0',64),
            token_id=NULL,account_id=NULL
        WHERE account_id=:account
    """),{"account":account,"actor":actor,"reason":reason})
    await session.execute(text("""
        UPDATE panel_email_tokens SET
            used_at=COALESCE(used_at,now()),
            token_digest=md5(gen_random_uuid()::text)||md5(gen_random_uuid()::text)
        WHERE account_id=:account
    """),{"account":account,"actor":actor,"reason":reason})
    await session.execute(text("""
        UPDATE tenant_memberships SET active=false
        WHERE account_id=:account AND active
    """),{"account":account,"actor":actor,"reason":reason})
    await session.execute(text("""
        UPDATE admin_account_controls SET
            state='erased',archived_at=NULL,purge_after=NULL,
            legal_hold=false,purged_at=now(),updated_at=now(),
            reason=:reason
        WHERE account_id=:account AND state='archived'
    """),{"account":account,"actor":actor,"reason":reason})
    await session.execute(text("""
        INSERT INTO admin_account_audit(actor_id,account_id,action,reason)
        VALUES(:actor,:account,'erase',:reason)
    """),{"account":account,"actor":actor,"reason":reason})
    

async def erase_customer_login_now(account: UUID, expected_version: int,
                                   actor: UUID, reason: str) -> bool:
    """Admin-only caller must authorize before invoking this helper.

    Locks the account AND its control row, revalidates version and legal hold,
    then removes all login identifiers atomically. Does not delete company,
    financial or audit records with independent retention requirements.
    """
    async with _storage()() as session:
        async with session.begin():
            row=(await session.execute(text("""
                SELECT a.auth_version,c.state,c.legal_hold
                FROM panel_accounts a JOIN admin_account_controls c ON c.account_id=a.id
                WHERE a.id=:account AND a.role='member'
                  AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
                  AND c.state='archived' AND c.legal_hold=false
                FOR UPDATE OF a,c
            """),{"account":account})).mappings().first()
            if not row or row["auth_version"]!=expected_version:
                return False
            await _erase_locked(session,account,actor,reason)
    return True


async def erase_due_account_logins(_ctx=None, *, batch_size=50):
    """Worker: erase only archived accounts past their recovery deadline."""
    if not 1 <= batch_size <= 100:
        raise ValueError("Invalid erasure batch size")
    erased=0
    async with _storage()() as db:
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
                    SELECT a.id FROM panel_accounts a
                    JOIN admin_account_controls c ON c.account_id=a.id
                    WHERE a.id=:account AND a.role='member'
                      AND c.state='archived' AND c.legal_hold=false
                      AND c.purge_after<=now()
                    FOR UPDATE OF a,c SKIP LOCKED
                """),{"account":account})).mappings().first()
                if not row:
                    continue
                await _erase_locked(session,account,None,
                    "Exclusão irreversível dos identificadores de login ao fim do prazo de recuperação")
                erased+=1
    return erased
