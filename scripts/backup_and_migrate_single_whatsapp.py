"""Reviewed, backup-gated single WhatsApp binding; never starts connections."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_admin_accounts import schema_ready as accounts_ready
from scripts.backup_and_migrate_cakto import schema_ready as cakto_ready
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.wait_for_single_whatsapp_schema import GUARDS_QUERY

PREVIOUS = "0043_cakto_events"
TARGET = "0044_single_whatsapp_connection"
LOCK_SCOPE, LOCK_RELEASE = 0x4F52494F, 44


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            db.execute(text("SET ROLE app_migrator"))
            if db.execute(text("SELECT current_user")).scalar_one() != "app_migrator":
                raise RuntimeError("whatsapp_migrator_role_required")
            db.commit()
            if not db.execute(text("SELECT pg_try_advisory_lock(:s,:r)"),
                              {"s": LOCK_SCOPE, "r": LOCK_RELEASE}).scalar_one():
                raise RuntimeError("whatsapp_release_in_progress")
            db.commit()
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(:s,:r)"),
                           {"s": LOCK_SCOPE, "r": LOCK_RELEASE})
                db.commit()
    finally:
        engine.dispose()


def _normalized(definition):
    value = "".join(str(definition).split()).replace("(", "").replace(")", "")
    for cast in ("::text", "::jsonpath", "::jsonb"):
        value = value.replace(cast, "")
    return value


def schema_contract_ready(raw):
    """Read-only verification of the binding, deny-all policy and grants."""
    if not source_ready(raw):
        return False
    engine = create_engine(raw, poolclass=NullPool, connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as db:
            column = db.execute(text("""SELECT data_type,is_nullable,column_default
                FROM information_schema.columns WHERE table_schema='public'
                AND table_name='tenants' AND column_name='whatsapp_channel_id'""")).first()
            if tuple(column or ()) != ("uuid", "YES", None):
                return False
            binding = db.execute(text("""SELECT c.convalidated,c.condeferrable,c.condeferred,c.confdeltype,
                ARRAY(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.conrelid
                  AND a.attnum=ANY(c.conkey) ORDER BY array_position(c.conkey,a.attnum)),
                ARRAY(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.confrelid
                  AND a.attnum=ANY(c.confkey) ORDER BY array_position(c.confkey,a.attnum))
                FROM pg_constraint c WHERE c.conname='fk_tenant_whatsapp_channel'
                AND c.contype='f' AND c.conrelid='public.tenants'::regclass
                AND c.confrelid='public.qr_channels'::regclass""")).first()
            if (not binding or tuple(binding[:4]) != (True, False, False, "r")
                    or list(binding[4]) != ["id", "whatsapp_channel_id"]
                    or list(binding[5]) != ["tenant_id", "id"]):
                return False
            unique = db.execute(text("""SELECT i.indisunique,i.indisvalid,i.indnkeyatts,
                pg_get_expr(i.indpred,i.indrelid),a.attname
                FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
                WHERE idx.relname='ix_tenant_whatsapp_channel'
                AND i.indrelid='public.tenants'::regclass""")).first()
            if (not unique or tuple(unique[:3]) != (True, True, 1)
                    or _normalized(unique[3]) != "whatsapp_channel_idISNOTNULL"
                    or unique[4] != "whatsapp_channel_id"):
                return False
            checks = {name: (validated, definition) for name, validated, definition in
                      db.execute(text("""SELECT conname,convalidated,pg_get_constraintdef(oid)
                        FROM pg_constraint WHERE conrelid='public.qr_channels'::regclass
                        AND contype='c' AND conname IN
                        ('ck_qr_channel_senders','ck_qr_channel_private_policy')""")).all()}
            sender = ("CHECK (jsonb_typeof(allowed_senders)='array' AND "
                      "jsonb_array_length(allowed_senders)>=0 AND "
                      "jsonb_array_length(allowed_senders)<=10 AND NOT "
                      "jsonb_path_exists(allowed_senders, '$[*] ? (@.type() != \"string\")'))")
            policy = "CHECK (NOT assistant_enabled OR jsonb_array_length(allowed_senders)>0)"
            if any(name not in checks or checks[name][0] is not True
                   or _normalized(checks[name][1]) != _normalized(expected)
                   for name, expected in (("ck_qr_channel_senders", sender),
                                          ("ck_qr_channel_private_policy", policy))):
                return False
            grants = db.execute(text("""SELECT
                has_column_privilege('app_support','public.tenants','whatsapp_channel_id','SELECT'),
                has_column_privilege('app_support','public.tenants','whatsapp_channel_id','UPDATE'),
                has_column_privilege('app_runtime','public.tenants','whatsapp_channel_id','SELECT'),
                has_column_privilege('app_runtime','public.tenants','whatsapp_channel_id','UPDATE')""")).one()
            if tuple(grants) != (True, True, True, False):
                return False
            if db.execute(text(GUARDS_QUERY)).scalar() is not True:
                return False
            return True
    finally:
        engine.dispose()


def source_ready(raw):
    return accounts_ready(raw) and cakto_ready(raw)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                return 2
            if current == TARGET:
                return 0 if schema_contract_ready(raw) else 3
            if not source_ready(raw):
                return 3
            with tempfile.TemporaryDirectory(prefix="orion-single-whatsapp-") as folder:
                perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                               backup_context="single-whatsapp-0044", schema_revision=PREVIOUS,
                               preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not source_ready(raw):
                return 3
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", TARGET],
                                    env={**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"},
                                    timeout=600, check=False, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if result.returncode or migrated_version(raw) != TARGET or not schema_contract_ready(raw):
                print("orion_single_whatsapp_upgrade_failed", file=sys.stderr)
                return 3
            print("orion_single_whatsapp_schema_0044_verified_after_backup", flush=True)
            return 0
    except Exception:
        print("orion_single_whatsapp_release_failed_closed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
