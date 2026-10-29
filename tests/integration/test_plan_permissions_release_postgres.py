"""Run FIRST in the disposable PostgreSQL job; the entire bootstrap rolls back."""
import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from scripts.backup_and_migrate_plan_permissions import bootstrap, schema_ready, TARGET
from app.plan_permissions import derive_plan

pytestmark=pytest.mark.integration


@pytest.fixture
def release_db():
    raw=os.getenv("MIGRATION_DATABASE_URL")
    if not raw:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1": pytest.fail("Disposable PostgreSQL is mandatory")
        pytest.skip("Disposable PostgreSQL unavailable")
    url=make_url(raw)
    if url.host not in {"localhost","127.0.0.1","::1"} or url.username!="app_migrator":
        pytest.fail("Release test requires loopback app_migrator")
    engine=create_engine(raw,poolclass=NullPool)
    try:
        with engine.connect() as db:
            transaction=db.begin()
            try:
                yield db
            finally:
                transaction.rollback()
    finally: engine.dispose()


def test_authorized_bootstrap_is_atomic_idempotent_preserves_records_and_marks_admin(release_db):
    db=release_db
    assert not db.execute(text("SELECT public.orion_plan_release_ready()")).scalar_one()
    admin,owner,a,b,paused=(uuid4() for _ in range(5))
    digest="f"*64
    db.execute(text("""INSERT INTO panel_accounts(id,name,role,auth_provider,legacy_username,legacy_credential_digest)
        VALUES(:id,'Administrador sintético','admin','legacy_admin','synthetic-admin',:digest)"""),{"id":admin,"digest":digest})
    db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(:id,'Pessoa sintética',:email,'unused','member')"),{"id":owner,"email":str(owner)+"@example.test"})
    for tenant,status in ((a,"active"),(b,"active"),(paused,"paused")):
        db.execute(text("INSERT INTO tenants(id,name,slug,status) VALUES(:id,'Plano sintético',:slug,:status)"),{"id":tenant,"slug":"release-"+str(tenant),"status":status})
        db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:t,:a,'owner',true)"),{"t":tenant,"a":admin if tenant==a else owner})
    db.execute(text("INSERT INTO professionals(tenant_id,name,active) VALUES(:t,'Preservado',true)"),{"t":b})
    db.execute(text("""INSERT INTO business_onboarding(tenant_id,created_by,is_new,configuration,current_step)
        VALUES(:tenant,:owner,false,'{"contact_phone":"+5567999999901"}'::jsonb,'review')"""),{"tenant":b,"owner":owner})
    assert db.execute(text("SELECT public.orion_plan_snapshot(:t)->>'contact_phone'"),{"t":b}).scalar_one()=='+5567999999901'
    before_accounts=db.execute(text("SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM panel_accounts a")).scalar_one()
    before_tenants=db.execute(text("SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM tenants t")).scalar_one()
    before_staff=db.execute(text("SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM professionals p")).scalar_one()
    before_billing=db.execute(text("SELECT count(*) FROM subscriptions")).scalar_one()
    with pytest.raises(RuntimeError,match="verified_administrator"):
        bootstrap(db,"wrong",digest)
    assert db.execute(text("SELECT count(*) FROM tenant_plan_controls")).scalar_one()==0
    result=bootstrap(db,"synthetic-admin",digest)
    assert result["count"]>=3 and result["replayed"] is False
    assert bootstrap(db,"wrong","wrong")=={"replayed":True,"count":result["count"]}
    snapshots={str(t):derive_plan(db.execute(text("SELECT public.orion_plan_snapshot(:t)"),{"t":t}).scalar_one()) for t in (a,b,paused)}
    assert snapshots[str(a)]["source"]=="platform_admin"
    assert snapshots[str(a)]["permissions"]["reception.ai"]
    assert snapshots[str(b)]["code"]=="essencial" and snapshots[str(b)]["source"]=="manual"
    assert snapshots[str(paused)]["status"]=="tenant_inactive"
    assert db.execute(text("SELECT public.orion_plan_release_ready()")).scalar_one()
    assert db.execute(text("SELECT count(*) FROM tenant_plan_audit")).scalar_one()==result["count"]
    assert db.execute(text("SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM panel_accounts a")).scalar_one()==before_accounts
    assert db.execute(text("SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM tenants t")).scalar_one()==before_tenants
    assert db.execute(text("SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM professionals p")).scalar_one()==before_staff
    assert db.execute(text("SELECT count(*) FROM subscriptions")).scalar_one()==before_billing
    assert db.execute(text("SELECT marker FROM plan_permissions_release")).scalar_one()==TARGET


def test_projection_and_audit_role_contracts(release_db):
    db=release_db
    assert schema_ready(os.environ['MIGRATION_DATABASE_URL'],completed=False)
    assert db.execute(text("""SELECT
        NOT has_table_privilege('app_runtime','tenant_plan_controls','SELECT,INSERT,UPDATE,DELETE')
        AND NOT has_column_privilege('app_support','tenant_plan_controls','platform_account_id','UPDATE')
        AND NOT has_table_privilege('app_support','tenant_plan_audit','UPDATE,DELETE')
        AND NOT has_table_privilege('app_support','plan_permissions_release','INSERT,UPDATE,DELETE')
        AND has_function_privilege('app_runtime','orion_plan_snapshot(uuid)','EXECUTE')""")).scalar_one()
