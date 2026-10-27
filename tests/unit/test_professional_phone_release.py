"""Release boundaries without a real backup, external connection or DDL."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_commissions as common
from scripts import backup_and_migrate_professional_phone as release
from scripts import backup_and_migrate_registration as registration


def configure(monkeypatch, *, current=None, source=True, target=True, backup_error=False,
              busy=False, drift=False, result=0, confirm=True):
    version = [current or release.PREVIOUS]
    events = []
    monkeypatch.setattr(release, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(release, 'migrated_version', lambda _: version[0])
    @contextmanager
    def lock(_):
        events.append('lock')
        if busy:
            raise RuntimeError('private-lock-detail')
        try:
            yield
        finally:
            events.append('unlock')
    monkeypatch.setattr(release, 'release_lock', lock)
    def source_check(_): events.append('source'); return source
    def target_check(_): events.append('target'); return target
    def storage(): events.append('storage'); return {'bucket': 'synthetic'}
    def backup(url, settings, path, **kwargs):
        events.append('backup')
        assert path.name == 'backup.dump' and settings == {'bucket': 'synthetic'}
        assert kwargs == {'backup_context': 'professional-phone-0040',
                          'schema_revision': release.PREVIOUS, 'preserve_ownership': True}
        if drift:
            version[0] = '0041_unreviewed'
        if backup_error:
            raise RuntimeError('private-backup-detail')
    def migrate(args, **kwargs):
        events.append('ddl')
        assert args == [release.sys.executable, '-m', 'alembic', 'upgrade', release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE'] == '1'
        assert kwargs['stdout'] == kwargs['stderr'] == release.subprocess.DEVNULL
        assert kwargs['timeout'] == 600 and kwargs['check'] is False
        if confirm:
            version[0] = release.TARGET
        return SimpleNamespace(returncode=result)
    for name, fn in [('source_contract_ready', source_check), ('schema_contract_ready', target_check),
                     ('storage_values', storage), ('perform_backup', backup)]:
        monkeypatch.setattr(release, name, fn)
    monkeypatch.setattr(release.subprocess, 'run', migrate)
    return events


def test_shared_verified_private_backup_and_database_scope_are_reused():
    assert release.connection_values is common.connection_values
    assert release.storage_values is common.storage_values
    assert release.perform_backup is common.perform_backup
    assert release.PREVIOUS == registration.TARGET


def test_upgrade_follows_verified_backup_and_repeat_is_read_only(monkeypatch):
    events = configure(monkeypatch)
    assert release.main() == 0
    assert events == ['lock', 'source', 'storage', 'backup', 'source', 'ddl', 'target', 'unlock']
    events.clear()
    assert release.main() == 0
    assert events == ['lock', 'target', 'unlock']


@pytest.mark.parametrize('revision', ['0038_email_outbox', '0041_unreviewed', 'unknown'])
def test_unknown_revision_never_reaches_backup_or_ddl(monkeypatch, revision):
    events = configure(monkeypatch, current=revision)
    assert release.main() == 2 and events == ['lock', 'unlock']


@pytest.mark.parametrize('option', ['source', 'backup_error', 'busy', 'drift'])
def test_failed_prerequisite_prevents_ddl_and_suppresses_details(monkeypatch, capsys, option):
    events = configure(monkeypatch, **{option: False if option == 'source' else True})
    assert release.main() == 3 and 'ddl' not in events
    output = capsys.readouterr()
    assert 'private-' not in output.out + output.err


@pytest.mark.parametrize('options', [{'result': 1}, {'confirm': False}, {'target': False},
                                   {'current': release.TARGET, 'target': False}])
def test_failed_upgrade_or_invalid_result_cannot_be_reported_as_ready(monkeypatch, options):
    configure(monkeypatch, **options)
    assert release.main() == 3


@pytest.mark.parametrize('target', [False, True])
def test_existing_registration_contract_is_required_for_source_and_target(monkeypatch, target):
    calls = []
    monkeypatch.setattr(release, 'registration_contract_ready', lambda _: calls.append('registration') or False)
    monkeypatch.setattr(release, 'professional_contract_ready', lambda *a, **k: calls.append('professional') or True)
    check = release.schema_contract_ready if target else release.source_contract_ready
    assert check('synthetic') is False and calls == ['registration']


@pytest.mark.parametrize('revision', [registration.PREVIOUS, registration.TARGET, release.TARGET])
def test_current_predeploy_entrypoint_dispatches_only_reviewed_revisions(monkeypatch, revision):
    events = []
    monkeypatch.setattr(registration, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(registration, 'migrated_version', lambda _: revision)
    monkeypatch.setattr(registration, 'main', lambda: events.append('registration') or 0)
    monkeypatch.setattr(release, 'main', lambda: events.append('phone') or 0)
    assert registration.predeploy_main() == 0
    assert events == (['registration', 'phone'] if revision == registration.PREVIOUS else ['phone'])


def test_failed_prior_release_or_unknown_revision_never_runs_phone_release(monkeypatch):
    events = []
    version = [registration.PREVIOUS]
    monkeypatch.setattr(registration, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(registration, 'migrated_version', lambda _: version[0])
    monkeypatch.setattr(registration, 'main', lambda: 3)
    monkeypatch.setattr(release, 'main', lambda: events.append('phone') or 0)
    assert registration.predeploy_main() == 3 and events == []
    version[0] = '0041_unreviewed'
    assert registration.predeploy_main() == 2 and events == []
