"""Release contract and reversible migration checks in disposable PostgreSQL."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from scripts import backup_and_migrate_professional_phone as release

pytestmark = pytest.mark.integration


@pytest.fixture
def local_database_url():
    raw = os.getenv('MIGRATION_DATABASE_URL')
    if not raw:
        if os.getenv('REQUIRE_POSTGRES_TESTS') == '1':
            pytest.fail('Requires the isolated migrated PostgreSQL database')
        pytest.skip('Requires PostgreSQL')
    parsed = make_url(raw)
    assert parsed.host in {'localhost', '127.0.0.1'}
    return raw


def test_professional_contact_release_matches_real_postgres_contract(local_database_url):
    raw = local_database_url
    assert release.schema_contract_ready(raw)
    assert not release.source_contract_ready(raw), 'An already-applied column must not be treated as an unmigrated source'


def test_phone_migration_preserves_existing_profiles_and_refuses_contact_loss(local_database_url):
    """Exercise real DDL in a private schema, rolled back after the assertion."""
    path = Path(__file__).parents[2] / 'alembic/versions/0040_professional_phone.py'
    spec = importlib.util.spec_from_file_location('professional_phone_migration_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = 'phone_release_' + uuid4().hex
    engine = create_engine(local_database_url, poolclass=NullPool)
    try:
        with engine.connect() as db:
            transaction = db.begin()
            try:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))
                db.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
                db.execute(text('CREATE TABLE professionals (id integer PRIMARY KEY, name text NOT NULL)'))
                db.execute(text("INSERT INTO professionals (id,name) VALUES (1,'Synthetic professional')"))
                with Operations.context(MigrationContext.configure(db)):
                    migration.upgrade()
                    assert tuple(db.execute(text('SELECT id,name,phone_e164 FROM professionals')).one()) == (
                        1, 'Synthetic professional', None)
                    db.execute(text("UPDATE professionals SET phone_e164='+5511987654321' WHERE id=1"))
                    with pytest.raises(RuntimeError, match='professional_phone_downgrade_would_discard_contacts'):
                        migration.downgrade()
                    assert db.execute(text('SELECT phone_e164 FROM professionals WHERE id=1')).scalar_one() == '+5511987654321'
                    db.execute(text('UPDATE professionals SET phone_e164=NULL WHERE id=1'))
                    migration.downgrade()
                    assert tuple(db.execute(text('SELECT id,name FROM professionals')).one()) == (1, 'Synthetic professional')
                    assert not db.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                        WHERE table_schema=:schema AND table_name='professionals' AND column_name='phone_e164')"""),
                        {'schema': schema}).scalar_one()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
