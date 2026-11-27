"""Publish only the approved Delivery revisions after a verified private archive."""
from __future__ import annotations

from itertools import pairwise
import os
from pathlib import Path
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


BASE_REVISION = "0098_manual_pix_successor"
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
)
TABLES = {
    "0099_delivery_catalog": ("delivery_variant_details", "delivery_catalog_operations"),
    "0100_delivery_orders": ("delivery_order_details",),
    "0102_delivery_containers": ("delivery_containers", "delivery_container_movements"),
    "0105_delivery_logistics": ("delivery_logistics_policies", "delivery_couriers"),
    "0106_delivery_storefront": ("delivery_storefronts", "delivery_storefront_items", "delivery_storefront_orders"),
    "0107_delivery_order_containers": ("delivery_order_container_plans",),
    "0108_delivery_payments": ("delivery_credit_collections",),
}
COLUMNS = {
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
}


def approved_chain():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    delivery_heads = [head for head in heads if head in PHASES]
    # Historical releases can have unused migration files on separate branches.
    # The production database is fenced independently to one approved revision,
    # and the command upgrades only the explicit Delivery target below.
    if len(delivery_heads) != 1:
        raise ValueError(f"delivery_revision_head_not_approved heads={','.join(sorted(heads))}")
    chain = (BASE_REVISION, *PHASES[: PHASES.index(delivery_heads[0]) + 1])
    for previous, successor in pairwise(chain):
        revision = script.get_revision(successor)
        if revision.down_revision != previous or revision.dependencies:
            raise ValueError("delivery_revision_chain_not_approved")
    return chain


def existing_contracts_ready(raw):
    return preserved_contract(raw) and audience_contract(raw) and read_contract(raw, PLAN_CYCLE_CONTRACT)


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
    stage = "preflight"
    try:
        chain = approved_chain()
        target = chain[-1]
        raw, url = connection_values()
        runtime = make_url(os.environ["APP_DATABASE_URL"])
        if (runtime.host, runtime.port or 5432, runtime.database) != (url.host, url.port or 5432, url.database):
            raise ValueError("delivery_database_scope_mismatch")

        with release_lock(raw):
            current = migrated_version(raw)
            if current not in chain or not existing_contracts_ready(raw):
                raise RuntimeError("delivery_predecessor_not_approved")
            if not delivery_schema_ready(raw, chain[1 : chain.index(current) + 1]):
                raise RuntimeError("delivery_predecessor_contract_invalid")
            print(f"orion_delivery_release current={current} target={target}", flush=True)
            if audit:
                return 0

            if current != target:
                stage = "backup"
                with tempfile.TemporaryDirectory(prefix="orion-delivery-release-") as folder:
                    perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                                   backup_context="orion-delivery", schema_revision=current,
                                   preserve_ownership=True)
                if migrated_version(raw) != current or not existing_contracts_ready(raw):
                    raise RuntimeError("delivery_predecessor_changed_after_backup")
                stage = "migrate"
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", target],
                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True,
                )
            stage = "verify"
            if migrated_version(raw) != target or not existing_contracts_ready(raw):
                raise RuntimeError("delivery_predecessor_contract_invalid")
            if not delivery_schema_ready(raw, chain[1:]):
                raise RuntimeError("delivery_target_contract_invalid")
        print(f"orion_delivery_schema_ready revision={target}", flush=True)
        return 0
    except Exception:
        print(f"orion_delivery_predeploy_failed_closed stage={stage}", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--audit"]):
        raise SystemExit("Use scripts.backup_and_migrate_delivery [--audit]")
    raise SystemExit(main(audit=sys.argv[1:] == ["--audit"]))
