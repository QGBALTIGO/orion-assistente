"""Reviewed additive 0036/0037 -> 0038 release after verified private backup.

Creates schema only. It never corrects a timezone, shifts a reservation, publishes
a site, sends a message or runs a downgrade. Existing archive ownership and ACLs
are preserved. A current revision is accepted only after the schema contract is
verified, including RLS, role privileges and catalogue invalidation triggers.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.apply_variable_combo_migration import migrated_version
from scripts.backup_and_migrate_commissions import connection_values, storage_values, perform_backup
from scripts.backup_and_migrate_site_publication import schema_contract_ready as publication_contract_ready

PREVIOUS='0036_site_publication'
INTERMEDIATE='0037_public_runtime'
TARGET='0038_timezone_review'
APPROVED_SOURCES=frozenset((PREVIOUS,INTERMEDIATE))
TABLES=('public_site_metric_shards','public_site_catalogue_versions','public_site_timezone_reviews')
CATALOGUE_TABLES=('tenants','professionals','services','professional_services','working_hours','working_breaks',
                  'service_addons','service_packages','service_package_items')
PRIVILEGES=('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
TENANT_PREDICATE="(tenant_id = ( SELECT (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid AS \"nullif\"))"


def _expression(value):
    return re.sub(r'\s+',' ',value or '').strip()


def _default(kind,value):
    # PostgreSQL represents equivalent integer defaults differently after an
    # ALTER COLUMN; accept only the same zero value, never a broader expression.
    return "'0'::bigint" if kind=='bigint' and value in ('0','0::bigint',"'0'::bigint") else value


def _contract_rows_ready(rows,*,include_timezone=True):
    tables=set(TABLES if include_timezone else TABLES[:2])
    if {row[0] for row in rows['tables']}!=tables or not all(
        row[1] and row[2] and row[3]=='app_migrator' and not row[4] for row in rows['tables']):return False
    if {row[0] for row in rows['roles']}!={'app_runtime','app_migrator','app_support'} or not all(
        not any(row[1:5]) and row[5] for row in rows['roles']):return False
    expected={
        'public_site_metric_shards':{'SELECT','INSERT','UPDATE'},
        'public_site_catalogue_versions':{'SELECT'},
        'public_site_timezone_reviews':{'SELECT'},
    }
    expected_privileges={(table,role,privilege,role=='app_runtime' and privilege in expected[table])
        for table in tables for role in ('app_runtime','app_support') for privilege in PRIVILEGES}
    if set(rows['privileges'])!=expected_privileges:return False
    expected_column_grants={('public_site_timezone_reviews','acknowledged_at','app_runtime','UPDATE',False,'app_migrator')} if include_timezone else set()
    if set(rows['column_grants'])!=expected_column_grants:return False
    expected_columns={
        ('public_site_metric_shards','tenant_id'):('uuid',True,None),
        ('public_site_metric_shards','day'):('date',True,None),
        ('public_site_metric_shards','shard'):('smallint',True,None),
        ('public_site_metric_shards','visits'):('bigint',True,"'0'::bigint"),
        ('public_site_metric_shards','bookings'):('bigint',True,"'0'::bigint"),
        ('public_site_catalogue_versions','tenant_id'):('uuid',True,None),
        ('public_site_catalogue_versions','version'):('bigint',True,"'0'::bigint"),
    }
    if include_timezone:
        expected_columns.update({('public_site_timezone_reviews',name):(kind,nonnull,default) for name,kind,nonnull,default in (
            ('tenant_id','uuid',True,None),('appointment_id','uuid',True,None),
            ('from_timezone','character varying(64)',True,None),('to_timezone','character varying(64)',True,None),
            ('original_start_at','timestamp with time zone',True,None),('created_at','timestamp with time zone',True,'now()'),
            ('acknowledged_at','timestamp with time zone',False,None))})
    if {(row[0],row[1]):(row[2],row[3],_default(row[2],row[4])) for row in rows['columns']}!=expected_columns:return False
    if include_timezone:
        updates={row[0]:row[1] for row in rows['review_updates']}
        if set(updates)!={'tenant_id','appointment_id','from_timezone','to_timezone','original_start_at','created_at','acknowledged_at'}:
            return False
        if not all(value==(column=='acknowledged_at') for column,value in updates.items()):return False
    prefixes={'public_site_metric_shards':'public_metric_shards',
              'public_site_catalogue_versions':'public_catalogue_versions',
              'public_site_timezone_reviews':'public_timezone_reviews'}
    expected_policies={}
    for table in tables:
        runtime_cmd='r' if table=='public_site_catalogue_versions' else '*'
        expected_policies[(table,prefixes[table]+'_runtime')]=(runtime_cmd,['app_runtime'],True,
            _expression(TENANT_PREDICATE),None if runtime_cmd=='r' else _expression(TENANT_PREDICATE))
        expected_policies[(table,prefixes[table]+'_migrator')]=('*',['app_migrator'],True,'true','true')
    actual_policies={(row[0],row[1]):(row[2],list(row[3]),row[4],_expression(row[5]),
        None if row[6] is None else _expression(row[6])) for row in rows['policies']}
    if actual_policies!=expected_policies:return False
    functions=rows['functions']
    if len(functions)!=1:return False
    function=functions[0]
    if (function[0]!='bump_public_catalogue_version' or function[1]!='app_migrator' or not function[2]
        or function[3]!=['search_path=pg_catalog, public'] or function[4] or function[5]
        or function[6]!=0 or not function[7]):return False
    if set(rows['triggers'])!={(table,'public_catalogue_version',29,'bump_public_catalogue_version') for table in CATALOGUE_TABLES}:
        return False
    return bool(rows['structure'])


def _runtime_contract_ready(raw,*,include_timezone=True):
    engine=create_engine(raw,poolclass=NullPool,connect_args={'options':
        '-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000'})
    tables=list(TABLES if include_timezone else TABLES[:2])
    try:
        with engine.connect() as db:
            rows={}
            rows['tables']=db.execute(text("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner),
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl WHERE acl.grantee=0)
                FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
                AND c.relname=ANY(CAST(:tables AS text[]))"""),{'tables':tables}).all()
            rows['roles']=db.execute(text("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname IN ('app_runtime','app_migrator','app_support')")).all()
            rows['privileges']=db.execute(text("""SELECT c.relname,r.role,p.privilege,has_table_privilege(r.role,c.oid,p.privilege)
                FROM pg_class c CROSS JOIN (VALUES('app_runtime'),('app_support')) r(role)
                CROSS JOIN (VALUES('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) p(privilege)
                WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(CAST(:tables AS text[]))"""),{'tables':tables}).all()
            rows['columns']=db.execute(text("""SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid)
                FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(CAST(:tables AS text[]))
                  AND a.attnum>0 AND NOT a.attisdropped"""),{'tables':tables}).all()
            rows['column_grants']=db.execute(text("""SELECT c.relname,a.attname,COALESCE(r.rolname,'PUBLIC'),acl.privilege_type,
                acl.is_grantable,pg_get_userbyid(acl.grantor) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                CROSS JOIN LATERAL aclexplode(a.attacl) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee
                WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(CAST(:tables AS text[]))
                  AND a.attnum>0 AND NOT a.attisdropped"""),{'tables':tables}).all()
            rows['review_updates']=db.execute(text("""SELECT a.attname,has_column_privilege('app_runtime',a.attrelid,a.attnum,'UPDATE')
                FROM pg_attribute a WHERE a.attrelid=to_regclass('public.public_site_timezone_reviews') AND a.attnum>0 AND NOT a.attisdropped""")).all() if include_timezone else []
            rows['policies']=db.execute(text("""SELECT c.relname,p.polname,p.polcmd,
                ARRAY(SELECT rolname FROM pg_roles r WHERE r.oid=ANY(p.polroles) ORDER BY rolname),p.polpermissive,
                pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(CAST(:tables AS text[]))"""),{'tables':tables}).all()
            rows['functions']=db.execute(text("""SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,
                has_function_privilege('app_runtime',p.oid,'EXECUTE'),has_function_privilege('app_support',p.oid,'EXECUTE'),
                p.pronargs,p.prorettype='trigger'::regtype FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='bump_public_catalogue_version'""")).all()
            rows['triggers']=db.execute(text("""SELECT c.relname,t.tgname,t.tgtype,p.proname FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE c.relnamespace='public'::regnamespace
                AND n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled='O' AND t.tgname='public_catalogue_version'""")).all()
            rows['structure']=db.execute(text("""SELECT
                EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_metric_shards') AND contype='p'
                  AND pg_get_constraintdef(oid)='PRIMARY KEY (tenant_id, day, shard)')
                AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_metric_shards')
                  AND conname='ck_public_metric_shard' AND contype='c' AND convalidated
                  AND pg_get_constraintdef(oid)='CHECK (((shard >= 0) AND (shard < 128)))')
                AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_metric_shards')
                  AND conname='ck_public_metric_counts' AND contype='c' AND convalidated
                  AND pg_get_constraintdef(oid)='CHECK (((visits >= 0) AND (bookings >= 0)))')
                AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_catalogue_versions') AND contype='p'
                  AND pg_get_constraintdef(oid)='PRIMARY KEY (tenant_id)')
                AND (NOT :timezone OR (EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_timezone_reviews')
                  AND contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (tenant_id, appointment_id)')
                  AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.public_site_timezone_reviews') AND contype='f'
                    AND confrelid='appointments'::regclass AND confdeltype='c' AND convalidated
                    AND pg_get_constraintdef(oid)='FOREIGN KEY (tenant_id, appointment_id) REFERENCES appointments(tenant_id, id) ON DELETE CASCADE')))
                AND (SELECT count(*)=2 FROM pg_constraint WHERE conrelid IN (to_regclass('public.public_site_metric_shards'),to_regclass('public.public_site_catalogue_versions'))
                  AND contype='f' AND confrelid='tenants'::regclass AND confdeltype='c' AND convalidated)"""),{'timezone':include_timezone}).scalar_one()
            return _contract_rows_ready(rows,include_timezone=include_timezone)
    finally:engine.dispose()


