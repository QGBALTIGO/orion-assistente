"""Publish only the additive Barbearia plan lifecycle on the approved database."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, perform_backup, storage_values
from scripts.backup_and_migrate_exceptional_overlap import release_lock
from scripts.backup_and_migrate_subscription_audience import audience_contract, preserved_contract, read_contract


SOURCE = "0093_dental_reception"
PREVIOUS = "0097_confirmed_billing_state"
TARGET = "0098_manual_pix_successor"
CONTRACT = """
SELECT count(*)=3 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity
    AND NOT has_table_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,DELETE')
    AND has_table_privilege('app_support',c.oid,'SELECT')
    AND NOT has_table_privilege('app_support',c.oid,'DELETE,TRUNCATE'))
    AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='manual_term_required' AND tgenabled='O'
                AND tgrelid='public.tenant_plan_controls'::regclass)
    AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='zz_preserve_manual_pix_term' AND tgenabled='O'
                AND tgrelid='public.tenant_plan_controls'::regclass)
    AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_manual_pix_anchor' AND convalidated
                AND conrelid='public.orion_prepaid_pix_orders'::regclass)
    AND coalesce(has_function_privilege('app_runtime',to_regprocedure('public.orion_manual_schedule(uuid)'),'EXECUTE'),false)
    AND coalesce(has_function_privilege('app_runtime',to_regprocedure('public.orion_confirmed_billing_snapshot(uuid)'),'EXECUTE'),false)
FROM pg_class c WHERE c.oid IN (
    to_regclass('public.orion_trial_benefits'),to_regclass('public.orion_welcome_outbox'),
    to_regclass('public.orion_confirmed_billing_state'))
"""


def aggregate_baseline(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=12000"
    })
    try:
        with engine.connect() as db:
            return dict(db.execute(text("""
                WITH entitlement AS (
                    SELECT t.id, public.orion_plan_snapshot(t.id) AS plan,
                           public.orion_prepaid_plan_snapshot(t.id) AS pix
                    FROM tenants t
                    JOIN business_onboarding o ON o.tenant_id=t.id
                    WHERE o.configuration->>'niche'='barbearia'
                ), limits AS (
                    SELECT plan,CASE
                        WHEN plan->'manual'->>'plan_code' IS NOT NULL
                         AND (plan->'manual'->>'expires_at' IS NULL OR
                              (plan->'manual'->>'expires_at')::timestamptz > now())
                            THEN (SELECT p.max_professionals FROM plans p
                                  WHERE p.code='orion_'||(plan->'manual'->>'plan_code'))
                        WHEN plan->'commercial'->>'cakto_bound'='true'
                         AND (plan->'commercial'->>'last_verified_payment_at')::timestamptz>now()-interval '30 days'
                            THEN (plan->'commercial'->>'max_professionals')::int
                        WHEN (pix->>'paid_until')::timestamptz>now()
                            THEN (SELECT p.max_professionals FROM plans p WHERE p.code='orion_'||(pix->>'plan_code'))
                        WHEN plan->'trial'->>'status'='active' AND (plan->'trial'->>'ends_at')::timestamptz>now()
                            THEN (SELECT p.max_professionals FROM plans p WHERE p.code='orion_essencial')
                    END AS maximum FROM entitlement
                )
                SELECT
                    (SELECT count(*) FROM tenants) AS companies,
                    (SELECT count(*) FROM subscriptions WHERE status='active') AS recurring_active,
                    (SELECT count(*) FROM subscriptions WHERE status='past_due') AS recurring_late,
                    (SELECT count(*) FROM subscriptions WHERE status='suspended') AS recurring_suspended,
                    (SELECT count(*) FROM orion_plan_trial_requests WHERE status='active' AND ends_at>now()
                        AND ends_at-started_at<=interval '72 hours') AS legacy_trials_current,
                    (SELECT count(*) FROM orion_plan_trial_requests WHERE status='pending') AS trials_waiting_review,
                    (SELECT count(*) FROM orion_plan_trial_requests WHERE started_at IS NOT NULL) AS trials_used,
                    (SELECT count(*) FROM orion_prepaid_pix_access WHERE paid_until>now()) AS prepaid_current,
                    (SELECT count(*) FROM panel_accounts WHERE role='member' AND verified_at IS NULL) AS unverified_members,
                    (SELECT count(*) FROM tenants t LEFT JOIN business_onboarding o ON o.tenant_id=t.id
                        WHERE o.configuration->>'niche' IS NULL) AS companies_without_niche,
                    (SELECT count(*) FROM limits WHERE maximum<(plan->>'active_professionals')::int) AS barber_over_limit,
                    (SELECT count(*) FROM orion_cakto_bindings b JOIN subscriptions s ON s.tenant_id=b.tenant_id
                        JOIN plans p ON p.id=s.plan_id
                        WHERE b.subscription_id<>s.id OR b.external_subscription_id IS DISTINCT FROM s.external_subscription_id
                            OR p.code<>'orion_'||b.plan_code OR s.last_verified_payment_at IS NULL) AS inconsistent_cakto_bindings,
                    (SELECT count(*) FROM tenant_plan_controls WHERE manual_plan_code IS NOT NULL
                        AND manual_expires_at>now()) AS manual_timed,
                    (SELECT count(*) FROM tenant_plan_controls WHERE manual_plan_code IS NOT NULL
                        AND manual_expires_at IS NULL) AS manual_without_expiry
            """)).mappings().one())
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        runtime = make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host, runtime.port or 5432, runtime.database) != (url.host, url.port or 5432, url.database):
            raise RuntimeError("plan_cycle_database_scope_mismatch")
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {SOURCE, PREVIOUS, TARGET} or not preserved_contract(raw) or not audience_contract(raw):
                raise RuntimeError("plan_cycle_unapproved_predecessor")
            baseline = aggregate_baseline(raw)
            print("orion_plan_cycle_baseline " + " ".join(f"{field}={count}" for field, count in baseline.items()), flush=True)
            if "--audit" in sys.argv:
                return 0
            if current != TARGET:
                with tempfile.TemporaryDirectory(prefix="orion-plan-cycle-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="orion-barber-plan-cycle", schema_revision=current,
                                   preserve_ownership=True)
                if migrated_version(raw) != current:
                    raise RuntimeError("plan_cycle_predecessor_changed_after_backup")
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                               env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)
            if migrated_version(raw) != TARGET or not read_contract(raw, CONTRACT):
                raise RuntimeError("plan_cycle_schema_not_ready")
        print("orion_plan_cycle_schema_ready", flush=True)
        if os.getenv("ORION_BARBER_MANUAL_PIX_ENABLED", "0") == "1":
            subprocess.run([sys.executable, "-m", "scripts.probe_prepaid_pix_read_only"],
                           timeout=60, check=True)
        return 0
    except Exception:
        print("orion_plan_cycle_predeploy_failed_closed", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
