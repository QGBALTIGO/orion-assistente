"""Real PG16 dump, full restore and transactional 0011 -> 0012 preservation.

Only synthetic, UUID-named disposable databases on loopback are accepted by the
shared fixture. HTTP storage is simulated; encryption, PG clients and DDL are real.
The mandatory CI flag must fail rather than skip when its owner URL is missing.
"""
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from sqlalchemy import text

from scripts import one_time_business_migration as backup
from scripts import one_time_fulfillment_migration as ops
from test_business_migration_ops_postgres import migration_source, install_mock_private_store

pytestmark = pytest.mark.integration


@pytest.fixture
def fulfillment_source(migration_source):
    engine, settings = migration_source
    backup.apply_exact_migrations(engine)
    with engine.begin() as conn:
        tenant = conn.exec_driver_sql("SELECT id FROM tenants").scalar_one()
        account = conn.exec_driver_sql("SELECT id FROM panel_accounts").scalar_one()
        params = {"t": tenant, "a": account, "profile": uuid4(), "product": uuid4(),
                  "variant": uuid4(), "quote": uuid4(), "order": uuid4(),
                  "snapshot": json.dumps({"quantity": 1, "total_cents": 2500})}
        for sql in (
            "INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES (:t,:a,'owner')",
            "INSERT INTO tenant_membership_events(tenant_id,account_id,role,active) VALUES (:t,:a,'owner',true)",
            "INSERT INTO business_profile_versions(id,tenant_id,revision,profile,actor_role) VALUES (:profile,:t,1,'{\"identity\":{\"business_name\":\"Synthetic existing business\"}}','owner')",
            "INSERT INTO business_profile_state(tenant_id,published_version_id,generation) VALUES (:t,:profile,1)",
            "INSERT INTO business_profile_events(tenant_id,version_id,action,actor_role) VALUES (:t,:profile,'published','owner')",
            "INSERT INTO commercial_products(id,tenant_id,name,active) VALUES (:product,:t,'Synthetic existing product',true)",
            "INSERT INTO commercial_variants(id,tenant_id,product_id,name,price_kind,unit_price_cents,active) VALUES (:variant,:t,:product,'Existing variant','fixed',2500,true)",
            "INSERT INTO commercial_inventory(tenant_id,variant_id) VALUES (:t,:variant)",
            "INSERT INTO commercial_quotes(id,tenant_id,contact_e164,total_cents,confirmation_digest,expires_at) VALUES (:quote,:t,'+5567999999901',2500,repeat('a',64),now()+interval '1 hour')",
            "INSERT INTO commercial_quote_items(tenant_id,quote_id,variant_id,product_id,item_order,quantity,product_revision,variant_revision,inventory_revision,snapshot) VALUES (:t,:quote,:variant,:product,0,1,1,1,1,CAST(:snapshot AS jsonb))",
            "INSERT INTO commercial_orders(id,tenant_id,quote_id,idempotency_key,status) VALUES (:order,:t,:quote,'synthetic-existing-order','confirmed')",
            "INSERT INTO commercial_events(tenant_id,entity,entity_id,action,actor_role) VALUES (:t,'order',:order,'confirmed','customer')",
        ):
            conn.execute(text(sql), params)
        assert backup.revision(conn) == ops.SOURCE and backup.table_names(conn) == ops.TABLES
    yield engine, settings


def evidence(engine):
    with engine.begin() as conn:
        return {"expected_catalog": backup.catalog_fingerprint(conn, ops.TABLES),
                "expected_security": ops.security_structure(conn)}