def schema_contract_ready(raw):
    return publication_contract_ready(raw) and _runtime_contract_ready(raw)


def source_contract_ready(raw,current):
    return publication_contract_ready(raw) and (current==PREVIOUS or _runtime_contract_ready(raw,include_timezone=False))


def main():
    try:
        raw,url=connection_values();current=migrated_version(raw)
        if current==TARGET:
            if not schema_contract_ready(raw):
                print('orion_public_runtime_schema_contract_failed',file=sys.stderr);return 3
            print('orion_public_runtime_schema_ready',flush=True);return 0
        if current not in APPROVED_SOURCES:
            print('orion_public_runtime_unreviewed_schema_revision',file=sys.stderr);return 2
        if not source_contract_ready(raw,current):
            print('orion_public_runtime_source_contract_failed',file=sys.stderr);return 3
        storage=storage_values()
        with tempfile.TemporaryDirectory(prefix='orion-public-runtime-') as folder:
            perform_backup(url,storage,Path(folder)/'backup.dump',backup_context='public-runtime-0038',
                           schema_revision=current,preserve_ownership=True)
        environment={**os.environ,'ORION_MIGRATION_SWITCH_ROLE':'1'}
        result=subprocess.run([sys.executable,'-m','alembic','upgrade',TARGET],env=environment,check=False,
                              timeout=600,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if result.returncode!=0 or migrated_version(raw)!=TARGET or not schema_contract_ready(raw):
            print('orion_public_runtime_schema_upgrade_failed',file=sys.stderr);return 3
        print('orion_public_runtime_schema_0038_verified_after_backup',flush=True);return 0
    except Exception:
        print('orion_public_runtime_predeploy_failed_closed',file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
