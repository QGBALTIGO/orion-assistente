"""Reviewed 0037 -> 0038 only, after a private ownership-preserving verified backup.

Repeated releases perform read-only contract checks, never a second migration or
backup. This script does not enqueue, send or retry email or other messages.
"""
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
from scripts.backup_and_migrate_recurring_goals import schema_contract_ready as goals_contract_ready
from scripts.backup_and_migrate_site_publication import schema_contract_ready as publication_contract_ready

PREVIOUS = "0037_recurring_goals"
TARGET = "0038_email_outbox"
LOCK_SCOPE, LOCK_RELEASE = 0x4F52494F, 38


def normalized(value):
    return "".join((value or "").split())


EXPECTED_COLUMNS = {
    'id': ('uuid', 'NO', None, 'gen_random_uuid()'),
    'kind': ('varchar', 'NO', 32, ''), 'recipient_digest': ('bpchar', 'NO', 64, ''),
    **{name: ('uuid', 'YES', None, '') for name in ('account_id', 'tenant_id', 'invitation_id', 'token_id')},
    'dedupe_key': ('varchar', 'NO', 200, ''), 'payload_enc': ('text', 'YES', None, ''),
    'status': ('varchar', 'NO', 16, "'pending'::character varying"),
    'attempts': ('int4', 'NO', None, '0'), 'next_attempt_at': ('timestamptz', 'NO', None, 'now()'),
    'first_attempt_at': ('timestamptz', 'YES', None, ''), 'expires_at': ('timestamptz', 'NO', None, ''),
    'provider_message_id': ('varchar', 'YES', 200, ''), 'last_error_code': ('varchar', 'YES', 64, ''),
    'created_at': ('timestamptz', 'NO', None, 'now()'), 'accepted_at': ('timestamptz', 'YES', None, ''),
}
EXPECTED_CONSTRAINTS = {
    'panel_email_outbox_pkey': ('p', 'PRIMARY KEY (id)'),
    'panel_email_outbox_dedupe_key_key': ('u', 'UNIQUE (dedupe_key)'),
    'panel_email_outbox_account_id_fkey': ('f', 'FOREIGN KEY (account_id) REFERENCES panel_accounts(id) ON DELETE SET NULL'),
    'panel_email_outbox_tenant_id_fkey': ('f', 'FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT'),
    'panel_email_outbox_invitation_id_fkey': ('f', 'FOREIGN KEY (invitation_id) REFERENCES management_team_invitations(id) ON DELETE RESTRICT'),
    'panel_email_outbox_token_id_fkey': ('f', 'FOREIGN KEY (token_id) REFERENCES panel_email_tokens(id) ON DELETE SET NULL'),
    'panel_email_outbox_kind_check': ('c', "CHECK (((kind)::text = ANY ((ARRAY['verify'::character varying, 'reset'::character varying, 'password_changed'::character varying, 'team_invite'::character varying, 'team_access_approved'::character varying, 'team_access_revoked'::character varying])::text[])))"),
    'panel_email_outbox_status_check': ('c', "CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'accepted'::character varying, 'dead'::character varying, 'uncertain'::character varying, 'suppressed'::character varying, 'expired'::character varying])::text[])))"),
    'panel_email_outbox_attempts_check': ('c', 'CHECK (((attempts >= 0) AND (attempts <= 8)))'),
    'panel_email_outbox_dedupe_key_check': ('c', 'CHECK (((length((dedupe_key)::text) >= 1) AND (length((dedupe_key)::text) <= 200)))'),
    'panel_email_outbox_check': ('c', 'CHECK ((expires_at > created_at))'),
    'panel_email_outbox_check1': ('c', "CHECK ((((status)::text <> 'accepted'::text) OR ((provider_message_id IS NOT NULL) AND (accepted_at IS NOT NULL))))"),
    'panel_email_outbox_check2': ('c', "CHECK ((((status)::text <> 'pending'::text) OR (payload_enc IS NOT NULL)))"),
}
EXPECTED_INDEXES = {
    'panel_email_outbox_pkey': (True, ['id'], ''),
    'panel_email_outbox_dedupe_key_key': (True, ['dedupe_key'], ''),
    'ix_panel_email_due': (False, ['next_attempt_at', 'id'], "((status)::text = 'pending'::text)"),
    'ix_panel_email_recipient': (False, ['recipient_digest', 'created_at'], ''),
    'ix_panel_email_invitation': (False, ['invitation_id', 'kind'], ''),
    'uq_panel_email_invitation': (True, ['invitation_id'], "((kind)::text = 'team_invite'::text)"),
}


