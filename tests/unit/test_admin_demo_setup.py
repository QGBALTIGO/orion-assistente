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


def _lease_plan_process_sample():
    """Synthetic SQL result boundary; the integration cases execute this SQL."""
    import hashlib
    import re
    from types import SimpleNamespace
    from unittest.mock import patch
    row={'id':'synthetic-channel','tenant_id':'synthetic-tenant','owner_account_id':'synthetic-owner',
        'label':'Synthetic label','idempotency_key':'synthetic-request','created_at':'2026-10-19T00:00:00Z',
        'credential_hash':'a'*64,'credential_encrypted':'synthetic-encrypted','allowed_senders':['5567999999901'],
        'enabled':True,'assistant_enabled':True,'revision':1,'request_fingerprint':'b'*64,
        'lease_owner':'synthetic-lease-owner','lease_expires_at':'2026-10-20T00:00:30Z','fence':1,
        'updated_at':'2026-10-20T00:00:00Z'}
    def rows_hash(value):
        item=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        return hashlib.sha256(item.encode()+b'\n').hexdigest()
    def raw_snapshot(_):
        return {'revision':migration.TARGET,'counts':{'qr_channels':1,'customers':1},
            'rows':{'qr_channels':rows_hash(row),'customers':'synthetic-preserved-customer'}}
    class DB:
        def execute(self,query,params):
            sql=str(query)
            assert 'FROM public."qr_channels" t' in sql and sql.endswith(' ORDER BY 1')
            removed=re.findall(r" - '([a-z_]+)'",sql)
            assert set(removed)=={'lease_owner','lease_expires_at','fence','updated_at'}
            value={key:value for key,value in row.items() if key not in removed}
            return [(hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest(),)]
    settings=SimpleNamespace(run_id='075a435e-ed21-44b7-8d59-ba5f01087ec0')
    plan={'session_digest':'c'*64,'credential_digest':'d'*64,'owner':'synthetic-demo-owner'}
    with patch.object(setup.schema,'snapshot',raw_snapshot):
        first=setup.snapshot(DB());expected=setup.plan_digest(first,plan,settings)
        row.update(lease_owner='synthetic-new-lease-owner',lease_expires_at='2026-10-20T00:01:30Z',
            fence=9,updated_at='2026-10-20T00:01:00Z')
        lease_hash=setup.plan_digest(setup.snapshot(DB()),plan,settings)
        protected={}
        for field,value in {'id':'synthetic-other-channel','tenant_id':'synthetic-other-tenant',
            'label':'Synthetic changed label','idempotency_key':'synthetic-other-request',
            'created_at':'2026-10-18T00:00:00Z','owner_account_id':'synthetic-other-owner','credential_hash':'f'*64,
            'credential_encrypted':'synthetic-rotated','allowed_senders':['5567999999902'],
            'enabled':False,'assistant_enabled':False,'revision':2,'request_fingerprint':'e'*64}.items():
            old=row[field];row[field]=value
            protected[field]=setup.plan_digest(setup.snapshot(DB()),plan,settings)!=expected
            row[field]=old
        other=setup.snapshot(DB());other['rows']['customers']='synthetic-changed-customer'
        protected['other_table']=setup.plan_digest(other,plan,settings)!=expected
        protected['session']=setup.plan_digest(first,{**plan,'session_digest':'e'*64},settings)!=expected
        protected['credential_binding']=setup.plan_digest(first,{**plan,'credential_digest':'e'*64},settings)!=expected
    return {'stable_hash':expected,'lease_hash':lease_hash,'protected':protected}


def test_plan_snapshot_ignores_only_transport_lease_across_processes():
    import os
    from pathlib import Path
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[2]
    code="import sys;sys.path[:0]=[sys.argv[1],sys.argv[2]];from test_admin_demo_setup import _lease_plan_process_sample;import json;print(json.dumps(_lease_plan_process_sample(),sort_keys=True))"
    # A child never inherits database/provider credentials. No database is used.
    allowed={'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT','LOCALAPPDATA','APPDATA','USERPROFILE'}
    clean={key:value for key,value in os.environ.items() if key.upper() in allowed}
    samples=[]
    for seed in ('0','1','42'):
        result=subprocess.run([sys.executable,'-c',code,str(root),str(Path(__file__).parent)],
            cwd=root,env={**clean,'PYTHONHASHSEED':seed,'PYTHONIOENCODING':'utf-8'},
            check=True,capture_output=True,text=True,timeout=45)
        samples.append(json.loads(result.stdout))
    assert len({sample['stable_hash'] for sample in samples})==1
    assert all(sample['lease_hash']==sample['stable_hash'] for sample in samples)
    assert all(all(sample['protected'].values()) for sample in samples)
