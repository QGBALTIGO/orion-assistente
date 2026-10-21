"""Explicit, backed-up provisioning of the configured legacy admin on the demo.

Default is disabled. The separate plan phase is read-only; apply requires the
exact approved plan digest, encrypted backup/readback and a restored rehearsal.
No email/password is invented, existing identities are never rebound, and no
transport, session, campaign, channel or conversation is activated here.
"""
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import UUID,uuid5,NAMESPACE_URL

import httpx
from sqlalchemy import text
from app import legacy_admin_identity
from app import qr_legacy
from scripts import one_time_admin_identity_migration as schema
from scripts import one_time_legacy_adoption as adoption

backup,proof,Stop,require=schema.backup,schema.proof,schema.Stop,schema.require
DEMO_TENANT=UUID('82552b25-d0c8-5338-8c42-49f11de59e45')
OFF_FLAGS=('QR_LEGACY_CENTRAL_ENABLED','INBOX_CONTROL_ENABLED',
    'INBOX_SEND_ENABLED','META_LIVE_SEND','MARKETING_EXECUTION_ENABLED',
    'MARKETING_RECONCILIATION_ENABLED','BIRTHDAY_COLLECTION_ENABLED','GROQ_PROBE_ON_START',
    'ORION_EMAIL_DELIVERY_PROBE')


@dataclass(frozen=True,repr=False)
class Settings(schema.BaseSettings):
    tenant:UUID
    phase:str
    username:str
    credential_digest:str
    session_digest:str
    approved_plan_sha256:str=''

    @classmethod
    def from_environment(cls):
        base=schema.BaseSettings.from_environment(flag='ORION_RUN_ADMIN_DEMO_SETUP',
            run_id_name='ORION_ADMIN_DEMO_SETUP_RUN_ID')
        try:
            tenant=UUID(os.environ['ORION_ADMIN_DEMO_TENANT_ID'])
            phase=os.getenv('ORION_ADMIN_DEMO_SETUP_PHASE','plan')
            username,digest=legacy_admin_identity.configuration_binding()
        except Exception:
            raise Stop('admin_setup_configuration_invalid') from None
        require(tenant==DEMO_TENANT,'expected_demo_tenant_required')
        require(phase in {'plan','apply'},'explicit_phase_required')
        approved=os.getenv('ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256','')
        if phase=='apply':require(re.fullmatch('[0-9a-f]{64}',approved) is not None,'approved_plan_required')
        value=cls(base.url,base.bridge_url,base.token,base.run_id,tenant,phase,username,digest,
            adoption.session_proof(base),approved)
        validate_configuration(value)
        return value


def validate_configuration(settings):
    require(settings.tenant==DEMO_TENANT,'expected_demo_tenant_required')
    require(settings.phase in {'plan','apply'},'explicit_phase_required')
    # The existing display-name column is varchar(120). Refuse a plan that
    # could not store the actual configured name; never invent or truncate it.
    require(len(settings.username)<=120,'admin_name_too_long')
    require(os.getenv('QR_AI_ENABLED')=='1','existing_assistant_required')
    require(os.getenv('QR_CHANNELS_ENABLED')=='1','existing_qr_channels_required')
    require(os.getenv('INBOX_TOOLS_ENABLED') in {'0','1'},'existing_tools_configuration_required')
    require(all(os.getenv(name)=='0' for name in OFF_FLAGS),'admin_setup_safe_flags_required')
    try:username,digest=legacy_admin_identity.configuration_binding()
    except Exception:raise Stop('admin_credential_configuration_invalid') from None
    require(username==settings.username and hmac.compare_digest(digest,settings.credential_digest),
        'admin_credential_changed_preserved')
    require(re.fullmatch('[0-9a-f]{64}',settings.session_digest) is not None,'session_proof_required')


