"""Private backup ordering, finite migration scope and idempotent verification."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_public_runtime as release
from scripts import backup_and_migrate_commissions as common


def configure(monkeypatch,*,current=None,backup_error=False,migration_result=0,target_after=True,contract=True,source=True):
    version=[current or release.PREVIOUS];events=[]
    monkeypatch.setattr(release,'connection_values',lambda:('synthetic-private-connection',object()))
    monkeypatch.setattr(release,'migrated_version',lambda raw:version[0])
    monkeypatch.setattr(release,'storage_values',lambda:{'bucket':'synthetic-private'})
    def backup(url,storage,destination,**kwargs):
        events.append('backup')
        assert isinstance(destination,Path) and destination.name=='backup.dump'
        assert storage=={'bucket':'synthetic-private'}
        assert kwargs=={'backup_context':'public-runtime-0038','schema_revision':version[0],'preserve_ownership':True}
        if backup_error:raise RuntimeError('secret-connection-and-backup-credentials')
    def migrate(args,**kwargs):
        events.append('migration')
        assert args==[release.sys.executable,'-m','alembic','upgrade',release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE']=='1'
        assert kwargs['stdout']==kwargs['stderr']==release.subprocess.DEVNULL
        assert kwargs['check'] is False and kwargs['timeout']==600
        if target_after:version[0]=release.TARGET
        return SimpleNamespace(returncode=migration_result)
    def schema(raw):events.append('contract');return contract
    def source_schema(raw,current):events.append('source');return source
    monkeypatch.setattr(release,'perform_backup',backup)
    monkeypatch.setattr(release.subprocess,'run',migrate)
    monkeypatch.setattr(release,'schema_contract_ready',schema)
    monkeypatch.setattr(release,'source_contract_ready',source_schema)
    return version,events


def test_reuses_reviewed_private_backup_instead_of_new_storage_mechanism():
    assert release.PREVIOUS=='0036_site_publication' and release.TARGET=='0038_timezone_review'
    assert release.APPROVED_SOURCES=={'0036_site_publication','0037_public_runtime'}
    assert release.connection_values is common.connection_values
    assert release.storage_values is common.storage_values
    assert release.perform_backup is common.perform_backup


def test_only_semantically_identical_zero_integer_defaults_are_normalized():
    for value in ('0','0::bigint',"'0'::bigint"):
        assert release._default('bigint',value)=="'0'::bigint"
    for value in ('999',"'999'::bigint",'random()',None):
        assert release._default('bigint',value)==value
    assert release._default('text','0')=='0'


@pytest.mark.parametrize('current',[release.PREVIOUS,release.INTERMEDIATE])
def test_backup_preserves_ownership_and_precedes_exact_target_then_idempotent_recheck(monkeypatch,current):
    version,events=configure(monkeypatch,current=current)
    assert release.main()==0 and version[0]==release.TARGET
    assert events==['source','backup','migration','contract']
    assert release.main()==0 and events==['source','backup','migration','contract','contract']


@pytest.mark.parametrize('current',['0035_public_amenities','0039_unknown','head'])
def test_other_revisions_stop_before_storage_backup_or_ddl(monkeypatch,current):
    _,events=configure(monkeypatch,current=current)
    monkeypatch.setattr(release,'storage_values',lambda:pytest.fail('Unreviewed source must not access storage'))
    assert release.main()==2 and events==[]


def test_bad_source_contract_cannot_begin_backup_or_ddl(monkeypatch):
    _,events=configure(monkeypatch,source=False)
    assert release.main()==3 and events==['source']


def test_backup_failure_stops_before_ddl_and_never_prints_credentials(monkeypatch,capsys):
    _,events=configure(monkeypatch,backup_error=True)
    assert release.main()==3 and events==['source','backup']
    captured=capsys.readouterr()
    assert 'secret-connection' not in captured.out+captured.err
    assert 'predeploy_failed_closed' in captured.err


@pytest.mark.parametrize('code,target_after',[(1,True),(0,False)])
def test_failed_migration_or_missing_target_never_reports_success(monkeypatch,code,target_after):
    _,events=configure(monkeypatch,migration_result=code,target_after=target_after)
    assert release.main()==3 and events==['source','backup','migration']


@pytest.mark.parametrize('current',[release.PREVIOUS,release.TARGET])
def test_target_revision_does_not_replace_schema_contract_validation(monkeypatch,current):
    _,events=configure(monkeypatch,current=current,contract=False)
    assert release.main()==3
    assert events==(['source','backup','migration','contract'] if current==release.PREVIOUS else ['contract'])


def test_missing_private_storage_aborts_before_any_backup_or_ddl(monkeypatch):
    _,events=configure(monkeypatch)
    def missing():raise ValueError('private-bucket-credential')
    monkeypatch.setattr(release,'storage_values',missing)
    assert release.main()==3 and events==['source']


def test_schema_probe_uses_readonly_connection_and_disposes_on_failure(monkeypatch):
    events=[]
    class Engine:
        def connect(self):raise RuntimeError('synthetic-database-probe-failure')
        def dispose(self):events.append('dispose')
    def create(raw,**kwargs):
        assert raw=='synthetic-private'
        assert kwargs['poolclass'] is release.NullPool
        assert 'default_transaction_read_only=on' in kwargs['connect_args']['options']
        assert 'statement_timeout=10000' in kwargs['connect_args']['options']
        return Engine()
    monkeypatch.setattr(release,'create_engine',create)
    with pytest.raises(RuntimeError):release._runtime_contract_ready('synthetic-private')
    assert events==['dispose']
