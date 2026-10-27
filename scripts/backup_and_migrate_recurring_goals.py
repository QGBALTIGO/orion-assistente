"""Reviewed 0036 -> 0037 release after a verified private, ownership-preserving backup.

The target is deliberately fixed. Repeated deployment validates the complete
contract without another backup or DDL; an unknown revision always fails closed.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_site_publication import schema_contract_ready as publication_contract_ready

PREVIOUS = "0036_site_publication"
TARGET = "0037_recurring_goals"
LOCK_SCOPE = 0x4F52494F
LOCK_RELEASE = 37


def normalized(value):
    return "".join((value or "").split())


@contextmanager
def release_lock(raw):
    """Serialize this release across simultaneous predeploys without holding a transaction."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={"options":
        "-c statement_timeout=30000 -c lock_timeout=10000"})
    try:
        with engine.connect() as db:
            acquired = db.execute(text("SELECT pg_try_advisory_lock(:scope,:release)"),
                                  {"scope": LOCK_SCOPE, "release": LOCK_RELEASE}).scalar_one()
            db.commit()
            if not acquired:
                raise RuntimeError("recurring_goal_release_in_progress")
            try:
                yield
            finally:
                db.execute(text("SELECT pg_advisory_unlock(:scope,:release)"),
                           {"scope": LOCK_SCOPE, "release": LOCK_RELEASE})
                db.commit()
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    """Verify nullable company scope, immutable revisions, RLS, grants and writer fencing."""
    engine = create_engine(raw, poolclass=NullPool, connect_args={"options":
        "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000"})
    try:
        with engine.connect() as db:
            tables = db.execute(text("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                pg_get_userbyid(c.relowner),
                has_table_privilege('app_runtime',c.oid,'SELECT'),
                has_table_privilege('app_runtime',c.oid,'INSERT'),
                has_table_privilege('app_runtime',c.oid,'UPDATE'),
                has_table_privilege('app_runtime',c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                has_table_privilege('app_support',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE a.grantee=0)
                FROM pg_class c WHERE c.relnamespace='public'::regnamespace
                AND c.relname IN ('professional_goals','professional_goal_revisions')""")).all()
            if len(tables) != 2 or any(
                tuple(row[1:]) != (True, True, 'app_migrator', True, True,
                                  row[0] == 'professional_goals', False, False, False)
                for row in tables
            ):
                return False
            columns = {(row[0], row[1]): (row[2], row[3]) for row in db.execute(text("""
                SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns
                WHERE table_schema='public' AND table_name IN ('professional_goals','professional_goal_revisions')
            """))}
            expected = {
                ('professional_goals','professional_id'): ('uuid','YES'),
                ('professional_goals','recurrence'): ('varchar','NO'),
                ('professional_goals','deleted_at'): ('timestamptz','YES'),
                **{('professional_goal_revisions', name): (kind, nullable) for name, kind, nullable in (
                    ('tenant_id','uuid','NO'), ('goal_id','uuid','NO'), ('version','int4','NO'),
                    ('effective_month','date','NO'), ('professional_id','uuid','YES'),
                    ('metric','varchar','NO'), ('target_value','int8','NO'), ('active','bool','NO'),
                    ('deleted','bool','NO'), ('timezone','varchar','NO'), ('created_at','timestamptz','NO'))},
            }
            if any(columns.get(key) != value for key, value in expected.items()):
                return False
            policies = db.execute(text("""SELECT c.relname,p.polname,p.polcmd,p.polpermissive,
                ARRAY(SELECT rolname FROM pg_roles WHERE oid=ANY(p.polroles) ORDER BY rolname),
                pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                WHERE c.relnamespace='public'::regnamespace
                AND c.relname IN ('professional_goals','professional_goal_revisions')""")).all()
            tenant_rule = normalized("(tenant_id = ( SELECT (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid AS \"nullif\"))")
            if len(policies) != 4:
                return False
            for table, name, command, permissive, roles, using, check in policies:
                runtime = name == table + '_runtime'
                if name not in {table + '_runtime', table + '_migrator'}:
                    return False
                expected_rule = tenant_rule if runtime else 'true'
                if (command != '*' or not permissive or roles != ['app_runtime' if runtime else 'app_migrator']
                        or normalized(using) != expected_rule or normalized(check) != expected_rule):
                    return False
            indexes = db.execute(text("""SELECT ci.relname,i.indisunique,i.indisvalid,i.indisready,
                pg_get_expr(i.indexprs,i.indrelid),pg_get_expr(i.indpred,i.indrelid),
                ARRAY(SELECT COALESCE(a.attname,'<expression>')
                    FROM unnest(i.indkey) WITH ORDINALITY k(attnum,seq)
                    LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                    WHERE k.seq<=i.indnkeyatts ORDER BY k.seq)
                FROM pg_index i JOIN pg_class ci ON ci.oid=i.indexrelid
                WHERE ci.relnamespace='public'::regnamespace
                AND ci.relname IN ('uq_professional_goal_active_monthly','ix_goal_revision_month')""")).all()
            expected_indexes = {
                'uq_professional_goal_active_monthly': (True, True, True,
                    normalized("COALESCE(professional_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
                    normalized("(((recurrence)::text = 'monthly'::text) AND active AND (deleted_at IS NULL))"),
                    ['tenant_id','<expression>','metric']),
                'ix_goal_revision_month': (False, True, True, '', '',
                    ['tenant_id','goal_id','effective_month','version']),
            }
            if len(indexes) != 2 or any(
                (row[1],row[2],row[3],normalized(row[4]),normalized(row[5]),row[6]) != expected_indexes[row[0]]
                for row in indexes
            ):
                return False
            constraints = db.execute(text("""SELECT conname,contype,convalidated,pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid='professional_goal_revisions'::regclass
                OR conrelid='professional_goals'::regclass
                    AND conname IN ('ck_professional_goal_recurrence','ck_professional_goal_deleted')""")).all()
            expected_constraints = {
                'ck_goal_revision_deleted': "CHECK (((NOT deleted) OR (NOT active)))",
                'ck_goal_revision_metric': "CHECK (((metric)::text = ANY ((ARRAY['revenue_cents'::character varying, 'appointments'::character varying])::text[])))",
                'ck_goal_revision_month': "CHECK ((EXTRACT(day FROM effective_month) = (1)::numeric))",
                'ck_goal_revision_value': "CHECK (((version >= 1) AND ((target_value >= 1) AND (target_value <= 1000000000))))",
                'ck_professional_goal_deleted': "CHECK (((deleted_at IS NULL) OR (NOT active)))",
                'ck_professional_goal_recurrence': "CHECK (((recurrence)::text = ANY ((ARRAY['none'::character varying, 'monthly'::character varying])::text[])))",
                'pk_professional_goal_revision': "PRIMARY KEY (tenant_id, goal_id, version)",
                'professional_goal_revisions_tenant_id_goal_id_fkey': "FOREIGN KEY (tenant_id, goal_id) REFERENCES professional_goals(tenant_id, id) ON DELETE CASCADE",
                'professional_goal_revisions_tenant_id_professional_id_fkey': "FOREIGN KEY (tenant_id, professional_id) REFERENCES professionals(tenant_id, id)",
            }
            if len(constraints) != len(expected_constraints) or any(
                not valid or normalized(definition) != normalized(expected_constraints.get(name))
                for name, _kind, valid, definition in constraints
            ):
                return False
            functions = db.execute(text("""SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,
                p.pronargs,p.prorettype='trigger'::regtype,
                has_function_privilege('app_runtime',p.oid,'EXECUTE'),
                has_function_privilege('app_support',p.oid,'EXECUTE'),
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0),
                p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname IN ('goal_history_immutable','guard_monthly_goal_writer')""")).all()
            expected_sources = {
                'goal_history_immutable': """BEGIN IF current_user<>'app_migrator' THEN
                    RAISE EXCEPTION 'Monthly goal history is immutable' USING ERRCODE='42501'; END IF;
                    IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END""",
                'guard_monthly_goal_writer': """BEGIN IF current_user<>'app_migrator'
                    AND current_setting('app.monthly_goal_writer',true) IS DISTINCT FROM 'on'
                    AND (NEW.recurrence='monthly' OR (TG_OP='UPDATE' AND OLD.recurrence='monthly')) THEN
                    RAISE EXCEPTION 'Monthly goals require the current revision writer' USING ERRCODE='55000';
                    END IF; RETURN NEW; END""",
            }
            if len(functions) != 2 or any(
                tuple(row[1:9]) != ('app_migrator', False, ['search_path=pg_catalog, public'], 0, True, False, False, False)
                or normalized(row[9]) != normalized(expected_sources[row[0]]) for row in functions
            ):
                return False
            triggers = db.execute(text("""SELECT c.relname,t.tgname,t.tgtype,p.proname
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE NOT t.tgisinternal AND t.tgenabled='O' AND c.relnamespace='public'::regnamespace
                AND t.tgname IN ('goal_history_immutable','guard_monthly_goal_writer')""")).all()
            return set(triggers) == {
                ('professional_goal_revisions','goal_history_immutable',27,'goal_history_immutable'),
                ('professional_goals','guard_monthly_goal_writer',23,'guard_monthly_goal_writer'),
            }
    finally:
        engine.dispose()


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print('orion_recurring_goals_unreviewed_schema_revision', file=sys.stderr)
                return 2
            if not publication_contract_ready(raw):
                print('orion_recurring_goals_source_contract_failed', file=sys.stderr)
                return 3
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print('orion_recurring_goals_schema_contract_failed', file=sys.stderr)
                    return 3
                print('orion_recurring_goals_schema_ready', flush=True)
                return 0
            storage = storage_values()
            with tempfile.TemporaryDirectory(prefix='orion-recurring-goals-') as folder:
                perform_backup(url, storage, Path(folder) / 'backup.dump',
                               backup_context='recurring-goals-0037', schema_revision=PREVIOUS,
                               preserve_ownership=True)
            result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', TARGET],
                env={**os.environ, 'ORION_MIGRATION_SWITCH_ROLE': '1'},
                check=False, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if (result.returncode != 0 or migrated_version(raw) != TARGET
                    or not publication_contract_ready(raw) or not schema_contract_ready(raw)):
                print('orion_recurring_goals_schema_upgrade_failed', file=sys.stderr)
                return 3
            print('orion_recurring_goals_schema_0037_verified_after_backup', flush=True)
            return 0
    except Exception:
        print('orion_recurring_goals_predeploy_failed_closed', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
