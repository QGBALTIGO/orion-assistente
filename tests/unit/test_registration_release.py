"""Release ordering and fail-closed checks; no external backup, DDL or email."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from scripts import backup_and_migrate_registration as release


@pytest.mark.parametrize('case',['success','repeated','wrong_revision','old_contract','backup_failed','ddl_failed','unconfirmed_revision','new_contract'])
def test_registration_release_requires_backup_and_verified_contract(monkeypatch,case):
    version=[release.TARGET if case=='repeated' else '0037_recurring_goals' if case=='wrong_revision' else release.PREVIOUS]
    events=[]
    monkeypatch.setattr(release,'connection_values',lambda:('synthetic',object()))
    monkeypatch.setattr(release,'migrated_version',lambda _:version[0])
    @contextmanager
    def lock(_):yield
    monkeypatch.setattr(release,'release_lock',lock)
    monkeypatch.setattr(release,'source_contract_ready',lambda _:case!='old_contract')
    monkeypatch.setattr(release,'email_contract_ready',lambda _:True)
    monkeypatch.setattr(release,'schema_contract_ready',lambda _:case!='new_contract')
    monkeypatch.setattr(release,'storage_values',lambda:{})
    def backup(*args,**kwargs):
        events.append('backup')
        assert kwargs=={'backup_context':'registration-0039','schema_revision':release.PREVIOUS,'preserve_ownership':True}
        if case=='backup_failed':raise RuntimeError('private synthetic detail')
    def migrate(args,**kwargs):
        events.append('ddl')
        assert events[0]=='backup'
        assert args[-2:]==['upgrade',release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE']=='1'
        if case!='unconfirmed_revision':version[0]=release.TARGET
        return SimpleNamespace(returncode=1 if case=='ddl_failed' else 0)
    monkeypatch.setattr(release,'perform_backup',backup)
    monkeypatch.setattr(release.subprocess,'run',migrate)
    status=release.main()
    assert (status==0)==(case in {'success','repeated'})
    if case in {'wrong_revision','old_contract','repeated'}:assert events==[]
    elif case=='backup_failed':assert events==['backup']
    else:assert events==['backup','ddl']
