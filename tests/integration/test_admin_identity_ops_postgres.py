"""Real PG16 restore/rehearsal and explicit admin setup; only fake transports."""
from contextlib import nullcontext
from dataclasses import replace
import base64
import hashlib
import hmac
import json
import os
from uuid import uuid4,uuid5,NAMESPACE_URL

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import text

from app import legacy_admin_identity
from scripts import one_time_admin_identity_migration as migration
from scripts import one_time_admin_demo_setup as setup
from scripts import one_time_legacy_adoption as adoption
from scripts import wait_for_admin_identity_schema as gate
from tests.admin_test_scope import role_url
from test_communication_migration_postgres import communication_source  # noqa:F401
from test_one_time_inbox_migration_postgres import inbox_source  # noqa:F401
from test_one_time_onboarding_migration_postgres import onboarding_source  # noqa:F401
from test_one_time_management_migration import management_source  # noqa:F401
from test_fulfillment_migration_ops_postgres import fulfillment_source  # noqa:F401
from test_business_migration_ops_postgres import migration_source  # noqa:F401

pytestmark=pytest.mark.integration
PHONE='5567999999901'


def upgrade(revision):
    config=Config(str(migration.ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(migration.ROOT/'alembic'))
    command.upgrade(config,revision)


def encoded_password(marker=b'synthetic-admin-only'):
    salt=b'orion-synthetic-admin-test-salt'
    value=hashlib.pbkdf2_hmac('sha256',marker,salt,200000)
    return 'pbkdf2_sha256$200000$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(value).decode()


@pytest.fixture
def admin_migration_source(communication_source):
    case=communication_source
    upgrade(migration.SOURCE)
    case.settings=migration.Settings(case.settings.url,case.settings.bridge_url,case.settings.token,str(uuid4()))
    case.runtime_url=role_url(case.settings.url,os.environ['APP_DATABASE_URL'],'app_runtime')
    case.support_url=role_url(case.settings.url,os.environ['SUPPORT_DATABASE_URL'],'app_support')
    with case.engine.begin() as db:
        # An upstream fixture intentionally has a pending historical Meta
        # reply. Model a definite synthetic no-send before preservation starts;
        # the adoption operator must continue refusing every real backlog.
        db.execute(text("""UPDATE message_outbox SET status='dead',last_error='channel_not_authorized'
            WHERE status='pending'"""))
        db.execute(text("INSERT INTO communication_pending(tenant_id,contact_e164,event_key,event,reason) VALUES(:t,:p,'admin-synthetic-preserved','booking_confirmed','human_paused')"),{'t':case.tenant,'p':'+'+PHONE})
        db.execute(text("UPDATE customers SET birth_month=2,birth_day=29 WHERE tenant_id=:t"),{'t':case.tenant})
        db.execute(text("UPDATE contact_consents SET marketing_opt_in=false,operational_opt_in=false,birthday_opt_in=false,birthday_declined_at=now(),birthday_purpose_version=1 WHERE tenant_id=:t"),{'t':case.tenant})
    return case


@pytest.fixture
def admin_source(admin_migration_source,monkeypatch):
    case=admin_migration_source
    upgrade(migration.TARGET)
    case.original_tenant=case.tenant
    case.tenant=setup.DEMO_TENANT
    monkeypatch.setenv('PANEL_ADMIN_USERNAME','synthetic-admin')
    monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH',encoded_password())
    monkeypatch.setenv('PANEL_LEGACY_ADMIN_IDENTITY_ENABLED','1')
    monkeypatch.setenv('QR_AI_ENABLED','1');monkeypatch.setenv('QR_CHANNELS_ENABLED','1')
    monkeypatch.setenv('INBOX_TOOLS_ENABLED','1')
    for name in setup.OFF_FLAGS:monkeypatch.setenv(name,'0')
    username,digest=legacy_admin_identity.configuration_binding()
    base=case.settings
    session=hmac.new(base.token.encode(),b'orion:preserved-primary-session:v1\0'+PHONE.encode(),hashlib.sha256).hexdigest()
    case.settings=setup.Settings(base.url,base.bridge_url,base.token,base.run_id,case.tenant,'plan',username,digest,session)
    case.admin=uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:account:'+base.run_id)
    with case.engine.begin() as db:
        db.execute(text("INSERT INTO tenants(id,name,slug,status,bot_enabled) VALUES(:id,'Synthetic admin demo',:slug,'active',false)"),
            {'id':case.tenant,'slug':'synthetic-demo-'+uuid4().hex})
    return case


def private_transport(monkeypatch,settings,*,phone=PHONE):
    stored={};requests=[]
    original=httpx.Client
    def handler(request):
        requests.append((request.method,request.url.path))
        assert request.headers['X-Orion-Bridge-Key']==settings.token
        if request.url.path=='/internal/status':
            assert request.method=='GET'
            return httpx.Response(200,json={'status':'connected','phone':phone})
        identifier=request.url.path.rsplit('/',1)[1]
        if request.method=='POST':
            assert identifier not in stored
            body=request.content
            assert migration.backup.decrypt_dump(body,settings.token,identifier).startswith(b'PGDMP')
            stored[identifier]=body;digest=hashlib.sha256(body).hexdigest()
            assert request.headers['X-Orion-SHA256']==digest
            return httpx.Response(201,json={'id':identifier,'sha256':digest,'size':len(body)})
        assert request.method=='GET'
        return httpx.Response(200,content=stored[identifier],headers={'content-type':'application/octet-stream'})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
    return stored,requests


def approved_settings(case):
    receipt=setup.execute(case.settings)
    assert receipt['status']=='plan_only' and receipt['writes_performed'] is False
    return replace(case.settings,phase='apply',approved_plan_sha256=receipt['plan_sha256'])


def test_0030_encrypted_backup_restore_and_rehearsal_preserves_every_old_fact(admin_migration_source,monkeypatch):
    case=admin_migration_source
    with case.engine.begin() as db:before=migration.snapshot(db)
    stored,requests=private_transport(monkeypatch,case.settings)
    result=migration.execute(case.settings)
    assert result['restore_verified'] and result['rehearsal_verified'] and result['revision']==migration.TARGET
    assert result['preserved_counts']==before['counts'] and len(stored)==1
    assert [method for method,_ in requests]==['POST','GET']
    with case.engine.begin() as db:
        assert migration.rows(db,projected=True)==before['rows']
        assert migration.identity_sequences(db)==before['identity_sequences']
        assert migration.old_catalog(db,projected=True)==before['catalog']
        assert not db.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM panel_accounts WHERE auth_provider<>'password')").scalar_one()
    assert migration.execute(case.settings)=={'status':'already_applied','revision':migration.TARGET}


