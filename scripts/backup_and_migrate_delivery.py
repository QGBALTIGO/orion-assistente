"""Publish only the approved Delivery revisions after a verified private archive."""
from __future__ import annotations

from itertools import pairwise
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, perform_backup, storage_values
from scripts.backup_and_migrate_exceptional_overlap import release_lock
from scripts.backup_and_migrate_plan_cycle import CONTRACT as PLAN_CYCLE_CONTRACT
from scripts.backup_and_migrate_subscription_audience import audience_contract, preserved_contract, read_contract


BASE_REVISION = "0099_admin_personal_password"
BASE_PREDECESSOR = "0098_manual_pix_successor"
# Preserve the already-deployed administrator release's table and access contract.
ADMIN_PASSWORD_CONTRACT = """
    SELECT COALESCE((
        SELECT c.relrowsecurity AND c.relforcerowsecurity
            AND has_table_privilege('app_support', c.oid, 'SELECT')
            AND has_table_privilege('app_support', c.oid, 'INSERT')
            AND has_table_privilege('app_support', c.oid, 'UPDATE')
            AND NOT has_table_privilege('app_support', c.oid, 'DELETE,TRUNCATE')
            AND NOT has_table_privilege('app_runtime', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
            AND NOT EXISTS (
                SELECT 1 FROM aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS a
                WHERE a.grantee = 0
            )
            AND EXISTS (
                SELECT 1 FROM pg_policy p JOIN pg_roles r ON r.oid = ANY(p.polroles)
                WHERE p.polrelid = c.oid AND p.polname = 'admin_personal_password_support'
                    AND r.rolname = 'app_support'
            )
        FROM pg_class c WHERE c.oid = to_regclass('public.orion_legacy_admin_passwords')
    ), false)
"""
SAFE_REASONS = frozenset({
    "migration_database_scope_missing", "migration_database_scope_mismatch", "migration_database_url_invalid",
    "delivery_database_scope_mismatch", "exceptional_overlap_migration_busy", "delivery_revision_chain_not_approved",
    "delivery_source_revision_not_approved", "delivery_preserved_clinic_contract_invalid",
    "delivery_preserved_audience_contract_invalid", "delivery_preserved_plan_cycle_contract_invalid",
    "delivery_preserved_admin_password_contract_invalid",
    "delivery_predecessor_contract_invalid", "delivery_predecessor_changed_after_backup",
    "delivery_target_revision_not_applied", "delivery_target_contract_invalid",
    "private_backup_storage_missing", "private_backup_bucket_mismatch", "private_backup_endpoint_must_be_https",
    "private_backup_region_invalid", "private_backup_url_style_invalid", "private_database_dump_failed",
    "private_database_backup_archive_invalid", "private_database_backup_remote_verification_failed",
    "private_database_backup_readback_checksum_failed",
})
PHASES = (
    "0099_delivery_catalog",
    "0100_delivery_orders",
    "0101_delivery_dispatch",
    "0102_delivery_containers",
    "0103_delivery_credit",
    "0104_delivery_returns",
    "0105_delivery_logistics",
    "0106_delivery_storefront",
    "0107_delivery_order_containers",
    "0108_delivery_payments",
    "0109_delivery_catalog_editor",
    "0110_delivery_checkout_options",
    "0111_delivery_customer_addresses",
    "0112_delivery_area_terms",
)
TABLES = {
    "0111_delivery_customer_addresses": ("delivery_customer_addresses",),
    "0099_delivery_catalog": ("delivery_variant_details", "delivery_catalog_operations"),
    "0100_delivery_orders": ("delivery_order_details",),
    "0102_delivery_containers": ("delivery_containers", "delivery_container_movements"),
    "0105_delivery_logistics": ("delivery_logistics_policies", "delivery_couriers"),
    "0106_delivery_storefront": ("delivery_storefronts", "delivery_storefront_items", "delivery_storefront_orders"),
    "0107_delivery_order_containers": ("delivery_order_container_plans",),
    "0108_delivery_payments": ("delivery_credit_collections",),
    "0109_delivery_catalog_editor": ("delivery_product_profiles",),
}
COLUMNS = {
    "0112_delivery_area_terms": (("delivery_logistics_policies", "area_terms"),),
    "0100_delivery_orders": (("commercial_orders", "origin"),),
    "0101_delivery_dispatch": (
        ("delivery_order_details", "address"), ("delivery_order_details", "courier_name"),
        ("delivery_order_details", "dispatched_at"), ("delivery_order_details", "delivery_fee_cents"),
    ),
    "0103_delivery_credit": (("delivery_order_details", "payment_due_date"),),
    "0104_delivery_returns": (("delivery_order_details", "return_financial_entry_id"),),
    "0105_delivery_logistics": (
        ("delivery_order_details", "courier_id"), ("delivery_order_details", "scheduled_at"),
        ("delivery_order_details", "logistics_snapshot"),
    ),
    "0107_delivery_order_containers": (
        ("delivery_container_movements", "ledger_sequence"), ("delivery_container_movements", "order_id"),
        ("delivery_container_movements", "order_phase"),
    ),
    "0109_delivery_catalog_editor": (
        ("delivery_variant_details", "sell_internally"), ("delivery_variant_details", "allow_delivery"),
        ("delivery_variant_details", "allow_pickup"), ("delivery_variant_details", "allow_storefront"),
        ("delivery_variant_details", "exchange_container_id"),
    ),
    "0110_delivery_checkout_options": (
        ("delivery_storefront_orders", "buyer_note"), ("delivery_storefront_orders", "change_for_cents"),
        ("delivery_storefront_items", "sort_order"),
    ),
}


