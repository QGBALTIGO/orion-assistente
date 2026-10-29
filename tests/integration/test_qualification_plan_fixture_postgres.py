"""Two licensed synthetic companies in a new loopback database; no HTTP load."""
import os

import pytest
from sqlalchemy import text
from scripts.qualification_load import DisposableDatabase,seed_database
from app.plan_permissions import derive_plan

pytestmark=pytest.mark.integration


def test_disposable_seed_grants_elite_without_commercial_or_session_changes():
    owner=os.getenv("ORION_MIGRATION_TEST_OWNER_URL")
    if not owner:
        if os.getenv("ORION_REQUIRE_MIGRATION_OPS_TEST")=="1":
            pytest.fail("Dedicated loopback owner URL required")
        pytest.skip("Dedicated loopback owner URL unavailable")
    with DisposableDatabase(owner,os.environ) as sandbox:
        sandbox.migrate("0050_plan_permissions")
        people,inventory=seed_database(sandbox,2,2)
        assert inventory["fixture_plan"]=={"code":"elite","source":"manual","companies":2,"synthetic_admin":True}
        with sandbox.engine.connect() as db:
            grants=db.execute(text("""SELECT c.tenant_id,c.manual_actor_id,a.role,a.auth_provider,a.email,
                a.password_hash,a.auth_version FROM tenant_plan_controls c
                JOIN panel_accounts a ON a.id=c.manual_actor_id ORDER BY c.tenant_id""")).mappings().all()
            assert len(grants)==2 and len({row["manual_actor_id"] for row in grants})==1
            for row in grants:
                assert row["role"]=="admin" and row["auth_provider"]=="password"
                assert row["email"].endswith("@example.test") and row["password_hash"]=="fixture-only-no-login"
                assert row["auth_version"]==1
                state=derive_plan(db.execute(text("SELECT public.orion_plan_snapshot(:tenant)"),{"tenant":row["tenant_id"]}).scalar_one())
                assert state["source"]=="manual" and state["code"]=="elite"
                assert state["permissions"]["agenda.manage"] and state["permissions"]["reception.ai"]
            for table in ("subscriptions","orion_cakto_bindings","orion_plan_trial_requests","tenant_plan_audit"):
                assert db.execute(text("SELECT count(*) FROM "+table)).scalar_one()==0
            assert db.execute(text("SELECT public.orion_plan_release_ready()")).scalar_one() is False
            users=db.execute(text("SELECT id,password_hash,auth_version FROM panel_accounts WHERE role='member'")).mappings().all()
            assert {str(row["id"]) for row in users}=={person["account"] for person in people}
            assert all(row["password_hash"]=="not-a-login-password" and row["auth_version"]==1 for row in users)