def test_plan_is_readonly_then_setup_and_native_owner_adoption_preserve_all_other_facts(admin_source,monkeypatch):
    case=admin_source
    stored,requests=private_transport(monkeypatch,case.settings)
    with case.engine.begin() as db:before=setup.snapshot(db)
    settings=approved_settings(case)
    with case.engine.begin() as db:assert setup.snapshot(db)==before
    assert not stored and all(method=='GET' and path=='/internal/status' for method,path in requests)
    result=setup.execute(settings)
    assert result['restore_verified'] and result['rehearsal_verified'] and result['credential_configuration_preserved']
    with case.engine.begin() as db:
        row=db.execute(text("SELECT id,email,password_hash,role,auth_provider,legacy_username,legacy_credential_digest FROM panel_accounts WHERE auth_provider='legacy_admin'")).one()
        assert tuple(row)==(case.admin,None,None,'admin','legacy_admin',settings.username,settings.credential_digest)
        assert db.execute(text("SELECT bot_enabled FROM tenants WHERE id=:id"),{'id':case.tenant}).scalar_one()
        plan={'tenant':case.tenant,'account':case.admin,'membership':uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:membership:'+settings.run_id),
            'audit':uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:audit:'+settings.run_id)}
        assert setup.snapshot(db,exclude=plan)==before
    # 0030 adoption uses the existing registered identity and a real restore;
    # Redis conversation state is not part of this new operator test.
    monkeypatch.setattr(adoption,'paused_contacts',lambda *_:())
    monkeypatch.setattr(adoption,'conversation_locks',lambda *_:nullcontext())
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',__import__('cryptography.fernet',fromlist=['Fernet']).Fernet.generate_key().decode())
    register=adoption.Settings(settings.url,settings.bridge_url,settings.token,str(uuid4()),case.tenant,(PHONE,),'register',settings.session_digest)
    first=adoption.execute(register)
    second=adoption.execute(replace(register,run_id=str(uuid4()),phase='activate'))
    assert first['revision']==second['revision']==migration.TARGET and second['authority_adopted']
    assert len(stored)==3
    with case.engine.begin() as db:
        assert db.execute(text('SELECT owner_account_id FROM qr_channels WHERE tenant_id=:t'),{'t':case.tenant}).scalar_one()==case.admin
        assert db.execute(text('SELECT count(*) FROM communication_pending WHERE tenant_id=:t'),{'t':case.original_tenant}).scalar_one()==1
        assert migration.identity_sequences(db)==before['identity_sequences']


@pytest.mark.parametrize('tamper',['check','function'])
def test_malformed_existing_0030_fails_before_backup_or_setup(admin_source,monkeypatch,tamper):
    case=admin_source
    with case.engine.begin() as db:
        if tamper=='check':
            db.exec_driver_sql('ALTER TABLE panel_accounts DROP CONSTRAINT '+migration.ADMIN_CHECK)
            db.exec_driver_sql('ALTER TABLE panel_accounts ADD CONSTRAINT '+migration.ADMIN_CHECK+' CHECK(true)')
        else:
            db.exec_driver_sql("""CREATE OR REPLACE FUNCTION public.inbox_operator_version(t uuid,a uuid) RETURNS integer
                LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
                PERFORM current_setting('app.legacy_admin_digest',true); -- legacy_credential_digest
                RETURN 1; END $$""")
    monkeypatch.setattr(migration,'capture_dump',lambda *_:pytest.fail('Malformed target reached backup'))
    with pytest.raises(migration.Stop,match='admin_identity_check_mismatch' if tamper=='check' else 'operator_binding_guard_mismatch'):
        migration.execute(migration.Settings(case.settings.url,case.settings.bridge_url,case.settings.token,case.settings.run_id))


def test_setup_rejects_flags_old_approval_wrong_tenant_and_changed_session_without_writes(admin_source,monkeypatch):
    case=admin_source
    private_transport(monkeypatch,case.settings)
    with case.engine.begin() as db:before=setup.snapshot(db)
    settings=approved_settings(case)
    monkeypatch.setattr(setup,'capture_dump',lambda *_:pytest.fail('Invalid setup reached backup'))
    for name in setup.OFF_FLAGS:
        with monkeypatch.context() as guarded:
            guarded.setenv(name,'1')
            with pytest.raises(setup.Stop,match='admin_setup_safe_flags_required'):setup.execute(settings)
    with pytest.raises(setup.Stop,match='approved_plan_mismatch'):setup.execute(replace(settings,approved_plan_sha256='f'*64))
    with pytest.raises(setup.Stop,match='expected_demo_tenant_required'):setup.execute(replace(settings,tenant=uuid4()))
    with monkeypatch.context() as guarded:
        guarded.setattr(adoption,'session_proof',lambda *_:'e'*64)
        with pytest.raises(setup.Stop,match='primary_account_changed_preserved'):setup.execute(settings)
    with case.engine.begin() as db:assert setup.snapshot(db)==before


def test_credential_rotation_after_rehearsal_rolls_back_source_and_never_rebinds(admin_source,monkeypatch):
    case=admin_source
    private_transport(monkeypatch,case.settings)
    settings=approved_settings(case)
    with case.engine.begin() as db:before=setup.snapshot(db)
    original=setup.verify_restore
    def rotate(*args):
        receipt=original(*args)
        monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH',encoded_password(b'rotated-synthetic-password'))
        return receipt
    monkeypatch.setattr(setup,'verify_restore',rotate)
    with pytest.raises(setup.Stop,match='admin_credential_changed_preserved'):setup.execute(settings)
    with case.engine.begin() as db:assert setup.snapshot(db)==before


def test_existing_owner_native_admin_or_active_bot_is_never_overwritten(admin_source,monkeypatch):
    case=admin_source
    private_transport(monkeypatch,case.settings)
    with case.engine.begin() as db:
        db.execute(text('UPDATE tenants SET bot_enabled=true WHERE id=:id'),{'id':case.tenant})
    with pytest.raises(setup.Stop,match='expected_demo_bot_off_required'):setup.execute(case.settings)
    with case.engine.begin() as db:
        db.execute(text('UPDATE tenants SET bot_enabled=false WHERE id=:id'),{'id':case.tenant})
        account=db.execute(text("SELECT id FROM panel_accounts WHERE role='member' LIMIT 1")).scalar_one()
        db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:t,:a,'owner',true)"),{'t':case.tenant,'a':account})
    with pytest.raises(setup.Stop,match='existing_demo_owner_preserved'):setup.execute(case.settings)