def preflight(db,settings):
    identity=schema.owner_preflight(db,settings,source=False)
    require(backup.revision(db)==schema.TARGET,'admin_identity_schema_required')
    schema.verify_new_schema(db)
    return identity


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def plan_for(db,settings):
    require(settings.tenant==DEMO_TENANT,'expected_demo_tenant_required')
    company=db.execute(text('SELECT status,bot_enabled FROM tenants WHERE id=:t'),{'t':settings.tenant}).one_or_none()
    require(company is not None and company.status=='active','expected_active_demo_required')
    require(not db.execute(text('SELECT EXISTS(SELECT 1 FROM qr_channels WHERE id=:id)'),
        {'id':qr_legacy.channel_id(settings.tenant)}).scalar_one(),'demo_alias_must_be_absent')
    native=db.execute(text("SELECT id FROM panel_accounts WHERE auth_provider='legacy_admin'")).scalars().all()
    require(not native,'existing_native_admin_preserved')
    owners=db.execute(text("SELECT account_id FROM tenant_memberships WHERE tenant_id=:t AND role='owner' AND active"),
        {'t':settings.tenant}).scalars().all()
    require(not owners,'existing_demo_owner_preserved')
    require(company.bot_enabled is False,'expected_demo_bot_off_required')
    account=uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:account:'+settings.run_id)
    membership=uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:membership:'+settings.run_id)
    audit=uuid5(NAMESPACE_URL,'orion:legacy-admin-setup:audit:'+settings.run_id)
    for table,identity in [('panel_accounts',account),('tenant_memberships',membership),('platform_audit',audit)]:
        require(not db.execute(text('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE id=:id)'),{'id':identity}).scalar_one(),
            'setup_identity_collision')
    return {'tenant':settings.tenant,'account':account,'membership':membership,'audit':audit,
        'username':settings.username,'credential_digest':settings.credential_digest,
        'audit_action':'admin_setup:'+settings.run_id,'session_digest':settings.session_digest,
        'preserved_flags':{name:os.getenv(name) for name in ('QR_AI_ENABLED','QR_CHANNELS_ENABLED','INBOX_TOOLS_ENABLED',*OFF_FLAGS)}}


def snapshot(db,*,exclude=None):
    value=schema.snapshot(db)
    # Match adoption's reviewed lease projection. Live renewal is preserved,
    # while every QR identity/configuration field remains approval-bound.
    rows=db.execute(text("SELECT encode(sha256(convert_to((to_jsonb(t) - 'lease_owner' - 'lease_expires_at' - 'fence' - 'updated_at')::text,'UTF8')),'hex') FROM public.\"qr_channels\" t ORDER BY 1"),{})
    digest=hashlib.sha256();count=0
    for row in rows:digest.update(row[0].encode()+b'\n');count+=1
    value['rows']['qr_channels']=digest.hexdigest();value['counts']['qr_channels']=count
    if exclude:
        changes={'panel_accounts':exclude['account'],'tenant_memberships':exclude['membership'],'platform_audit':exclude['audit']}
        for name in sorted(schema.TARGET_TABLES-{'alembic_version'}):
            if name not in changes and name!='tenants':continue
            expression='to_jsonb(t)'
            condition=''
            params={}
            if name in changes:
                condition=' WHERE id<>:id';params['id']=changes[name]
            else:
                expression="CASE WHEN t.id=:tenant THEN to_jsonb(t)||jsonb_build_object('bot_enabled',false) ELSE to_jsonb(t) END"
                params['tenant']=exclude['tenant']
            rows=db.execute(text("SELECT encode(sha256(convert_to(("+expression+")::text,'UTF8')),'hex') FROM public.\""+name+'\" t'+condition+' ORDER BY 1'),params)
            digest=hashlib.sha256();count=0
            for row in rows:digest.update(row[0].encode()+b'\n');count+=1
            value['rows'][name]=digest.hexdigest();value['counts'][name]=count
    return value


def plan_digest(before,plan,settings):
    return fingerprint({'snapshot':before,'plan':plan,'run_id':settings.run_id,'revision':schema.TARGET})


@dataclass(frozen=True,repr=False)
class RestoreProof:
    run_id:str
    database:str


@dataclass(frozen=True,repr=False)
class RehearsalProof:
    run_id:str
    database:str
    plan_sha256:str


