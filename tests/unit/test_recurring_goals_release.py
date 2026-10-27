"""The reviewed goal release cannot bypass backup, scope, schema or concurrency guards."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_commissions as common
from scripts import backup_and_migrate_recurring_goals as release
from scripts import backup_and_migrate_site_publication as publication


def configure(monkeypatch, *, current=None, backup_error=False, result=0,
              target_after=True, source_contract=True, target_contract=True, busy=False):
    version = [current or release.PREVIOUS]
    events = []
    monkeypatch.setattr(release, 'connection_values', lambda: ('synthetic-connection', object()))
    monkeypatch.setattr(release, 'migrated_version', lambda _raw: version[0])

    @contextmanager
    def lock(_raw):
        events.append('lock')
        if busy:
            raise RuntimeError('synthetic-lock-busy')
        try:
            yield
        finally:
            events.append('unlock')

    def source(_raw):
        events.append('source-contract')
        return source_contract

    def contract(_raw):
        events.append('target-contract')
        return target_contract

    def storage():
        events.append('storage')
        return {'bucket': 'synthetic-private'}

    def backup(_url, settings, destination, **kwargs):
        events.append('backup')
        assert settings == {'bucket': 'synthetic-private'}
        assert isinstance(destination, Path) and destination.name == 'backup.dump'
        assert kwargs == {'backup_context': 'recurring-goals-0037', 'schema_revision': release.PREVIOUS,
                          'preserve_ownership': True}
        if backup_error:
            raise RuntimeError('synthetic-secret-must-not-be-logged')

    def migrate(args, **kwargs):
        events.append('migration')
        assert args == [release.sys.executable, '-m', 'alembic', 'upgrade', '0037_recurring_goals']
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE'] == '1'
        assert kwargs['check'] is False and kwargs['timeout'] == 600
        assert kwargs['stdout'] == kwargs['stderr'] == release.subprocess.DEVNULL
        if target_after:
            version[0] = release.TARGET
        return SimpleNamespace(returncode=result)

    monkeypatch.setattr(release, 'release_lock', lock)
    monkeypatch.setattr(release, 'publication_contract_ready', source)
    monkeypatch.setattr(release, 'schema_contract_ready', contract)
    monkeypatch.setattr(release, 'storage_values', storage)
    monkeypatch.setattr(release, 'perform_backup', backup)
    monkeypatch.setattr(release.subprocess, 'run', migrate)
    return version, events


def test_reviewed_target_reuses_original_private_backup_and_scope_guards():
    assert release.PREVIOUS == '0036_site_publication'
    assert release.TARGET == '0037_recurring_goals'
    assert release.connection_values is common.connection_values
    assert release.storage_values is common.storage_values
    assert release.perform_backup is common.perform_backup


@pytest.mark.parametrize('revision', ['0035_public_amenities', '0038_unknown', 'unreviewed_branch'])
def test_unknown_revision_stops_before_storage_backup_or_migration(monkeypatch, revision):
    _, events = configure(monkeypatch, current=revision)
    assert release.main() == 2
    assert events == ['lock', 'unlock']


def test_verified_backup_precedes_exact_upgrade_and_both_contracts(monkeypatch):
    _, events = configure(monkeypatch)
    assert release.main() == 0
    assert events == ['lock', 'source-contract', 'storage', 'backup', 'migration',
                      'source-contract', 'target-contract', 'unlock']
    events.clear()
    assert release.main() == 0
    assert events == ['lock', 'source-contract', 'target-contract', 'unlock']


def test_backup_failure_blocks_ddl_and_never_logs_exception_contents(monkeypatch, capsys):
    _, events = configure(monkeypatch, backup_error=True)
    assert release.main() == 3
    assert events == ['lock', 'source-contract', 'storage', 'backup', 'unlock']
    output = capsys.readouterr()
    assert 'synthetic-secret' not in output.out + output.err
    assert 'predeploy_failed_closed' in output.err


def test_missing_source_contract_blocks_even_a_revision_marked_current(monkeypatch):
    _, events = configure(monkeypatch, current=release.TARGET, source_contract=False)
    assert release.main() == 3
    assert events == ['lock', 'source-contract', 'unlock']


def test_busy_release_does_not_attempt_a_second_migration(monkeypatch):
    _, events = configure(monkeypatch, busy=True)
    assert release.main() == 3 and events == ['lock']


@pytest.mark.parametrize('result,target_after', [(1, True), (0, False)])
def test_failed_migration_or_unconfirmed_revision_cannot_report_success(monkeypatch, result, target_after):
    _, events = configure(monkeypatch, result=result, target_after=target_after)
    assert release.main() == 3
    assert events == ['lock', 'source-contract', 'storage', 'backup', 'migration', 'unlock']


@pytest.mark.parametrize('current', [release.PREVIOUS, release.TARGET])
def test_target_schema_contract_is_required_after_upgrade_and_on_redeploy(monkeypatch, current):
    _, events = configure(monkeypatch, current=current, target_contract=False)
    assert release.main() == 3
    assert events[-2:] == ['target-contract', 'unlock']


def test_missing_storage_aborts_before_dump_or_ddl(monkeypatch):
    _, events = configure(monkeypatch)

    def missing():
        raise ValueError('synthetic-storage-missing')

    monkeypatch.setattr(release, 'storage_values', missing)
    assert release.main() == 3
    assert events == ['lock', 'source-contract', 'unlock']


@pytest.mark.parametrize('current', [publication.TARGET, release.TARGET])
def test_existing_railway_entrypoint_dispatches_supported_revisions(monkeypatch, current):
    monkeypatch.setattr(publication, 'connection_values', lambda: ('synthetic-connection', object()))
    monkeypatch.setattr(publication, 'migrated_version', lambda _raw: current)
    calls = []
    monkeypatch.setattr(publication, 'main', lambda: calls.append('publication') or 0)
    monkeypatch.setattr(release, 'main', lambda: calls.append('goals') or 0)
    assert publication.predeploy_main() == 0
    assert calls == ['goals']


@pytest.mark.parametrize('result', [0, 3])
def test_legacy_publication_must_finish_before_goal_release(monkeypatch, result):
    monkeypatch.setattr(publication, 'connection_values', lambda: ('synthetic-connection', object()))
    monkeypatch.setattr(publication, 'migrated_version', lambda _raw: publication.PREVIOUS)
    calls = []
    monkeypatch.setattr(publication, 'main', lambda: calls.append('publication') or result)
    monkeypatch.setattr(release, 'main', lambda: calls.append('goals') or 0)
    assert publication.predeploy_main() == result
    assert calls == (['publication', 'goals'] if result == 0 else ['publication'])


def test_dispatcher_rejects_future_revision_without_invoking_either_migration(monkeypatch):
    monkeypatch.setattr(publication, 'connection_values', lambda: ('synthetic-connection', object()))
    monkeypatch.setattr(publication, 'migrated_version', lambda _raw: '0038_unreviewed')
    calls = []
    monkeypatch.setattr(publication, 'main', lambda: calls.append('publication') or 0)
    monkeypatch.setattr(release, 'main', lambda: calls.append('goals') or 0)
    assert publication.predeploy_main() == 2
    assert calls == []