async def test_runtime_metadata_gate_accepts_0030_and_denies_0029(admin_migration_source):
    case=admin_migration_source
    runtime=gate.Settings(case.runtime_url.set(drivername='postgresql').render_as_string(hide_password=False))
    assert await gate.probe_schema(runtime,3) is False
    upgrade(migration.TARGET)
    assert await gate.probe_schema(runtime,3) is True


async def test_password_account_queries_work_on_0029_and_0030_and_exclude_native_identity(admin_migration_source,monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app import account_auth
    case=admin_migration_source
    account=uuid4();email=account.hex+'@example.invalid'
    password='synthetic-password-account-only'
    with case.engine.begin() as db:
        db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(:id,'Synthetic password member',:email,:hash,'member')"),
            {'id':account,'email':email,'hash':account_auth._hasher.hash(password)})
        db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),{'t':case.tenant,'a':account})
    engine=create_async_engine(case.support_url,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(account_auth,'_storage',lambda:factory)
    try:
        for revision in (migration.SOURCE,migration.TARGET):
            if revision==migration.TARGET:upgrade(revision)
            assert (await account_auth.authenticate_account(email,password))['id']==str(account)
            assert (await account_auth.current_account(str(account),1))['role']=='member'
            assert (await account_auth.current_account_and_membership(str(account),1,case.tenant))['membership_role']=='owner'
        monkeypatch.setenv('PANEL_ADMIN_USERNAME','synthetic-native-query')
        monkeypatch.setenv('PANEL_ADMIN_PASSWORD_HASH',encoded_password())
        username,digest=legacy_admin_identity.configuration_binding();native=uuid4()
        with case.engine.begin() as db:
            db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,role,auth_provider,legacy_username,legacy_credential_digest)
                VALUES(:id,:username,NULL,NULL,'admin','legacy_admin',:username,:digest)"""),{'id':native,'username':username,'digest':digest})
        with pytest.raises(HTTPException) as denied:await account_auth.current_account(str(native),1)
        assert denied.value.status_code==401
        with pytest.raises(HTTPException) as denied:await account_auth.current_account_and_membership(str(native),1,case.tenant)
        assert denied.value.status_code==401
    finally:await engine.dispose()


def test_0030_constraints_require_exact_native_admin_branch_and_singleton(admin_source):
    from sqlalchemy.exc import IntegrityError
    case=admin_source
    statement=text("""INSERT INTO panel_accounts(id,name,email,password_hash,role,auth_provider,legacy_username,legacy_credential_digest)
        VALUES(:id,'Synthetic constraint check',:email,:password,:role,:provider,:username,:digest)""")
    native={'id':uuid4(),'email':None,'password':None,'role':'admin','provider':'legacy_admin',
        'username':case.settings.username,'digest':case.settings.credential_digest}
    invalid=[{**native,'provider':'password','username':None,'digest':None},
        {**native,'role':'member'},{**native,'username':' Uppercase '},{**native,'digest':'not-a-credential-digest'},
        {**native,'email':'synthetic@example.invalid'},{**native,'password':'invented-password-forbidden'}]
    with case.engine.begin() as db:before=setup.snapshot(db)
    for values in invalid:
        with pytest.raises(IntegrityError):
            with case.engine.begin() as db:db.execute(statement,values)
        with case.engine.begin() as db:assert setup.snapshot(db)==before
    with case.engine.begin() as db:db.execute(statement,native)
    with pytest.raises(IntegrityError):
        with case.engine.begin() as db:db.execute(statement,{**native,'id':uuid4()})
    with case.engine.begin() as db:
        assert db.exec_driver_sql("SELECT count(*) FROM panel_accounts WHERE auth_provider='legacy_admin'").scalar_one()==1


@pytest.mark.parametrize('revision',[adoption.inbox.TARGET,adoption.communication.TARGET])
def test_older_member_owner_registration_keeps_0028_and_0029_compatibility(communication_source,monkeypatch,revision):
    from cryptography.fernet import Fernet
    case=communication_source
    if revision==adoption.communication.TARGET:upgrade(revision)
    with case.engine.begin() as db:
        db.execute(text('UPDATE tenants SET bot_enabled=true WHERE id=:t'),{'t':case.tenant})
    session=hmac.new(case.settings.token.encode(),b'orion:preserved-primary-session:v1\0'+PHONE.encode(),hashlib.sha256).hexdigest()
    settings=adoption.Settings(case.settings.url,case.settings.bridge_url,case.settings.token,str(uuid4()),case.tenant,(PHONE,),'register',session)
    monkeypatch.setenv('INTEGRATION_FERNET_KEY',Fernet.generate_key().decode())
    stored,_=private_transport(monkeypatch,settings)
    with case.engine.begin() as db:before=adoption.snapshot(db)
    receipt=adoption.execute(settings)
    assert receipt['revision']==revision and receipt['restore_verified'] and receipt['rehearsal_verified'] and len(stored)==1
    with case.engine.begin() as db:
        plan=adoption.plan_for(db,settings)
        assert adoption.snapshot(db,exclude=plan)==before
        assert db.execute(text('SELECT role FROM panel_accounts WHERE id=:a'),{'a':plan['owner']}).scalar_one()=='member'
