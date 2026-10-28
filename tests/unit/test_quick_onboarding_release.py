"""Fail-closed 0041 release ordering with synthetic backup and no real DDL."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_quick_onboarding as release
from scripts import backup_and_migrate_registration as registration


def configure(monkeypatch, *, current=None, source=True, target=True,
              backup_error=False, busy=False, drift=False, result=0, confirm=True):
    version=[current or release.PREVIOUS]
    events=[]
    monkeypatch.setattr(release,'connection_values',lambda:('synthetic',object()))
    monkeypatch.setattr(release,'migrated_version',lambda _:version[0])

    @contextmanager
    def locked(_raw):
        events.append('lock')
        if busy:
            raise RuntimeError('private-reentrant-lock')
        try:
            yield
        finally:
            events.append('unlock')

    monkeypatch.setattr(release,'release_lock',locked)
    monkeypatch.setattr(release,'phone_contract_ready',
                        lambda _:events.append('source') or source)
    monkeypatch.setattr(release,'schema_contract_ready',
                        lambda _:events.append('target') or target)
    monkeypatch.setattr(release,'storage_values',
                        lambda:events.append('storage') or {'bucket':'synthetic'})

    def backup(url,storage,path,**kwargs):
        events.append('backup')
        assert path.name=='backup.dump'
        assert kwargs=={
            'backup_context':'quick-onboarding-0041',
            'schema_revision':release.PREVIOUS,
            'preserve_ownership':True,
        }
        assert storage=={'bucket':'synthetic'}
        if backup_error:
            raise RuntimeError('private-backup-credentials')
        if drift:
            version[0]='0042_unreviewed'

    def migrate(args,**kwargs):
        events.append('ddl')
        assert args==[release.sys.executable,'-m','alembic','upgrade',release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE']=='1'
        assert kwargs['timeout']==600 and kwargs['check'] is False
        assert kwargs['stdout']==kwargs['stderr']==release.subprocess.DEVNULL
        assert events.index('backup')<events.index('ddl')
        if confirm:version[0]=release.TARGET
        return SimpleNamespace(returncode=result)

    monkeypatch.setattr(release,'perform_backup',backup)
    monkeypatch.setattr(release.subprocess,'run',migrate)
    return events


def test_release_reuses_reviewed_private_backup_and_scope():
    from scripts import backup_and_migrate_commissions as common
    from scripts import backup_and_migrate_professional_phone as phone
    assert release.connection_values is common.connection_values
    assert release.perform_backup is common.perform_backup
    assert release.storage_values is common.storage_values
    assert release.PREVIOUS==phone.TARGET
    assert release.TARGET=='0041_quick_onboarding_activation'


def test_success_requires_backup_before_migration_and_repeat_is_read_only(monkeypatch):
    events=configure(monkeypatch)
    assert release.main()==0
    assert events==['lock','source','storage','backup','source','ddl','target','unlock']
    events.clear()
    assert release.main()==0
    assert events==['lock','target','unlock']


@pytest.mark.parametrize('revision',['0038_email_outbox','0042_unreviewed','unknown'])
def test_unreviewed_revision_has_no_backup_ddl_or_schema_check(monkeypatch,revision):
    events=configure(monkeypatch,current=revision)
    assert release.main()==2
    assert events==['lock','unlock']


@pytest.mark.parametrize('options',[
    {'source':False},{'backup_error':True},{'busy':True},{'drift':True},
])
def test_failed_prerequisite_never_reaches_ddl_and_hides_private_context(monkeypatch,capsys,options):
    events=configure(monkeypatch,**options)
    assert release.main()==3
    assert 'ddl' not in events
    captured=capsys.readouterr()
    assert 'private-' not in captured.out+captured.err


@pytest.mark.parametrize('options',[
    {'result':1},{'confirm':False},{'target':False},
    {'current':release.TARGET,'target':False},
])
def test_invalid_upgrade_cannot_be_reported_as_ready(monkeypatch,options):
    configure(monkeypatch,**options)
    assert release.main()==3


def test_predeploy_refuses_unreviewed_revisions_without_calling_quick_migration(monkeypatch):
    events=[]
    monkeypatch.setattr(registration,'connection_values',lambda:('synthetic',object()))
    monkeypatch.setattr(registration,'migrated_version',lambda _:'0042_unreviewed')
    monkeypatch.setattr(release,'main',lambda:events.append('quick') or 0)
    assert registration.predeploy_main()==2
    assert events==[]
