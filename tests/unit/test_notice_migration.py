"""Startup controls and exact migration bytes are independent of customer data."""
from pathlib import Path
import pytest
from scripts import one_time_notice_migration as ops
from scripts.start_api import runtime_environment


def test_notice_migration_uses_only_reviewed_additive_revision():
    module=ops.reviewed_migration()
    assert module.revision=='0020_appointment_notices'
    assert module.down_revision=='0019_appointment_lifecycle'
    assert ops.TARGET_TABLES-ops.TABLES=={'appointment_notices'}


def test_notice_runner_requires_explicit_flag_and_role(monkeypatch):
    monkeypatch.delenv('ORION_RUN_NOTICE_MIGRATION',raising=False)
    with pytest.raises(ops.Stop,match='explicit_flag_required'):ops.Settings.from_environment()
    monkeypatch.setenv('ORION_RUN_NOTICE_MIGRATION','1')
    monkeypatch.delenv('ORION_MIGRATION_SWITCH_ROLE',raising=False)
    with pytest.raises(ops.Stop,match='role_switch_required'):ops.Settings.from_environment()


def test_notice_disabled_runner_does_not_connect(monkeypatch,capsys):
    monkeypatch.setenv('ORION_RUN_NOTICE_MIGRATION','0')
    monkeypatch.setattr(ops,'execute',lambda _:pytest.fail('disabled runner touched database'))
    assert ops.main()==0 and 'disabled' in capsys.readouterr().out


def test_notice_migration_credentials_do_not_reach_http_process():
    source={'ORION_RUN_NOTICE_MIGRATION':'1','MIGRATION_DATABASE_URL':'PRIVATE',
            'ORION_APPOINTMENT_NOTICES_ENABLED':'1','QR_BRIDGE_TOKEN':'TEST_ONLY','APP_DATABASE_URL':'RUNTIME'}
    cleaned=runtime_environment(source)
    assert 'MIGRATION_DATABASE_URL' not in cleaned and 'ORION_RUN_NOTICE_MIGRATION' not in cleaned
    assert cleaned['ORION_APPOINTMENT_NOTICES_ENABLED']=='1' and cleaned['APP_DATABASE_URL']=='RUNTIME'
