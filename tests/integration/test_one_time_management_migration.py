"""Real PG16 restore/rehearsal and atomic 0012 -> 0017 source migration.

Only the existing disposable, UUID-named loopback fixture is used. The private
HTTP store is simulated; encryption, PostgreSQL clients, DDL and RLS are real.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts import one_time_business_migration as backup
from scripts import one_time_fulfillment_migration as fulfillment
from scripts import one_time_management_migration as ops
from test_business_migration_ops_postgres import migration_source, install_mock_private_store
from test_fulfillment_migration_ops_postgres import fulfillment_source

pytestmark = pytest.mark.integration


@pytest.fixture
def management_source(fulfillment_source):
    engine, original_settings = fulfillment_source
    with engine.begin() as conn:
        gates = {"expected_catalog": backup.catalog_fingerprint(conn, fulfillment.TABLES),
                 "expected_security": fulfillment.security_structure(conn)}
    fulfillment.apply_exact_migration(engine, original_settings, **gates)
    settings = ops.Settings(original_settings.url, original_settings.bridge_url,
                            original_settings.token, original_settings.run_id)
    with engine.begin() as conn:
        tenant = conn.exec_driver_sql("SELECT id FROM tenants").scalar_one()
        customer = conn.exec_driver_sql("SELECT id FROM customers").scalar_one()
        values = {"tenant": tenant, "customer": customer, "other": uuid4(),
                  "professional": uuid4(), "service": uuid4(), "appointment": uuid4(),
                  "channel": uuid4(), "inbound": uuid4()}
        statements = (
            "INSERT INTO tenants(id,name,slug) VALUES(:other,'Synthetic other business',:other_slug)",
            "INSERT INTO customers(tenant_id,whatsapp_e164,name) VALUES(:other,'+5567999999902','Synthetic other customer')",
            "INSERT INTO professionals(id,tenant_id,name) VALUES(:professional,:tenant,'Synthetic existing professional')",
            "INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:service,:tenant,'Synthetic existing service',30,3500)",
            "INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:tenant,:professional,:service)",
            "INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents) VALUES(:appointment,:tenant,:customer,:professional,'SYN123456','synthetic-existing-hold',repeat('a',64),'2026-01-01 12:00+00','2026-01-01 12:30+00','2026-01-01 12:00+00','2026-01-01 12:30+00','confirmed',3500)",
            "INSERT INTO appointment_items(tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order) VALUES(:tenant,:appointment,:service,'Synthetic historical name',30,3500,0,0,0)",
            "INSERT INTO appointment_history(tenant_id,appointment_id,event,operator) VALUES(:tenant,:appointment,'confirmed','synthetic-test')",
            "INSERT INTO campaigns(tenant_id,name,template_name,status) VALUES(:tenant,'Synthetic existing draft','synthetic_template','draft')",
            "INSERT INTO whatsapp_channels(id,tenant_id,phone_number_id,business_number,status,token_enc) VALUES(:channel,:tenant,'synthetic-channel','+5567999999903','connected',decode('abcdef','hex'))",
            "INSERT INTO inbound_events(id,tenant_id,channel_id,external_id,sender_e164,message_text) VALUES(:inbound,:tenant,:channel,'synthetic-event','+5567999999901','Synthetic secret incoming text')",
            "INSERT INTO message_outbox(tenant_id,channel_id,event_id,to_e164,body) VALUES(:tenant,:channel,:inbound,'+5567999999901','Synthetic secret outbound text')",
            "UPDATE commercial_quotes SET fulfillment_mode='pickup',pickup_appointment_id=:appointment,fulfillment_snapshot=CAST(:fulfillment AS jsonb)",
        )
        values["other_slug"] = "management-" + values["other"].hex
        values["fulfillment"] = json.dumps({"schema_version": 1, "mode": "pickup",
                                             "note": "Synthetic preserved receipt"})
        for statement in statements:
            conn.execute(text(statement), values)
        assert backup.revision(conn) == ops.SOURCE
        assert backup.table_names(conn) == ops.SOURCE_TABLES
    yield SimpleNamespace(engine=engine, settings=settings, **values)


def old_column_names(conn):
    return {row[0]: [column[0] for column in row[3]]
            for row in backup.catalog_structure(conn, ops.SOURCE_TABLES)}


def independently_hash_old_rows(conn, columns):
    """Hash exactly the pre-upgrade columns, independent of executor projections."""
    result = {}
    for table, names in sorted(columns.items()):
        assert table in ops.SOURCE_TABLES
        query = text('SELECT encode(sha256(convert_to((SELECT jsonb_object_agg(k,v) '
                     'FROM jsonb_each(to_jsonb(t)) AS fields(k,v) WHERE k=ANY(:columns))::text,'
                     "'UTF8')),'hex') FROM public.\"" + table + '\" AS t ORDER BY 1')
        digest = hashlib.sha256()
        for row in conn.execute(query, {"columns": names}):
            digest.update(row[0].encode("ascii") + b"\n")
        result[table] = digest.hexdigest()
    return result


def source_evidence(case):
    with case.engine.begin() as conn:
        columns = old_column_names(conn)
        return {"columns": columns, "revision": backup.revision(conn),
                "tables": backup.table_names(conn), "counts": backup.counts(conn, ops.SOURCE_TABLES),
                "rows": independently_hash_old_rows(conn, columns),
                "catalog": backup.catalog_fingerprint(conn, ops.SOURCE_TABLES),
                "security": fulfillment.security_structure(conn)}


def assert_source_unchanged(case, expected):
    assert source_evidence(case) == expected


def assert_restore_removed(case):
    with case.engine.begin() as conn:
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                                {"name": backup.restore_name(case.settings.run_id)}).scalar_one()


def assert_old_security_unchanged(conn, before):
    after = fulfillment.security_structure(conn)
    old_relations = {row[0] for row in before["relations"]}
    old_columns = {(row[0], row[1]) for row in before["columns"]}
    after["relations"] = [row for row in after["relations"] if row[0] in old_relations]
    after["columns"] = [row for row in after["columns"] if (row[0], row[1]) in old_columns]
    assert after == before


def test_full_0012_backup_restore_rehearsal_and_upgrade_preserve_rows_and_security(
    management_source, monkeypatch, capsys,
):
    case = management_source
    before = source_evidence(case)
    stored, requests = install_mock_private_store(monkeypatch, case.settings)
    phases = []
    real_restore = ops.verify_restore
    real_apply = ops.apply_exact_migrations
    def verify_restore(*args, **kwargs):
        result = real_restore(*args, **kwargs)
        assert result, "A successful rehearsal must supply a target proof"
        phases.append("restored_and_rehearsed")
        return result
    def apply(engine, settings, *args, **kwargs):
        if settings.url.database == case.settings.url.database:
            assert phases == ["restored_and_rehearsed"]
            phases.append("source_ddl")
        return real_apply(engine, settings, *args, **kwargs)
    monkeypatch.setattr(ops, "verify_restore", verify_restore)
    monkeypatch.setattr(ops, "apply_exact_migrations", apply)
    result = ops.execute(case.settings)
    assert result["status"] == "complete" and result["revision"] == ops.TARGET
    assert result["restore_verified"] is True and requests == ["POST", "GET"]
    assert phases == ["restored_and_rehearsed", "source_ddl"]
    assert stored["body"].startswith(backup.MAGIC)
    assert not stored["body"].startswith(b"PGDMP")
    captured = capsys.readouterr()
    report = json.dumps(result, default=str) + captured.out + captured.err
    for private in (case.settings.token, case.settings.url.password,
                    "Synthetic secret incoming text", "Synthetic secret outbound text",
                    "Synthetic customer", "+5567999999901"):
        if private:
            assert private not in report
    with case.engine.begin() as conn:
        assert backup.revision(conn) == ops.TARGET
        assert backup.table_names(conn) == ops.TARGET_TABLES
        assert backup.counts(conn, ops.SOURCE_TABLES) == before["counts"]
        after = independently_hash_old_rows(conn, before["columns"])
        assert {key: value for key, value in after.items() if key != "alembic_version"} == {
            key: value for key, value in before["rows"].items() if key != "alembic_version"
        }
        assert_old_security_unchanged(conn, before["security"])
        assert not any(ops.counts(conn, ops.NEW_TABLES).values())
        assert conn.exec_driver_sql("SELECT original_total_cents,discount_cents,commission_percent_snapshot,commission_cents FROM appointments").one() == (None, 0, None, None)
        assert conn.exec_driver_sql("SELECT catalog_snapshot FROM appointment_items").scalar_one() is None
        assert conn.exec_driver_sql("SELECT accepts_appointments,commission_percent FROM professionals").one() == (True, 0)
        assert conn.exec_driver_sql("SELECT catalog_kind,addons_version FROM services").one() == ("base", 0)
        tables = conn.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(:tables)"),
                              {"tables": sorted(ops.NEW_TABLES)}).all()
        assert len(tables) == 20 and all(row[1:] == (True, True, "app_migrator") for row in tables)
        assert conn.exec_driver_sql("SELECT bool_and(NOT rolsuper AND NOT rolbypassrls) FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator')").scalar_one()
        resolver = conn.exec_driver_sql("SELECT pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='resolve_published_site'").one()
        assert resolver[0] == "app_migrator" and resolver[1] is True
        assert any(value.startswith("search_path=") for value in resolver[2])
        for tenant in (case.tenant, case.other):
            conn.execute(text("INSERT INTO financial_entries(tenant_id,kind,description,amount_cents,occurred_at) VALUES(:tenant,'income','Synthetic post-upgrade entry',100,now())"), {"tenant": tenant})
        conn.exec_driver_sql("SET LOCAL ROLE app_runtime")
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM financial_entries").scalar_one() == 0
        for tenant in (case.tenant, case.other):
            conn.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)})
            assert conn.exec_driver_sql("SELECT tenant_id FROM customers").scalar_one() == tenant
            assert conn.exec_driver_sql("SELECT tenant_id FROM financial_entries").scalar_one() == tenant
    with pytest.raises(DBAPIError):
        with case.engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL ROLE app_runtime")
            conn.exec_driver_sql("SELECT * FROM management_team_invitations")
    assert_restore_removed(case)
    # A committed target cannot trigger a second backup or replay of source DDL.
    def forbidden(*args, **kwargs):
        pytest.fail("An already upgraded database must stop before backup I/O")
    monkeypatch.setattr(ops, "capture_dump", forbidden)
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)


def test_wrong_source_revision_stops_before_any_backup_io(fulfillment_source, monkeypatch):
    engine, original = fulfillment_source
    case = SimpleNamespace(engine=engine, settings=ops.Settings(original.url, original.bridge_url,
                                                               original.token, original.run_id))
    before = source_evidence(case)
    assert before["revision"] == "0011_commercial_catalog"
    def forbidden(*args, **kwargs):
        pytest.fail("No backup may start from the wrong source revision")
    monkeypatch.setattr(ops, "capture_dump", forbidden)
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("proof", ["missing", "incorrect"])
def test_source_apply_requires_the_restore_rehearsal_manifest(management_source, proof):
    case = management_source
    before = source_evidence(case)
    with case.engine.begin() as conn:
        ops.owner_preflight(conn, case.settings)
        snapshot = ops.source_snapshot(conn)
    # A valid source snapshot alone cannot authorize source DDL. The only call
    # without a target proof is the rehearsal inside this run's restore database.
    target = None if proof == "missing" else {"revision": ops.TARGET, "unverified": True}
    expected_code = "rehearsal_proof_required" if proof == "missing" else "rehearsed_target_mismatch"
    with pytest.raises(backup.Stop, match=expected_code):
        ops.apply_exact_migrations(case.engine, case.settings, snapshot, expected_target=target)
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE commercial_quotes DROP CONSTRAINT ck_quote_fulfillment",
    "ALTER TABLE commercial_quotes DROP CONSTRAINT fk_quote_pickup_appointment",
    "DROP INDEX ix_quote_pickup_appointment",
])
def test_partial_0012_source_contract_stops_before_backup(management_source, monkeypatch, mutation):
    case = management_source
    with case.engine.begin() as conn:
        conn.exec_driver_sql(mutation)
    before = source_evidence(case)
    def forbidden(*args, **kwargs):
        pytest.fail("A partial fulfillment schema must fail before backup I/O")
    monkeypatch.setattr(ops, "capture_dump", forbidden)
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("role", ["app_runtime", "app_migrator", "app_support"])
def test_restricted_application_role_cannot_run_owner_operation(
    management_source, role,
):
    case = management_source
    before = source_evidence(case)
    # A real PostgreSQL role switch proves that even valid owner settings cannot
    # authorize a connection currently acting as one of the restricted roles.
    with case.engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL ROLE " + role)
        assert conn.exec_driver_sql("SELECT current_user").scalar_one() == role
        with pytest.raises(backup.Stop, match="database_owner_required"):
            ops.owner_preflight(conn, case.settings)
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


def test_encrypted_backup_readback_mismatch_never_changes_source(management_source, monkeypatch):
    case = management_source
    before = source_evidence(case)
    stored = {}
    requests = []
    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            stored["body"] = request.content
            return httpx.Response(201, json={"id": case.settings.run_id,
                "sha256": hashlib.sha256(request.content).hexdigest(), "size": len(request.content)})
        body = stored["body"]
        changed = body[:-1] + bytes([body[-1] ^ 1])
        return httpx.Response(200, content=changed, headers={"content-type": "application/octet-stream"})
    original_client = httpx.Client
    monkeypatch.setattr(ops.httpx, "Client", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(backup.Stop, match="backup_readback_mismatch"):
        ops.execute(case.settings)
    assert requests == ["POST", "GET"]
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("failure", ["invalid_archive", "wrong_restored_count"])
def test_real_restore_failure_leaves_source_intact_and_cleans_its_database(
    management_source, monkeypatch, failure,
):
    case = management_source
    before = source_evidence(case)
    install_mock_private_store(monkeypatch, case.settings)
    original_restore = ops.verify_restore
    reached = []
    def verify(engine, settings, identity, path, snapshot):
        reached.append(failure)
        if failure == "invalid_archive":
            path.write_bytes(b"PGDMPsynthetic-truncated-archive")
        else:
            snapshot = deepcopy(snapshot)
            snapshot["counts"]["customers"] += 1
        return original_restore(engine, settings, identity, path, snapshot)
    monkeypatch.setattr(ops, "verify_restore", verify)
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)
    assert reached == [failure]
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


def inject_source_upgrade(monkeypatch, case, revision, mutation):
    modules = ops.reviewed_migrations()
    module = next(value for value in modules if value.revision == revision)
    original_upgrade = module.upgrade
    reached = []
    def upgrade():
        original_upgrade()
        conn = module.op.get_bind()
        if conn.exec_driver_sql("SELECT current_database()").scalar_one() == case.settings.url.database:
            reached.append(revision)
            mutation(conn)
    module.upgrade = upgrade
    monkeypatch.setattr(ops, "reviewed_migrations", lambda: modules)
    return reached


def test_failure_halfway_through_source_upgrade_rolls_back_all_five_steps(
    management_source, monkeypatch,
):
    case = management_source
    before = source_evidence(case)
    install_mock_private_store(monkeypatch, case.settings)
    def fail(conn):
        assert conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relname='financial_entries')").scalar_one()
        conn.exec_driver_sql("UPDATE services SET name='Synthetic unintended mutation'")
        raise RuntimeError("synthetic migration failure after transactional DDL")
    reached = inject_source_upgrade(monkeypatch, case, "0015_loyalty_rewards", fail)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        ops.execute(case.settings)
    assert reached == ["0015_loyalty_rewards"]
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("mutation", [
    "UPDATE services SET name='Synthetic same-count unauthorized change'",
    "GRANT DELETE ON commercial_quotes TO app_runtime",
    "ALTER TABLE customers NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE financial_entries NO FORCE ROW LEVEL SECURITY",
    "GRANT EXECUTE ON FUNCTION public.resolve_published_site(text) TO PUBLIC",
])
def test_rehearsal_proof_rejects_same_count_data_or_security_drift_in_source_transaction(
    management_source, monkeypatch, mutation,
):
    case = management_source
    before = source_evidence(case)
    install_mock_private_store(monkeypatch, case.settings)
    reached = inject_source_upgrade(monkeypatch, case, "0017_financial_settlements",
                                    lambda conn: conn.exec_driver_sql(mutation))
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)
    assert reached == ["0017_financial_settlements"]
    assert_source_unchanged(case, before)
    assert_restore_removed(case)


@pytest.mark.parametrize("mutation", [
    "UPDATE customers SET name='Synthetic concurrent update'",
    "GRANT DELETE ON commercial_quotes TO app_runtime",
])
def test_source_changed_after_backup_stops_before_ddl(management_source, monkeypatch, mutation):
    case = management_source
    install_mock_private_store(monkeypatch, case.settings)
    original_restore = ops.verify_restore
    changed = []
    def restore(*args, **kwargs):
        proof = original_restore(*args, **kwargs)
        with case.engine.begin() as conn:
            conn.exec_driver_sql(mutation)
        changed.append(source_evidence(case))
        return proof
    monkeypatch.setattr(ops, "verify_restore", restore)
    with pytest.raises(backup.Stop):
        ops.execute(case.settings)
    assert len(changed) == 1
    assert_source_unchanged(case, changed[0])
    assert_restore_removed(case)


def test_preexisting_restore_database_is_never_reused_or_deleted(management_source, monkeypatch):
    case = management_source
    before = source_evidence(case)
    install_mock_private_store(monkeypatch, case.settings)
    name = backup.restore_name(case.settings.run_id)
    created_oid = None
    with case.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0')
            created_oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one()
            with pytest.raises(backup.Stop, match="restore_database_already_exists"):
                ops.execute(case.settings)
            assert admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one() == created_oid
            assert_source_unchanged(case, before)
        finally:
            if created_oid is not None:
                assert admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one() == created_oid
                admin.exec_driver_sql('DROP DATABASE "' + name + '"')
