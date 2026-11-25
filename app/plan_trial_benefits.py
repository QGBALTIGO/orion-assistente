"""Eligibility for the owner-activated evaluation, separate from billing."""
import hashlib
import hmac
import os

from sqlalchemy import text


def identity_digest(email):
    key = os.environ.get("ORION_TRIAL_IDENTITY_KEY", "")
    if len(key) < 32 or not email:
        return None
    return hmac.new(key.encode(), ("orion-trial:v1\0" + email.strip().lower()).encode(),
                    hashlib.sha256).hexdigest()


async def automatic_trials_ready(db):
    return (os.environ.get("ORION_BARBER_SELF_SERVICE_TRIAL", "0") == "1"
            and await ledger_ready(db))


async def ledger_ready(db):
    if len(os.environ.get("ORION_TRIAL_IDENTITY_KEY", "")) < 32:
        return False
    return (await db.execute(text(
        "SELECT to_regclass('public.orion_trial_benefits') IS NOT NULL"
    ))).scalar_one()


async def verified_trial_owner(db, tenant_id, account_id):
    return (await db.execute(text("""
        SELECT a.name, a.email, t.name AS company_name
        FROM panel_accounts a JOIN tenants t ON t.id=:tenant
        JOIN business_onboarding o ON o.tenant_id=t.id
        WHERE a.id=:account AND a.role='member' AND a.auth_provider='password'
          AND a.verified_at IS NOT NULL AND NOT a.registration_pending
          AND o.configuration->>'niche'='barbearia'
          AND (t.status='active' OR (t.status='paused' AND o.is_new AND o.completed_at IS NULL))
        FOR SHARE OF a
    """), {"account": account_id, "tenant": tenant_id})).mappings().one_or_none()


async def prior_trial_or_purchase(db, tenant_id, account_id):
    return (await db.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM orion_plan_trial_requests WHERE tenant_id=:tenant OR account_id=:account
            UNION ALL SELECT 1 FROM subscriptions WHERE tenant_id=:tenant
            UNION ALL SELECT 1 FROM orion_prepaid_pix_orders
              WHERE tenant_id=:tenant AND state IN ('paid','refunded')
            UNION ALL SELECT 1 FROM orion_prepaid_pix_access WHERE tenant_id=:tenant
        )
    """), {"tenant": tenant_id, "account": account_id})).scalar_one()


async def claim_benefit(db, digest):
    return (await db.execute(text("""
        INSERT INTO public.orion_trial_benefits (identity_digest) VALUES (:digest)
        ON CONFLICT DO NOTHING RETURNING identity_digest
    """), {"digest": digest})).scalar_one_or_none() is not None


async def eligible(db, tenant_id, account_id, owner):
    proof = identity_digest(owner["email"]) if owner else None
    if not proof or await prior_trial_or_purchase(db, tenant_id, account_id):
        return False
    return not (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM public.orion_trial_benefits WHERE identity_digest=:digest)
    """), {"digest": proof})).scalar_one()


async def remember_trial_before_erasure(db, account_id):
    if not await ledger_ready(db):
        return
    email = (await db.execute(text("""
        SELECT email FROM panel_accounts WHERE id=:account AND verified_at IS NOT NULL
          AND EXISTS(SELECT 1 FROM orion_plan_trial_requests WHERE account_id=:account)
    """), {"account": account_id})).scalar_one_or_none()
    if proof := identity_digest(email):
        await claim_benefit(db, proof)


async def finish_for_payment(db, tenant_id):
    await db.execute(text("""
        UPDATE orion_plan_trial_requests SET status='superseded',version=version+1
        WHERE tenant_id=:tenant AND status='active'
    """), {"tenant": tenant_id})