def verify_insertions(db,plan):
    account=db.execute(text("""SELECT name,email,password_hash,role,auth_version,auth_provider,legacy_username,
        legacy_credential_digest,verified_at FROM panel_accounts WHERE id=:account"""),plan).one()
    require(tuple(account)==(plan['username'],None,None,'admin',1,'legacy_admin',plan['username'],plan['credential_digest'],None),
        'native_admin_identity_mismatch')
    membership=db.execute(text('SELECT tenant_id,account_id,role,active FROM tenant_memberships WHERE id=:membership'),plan).one()
    require(tuple(membership)==(plan['tenant'],plan['account'],'owner',True),'native_admin_membership_mismatch')
    require(db.execute(text('SELECT status,bot_enabled FROM tenants WHERE id=:tenant'),plan).one()==('active',True),
        'demo_activation_mismatch')
    audit=db.execute(text('SELECT tenant_id,action FROM platform_audit WHERE id=:audit'),plan).one()
    require(tuple(audit)==(plan['tenant'],plan['audit_action']),'setup_audit_mismatch')


def apply(engine,settings,before,plan,*,restored=False,receipt=None):
    validate_configuration(settings)
    if restored:
        require(isinstance(receipt,RestoreProof) and receipt.run_id==settings.run_id and
            receipt.database==settings.url.database==backup.restore_name(settings.run_id),'isolated_restore_proof_required')
    else:
        require(isinstance(receipt,RehearsalProof) and receipt.run_id==settings.run_id and
            receipt.database==settings.url.database and receipt.plan_sha256==plan_digest(before,plan,settings),
            'rehearsal_proof_required')
        require(settings.phase=='apply' and hmac.compare_digest(settings.approved_plan_sha256,receipt.plan_sha256),
            'approved_plan_mismatch')
        require(adoption.session_proof(settings)==settings.session_digest,'primary_account_changed_preserved')
    with engine.begin() as db:
        preflight(db,settings)
        require(db.exec_driver_sql('SELECT pg_try_advisory_xact_lock(681692820129)').scalar_one(),'admin_setup_already_running')
        db.exec_driver_sql('LOCK TABLE '+','.join('public."'+n+'"' for n in sorted(schema.TARGET_TABLES))+' IN SHARE MODE')
        require(snapshot(db)==before,'source_changed_after_backup')
        require(plan_for(db,settings)==plan,'admin_setup_plan_changed')
        db.exec_driver_sql('SET LOCAL ROLE app_migrator')
        db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,role,auth_provider,legacy_username,legacy_credential_digest)
            VALUES(:account,:username,NULL,NULL,'admin','legacy_admin',:username,:credential_digest)"""),plan)
        db.execute(text("""INSERT INTO tenant_memberships(id,tenant_id,account_id,role,active)
            VALUES(:membership,:tenant,:account,'owner',true)"""),plan)
        changed=db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:tenant AND status='active' AND NOT bot_enabled"),plan)
        require(changed.rowcount==1,'demo_activation_changed')
        db.execute(text('INSERT INTO platform_audit(id,tenant_id,action) VALUES(:audit,:tenant,:audit_action)'),plan)
        db.exec_driver_sql('RESET ROLE')
        verify_insertions(db,plan)
        require(snapshot(db,exclude=plan)==before,'existing_records_or_permissions_changed')
        validate_configuration(settings)
        if not restored:require(adoption.session_proof(settings)==settings.session_digest,'primary_account_changed_preserved')
    return {'identity_provisioned':True,'demo_owner_added':True,'demo_bot_enabled':True,'audit_appended':True}


def capture_dump(engine,settings,path):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
        with db.begin():
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
            preflight(db,settings)
            db.exec_driver_sql('LOCK TABLE '+','.join('public."'+n+'"' for n in sorted(schema.TARGET_TABLES))+' IN ACCESS SHARE MODE')
            snapshot_id=db.exec_driver_sql('SELECT pg_export_snapshot()').scalar_one()
            before=snapshot(db)
            backup.private_write(path,b'')
            backup.run_client('pg_dump',['--format=custom','--no-password','--snapshot='+snapshot_id,
                '--lock-wait-timeout=10000','--file='+str(path)],settings.url,max_file=path)
            require(5<=path.stat().st_size<=backup.MAX_BYTES-36,'dump_size_invalid')
            return before


def verify_restore(engine,settings,identity,path,before,plan):
    name=backup.restore_name(settings.run_id)
    require(name!=settings.url.database,'restore_source_collision')
    oid=None
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as admin:
        try:
            require(not admin.execute(text('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:n)'),{'n':name}).scalar_one(),
                'restore_database_already_exists')
            admin.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ALLOW_CONNECTIONS false')
            oid=admin.execute(text('SELECT oid FROM pg_database WHERE datname=:n'),{'n':name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "'+name+'" FROM PUBLIC,app_runtime,app_support,app_migrator')
            admin.exec_driver_sql('ALTER DATABASE "'+name+'" ALLOW_CONNECTIONS true')
            temporary=replace(settings,url=settings.url.set(database=name))
            backup.run_client('pg_restore',['--no-password','--exit-on-error','--single-transaction','--dbname='+name,str(path)],temporary.url)
            restored=backup.engine_for(temporary.url)
            try:
                with restored.begin() as db:
                    found=preflight(db,temporary)
                    require(found['oid']==oid and found['role_oid']==identity['role_oid'],'restore_database_identity_changed')
                    require(not db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_roles WHERE
                        rolname IN ('app_runtime','app_support','app_migrator') AND has_database_privilege(rolname,:n,'CONNECT'))"""),
                        {'n':name}).scalar_one(),'restore_connect_gate_changed')
                    actual=snapshot(db)
                    normalize=lambda value:{**value,'security':proof.without_database_identity(value['security'])}
                    require(normalize(actual)==normalize(before),'restored_snapshot_mismatch')
                apply(restored,temporary,actual,plan,restored=True,receipt=RestoreProof(settings.run_id,name))
            finally:restored.dispose()
        finally:
            if oid is not None:backup.safe_drop_created(admin,name,oid,identity['role_oid'],identity['oid'])
    return RehearsalProof(settings.run_id,settings.url.database,plan_digest(before,plan,settings))