def constraint_definitions(name):
    definition = EXPECTED_CONSTRAINTS[name][1]
    variants = {normalized(definition)}
    restored = {
        'panel_email_outbox_kind_check': "CHECK (((kind)::text = ANY (ARRAY[('verify'::character varying)::text, ('reset'::character varying)::text, ('password_changed'::character varying)::text, ('team_invite'::character varying)::text, ('team_access_approved'::character varying)::text, ('team_access_revoked'::character varying)::text])))",
        'panel_email_outbox_status_check': "CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('accepted'::character varying)::text, ('dead'::character varying)::text, ('uncertain'::character varying)::text, ('suppressed'::character varying)::text, ('expired'::character varying)::text])))",
    }
    if name in restored:
        variants.add(normalized(restored[name]))
    return variants


@contextmanager
def release_lock(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={'options': '-c statement_timeout=30000 -c lock_timeout=10000'})
    try:
        with engine.connect() as db:
            db.execute(text('SET ROLE app_migrator'))
            if db.execute(text('SELECT current_user')).scalar_one() != 'app_migrator':
                raise RuntimeError('email_outbox_migrator_role_required')
            db.commit()
            acquired = db.execute(text('SELECT pg_try_advisory_lock(:scope,:release)'), {'scope': LOCK_SCOPE, 'release': LOCK_RELEASE}).scalar_one()
            db.commit()
            if not acquired:
                raise RuntimeError('email_outbox_release_in_progress')
            try:
                yield
            finally:
                db.execute(text('SELECT pg_advisory_unlock(:scope,:release)'), {'scope': LOCK_SCOPE, 'release': LOCK_RELEASE})
                db.commit()
    finally:
        engine.dispose()


