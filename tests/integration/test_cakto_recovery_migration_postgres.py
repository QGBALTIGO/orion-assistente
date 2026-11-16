"""Populated 0072→0073, provenance and catalog drift on a NEW loopback DB.

The shared test database is never downgraded. Only the random database created
by DisposableDatabase is migrated and removed; no provider is called.
"""
import asyncio
import hashlib
import os
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from scripts.qualification_load import DisposableDatabase
from scripts import backup_and_migrate_cakto_recovery as release
from scripts import backup_and_migrate_subscription_group_add as previous
from scripts import wait_for_cakto_recovery_schema as barrier
from scripts.backup_and_migrate_plan_permissions import bootstrap
from scripts.predeploy_one_click_links import predecessors_ready

pytestmark = pytest.mark.integration
TABLES = ("tenants", "panel_accounts", "plans", "subscriptions",
          "orion_cakto_checkout_intents", "orion_cakto_bindings",
          "orion_direct_payment_attempts", "orion_prepaid_pix_orders", "cakto_webhook_events",
          "tenant_memberships", "tenant_plan_controls", "tenant_plan_audit", "plan_permissions_release")
PRIVATE_TABLES = ("orion_cakto_bindings", "orion_direct_payment_attempts",
                  "orion_prepaid_pix_orders", "cakto_webhook_events")


def snapshot(db, projection=None):
    if projection is None:
        projection = {}
        for table, column in db.execute(text("""SELECT table_name,column_name
            FROM information_schema.columns WHERE table_schema='public'
              AND table_name=ANY(:tables) ORDER BY table_name,ordinal_position"""), {"tables": list(TABLES)}):
            projection.setdefault(table, []).append(column)
    result = {}
    for table, names in projection.items():
        selected = ",".join('"' + value.replace('"', '""') + '"' for value in names)
        rows = sorted(db.exec_driver_sql('SELECT row_to_json(r)::text FROM (SELECT ' + selected +
            ' FROM public."' + table + '") r').scalars())
        result[table] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
    return projection, result


def privileges(db):
    return db.execute(text("""SELECT c.relname,pg_get_userbyid(c.relowner),
        c.relrowsecurity,c.relforcerowsecurity,c.relacl::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(:tables) ORDER BY c.relname"""),
        {"tables": list(TABLES)}).all()


