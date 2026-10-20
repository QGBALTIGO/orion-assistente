"""Configuration and irreversible-step guards, with no database or provider."""
import base64
from dataclasses import replace
import json
from uuid import uuid4

import pytest
from scripts import one_time_admin_demo_setup as setup
from scripts import one_time_admin_identity_migration as migration


@pytest.fixture
def configured(monkeypatch):
    values={'ORION_RUN_ADMIN_DEMO_SETUP':'1','ORION_MIGRATION_SWITCH_ROLE':'1',
        'ORION_ADMIN_DEMO_SETUP_RUN_ID':str(uuid4()),'ORION_ADMIN_DEMO_TENANT_ID':str(setup.DEMO_TENANT),
        'MIGRATION_DATABASE_URL':'postgresql+psycopg://synthetic_owner:synthetic-only@db.railway.internal:5432/synthetic_test',
        'ORION_EXPECTED_DATABASE_HOST':'db.railway.internal','ORION_EXPECTED_DATABASE_NAME':'synthetic_test',
        'QR_BRIDGE_URL':'http://bridge.railway.internal:3000','QR_BRIDGE_TOKEN':'synthetic-local-only-'+('x'*40),
        'QR_AI_ENABLED':'1','QR_CHANNELS_ENABLED':'1','INBOX_TOOLS_ENABLED':'1','PANEL_ADMIN_USERNAME':' Synthetic-Admin ',
        'PANEL_ADMIN_PASSWORD_HASH':'pbkdf2_sha256$200000$'+base64.urlsafe_b64encode(b'synthetic-salt').decode()+'$'+base64.urlsafe_b64encode(b'x'*32).decode()}
    values.update({name:'0' for name in setup.OFF_FLAGS})
    for name,value in values.items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('ORION_ADMIN_DEMO_SETUP_PHASE',raising=False)
    monkeypatch.delenv('ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256',raising=False)
    monkeypatch.setattr(setup.adoption,'session_proof',lambda *_:'a'*64)
    monkeypatch.setattr(setup.backup,'engine_for',lambda *_:pytest.fail('Configuration touched a database'))
    return setup.Settings.from_environment()


def test_default_phase_is_plan_and_identity_uses_exact_config_without_plaintext_password(configured):
    assert configured.phase=='plan' and configured.approved_plan_sha256==''
    assert configured.username=='synthetic-admin' and len(configured.credential_digest)==64
    assert 'synthetic_owner' not in repr(configured) and 'synthetic-admin' not in repr(configured)


@pytest.mark.parametrize('flag',['QR_AI_ENABLED','QR_CHANNELS_ENABLED'])
def test_existing_ai_and_channels_must_remain_enabled(configured,monkeypatch,flag):
    monkeypatch.setenv(flag,'0')
    with pytest.raises(setup.Stop,match='existing_assistant_required|existing_qr_channels_required'):
        setup.validate_configuration(configured)


@pytest.mark.parametrize('value',['0','1'])
def test_tools_existing_state_is_accepted_and_never_changed(configured,monkeypatch,value):
    monkeypatch.setenv('INBOX_TOOLS_ENABLED',value)
    setup.validate_configuration(configured)
    import os
    assert os.getenv('INBOX_TOOLS_ENABLED')==value


def test_apply_phase_requires_explicit_approved_digest(configured,monkeypatch):
    monkeypatch.setenv('ORION_ADMIN_DEMO_SETUP_PHASE','apply')
    with pytest.raises(setup.Stop,match='approved_plan_required'):setup.Settings.from_environment()


def test_actual_display_name_must_fit_before_a_plan_or_backup(configured):
    with pytest.raises(setup.Stop,match='admin_name_too_long'):
        setup.validate_configuration(replace(configured,username='a'*121))


def test_apply_never_accepts_fabricated_or_missing_rehearsal(configured):
    with pytest.raises(setup.Stop,match='rehearsal_proof_required'):
        setup.apply(None,replace(configured,phase='apply',approved_plan_sha256='b'*64),{}, {},receipt=None)
    with pytest.raises(setup.Stop,match='isolated_restore_proof_required'):
        setup.apply(None,configured,{}, {},restored=True,receipt=None)


def test_disabled_commands_never_connect_or_provision(configured,monkeypatch,capsys):
    monkeypatch.setenv('ORION_RUN_ADMIN_DEMO_SETUP','0')
    monkeypatch.setenv('ORION_RUN_ADMIN_IDENTITY_MIGRATION','0')
    assert setup.main()==migration.main()==0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()]==[{'status':'disabled'},{'status':'disabled'}]