def schema_contract_ready(raw):
    engine = create_engine(raw, poolclass=NullPool, connect_args={'options': '-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000'})
    try:
        with engine.connect() as db:
            table = db.execute(text("""SELECT c.relkind,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner),
                has_table_privilege('app_support',c.oid,'SELECT'),has_table_privilege('app_support',c.oid,'INSERT'),
                has_table_privilege('app_support',c.oid,'UPDATE'),
                has_table_privilege('app_support',c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                has_table_privilege('app_runtime',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                    WHERE a.grantee NOT IN(c.relowner,(SELECT oid FROM pg_roles WHERE rolname='app_support'))),
                EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL)
                FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname='panel_email_outbox'""")).first()
            if table is None or tuple(table) != ('r', True, True, 'app_migrator', True, True, True, False, False, False, False):
                return False
            columns = {row[0]: (row[1], row[2], row[3], normalized(row[4])) for row in db.execute(text("""
                SELECT column_name,udt_name,is_nullable,character_maximum_length,column_default
                FROM information_schema.columns WHERE table_schema='public' AND table_name='panel_email_outbox'"""))}
            if columns != {name: (*value[:3], normalized(value[3])) for name, value in EXPECTED_COLUMNS.items()}:
                return False
            policies = db.execute(text("""SELECT p.polname,p.polcmd,p.polpermissive,
                ARRAY(SELECT rolname FROM pg_roles WHERE oid=ANY(p.polroles) ORDER BY rolname),
                pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_policy p WHERE p.polrelid='public.panel_email_outbox'::regclass""")).all()
            expected_policies = {'panel_email_outbox_support': 'app_support', 'panel_email_outbox_migrator': 'app_migrator'}
            if len(policies) != 2 or any(name not in expected_policies or command != '*' or not permissive
                    or roles != [expected_policies[name]] or normalized(using) != 'true' or normalized(check) != 'true'
                    for name, command, permissive, roles, using, check in policies):
                return False
            constraints = db.execute(text("""SELECT conname,contype,convalidated,condeferrable,condeferred,pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid='public.panel_email_outbox'::regclass""")).all()
            if len(constraints) != len(EXPECTED_CONSTRAINTS) or any(name not in EXPECTED_CONSTRAINTS or not valid or deferrable or deferred
                    or kind != EXPECTED_CONSTRAINTS[name][0] or normalized(definition) not in constraint_definitions(name)
                    for name, kind, valid, deferrable, deferred, definition in constraints):
                return False
            indexes = db.execute(text("""SELECT ci.relname,i.indisunique,i.indisvalid,i.indisready,am.amname,
                ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,seq)
                    LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                    WHERE k.seq<=i.indnkeyatts ORDER BY k.seq),i.indexprs IS NULL,
                i.indnatts=i.indnkeyatts,pg_get_expr(i.indpred,i.indrelid)
                FROM pg_index i JOIN pg_class ci ON ci.oid=i.indexrelid JOIN pg_am am ON am.oid=ci.relam
                WHERE i.indrelid='public.panel_email_outbox'::regclass""")).all()
            if len(indexes) != len(EXPECTED_INDEXES) or any(name not in EXPECTED_INDEXES or not valid or not ready or method != 'btree'
                    or not plain or not no_includes or (unique, keys, normalized(predicate)) !=
                    (EXPECTED_INDEXES[name][0], EXPECTED_INDEXES[name][1], normalized(EXPECTED_INDEXES[name][2]))
                    for name, unique, valid, ready, method, keys, plain, no_includes, predicate in indexes):
                return False
            # 0038 adds neither a callable public function nor a trigger bypass.
            return not db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.panel_email_outbox'::regclass AND NOT tgisinternal)
                OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND (p.prosrc ILIKE '%panel_email_outbox%'
                    OR p.prorettype=(SELECT reltype FROM pg_class WHERE oid='public.panel_email_outbox'::regclass)
                    OR EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid
                        AND d.refclassid='pg_class'::regclass AND d.refobjid='public.panel_email_outbox'::regclass)))""")).scalar_one()
    finally:
        engine.dispose()


def source_contract_ready(raw):
    return publication_contract_ready(raw) and goals_contract_ready(raw)


def main():
    try:
        raw, url = connection_values()
        with release_lock(raw):
            current = migrated_version(raw)
            if current not in {PREVIOUS, TARGET}:
                print('orion_email_outbox_unreviewed_schema_revision', file=sys.stderr); return 2
            if not source_contract_ready(raw):
                print('orion_email_outbox_source_contract_failed', file=sys.stderr); return 3
            if current == TARGET:
                if not schema_contract_ready(raw):
                    print('orion_email_outbox_schema_contract_failed', file=sys.stderr); return 3
                print('orion_email_outbox_schema_ready', flush=True); return 0
            storage = storage_values()
            with tempfile.TemporaryDirectory(prefix='orion-email-outbox-') as folder:
                perform_backup(url, storage, Path(folder) / 'backup.dump', backup_context='email-outbox-0038', schema_revision=PREVIOUS, preserve_ownership=True)
            if migrated_version(raw) != PREVIOUS or not source_contract_ready(raw):
                print('orion_email_outbox_source_changed_after_backup', file=sys.stderr); return 3
            result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', TARGET],
                env={**os.environ, 'ORION_MIGRATION_SWITCH_ROLE': '1'}, check=False, timeout=600,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode != 0 or migrated_version(raw) != TARGET or not source_contract_ready(raw) or not schema_contract_ready(raw):
                print('orion_email_outbox_schema_upgrade_failed', file=sys.stderr); return 3
            print('orion_email_outbox_schema_0038_verified_after_backup', flush=True); return 0
    except Exception:
        print('orion_email_outbox_predeploy_failed_closed', file=sys.stderr); return 3


if __name__ == '__main__':
    raise SystemExit(main())