@pytest.fixture(scope="module")
def migrated_source():
    owner = os.getenv("ORION_MIGRATION_TEST_OWNER_URL")
    if not owner:
        if os.getenv("ORION_REQUIRE_MIGRATION_OPS_TEST") == "1":
            pytest.fail("Mandatory isolated migration owner URL missing")
        pytest.skip("Dedicated loopback migration owner URL unavailable")
    with DisposableDatabase(owner, os.environ) as sandbox:
        sandbox.migrate(release.PREVIOUS)
        environment = sandbox.environment()
        runtime = barrier.Settings(make_url(environment["APP_DATABASE_URL"]).set(
            drivername="postgresql").render_as_string(hide_password=False))
        migration_url = environment["MIGRATION_DATABASE_URL"]
        assert previous.schema_ready(migration_url)
        assert not release.schema_ready(migration_url)
        assert asyncio.run(barrier.probe_schema(runtime, 3)) is False
        tenant, account, intent, subscription = (uuid4() for _ in range(4))
        with sandbox.engine.begin() as db:
            db.exec_driver_sql("SET LOCAL ROLE app_migrator")
            # Migrations create structure, not the separately authorized 0050
            # initial grants. Model a legitimately published predecessor before
            # asking the real predeploy to accept it; never weaken its marker.
            assert db.exec_driver_sql("SELECT public.orion_plan_release_ready()").scalar_one() is False
            db.execute(text("INSERT INTO tenants(id,name,slug) VALUES(:id,'Synthetic migration business',:slug)"),
                       {"id": tenant, "slug": "migration-" + tenant.hex})
            db.execute(text("""INSERT INTO panel_accounts
                (id,name,role,auth_provider,legacy_username,legacy_credential_digest)
                VALUES(:id,'Synthetic migration owner','admin','legacy_admin','synthetic-release-admin',:digest)"""),
                       {"id": account, "digest": "9" * 64})
            db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
                VALUES(:tenant,:account,'owner',true)"""), {"tenant": tenant, "account": account})
            assert bootstrap(db, "synthetic-release-admin", "9" * 64)["replayed"] is False
            db.execute(text("""INSERT INTO subscriptions(id,tenant_id,plan_id,external_subscription_id,status)
                SELECT :id,:tenant,id,'synthetic-preserved-subscription','active'
                FROM plans WHERE code='orion_essencial'"""), {"id": subscription, "tenant": tenant})
            db.execute(text("""INSERT INTO orion_cakto_checkout_intents
                (id,tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,expires_at)
                VALUES(:id,:tenant,:account,'essencial','synthetic-historical-offer',2990,:hash,now()+interval '1 hour')"""),
                       {"id": intent, "tenant": tenant, "account": account, "hash": "a" * 64})
            db.execute(text("""INSERT INTO orion_cakto_bindings
                (tenant_id,subscription_id,external_subscription_id,plan_code,last_order_id)
                VALUES(:tenant,:id,'synthetic-preserved-subscription','essencial','synthetic-prior-payment')"""),
                       {"tenant": tenant, "id": subscription})
            db.execute(text("""INSERT INTO orion_direct_payment_attempts
                (intent_id,tenant_id,account_id,method,state,external_order_id)
                VALUES(:id,:tenant,:account,'pix','needs_review','synthetic-uncertain-direct')"""),
                       {"id": intent, "tenant": tenant, "account": account})
            db.execute(text("""INSERT INTO orion_prepaid_pix_orders
                (tenant_id,account_id,plan_code,offer_id,expected_cents,callback_hash,state,expires_at)
                VALUES(:tenant,:account,'essencial','synthetic-one-time-offer',2990,:hash,'needs_review',now()+interval '1 hour')"""),
                       {"tenant": tenant, "account": account, "hash": "b" * 64})
            for digest, state in (("c" * 64, "pending"), ("d" * 64, "mismatch")):
                db.execute(text("""INSERT INTO cakto_webhook_events
                    (delivery_digest,external_order_id,event_type,reported_status,verification_state)
                    VALUES(:digest,:order,'purchase_approved','paid',:state)"""),
                           {"digest": digest, "order": "synthetic-" + state, "state": state})
            projection, before = snapshot(db)
            before_privileges = privileges(db)
        assert predecessors_ready(migration_url)
        sandbox.migrate(release.TARGET)
        with sandbox.engine.connect() as db:
            assert db.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == release.TARGET
            assert snapshot(db, projection)[1] == before
            assert privileges(db) == before_privileges
            assert db.exec_driver_sql("SELECT count(*) FROM cakto_webhook_events WHERE receipt_origin='webhook_hmac'").scalar_one() == 2
            for table in ("orion_cakto_bindings", "orion_direct_payment_attempts", "orion_prepaid_pix_orders"):
                assert db.exec_driver_sql("SELECT count(*) FROM public." + table + " WHERE provider_checked_at IS NOT NULL").scalar_one() == 0
            assert db.exec_driver_sql("SELECT count(*) FROM orion_prepaid_pix_orders WHERE pix_code_enc IS NOT NULL OR pix_expires_at IS NOT NULL").scalar_one() == 0
        assert previous.schema_ready(migration_url)
        assert release.schema_ready(migration_url)
        assert predecessors_ready(migration_url)
        assert asyncio.run(barrier.probe_schema(runtime, 3)) is True
        yield sandbox, runtime, migration_url


def test_populated_0072_preservation_and_runtime_readonly_barrier(migrated_source):
    sandbox, runtime, _ = migrated_source

    async def verify():
        db = await asyncpg.connect(runtime.dsn, timeout=3, command_timeout=3,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
        try:
            assert await db.fetchval("SELECT current_user") == "app_runtime"
            assert await db.fetchval("SELECT current_setting('default_transaction_read_only')") == "on"
            for table in PRIVATE_TABLES:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await db.fetch("SELECT 1 FROM public." + table + " LIMIT 1")
        finally:
            db.terminate()

    asyncio.run(verify())


def test_release_lock_rejects_a_second_publisher_and_is_released_after_failure(migrated_source):
    _, _, migration_url = migrated_source
    with pytest.raises(ValueError, match="synthetic-publisher-failure"):
        with release.release_lock(migration_url):
            with pytest.raises(RuntimeError, match="release_busy"):
                with release.release_lock(migration_url):
                    pytest.fail("Another predeploy must not enter the release section")
            raise ValueError("synthetic-publisher-failure")
    with release.release_lock(migration_url):
        pass


def test_old_insert_contract_defaults_to_hmac_and_provider_read_must_be_explicit(migrated_source):
    sandbox, _, _ = migrated_source
    with sandbox.engine.connect() as db:
        transaction = db.begin()
        try:
            for digest, origin in (("e" * 64, None), ("f" * 64, "provider_read")):
                columns, values = ("", "") if origin is None else (",receipt_origin", ",:origin")
                result = db.execute(text("INSERT INTO cakto_webhook_events "
                    "(delivery_digest,external_order_id,event_type,reported_status" + columns + ") "
                    "VALUES(:digest,:order,'purchase_approved','paid'" + values + ") RETURNING receipt_origin"),
                    {"digest": digest, "order": "synthetic-new-" + digest[0], "origin": origin}).scalar_one()
                assert result == (origin or "webhook_hmac")
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.execute(text("""INSERT INTO cakto_webhook_events
                        (delivery_digest,external_order_id,event_type,reported_status,receipt_origin)
                        VALUES(:digest,'synthetic-invalid','purchase_approved','paid','unproved')"""), {"digest": "0" * 64})
        finally:
            transaction.rollback()


@pytest.mark.parametrize("cipher,expiry", [("a" * 99, True), ("a" * 32769, True), ("a" * 100, False), (None, True)],
                         ids=["too_short", "too_long", "missing_expiry", "missing_cipher"])
def test_pix_resume_pair_and_cipher_size_are_enforced_by_postgres(migrated_source, cipher, expiry):
    sandbox, _, _ = migrated_source
    with sandbox.engine.connect() as db:
        transaction = db.begin()
        try:
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.execute(text("""UPDATE orion_prepaid_pix_orders SET pix_code_enc=:cipher,
                        pix_expires_at=CASE WHEN :expiry THEN now()+interval '1 hour' ELSE NULL END"""),
                               {"cipher": cipher, "expiry": expiry})
        finally:
            transaction.rollback()


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE public.cakto_webhook_events ALTER COLUMN receipt_origin DROP NOT NULL",
    "ALTER TABLE public.cakto_webhook_events ALTER COLUMN receipt_origin DROP DEFAULT",
    "ALTER TABLE public.cakto_webhook_events ALTER COLUMN receipt_origin SET DEFAULT 'provider_read'",
    "ALTER TABLE public.cakto_webhook_events DROP CONSTRAINT ck_cakto_receipt_origin",
    "ALTER TABLE public.orion_prepaid_pix_orders DROP CONSTRAINT ck_prepaid_pix_resume_cipher",
    "ALTER TABLE public.orion_prepaid_pix_orders DROP COLUMN pix_code_enc CASCADE",
    "ALTER TABLE public.orion_prepaid_pix_orders DROP COLUMN pix_expires_at CASCADE",
    "ALTER TABLE public.orion_prepaid_pix_orders DROP COLUMN provider_checked_at CASCADE",
    "DROP INDEX public.ix_prepaid_pix_provider_check",
    "DROP INDEX public.ix_direct_provider_check",
    "DROP INDEX public.ix_cakto_binding_provider_check",
    "GRANT SELECT ON public.cakto_webhook_events TO app_runtime",
    "ALTER TABLE public.orion_cakto_bindings NO FORCE ROW LEVEL SECURITY",
    "REVOKE UPDATE ON public.orion_direct_payment_attempts FROM app_support",
])
def test_financial_catalog_drift_fails_readiness_without_data_writes(migrated_source, mutation):
    sandbox, _, _ = migrated_source
    with sandbox.engine.connect() as db:
        transaction = db.begin()
        try:
            assert db.execute(text(barrier.CONTRACT_QUERY)).scalar_one() is True
            db.exec_driver_sql(mutation)
            assert db.execute(text(barrier.CONTRACT_QUERY)).scalar_one() is False
        finally:
            transaction.rollback()