def approved_chain():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    delivery_heads = [head for head in heads if head in PHASES]
    # Historical releases can have unused migration files on separate branches.
    # The production database is fenced independently to one approved revision,
    # and the command upgrades only the explicit Delivery target below.
    if len(delivery_heads) != 1:
        raise ValueError("delivery_revision_chain_not_approved")
    chain = (BASE_REVISION, *PHASES[: PHASES.index(delivery_heads[0]) + 1])
    for previous, successor in pairwise((BASE_PREDECESSOR, *chain)):
        revision = script.get_revision(successor)
        if revision.down_revision != previous or revision.dependencies:
            raise ValueError("delivery_revision_chain_not_approved")
    return chain


def require_existing_contracts(raw):
    if not preserved_contract(raw):
        raise RuntimeError("delivery_preserved_clinic_contract_invalid")
    if not audience_contract(raw):
        raise RuntimeError("delivery_preserved_audience_contract_invalid")
    if not read_contract(raw, PLAN_CYCLE_CONTRACT):
        raise RuntimeError("delivery_preserved_plan_cycle_contract_invalid")
    if not read_contract(raw, ADMIN_PASSWORD_CONTRACT):
        raise RuntimeError("delivery_preserved_admin_password_contract_invalid")


def delivery_schema_ready(raw, phases):
    tables = [table for phase in phases for table in TABLES.get(phase, ())]
    if tables:
        relations = ", ".join(f"to_regclass('public.{table}')" for table in tables)
        if not read_contract(raw, f"""
            SELECT count(*) = {len(tables)} AND COALESCE(bool_and(
                c.relrowsecurity AND c.relforcerowsecurity
                AND has_table_privilege('app_runtime', c.oid, 'SELECT')
                AND NOT has_table_privilege('app_runtime', c.oid, 'TRUNCATE')
                AND NOT has_table_privilege('app_support', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
                AND NOT EXISTS (
                    SELECT 1 FROM aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS a
                    WHERE a.grantee = 0
                )
                AND EXISTS (
                    SELECT 1 FROM pg_policy AS p JOIN pg_roles AS r ON r.oid=ANY(p.polroles)
                    WHERE p.polrelid=c.oid AND r.rolname='app_runtime'
                        AND pg_get_expr(p.polqual, p.polrelid) LIKE '%app.tenant_id%'
                )
            ), false) FROM pg_class AS c WHERE c.oid IN ({relations})
        """):
            return False
    if "0106_delivery_storefront" in phases and not read_contract(raw, """
        SELECT EXISTS (
            SELECT 1 FROM pg_proc AS p
            WHERE p.oid=to_regprocedure('public.resolve_delivery_storefront(text)')
                AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']
                AND has_function_privilege('app_runtime', p.oid, 'EXECUTE')
                AND NOT has_function_privilege('app_support', p.oid, 'EXECUTE')
                AND NOT EXISTS (
                    SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS a
                    WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
                )
        )
    """):
        return False
    columns = [column for phase in phases for column in COLUMNS.get(phase, ())]
    if columns:
        pairs = ", ".join(f"('public.{table}', '{name}')" for table, name in columns)
        if not read_contract(raw, f"""
            SELECT NOT EXISTS (
                SELECT 1 FROM (VALUES {pairs}) AS e(relation, name)
                WHERE NOT EXISTS (
                    SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass(e.relation)
                    AND attname=e.name AND attnum>0 AND NOT attisdropped
                )
            )
        """):
            return False
    return True


