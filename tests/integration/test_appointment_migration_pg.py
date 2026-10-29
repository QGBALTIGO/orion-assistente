"""Qualify the actual 0046/0048 -> 0049 source with all legacy columns preserved.

Only newly created loopback databases and synthetic rows are used. No WhatsApp
session or external provider is touched; registry records are disabled fixtures.
"""
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text

from scripts.qualification_load import DisposableDatabase, seed_database
from scripts import backup_and_migrate_appointment_communications as release
from tests.integration.test_qualification_populated_migration import columns, row_digests, privileges, client

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('revision',[release.PREVIOUS,release.COMMUNICATIONS_PREVIOUS])
def test_populated_upgrade_preserves_rows_registry_and_restorable_backup(tmp_path,revision):
    owner = os.getenv('ORION_MIGRATION_TEST_OWNER_URL')
    if not owner:
        if os.getenv('ORION_REQUIRE_MIGRATION_OPS_TEST') == '1':
            pytest.fail('Isolated migration owner URL required')
        pytest.skip('Dedicated loopback migration database required')
    with DisposableDatabase(owner, os.environ) as source:
        source.migrate(revision)
        people, inventory = seed_database(source, 4, 2, simulated_channels=True)
        with source.engine.connect() as db:
            projection = columns(db)
            before = row_digests(db, projection)
            grants = privileges(db, projection)
        dump = tmp_path/'synthetic-0046.dump'
        dump.touch(mode=0o600, exist_ok=False)
        client('pg_dump', ['--format=custom', '--no-password', '--file='+str(dump)], source)
        plain = dump.read_bytes()
        assert plain.startswith(b'PGDMP')
        cipher = Fernet(Fernet.generate_key())
        envelope = cipher.encrypt(plain)
        encrypted = tmp_path/'synthetic-0046.encrypted'
        encrypted.write_bytes(envelope); encrypted.chmod(0o600)
        digest = hashlib.sha256(envelope).hexdigest()
        assert cipher.decrypt(encrypted.read_bytes()) == plain
        with DisposableDatabase(owner, os.environ) as restored:
            client('pg_restore', ['--no-password', '--clean', '--if-exists', '--exit-on-error',
                '--single-transaction', '--dbname='+restored.name, str(dump)], restored)
            with restored.engine.connect() as db:
                assert row_digests(db, projection) == before
                assert privileges(db, projection) == grants
                assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == revision
            source.migrate(release.TARGET)
            with source.engine.connect() as db:
                assert row_digests(db, projection) == before
                assert privileges(db, projection) == grants
                assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == release.TARGET
                assert db.exec_driver_sql('SELECT count(*) FROM appointments WHERE customer_access_blocked').scalar_one() == 0
                for table in release.TABLES:
                    assert db.exec_driver_sql('SELECT count(*) FROM '+table).scalar_one() == 0
            assert release.schema_ready(source.environment()['MIGRATION_DATABASE_URL'])
            # The previous runtime write contract still works after upgrade.
            with source.engine.begin() as db:
                db.exec_driver_sql('SET LOCAL ROLE app_runtime')
                assert db.exec_driver_sql('SELECT count(*) FROM appointments').scalar_one() == 0
                db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {'tenant':people[0]['tenant']})
                assert db.exec_driver_sql('SELECT count(*) FROM appointments').scalar_one() == 10
                db.execute(text("INSERT INTO customers(tenant_id,whatsapp_e164,name) VALUES(:t,'+5567999999899','Synthetic post-upgrade customer')"), {'t':people[0]['tenant']})
    result = {'source':revision, 'target':release.TARGET, 'tables_preserved':len(before),
        'rows_preserved':sum(item['count'] for item in before.values()), 'backup_sha256':digest,
        'backup_bytes':len(envelope), 'restore_rows_and_grants_verified':True,
        'registry_rows_preserved':inventory['simulated_disabled_channel_records'],
        'actual_whatsapp_sessions':0, 'old_binary_executed':False, 'production_modified':False}
    directory = Path('test-results/appointment-communications'); directory.mkdir(parents=True, exist_ok=True)
    (directory/('migration-preservation-'+revision+'.json')).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
