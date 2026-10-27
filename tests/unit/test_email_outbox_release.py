"""No real backup/DDL: every release safety gate is exercised with isolated doubles."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_email_outbox as release
from scripts import backup_and_migrate_commissions as common
from scripts import backup_and_migrate_recurring_goals as goals


def configure(monkeypatch, *, current=None, source=True, contract=True, backup_error=False, busy=False, result=0, confirm=True, drift=False):
    version = [current or release.PREVIOUS]; events = []
    monkeypatch.setattr(release, 'connection_values', lambda: ('synthetic', object()))
    monkeypatch.setattr(release, 'migrated_version', lambda _: version[0])
    @contextmanager
    def lock(_):
        events.append('lock')
        if busy: raise RuntimeError('synthetic-busy')
        try: yield
        finally: events.append('unlock')
    monkeypatch.setattr(release, 'release_lock', lock)
    def source_check(_): events.append('source'); return source
    def target_check(_): events.append('target'); return contract
    def storage(): events.append('storage'); return {'bucket': 'synthetic'}
    def backup(url, settings, path, **kwargs):
        events.append('backup')
        assert path.name == 'backup.dump' and settings == {'bucket': 'synthetic'}
        assert kwargs == {'backup_context': 'email-outbox-0038', 'schema_revision': release.PREVIOUS, 'preserve_ownership': True}
        if drift: version[0] = '0039_unreviewed'
        if backup_error: raise RuntimeError('secret-do-not-print')
    def migrate(args, **kwargs):
        events.append('ddl')
        assert args == [release.sys.executable, '-m', 'alembic', 'upgrade', release.TARGET]
        assert kwargs['env']['ORION_MIGRATION_SWITCH_ROLE'] == '1'
        assert kwargs['stdout'] == kwargs['stderr'] == release.subprocess.DEVNULL
        assert kwargs['timeout'] == 600 and kwargs['check'] is False
        if confirm: version[0] = release.TARGET
        return SimpleNamespace(returncode=result)
    for name, fn in [('source_contract_ready',source_check),('schema_contract_ready',target_check),('storage_values',storage),('perform_backup',backup)]: monkeypatch.setattr(release,name,fn)
    monkeypatch.setattr(release.subprocess,'run',migrate)
    return events


def test_shared_dump_preserves_private_checksum_readback_and_scope_contract():
    assert release.connection_values is common.connection_values
    assert release.storage_values is common.storage_values
    assert release.perform_backup is common.perform_backup
    assert release.PREVIOUS == '0037_recurring_goals' and release.TARGET == '0038_email_outbox'


def test_backup_and_role_guarded_exact_migration_are_required_once(monkeypatch):
    events=configure(monkeypatch)
    assert release.main()==0
    assert events==['lock','source','storage','backup','source','ddl','source','target','unlock']
    events.clear();assert release.main()==0
    assert events==['lock','source','target','unlock']


@pytest.mark.parametrize('revision',['0036_site_publication','0039_unreviewed','unknown'])
def test_unreviewed_revision_never_reaches_backup_or_ddl(monkeypatch,revision):
    events=configure(monkeypatch,current=revision)
    assert release.main()==2 and events==['lock','unlock']


@pytest.mark.parametrize('current',[release.PREVIOUS,release.TARGET])
def test_old_schema_contract_is_required_even_on_repeat_release(monkeypatch,current):
    events=configure(monkeypatch,current=current,source=False)
    assert release.main()==3 and events==['lock','source','unlock']


def test_backup_failure_aborts_before_ddl_and_suppresses_secret_exception(monkeypatch,capsys):
    events=configure(monkeypatch,backup_error=True)
    assert release.main()==3 and 'ddl' not in events
    captured=capsys.readouterr();assert 'secret-do-not-print' not in captured.out+captured.err


def test_revision_drift_after_verified_backup_aborts_ddl(monkeypatch):
    events=configure(monkeypatch,drift=True)
    assert release.main()==3 and 'ddl' not in events


def test_busy_lock_blocks_dump_and_ddl(monkeypatch):
    events=configure(monkeypatch,busy=True)
    assert release.main()==3 and events==['lock']


@pytest.mark.parametrize('result,confirm',[(1,True),(0,False)])
def test_failed_or_unconfirmed_upgrade_never_reports_ready(monkeypatch,result,confirm):
    events=configure(monkeypatch,result=result,confirm=confirm)
    assert release.main()==3 and events[-2:]==['ddl','unlock']


@pytest.mark.parametrize('current',[release.PREVIOUS,release.TARGET])
def test_partial_or_tampered_target_cannot_pass_on_upgrade_or_repeat(monkeypatch,current):
    events=configure(monkeypatch,current=current,contract=False)
    assert release.main()==3 and events[-2:]==['target','unlock']


def test_both_prior_contracts_are_required(monkeypatch):
    calls=[]
    monkeypatch.setattr(release,'publication_contract_ready',lambda _:calls.append('publication') or True)
    monkeypatch.setattr(release,'goals_contract_ready',lambda _:calls.append('goals') or False)
    assert release.source_contract_ready('synthetic') is False and calls==['publication','goals']


@pytest.mark.parametrize('name',['panel_email_outbox_kind_check','panel_email_outbox_status_check'])
def test_restore_parser_variants_are_finite_and_do_not_accept_wider_values(name):
    variants=release.constraint_definitions(name)
    assert len(variants)==2
    assert release.normalized(release.EXPECTED_CONSTRAINTS[name][1]) in variants
    assert all('unreviewed_status' not in value for value in variants)
    for value in variants:
        assert value.replace("'verify'", "'unreviewed_status'").replace("'pending'", "'unreviewed_status'") not in variants
        assert value.replace('::text','::integer') not in variants


@pytest.mark.parametrize('name,column,values',[
    ('ck_goal_revision_metric','metric',['revenue_cents','appointments']),
    ('ck_professional_goal_recurrence','recurrence',['none','monthly']),
])
def test_goal_restore_guard_accepts_only_two_reviewed_cast_forms(name,column,values):
    array=', '.join("'"+value+"'::character varying" for value in values)
    original=f"CHECK ((({column})::text = ANY ((ARRAY[{array}])::text[])))"
    variants=goals.constraint_definitions(name,original)
    assert len(variants)==2 and goals.normalized(original) in variants
    assert goals.normalized(goals.RESTORED_CONSTRAINTS[name]) in variants
    for value in variants:
        assert value.replace("'"+values[0]+"'","'unreviewed_value'") not in variants
        assert value.replace('::text','::integer') not in variants