def main(*, audit=False):
    stage = "image_chain"
    try:
        chain = approved_chain()
        target = chain[-1]
        stage = "database_configuration"
        raw, url = connection_values()
        runtime = make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host, runtime.port or 5432, runtime.database) != (url.host, url.port or 5432, url.database):
            raise ValueError("delivery_database_scope_mismatch")

        stage = "migration_lock"
        with release_lock(raw):
            stage = "database_revision"
            current = migrated_version(raw)
            label = current if isinstance(current, str) and re.fullmatch(r"[0-9]{4}_[a-z0-9_]{2,48}", current) else "unknown"
            print(f"orion_delivery_release current={label} target={target}", flush=True)
            if current not in chain:
                raise RuntimeError("delivery_source_revision_not_approved")
            stage = "predecessor_contracts"
            require_existing_contracts(raw)
            if not delivery_schema_ready(raw, chain[1 : chain.index(current) + 1]):
                raise RuntimeError("delivery_predecessor_contract_invalid")
            if audit:
                return 0

            if current != target:
                stage = "backup"
                with tempfile.TemporaryDirectory(prefix="orion-delivery-release-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="orion-delivery", schema_revision=current,
                                   preserve_ownership=True)
                if migrated_version(raw) != current:
                    raise RuntimeError("delivery_predecessor_changed_after_backup")
                require_existing_contracts(raw)
                stage = "migrate"
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", target],
                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True,
                )
            stage = "verify"
            if migrated_version(raw) != target:
                raise RuntimeError("delivery_target_revision_not_applied")
            require_existing_contracts(raw)
            if not delivery_schema_ready(raw, chain[1:]):
                raise RuntimeError("delivery_target_contract_invalid")
        print(f"orion_delivery_schema_ready revision={target}", flush=True)
        return 0
    except Exception as exc:
        candidate = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else None
        if candidate in SAFE_REASONS:
            reason = candidate
        elif isinstance(exc, KeyError):
            reason = "missing_environment"
        elif isinstance(exc, subprocess.TimeoutExpired):
            reason = "timed_out"
        elif isinstance(exc, subprocess.CalledProcessError):
            reason = "command_failed"
        else:
            reason = "invalid_or_unavailable"
        sqlstate = getattr(getattr(exc, "orig", exc), "sqlstate", None)
        safe_sqlstate = f" sqlstate={sqlstate}" if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else ""
        print(f"orion_delivery_predeploy_failed_closed stage={stage} reason={reason}{safe_sqlstate}", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--audit"]):
        raise SystemExit("Use scripts.backup_and_migrate_delivery [--audit]")
    raise SystemExit(main(audit=sys.argv[1:] == ["--audit"]))
