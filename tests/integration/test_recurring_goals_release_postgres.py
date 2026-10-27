"""Release guards and idempotence on a real, isolated PostgreSQL 16 schema."""
import os

import pytest
from sqlalchemy.engine import make_url

from scripts import backup_and_migrate_recurring_goals as release
from scripts import backup_and_migrate_site_publication as publication

pytestmark = pytest.mark.integration


@pytest.fixture
def release_database(monkeypatch):
    raw = os.getenv('MIGRATION_DATABASE_URL')
    if not raw:
        if os.getenv('REQUIRE_POSTGRES_TESTS') == '1':
            pytest.fail('Release checks require the isolated migrated PostgreSQL database')
        pytest.skip('Requires PostgreSQL at migration 0037')
    url = make_url(raw)
    monkeypatch.setenv('ORION_EXPECTED_DATABASE_HOST', url.host)
    monkeypatch.setenv('ORION_EXPECTED_DATABASE_NAME', url.database)
    assert release.migrated_version(raw) == release.TARGET
    return raw


def test_full_publication_and_recurring_goal_contracts_are_ready(release_database):
    assert publication.schema_contract_ready(release_database)
    assert release.schema_contract_ready(release_database)


def test_idempotent_release_and_existing_entrypoint_need_no_backup_or_ddl(release_database, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('An already migrated database must not read backup storage or run DDL')

    monkeypatch.setattr(release, 'storage_values', forbidden)
    monkeypatch.setattr(release, 'perform_backup', forbidden)
    monkeypatch.setattr(release.subprocess, 'run', forbidden)
    assert release.main() == 0
    assert publication.predeploy_main() == 0
    assert release.migrated_version(release_database) == release.TARGET


def test_two_release_sessions_cannot_migrate_concurrently(release_database):
    with release.release_lock(release_database):
        with pytest.raises(RuntimeError, match='release_in_progress'):
            with release.release_lock(release_database):
                pytest.fail('Second release session acquired the same advisory lock')
    with release.release_lock(release_database):
        pass