def execute(settings):
    validate_configuration(settings)
    require(adoption.session_proof(settings)==settings.session_digest,'primary_account_changed_preserved')
    engine=backup.engine_for(settings.url)
    try:
        with engine.begin() as db:
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
            identity=preflight(db,settings)
            plan=plan_for(db,settings)
            before=snapshot(db)
            approved=plan_digest(before,plan,settings)
        if settings.phase=='plan':
            return {'status':'plan_only','revision':schema.TARGET,'run_id':settings.run_id,
                'tenant_id':str(settings.tenant),'plan_sha256':approved,'preserved_counts':before['counts'],
                'proposed_changes':['configured_native_admin_identity','demo_owner_membership','demo_bot_enable','platform_audit'],
                'writes_performed':False,'session_preserved':True}
        require(re.fullmatch('[0-9a-f]{64}',settings.approved_plan_sha256) is not None and
            hmac.compare_digest(approved,settings.approved_plan_sha256),'approved_plan_mismatch')
        require(backup.client_major('pg_dump')==backup.client_major('pg_restore')==16,'postgres_major_mismatch')
        with tempfile.TemporaryDirectory(prefix='orion-admin-demo-setup-') as directory:
            os.chmod(directory,0o700)
            require(shutil.disk_usage(directory).free>backup.MAX_BYTES*3+16*1024*1024,'temporary_disk_space_low')
            original=Path(directory)/'snapshot.dump'
            captured=capture_dump(engine,settings,original)
            require(captured==before,'source_changed_before_backup')
            envelope=backup.encrypt_dump(original.read_bytes(),settings.token,settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60,connect=10),follow_redirects=False,trust_env=False) as client:
                readback,digest=backup.durable_backup(settings,envelope,client)
            verified=Path(directory)/'verified.dump';backup.private_write(verified,readback)
            receipt=verify_restore(engine,settings,identity,verified,before,plan)
            result=apply(engine,settings,before,plan,receipt=receipt)
        return {**result,'status':'complete','revision':schema.TARGET,'run_id':settings.run_id,
            'tenant_id':str(settings.tenant),'plan_sha256':approved,'backup_id':settings.run_id,
            'backup_sha256':digest,'backup_bytes':len(envelope),'restore_verified':True,'rehearsal_verified':True,
            'session_preserved':True,'credential_configuration_preserved':True,'existing_records_preserved':True}
    finally:engine.dispose()


def main():
    try:
        if os.getenv('ORION_RUN_ADMIN_DEMO_SETUP','0')=='0':print(json.dumps({'status':'disabled'}));return 0
        result=execute(Settings.from_environment())
    except Exception as error:
        code=str(error) if isinstance(error,Stop) and re.fullmatch('[a-z][a-z0-9_]{0,79}',str(error)) else 'admin_demo_setup_failed'
        print(json.dumps({'status':'stopped','code':code}));return 1
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
