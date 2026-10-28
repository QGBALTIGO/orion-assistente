"""Backup, deployment ordering and read-only runtime barrier boundaries."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_single_whatsapp as release
from scripts import backup_and_migrate_registration as registration
from scripts import backup_and_migrate_professional_phone as contacts
from scripts import backup_and_migrate_quick_onboarding as quick
from scripts import backup_and_migrate_admin_accounts as accounts
from scripts import backup_and_migrate_cakto as cakto
from scripts import backup_and_migrate_plans as plans
from scripts import wait_for_single_whatsapp_schema as barrier


def configured(monkeypatch, *, revision=None, source=True, target=True, backup_error=False,
               drift=False, result=0, confirm=True):
    version = [revision or release.PREVIOUS]
    events = []
    monkeypatch.setattr(release, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(release, 'migrated_version', lambda _: version[0])
    @contextmanager
    def locked(_):
        events.append('lock')
        try:
            yield
        finally:
            events.append('unlock')
    monkeypatch.setattr(release, 'release_lock', locked)
    monkeypatch.setattr(release, 'source_ready', lambda _: events.append('source') or source)
    monkeypatch.setattr(release, 'schema_contract_ready', lambda _: events.append('target') or target)
    monkeypatch.setattr(release, 'storage_values', lambda: {'bucket': 'synthetic'})
    def backup(url, settings, path, **kwargs):
        events.append('backup')
        assert path.name == 'backup.dump' and settings == {'bucket': 'synthetic'}
        assert kwargs == {'backup_context': 'single-whatsapp-0044',
                          'schema_revision': release.PREVIOUS, 'preserve_ownership': True}
        if drift:
            version[0] = '0045_unreviewed'
        if backup_error:
            raise RuntimeError('private-backup-detail')
    monkeypatch.setattr(release, 'perform_backup', backup)
    def migrate(args, **kwargs):
        events.append('ddl')
        assert args == [release.sys.executable, '-m', 'alembic', 'upgrade', release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE'] == '1'
        assert kwargs['stdout'] == kwargs['stderr'] == release.subprocess.DEVNULL
        assert kwargs['timeout'] == 600 and kwargs['check'] is False
        if confirm:
            version[0] = release.TARGET
        return SimpleNamespace(returncode=result)
    monkeypatch.setattr(release.subprocess, 'run', migrate)
    return events


def test_verified_backup_precedes_ddl_and_repeat_only_checks_contract(monkeypatch):
    events = configured(monkeypatch)
    assert release.main() == 0
    assert events == ['lock', 'source', 'backup', 'source', 'ddl', 'target', 'unlock']
    events.clear()
    assert release.main() == 0
    assert events == ['lock', 'target', 'unlock']


@pytest.mark.parametrize('revision', ['0041_quick_onboarding', '0044_unreviewed', 'unknown'])
def test_unreviewed_origin_never_runs_backup_or_ddl(monkeypatch, revision):
    events = configured(monkeypatch, revision=revision)
    assert release.main() == 2 and events == ['lock', 'unlock']


@pytest.mark.parametrize('options', [{'source': False}, {'backup_error': True}, {'drift': True}])
def test_failed_prerequisite_never_runs_ddl_or_leaks_details(monkeypatch, capsys, options):
    events = configured(monkeypatch, **options)
    assert release.main() == 3 and 'ddl' not in events
    output = capsys.readouterr()
    assert 'private-' not in output.out + output.err


@pytest.mark.parametrize('options', [{'result': 1}, {'confirm': False}, {'target': False},
                                   {'revision': release.TARGET, 'target': False}])
def test_unverified_upgrade_cannot_be_reported_ready(monkeypatch, options):
    configured(monkeypatch, **options)
    assert release.main() == 3


@pytest.mark.parametrize('revision,expected', [
    (registration.PREVIOUS, ['registration','phone','quick','accounts','cakto','single','plans']),
    (registration.TARGET, ['phone','quick','accounts','cakto','single','plans']),
    (contacts.TARGET, ['quick','accounts','cakto','single','plans']),
    (quick.TARGET, ['quick','accounts','cakto','single','plans']),
    (accounts.TARGET, ['accounts','cakto','single','plans']),
    (cakto.TARGET, ['cakto','single','plans']),
    (release.TARGET, ['single','plans']),
    (plans.TARGET, ['plans']),
])
def test_all_reviewed_predeploy_origins_reach_single_connection_in_order(monkeypatch, revision, expected):
    calls = []
    monkeypatch.setattr(registration, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(registration, 'migrated_version', lambda _: revision)
    for module, name in [(registration,'registration'),(contacts,'phone'),(quick,'quick'),
                         (accounts,'accounts'),(cakto,'cakto'),(release,'single'),(plans,'plans')]:
        monkeypatch.setattr(module, 'main', lambda n=name: calls.append(n) or 0)
    assert registration.predeploy_main() == 0 and calls == expected


@pytest.mark.parametrize('failed', ['registration','phone','quick','accounts','cakto','single','plans'])
def test_predeploy_stops_at_first_failed_stage(monkeypatch, failed):
    calls = []
    monkeypatch.setattr(registration, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(registration, 'migrated_version', lambda _: registration.PREVIOUS)
    for module, name in [(registration,'registration'),(contacts,'phone'),(quick,'quick'),
                         (accounts,'accounts'),(cakto,'cakto'),(release,'single'),(plans,'plans')]:
        monkeypatch.setattr(module, 'main', lambda n=name: calls.append(n) or (3 if n == failed else 0))
    assert registration.predeploy_main() == 3
    order = ['registration','phone','quick','accounts','cakto','single','plans']
    assert calls == order[:order.index(failed)+1]


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [None,'pointer_ready','binding_ready','index_ready','policy_ready','guards_ready','grants_ready'])
async def test_runtime_probe_is_readonly_and_requires_every_contract(monkeypatch, missing):
    calls = []
    row = dict(runtime_role='app_runtime', authenticated_role='app_runtime', restricted_role=True,
               **{key: key != missing for key in ['pointer_ready','binding_ready','index_ready',
                                                  'policy_ready','guards_ready','grants_ready']})
    class Connection:
        async def fetchrow(self, query):
            assert query == barrier.SCHEMA_QUERY
            assert all(token not in query.upper() for token in ['UPDATE ', 'DELETE ', 'INSERT ', 'ALTER '])
            calls.append('catalog')
            return row
        def terminate(self): calls.append('closed')
    async def connect(dsn, **kwargs):
        assert dsn == 'synthetic'
        assert kwargs['server_settings']['default_transaction_read_only'] == 'on'
        assert kwargs['server_settings']['search_path'] == 'pg_catalog'
        calls.append('connect')
        return Connection()
    monkeypatch.setattr(barrier.asyncpg,'connect',connect)
    assert await barrier.probe_schema(barrier.Settings('synthetic'),3) is (missing is None)
    assert calls == ['connect','catalog','closed']


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('runtime_role','app_support'),('authenticated_role','app_migrator'),('restricted_role',False)])
async def test_runtime_probe_rejects_privileged_identity_and_closes(monkeypatch, field, value):
    closed=[]
    row=dict(runtime_role='app_runtime',authenticated_role='app_runtime',restricted_role=True)
    row[field]=value
    class Connection:
        async def fetchrow(self, _): return row
        def terminate(self): closed.append(True)
    async def connect(*args,**kwargs): return Connection()
    monkeypatch.setattr(barrier.asyncpg,'connect',connect)
    with pytest.raises(barrier.Stop,match='runtime_role_required'):
        await barrier.probe_schema(barrier.Settings('synthetic'),3)
    assert closed == [True]


@pytest.mark.parametrize('ready,result', [(True,0),(False,3)])
def test_existing_cakto_predeploy_verifies_0044_readonly_without_replaying_ddl(monkeypatch, ready, result):
    calls=[]
    monkeypatch.setattr(cakto,'connection_values',lambda:('synthetic',object()))
    monkeypatch.setattr(cakto,'migrated_version',lambda _:release.TARGET)
    monkeypatch.setattr(cakto,'schema_ready',lambda _:calls.append('check') or ready)
    monkeypatch.setattr(cakto,'perform_backup',lambda *a,**k:pytest.fail('Unnecessary backup'))
    monkeypatch.setattr(cakto.subprocess,'run',lambda *a,**k:pytest.fail('Unexpected DDL'))
    assert cakto.main() == result and calls == ['check']
