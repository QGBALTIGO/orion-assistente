"""Reviewed 0049→0050 release, private backup and explicitly authorized grants.

The initial Essencial grants were authorized by the operator on 2026-10-29.
They are manual access, never a Cakto subscription, charge or account mutation.
Normal restarts only verify the completed marker and schema contracts.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID, uuid5

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from app.legacy_admin_identity import configuration_binding
from app.plan_permissions import derive_plan
from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_appointment_communications import schema_ready as previous_ready

PREVIOUS = "0049_preserved_phone_guard"
TARGET = "0050_plan_permissions"
OPERATION_NAMESPACE = UUID("23bed91e-f253-52af-96ce-cf11201893fd")
REASON = "Atribuição inicial ao Essencial autorizada pelo administrador em 29/10/2026; sem cobrança."
TABLES = ("tenant_plan_controls", "tenant_plan_audit", "plan_permissions_release")


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator")); db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(1330792783,50)")).scalar_one():
                raise RuntimeError("plan_release_busy")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(1330792783,50)")); db.commit()
    finally:
        engine.dispose()


def schema_ready(raw, *, completed=True):
    engine = create_engine(raw, poolclass=NullPool, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            for table in TABLES:
                row = db.execute(text("""SELECT relrowsecurity AND relforcerowsecurity
                    AND pg_get_userbyid(relowner)='app_migrator'
                    AND NOT has_table_privilege('app_runtime',oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
                    FROM pg_class WHERE oid=to_regclass(:table)"""), {"table": "public." + table}).scalar_one_or_none()
                if row is not True:
                    return False
            ready = db.execute(text("""SELECT
                has_table_privilege('app_support','public.tenant_plan_audit','SELECT,INSERT')
                AND NOT has_table_privilege('app_support','public.tenant_plan_audit','UPDATE,DELETE,TRUNCATE')
                AND NOT has_column_privilege('app_support','public.tenant_plan_controls','platform_account_id','UPDATE')
                AND NOT has_table_privilege('app_support','public.plan_permissions_release','INSERT,UPDATE,DELETE')
                AND (SELECT count(*)=4 FROM pg_trigger WHERE NOT tgisinternal AND tgenabled='O'
                    AND tgfoid='public.orion_lock_plan_change()'::regprocedure
                    AND tgname IN ('tenant_plan_serial','commercial_plan_serial','cakto_plan_serial','trial_plan_serial'))
                AND (SELECT count(*)=2 FROM pg_proc WHERE oid IN
                    ('public.orion_plan_snapshot(uuid)'::regprocedure,'public.orion_plan_release_ready()'::regprocedure)
                    AND prosecdef AND pg_get_userbyid(proowner)='app_migrator'
                    AND proconfig=ARRAY['search_path=pg_catalog']::text[]
                    AND has_function_privilege('app_runtime',oid,'EXECUTE')
                    AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) a
                        WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))""")).scalar_one()
            if not ready:
                return False
            return (not completed or db.execute(text("SELECT public.orion_plan_release_ready()")).scalar_one()) and previous_ready(raw)
    finally:
        engine.dispose()


def bootstrap(db, username, digest):
    """One atomic, idempotent initial assignment. Caller must be app_migrator.

    Existing tenant status and all business/account/session rows stay intact.
    Companies created after this snapshot follow the normal access rules.
    """
    if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
        raise RuntimeError("plan_bootstrap_role_denied")
    marker = db.execute(text("SELECT bootstrap_count FROM public.plan_permissions_release WHERE marker=:marker"), {"marker": TARGET}).scalar_one_or_none()
    if marker is not None:
        return {"replayed": True, "count": marker}
    actor = db.execute(text("""SELECT a.id FROM public.panel_accounts a
        LEFT JOIN public.admin_account_controls c ON c.account_id=a.id
        WHERE a.auth_provider='legacy_admin' AND a.role='admin'
          AND a.legacy_username=:username AND a.legacy_credential_digest=:digest
          AND a.email IS NULL AND a.password_hash IS NULL AND coalesce(c.state,'active')='active'"""),
        {"username": username, "digest": digest}).scalar_one_or_none()
    if actor is None:
        raise RuntimeError("plan_bootstrap_verified_administrator_required")
    companies = db.execute(text("""SELECT t.id, EXISTS(SELECT 1 FROM public.tenant_memberships m
        WHERE m.tenant_id=t.id AND m.account_id=:actor AND m.role='owner' AND m.active) AS platform
        FROM public.tenants t ORDER BY t.id"""), {"actor": actor}).mappings().all()
    if not companies or not any(row["platform"] for row in companies):
        raise RuntimeError("plan_bootstrap_administrator_workspace_required")
    # Acquire the same plan locks before reading or changing any right.
    for row in companies:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "orion-plan-v1:" + str(row["id"])})
    if db.execute(text("SELECT EXISTS(SELECT 1 FROM public.tenant_plan_controls)")).scalar_one():
        raise RuntimeError("plan_bootstrap_unexpected_existing_grants")
    now = datetime.now(timezone.utc)
    manifest = []
    for row in companies:
        tenant = row["id"]
        before = derive_plan(db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"), {"tenant": tenant}).scalar_one(), now=now)
        # Never overwrite paid/trial rights if the inventory changed before release.
        if before["source"] != "none":
            raise RuntimeError("plan_bootstrap_commercial_inventory_changed")
        platform = row["platform"]
        if platform:
            db.execute(text("INSERT INTO public.tenant_plan_controls(tenant_id,platform_account_id) VALUES(:tenant,:actor)"), {"tenant": tenant, "actor": actor})
        else:
            db.execute(text("""INSERT INTO public.tenant_plan_controls
                (tenant_id,manual_plan_code,manual_started_at,manual_actor_id,manual_reason)
                VALUES(:tenant,'essencial',:now,:actor,:reason)"""), {"tenant": tenant, "now": now, "actor": actor, "reason": REASON})
        after = derive_plan(db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"), {"tenant": tenant}).scalar_one(), now=now)
        action = "bootstrap_platform" if platform else "bootstrap_essential"
        item = {"tenant_id": str(tenant), "action": action, "actor_id": str(actor)}
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        db.execute(text("""INSERT INTO public.tenant_plan_audit
            (tenant_id,actor_id,operation_id,action,previous_plan,new_plan,reason,access_type,request_fingerprint)
            VALUES(:tenant,:actor,:operation,:action,CAST(:previous AS jsonb),CAST(:new AS jsonb),:reason,:access,:fingerprint)"""),
            {"tenant": tenant, "actor": actor, "operation": uuid5(OPERATION_NAMESPACE, str(tenant)), "action": action,
             "previous": json.dumps(before, default=str), "new": json.dumps(after, default=str), "reason": REASON,
             "access": None if platform else "indefinite", "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()})
        manifest.append(item)
    db.execute(text("""INSERT INTO public.plan_permissions_release
        (marker,completed_at,bootstrap_count,bootstrap_admin,manifest_hash) VALUES(:marker,:now,:count,:actor,:hash)"""),
        {"marker": TARGET, "now": now, "count": len(companies), "actor": actor,
         "hash": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()})
    return {"replayed": False, "count": len(companies)}


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current == TARGET and schema_ready(raw):
                print("orion_plan_permissions_0050_ready", flush=True); return 0
            if os.getenv("ORION_RUN_PLAN_PERMISSIONS_MIGRATION") != "1":
                return 3
            if current not in {PREVIOUS, TARGET} or not previous_ready(raw):
                return 2
            # Validate the binding before DDL and keep its credential digest private.
            username, digest = configuration_binding()
            if current == PREVIOUS:
                with tempfile.TemporaryDirectory(prefix="orion-plan-permissions-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump", backup_context="plan-permissions-0050",
                        schema_revision=PREVIOUS, preserve_ownership=True)
                if migrated_version(raw) != PREVIOUS:
                    return 3
                run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}, timeout=600, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if run.returncode or migrated_version(raw) != TARGET:
                    return 3
            if not schema_ready(raw, completed=False):
                return 3
            engine = create_engine(raw, poolclass=NullPool, connect_args={"options": "-c statement_timeout=60000 -c lock_timeout=15000"})
            try:
                with engine.begin() as db:
                    db.execute(text("SET LOCAL ROLE app_migrator"))
                    result = bootstrap(db, username, digest)
            finally:
                engine.dispose()
            if not schema_ready(raw):
                return 3
            print("orion_plan_permissions_0050_backup_grants_verified count=" + str(result["count"]), flush=True)
            return 0
    except Exception:
        print("orion_plan_permissions_0050_failed_closed", file=sys.stderr); return 3


if __name__ == "__main__":
    raise SystemExit(main())
