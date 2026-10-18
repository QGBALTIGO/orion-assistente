"""0019 populated -> current head, encrypted backup/readback and real restore.

Only fresh random loopback databases are touched. This is not production DDL
and does not downgrade the CI application's existing database.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text

from scripts.qualification_load import DisposableDatabase, ROOT, seed_database
from scripts import one_time_business_migration as backup
from scripts import one_time_onboarding_migration as op

pytestmark = pytest.mark.integration


def columns(db):
    rows = db.exec_driver_sql("""SELECT table_name,column_name FROM information_schema.columns
        WHERE table_schema='public' ORDER BY table_name,ordinal_position""").all()
    result = {}
    for table, column in rows: result.setdefault(table, []).append(column)
    return result


def row_digests(db, projection):
    result = {}
    for table, names in sorted(projection.items()):
        if table == "alembic_version": continue
        # Identifiers originate solely from PostgreSQL's own catalog.
        selected = ",".join('"'+name.replace('"','""')+'"' for name in names)
        sql = 'SELECT row_to_json(r)::text FROM (SELECT '+selected+' FROM public."'+table.replace('"','""')+'") r'
        values = sorted(db.exec_driver_sql(sql).scalars())
        result[table] = {"count":len(values),"sha256":hashlib.sha256("\n".join(values).encode()).hexdigest()}
    return result


def privileges(db, names):
    return db.execute(text("""SELECT c.relname,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,
        (SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(a.grantor),a.grantee::regrole::text,a.privilege_type,a.is_grantable)
          ORDER BY a.grantee::regrole::text,a.privilege_type,a.is_grantable)
         FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(:names) ORDER BY c.relname"""), {"names":list(names)}).all()


def assert_same_catalog(actual, expected):
    """Compare every catalog field, reporting only the mismatched structure."""
    expected_by_table = {row[0]: row for row in expected}
    actual_by_table = {row[0]: row for row in actual}
    assert len(actual_by_table) == len(actual)
    assert len(expected_by_table) == len(expected)
    assert set(actual_by_table) == set(expected_by_table)
    sections = ("table", "rls", "force_rls", "columns", "constraints", "indexes", "policies")
    for name, wanted in expected_by_table.items():
        observed = actual_by_table[name]
        assert len(observed) == len(wanted) == len(sections), name
        for section, current, previous in zip(sections, observed, wanted):
            assert current == previous, f"Restored catalog differs: {name}.{section}"
    # Keep the original full equality, including the stable catalog ordering.
    assert actual == expected


def client(program, args, sandbox):
    env = backup.pg_environment(sandbox.owner.set(database=sandbox.name))
    result = subprocess.run([program,*args],env=env,capture_output=True,timeout=120)
    assert result.returncode == 0, f"Synthetic {program} failed; no connection credentials copied"
    return result.stdout


@pytest.fixture
def populated_source():
    owner = os.getenv("ORION_MIGRATION_TEST_OWNER_URL")
    if not owner:
        if os.getenv("ORION_REQUIRE_MIGRATION_OPS_TEST")=="1": pytest.fail("Mandatory disposable migration URL missing")
        pytest.skip("Dedicated local PostgreSQL owner URL not configured")
    assert backup.client_major("pg_dump") == backup.client_major("pg_restore") == 16
    with DisposableDatabase(owner,os.environ) as sandbox:
        sandbox.migrate("0019_appointment_lifecycle")
        people, inventory = seed_database(sandbox,4,2)
        with sandbox.engine.begin() as db:
            # Existing pending worker job and contact preferences must survive.
            appointment = db.exec_driver_sql("SELECT id,tenant_id FROM appointments ORDER BY id LIMIT 1").one()
            db.execute(text("INSERT INTO calendar_outbox(tenant_id,appointment_id,action) VALUES(:t,:a,'create')"), {"t":appointment.tenant_id,"a":appointment.id})
            db.execute(text("INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in) VALUES(:t,:phone,false)"),
                {"t":people[0]["tenant"],"phone":people[0]["phone"]})
        yield sandbox, people, inventory


def test_populated_0019_head_backup_restore_and_legacy_contract(populated_source,tmp_path):
    source, people, inventory = populated_source
    config = Config(str(ROOT/"alembic.ini")); config.set_main_option("script_location",str(ROOT/"alembic"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    with source.engine.connect() as db:
        before_columns = columns(db)
        before = row_digests(db,before_columns)
        old_privileges = privileges(db,before_columns)
        old_catalog = op.canonical_catalog(backup.catalog_structure(db,frozenset(before_columns)))
    dump = tmp_path/"synthetic-0019.dump"
    dump.touch(mode=0o600,exist_ok=False)
    client("pg_dump",["--format=custom","--no-password","--file="+str(dump)],source)
    plain = dump.read_bytes(); assert plain.startswith(b"PGDMP")
    cipher = Fernet(Fernet.generate_key()); envelope = cipher.encrypt(plain)
    retained = tmp_path/"synthetic-0019.encrypted"
    retained.write_bytes(envelope); retained.chmod(0o600)
    expected_digest = hashlib.sha256(envelope).hexdigest()
    readback = retained.read_bytes()
    assert hashlib.sha256(readback).hexdigest() == expected_digest
    assert cipher.decrypt(readback) == plain and not readback.startswith(b"PGDMP")
    dump.write_bytes(cipher.decrypt(readback)); dump.chmod(0o600)
    # Restore before touching the source schema. Keep real owners, grants and RLS.
    with DisposableDatabase(os.environ["ORION_MIGRATION_TEST_OWNER_URL"],os.environ) as restored:
        client("pg_restore",["--no-password","--clean","--if-exists","--exit-on-error","--single-transaction","--dbname="+restored.name,str(dump)],restored)
        with restored.engine.connect() as db:
            assert row_digests(db,before_columns) == before
            assert privileges(db,before_columns) == old_privileges
            assert_same_catalog(op.canonical_catalog(backup.catalog_structure(db,frozenset(before_columns))), old_catalog)
            assert db.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()=="0019_appointment_lifecycle"
        source.migrate("head")
        with source.engine.begin() as db:
            assert db.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()==expected_head
            assert row_digests(db,before_columns)==before
            assert privileges(db,before_columns)==old_privileges
            assert db.exec_driver_sql("SELECT count(*) FROM contact_consents WHERE operational_opt_in").scalar_one()==0
            assert db.exec_driver_sql("SELECT count(*) FROM qr_channels").scalar_one()==0
            assert db.exec_driver_sql("SELECT count(*) FROM automation_jobs").scalar_one()==0
            assert db.exec_driver_sql("SELECT count(*) FROM business_onboarding").scalar_one()==0
            # Existing workers retain readable pending rows; no external worker is run.
            assert db.exec_driver_sql("SELECT action,status,attempts FROM calendar_outbox").one()==("create","pending",0)
            db.exec_driver_sql("SET LOCAL ROLE app_runtime")
            assert db.exec_driver_sql("SELECT count(*) FROM appointments").scalar_one()==0
            db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"),{"tenant":people[0]["tenant"]})
            assert db.exec_driver_sql("SELECT count(*) FROM appointments").scalar_one()==10
            assert db.exec_driver_sql("SELECT count(*) FROM services").scalar_one()==1
            assert db.exec_driver_sql("SELECT count(*) FROM commercial_products").scalar_one()==1
        # A legacy INSERT contract still works after additive migration and is
        # isolated by RLS; this is not a claim of running an old API binary.
        with source.engine.begin() as db:
            db.exec_driver_sql("SET LOCAL ROLE app_runtime")
            db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"),{"tenant":people[0]["tenant"]})
            db.execute(text("INSERT INTO customers(tenant_id,whatsapp_e164,name) VALUES(:t,'+5567999999899','Synthetic post-upgrade customer')"),{"t":people[0]["tenant"]})
        # The restored old-schema database remains usable, without downgrading
        # or erasing post-upgrade data in the source.
        with restored.engine.begin() as db:
            db.exec_driver_sql("SET LOCAL ROLE app_runtime")
            db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"),{"tenant":people[0]["tenant"]})
            assert db.exec_driver_sql("SELECT count(*) FROM appointments").scalar_one()==10
            assert db.exec_driver_sql("SELECT count(*) FROM customers WHERE name='Synthetic post-upgrade customer'").scalar_one()==0
        with source.engine.connect() as db:
            assert db.exec_driver_sql("SELECT count(*) FROM customers WHERE name='Synthetic post-upgrade customer'").scalar_one()==1
    print(json.dumps({"synthetic_migration":True,"source":"0019_appointment_lifecycle","target":expected_head,
        "tables_preserved":len(before),"source_rows":sum(x["count"] for x in before.values()),
        "backup_bytes":len(envelope),"backup_sha256":expected_digest,"encrypted_readback":True,
        "restore_rows_grants_rls_verified":True,"new_features_activated":False,"old_binary_executed":False}))