def test_full_0011_backup_restore_and_0012_upgrade_preserves_existing_commerce(fulfillment_source, monkeypatch):
    engine, settings = fulfillment_source
    stored, requests = install_mock_private_store(monkeypatch, settings)
    with engine.begin() as conn:
        before = ops.row_fingerprints(conn)
        security = ops.security_structure(conn)
    result = ops.execute(settings)
    assert requests == ["POST", "GET"]
    assert stored["body"].startswith(backup.MAGIC) and not stored["body"].startswith(b"PGDMP")
    assert result["revision"] == ops.TARGET and result["restore_verified"] is True
    assert len(result["backup_counts"]) == len(result["preserved_counts"]) == 46
    for table in ("customers", "panel_accounts", "tenant_memberships", "business_profile_versions",
                  "commercial_products", "commercial_variants", "commercial_inventory",
                  "commercial_quotes", "commercial_quote_items", "commercial_orders", "commercial_events"):
        assert result["backup_counts"][table] == result["preserved_counts"][table] == 1
    with engine.begin() as conn:
        assert backup.revision(conn) == ops.TARGET
        after = ops.row_fingerprints(conn, projected=True)
        assert {k: v for k, v in after.items() if k != "alembic_version"} == {k: v for k, v in before.items() if k != "alembic_version"}
        assert ops.security_structure(conn, projected=True) == security
        ops.projected_catalog(conn)
        assert conn.exec_driver_sql("SELECT status,payment_status FROM commercial_orders").one() == ("confirmed", "not_verified")
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                                {"name": backup.restore_name(settings.run_id)}).scalar_one()
        conn.exec_driver_sql("SET LOCAL ROLE app_runtime")
        assert conn.exec_driver_sql("SELECT count(*) FROM commercial_orders").scalar_one() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 0


@pytest.mark.parametrize("tamper,expected", [
    ("UPDATE commercial_products SET name='Synthetic unintended change'", "existing_data_changed"),
    ("GRANT DELETE ON commercial_quotes TO app_runtime", "existing_security_changed"),
    ("ALTER TABLE customers NO FORCE ROW LEVEL SECURITY", "existing_catalog_changed"),
    ("ALTER TABLE commercial_quotes RENAME CONSTRAINT ck_quote_amount TO unexpected_name", "existing_catalog_changed"),
])
def test_unexpected_same_count_data_or_security_change_rolls_back_all_ddl(fulfillment_source, monkeypatch, tamper, expected):
    engine, settings = fulfillment_source
    original = ops.reviewed_migration()
    gates = evidence(engine)
    with engine.begin() as conn:
        before = ops.row_fingerprints(conn)
    def upgrade():
        original.upgrade()
        original.op.execute(tamper)
    monkeypatch.setattr(ops, "reviewed_migration", lambda: SimpleNamespace(upgrade=upgrade))
    with pytest.raises(backup.Stop, match=expected):
        ops.apply_exact_migration(engine, settings, **gates)
    with engine.begin() as conn:
        ops.source_only(conn)
        assert ops.row_fingerprints(conn) == before
        assert ops.security_structure(conn) == gates["expected_security"]
        assert backup.catalog_fingerprint(conn, ops.TABLES) == gates["expected_catalog"]


def test_full_restore_mismatch_cleans_only_created_restore_database(fulfillment_source, tmp_path):
    engine, settings = fulfillment_source
    with engine.begin() as conn:
        identity = backup.owner_preflight(conn, settings, source=ops.SOURCE, tables=ops.TABLES)
    path = tmp_path / "source-0011.dump"
    expected, fingerprint = backup.capture_dump(engine, settings, path, source=ops.SOURCE, tables=ops.TABLES)
    expected["commercial_orders"] += 1
    with pytest.raises(backup.Stop, match="restored_count_mismatch"):
        backup.verify_restore(engine, settings, identity, path, expected, fingerprint, source=ops.SOURCE, tables=ops.TABLES)
    with engine.begin() as conn:
        ops.source_only(conn)
        assert conn.exec_driver_sql("SELECT count(*) FROM commercial_orders").scalar_one() == 1
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                                {"name": backup.restore_name(settings.run_id)}).scalar_one()


def test_source_catalog_change_after_durable_backup_stops_before_ddl(fulfillment_source):
    engine, settings = fulfillment_source
    gates = evidence(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("GRANT DELETE ON commercial_quotes TO app_runtime")
    with pytest.raises(backup.Stop, match="source_changed_after_backup"):
        ops.apply_exact_migration(engine, settings, **gates)
    with engine.begin() as conn:
        ops.source_only(conn)


def test_already_0012_cannot_be_backed_up_or_migrated_by_this_executor(fulfillment_source, monkeypatch):
    engine, settings = fulfillment_source
    ops.apply_exact_migration(engine, settings, **evidence(engine))
    def forbidden(*args, **kwargs):
        pytest.fail("Backup I/O must not run against a different source revision")
    monkeypatch.setattr(backup, "capture_dump", forbidden)
    with pytest.raises(backup.Stop, match="unexpected_source_schema"):
        ops.execute(settings)
