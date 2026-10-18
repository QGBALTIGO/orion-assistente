"""0017 -> 0018 rehearsal in UUID databases only, with real PostgreSQL owners/ACLs.

The imported fixture requires a loopback PostgreSQL16 owner URL and creates its
own database. No application's existing database is migrated or restored here.
"""
from contextlib import contextmanager
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from alembic import command, op
from alembic.config import Config
import pytest
from sqlalchemy import event, text

from scripts import one_time_business_migration as pg_clients
from scripts import one_time_sales_requests_migration as ops
from test_business_migration_ops_postgres import migration_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def sales_source(migration_source):
    engine, previous = migration_source
    config = Config(str(ops.ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ops.ROOT / "alembic"))
    command.upgrade(config, ops.SOURCE)
    settings = ops.Settings(previous.url, previous.url.host, previous.url.database, True)
    with engine.begin() as conn:
        assert ops.revision(conn) == ops.SOURCE and ops.table_names(conn) == ops.SOURCE_TABLES
    return SimpleNamespace(engine=engine, settings=settings)


def snapshot(source):
    """Only tests hash ordinary data; the production runner reads metadata only."""
    with source.engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        conn.exec_driver_sql("SET LOCAL timezone='UTC'")
        rows = {}
        for name in sorted(ops.SOURCE_TABLES - {"alembic_version"}):
            digest = hashlib.sha256()
            query = ('SELECT encode(sha256(convert_to(to_jsonb(t)::text,\'UTF8\')),\'hex\') '
                     'FROM public."' + name + '" t ORDER BY 1')
            for value in conn.exec_driver_sql(query).scalars():
                digest.update(value.encode("ascii") + b"\n")
            rows[name] = digest.hexdigest()
        return {"catalog": ops.existing_catalog(conn), "rows": rows, "revision": ops.revision(conn)}


@contextmanager
def restored_database(source, dump_path):
    name = "orion_sales_restore_" + uuid4().hex
    engine = None
    created_oid = None
    with source.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0')
            created_oid = admin.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC')
            admin.exec_driver_sql('GRANT CONNECT ON DATABASE "' + name + '" TO app_migrator,app_runtime,app_support')
            url = source.settings.url.set(database=name)
            # Deliberately retain owners and ACLs, unlike --no-owner/--no-acl.
            pg_clients.run_client("pg_restore", ["--no-password", "--exit-on-error", "--single-transaction",
                                   "--dbname=" + name, str(dump_path)], url)
            engine = ops.engine_for(url)
            yield SimpleNamespace(engine=engine, settings=ops.Settings(url, url.host, name, True))
        finally:
            if engine is not None:
                engine.dispose()
            if created_oid is not None:
                found = admin.execute(text("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=:name"),
                                      {"name": name}).one()
                assert tuple(found) == (created_oid, "postgres") and name != source.settings.url.database
                # Never terminate sessions or DROP FORCE, even in the fixture.
                admin.exec_driver_sql('DROP DATABASE "' + name + '"')


def test_real_backup_restore_rehearsal_and_apply_preserve_data_owners_and_rls(sales_source, tmp_path):
    before = snapshot(sales_source)
    with sales_source.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM customers").scalar_one() == 1
        assert conn.exec_driver_sql("SELECT count(*) FROM panel_accounts").scalar_one() == 1
    path = tmp_path / "synthetic-sales-source.dump"
    pg_clients.private_write(path, b"")
    pg_clients.run_client("pg_dump", ["--no-password", "--format=custom", "--lock-wait-timeout=5000",
                                     "--file=" + str(path)], sales_source.settings.url, max_file=path)
    assert path.read_bytes().startswith(b"PGDMP")
    with restored_database(sales_source, path) as restored:
        restored_before = snapshot(restored)
        assert restored_before["rows"] == before["rows"] and restored_before["revision"] == ops.SOURCE
        # PostgreSQL can reparse CHECK expressions on restore; security and rows
        # remain exact, while the runner compares each database's own catalog.
        for name, value in before["catalog"]["tables"].items():
            actual = restored_before["catalog"]["tables"][name]
            for field in ("owner", "acl", "rls", "force_rls", "runtime_privileges", "support_privileges"):
                assert actual[field] == value[field], (name, field)
        result = ops.execute(restored.settings)
        assert result == {"ok": True, "status": "applied", "source": ops.SOURCE, "revision": ops.TARGET}
        after = snapshot(restored)
        assert after["rows"] == restored_before["rows"] and after["catalog"] == restored_before["catalog"]
        with restored.engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
            ops.verify_target(conn)
            assert conn.exec_driver_sql("SELECT count(*) FROM public.sales_requests").scalar_one() == 0
    assert snapshot(sales_source) == before, "Rehearsal must never modify its source"
    assert ops.execute(sales_source.settings)["status"] == "applied"
    after = snapshot(sales_source)
    assert after["rows"] == before["rows"] and after["catalog"] == before["catalog"]
    assert after["revision"] == ops.TARGET


def test_replay_is_read_only_and_preserves_received_request(sales_source, monkeypatch):
    ops.execute(sales_source.settings)
    with sales_source.engine.begin() as conn:
        conn.execute(text("""INSERT INTO public.sales_requests(idempotency_key,payload_hash,name,email,
            segment,intent,consent_version,status,version,contacted_at)
            VALUES (:key,:hash,'Synthetic person','synthetic@example.test','outro','trial',
                'sales-contact-v1','contacted',4,now())"""), {"key": uuid4(), "hash": "a" * 64})
        before = conn.exec_driver_sql("SELECT row_to_json(s)::text FROM public.sales_requests s").scalars().all()
    original = ops.verify_target
    read_only = []
    def check(conn):
        read_only.append(conn.exec_driver_sql("SHOW transaction_read_only").scalar_one())
        original(conn)
    monkeypatch.setattr(ops, "verify_target", check)
    monkeypatch.setattr(ops, "apply_exact_migration", lambda *_: pytest.fail("Replay must never call the upgrade"))
    assert ops.execute(sales_source.settings)["status"] == "already_applied"
    assert read_only == ["on"]
    with sales_source.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT row_to_json(s)::text FROM public.sales_requests s").scalars().all() == before


@pytest.mark.parametrize("alteration", [
    "ALTER TABLE public.sales_requests DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.sales_requests NO FORCE ROW LEVEL SECURITY",
    "GRANT SELECT ON public.sales_requests TO app_runtime",
    "GRANT UPDATE(name) ON public.sales_requests TO app_support",
    "ALTER TABLE public.sales_requests ADD COLUMN unexpected text",
    "ALTER TABLE public.sales_requests DROP CONSTRAINT sales_requests_consent_check",
    "DROP INDEX public.ix_sales_requests_created",
    "ALTER TABLE public.sales_requests OWNER TO postgres",
    "ALTER POLICY sales_requests_support ON public.sales_requests USING(false)",
])
def test_incompatible_replay_aborts_without_repair(sales_source, alteration):
    ops.execute(sales_source.settings)
    with sales_source.engine.begin() as conn:
        conn.exec_driver_sql(alteration)
        conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        before = ops.catalog(conn)
    with pytest.raises(ops.Stop, match="unexpected_sales_schema"):
        ops.execute(sales_source.settings)
    with sales_source.engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        assert ops.catalog(conn) == before and ops.revision(conn) == ops.TARGET


@pytest.mark.parametrize("alteration", [
    "UPDATE public.alembic_version SET version_num='0016_management_extras'",
    "INSERT INTO public.alembic_version(version_num) VALUES ('unreviewed_branch')",
    "CREATE TABLE public.unreviewed_extra_table(id integer)",
])
def test_wrong_source_revision_or_table_set_aborts_before_upgrade(sales_source, monkeypatch, alteration):
    with sales_source.engine.begin() as conn:
        conn.exec_driver_sql(alteration)
        before = list(conn.exec_driver_sql("SELECT version_num FROM public.alembic_version ORDER BY 1").scalars())
        names = ops.table_names(conn)
    monkeypatch.setattr(ops, "apply_exact_migration", lambda *_: pytest.fail("Invalid source must stop before DDL"))
    with pytest.raises(ops.Stop, match="unexpected_(source_schema|revision_rows)"):
        ops.execute(sales_source.settings)
    with sales_source.engine.begin() as conn:
        assert list(conn.exec_driver_sql("SELECT version_num FROM public.alembic_version ORDER BY 1").scalars()) == before
        assert ops.table_names(conn) == names and "sales_requests" not in names


def test_failure_after_revision_write_rolls_back_ddl_and_revision(sales_source, monkeypatch):
    before = snapshot(sales_source)
    original = ops.revision
    def fail_after_write(conn):
        value = original(conn)
        if value == ops.TARGET:
            raise ops.Stop("synthetic_failure_after_revision_write")
        return value
    monkeypatch.setattr(ops, "revision", fail_after_write)
    with pytest.raises(ops.Stop, match="synthetic_failure_after_revision_write"):
        ops.execute(sales_source.settings)
    monkeypatch.setattr(ops, "revision", original)
    assert snapshot(sales_source) == before
    with sales_source.engine.begin() as conn:
        assert "sales_requests" not in ops.table_names(conn)


def test_any_change_to_existing_catalog_rolls_back_the_whole_upgrade(sales_source, monkeypatch):
    before = snapshot(sales_source)
    module = ops.reviewed_migration()
    def altered_upgrade():
        module.upgrade()
        op.execute("ALTER TABLE public.customers ADD COLUMN unreviewed_column text")
    monkeypatch.setattr(ops, "reviewed_migration", lambda: SimpleNamespace(upgrade=altered_upgrade))
    with pytest.raises(ops.Stop, match="existing_catalog_changed"):
        ops.execute(sales_source.settings)
    assert snapshot(sales_source) == before


@pytest.mark.parametrize("program", ["trigger", "rule", "check"])
def test_revision_side_effect_program_is_rejected_without_touching_canary(sales_source, monkeypatch, program):
    with sales_source.engine.begin() as conn:
        if program == "trigger":
            conn.exec_driver_sql("""CREATE FUNCTION public.revision_canary() RETURNS trigger
                LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
                UPDATE public.tenants SET name='Canary was changed'; RETURN NEW; END $$;
                CREATE TRIGGER revision_side_effect BEFORE UPDATE ON public.alembic_version
                FOR EACH ROW EXECUTE FUNCTION public.revision_canary()""")
        elif program == "rule":
            conn.exec_driver_sql("""CREATE RULE revision_side_effect AS ON UPDATE TO public.alembic_version
                DO ALSO UPDATE public.tenants SET name='Canary was changed'""")
        else:
            conn.exec_driver_sql("""CREATE FUNCTION public.revision_canary(text) RETURNS boolean
                LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
                UPDATE public.tenants SET name='Canary was changed'; RETURN true; END $$;
                ALTER TABLE public.alembic_version ADD CONSTRAINT revision_side_effect
                CHECK(public.revision_canary(version_num)) NOT VALID""")
    before = snapshot(sales_source)
    monkeypatch.setattr(ops, "apply_exact_migration", lambda *_: pytest.fail("Revision programs must stop before DDL"))
    with pytest.raises(ops.Stop, match="(unreviewed_revision_programs|unexpected_revision_schema)"):
        ops.execute(sales_source.settings)
    assert snapshot(sales_source) == before
    with sales_source.engine.begin() as conn:
        assert not conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.tenants WHERE name='Canary was changed')").scalar_one()


@pytest.mark.parametrize("role,allowed", [("app_migrator", True), ("app_support", False), ("app_runtime", False)])
def test_actual_current_role_is_enforced_without_switch(sales_source, monkeypatch, role, allowed):
    # The owner URL stays private. SET ROLE exercises the actual restricted role
    # without assuming that CI application roles share the owner's password.
    engine = ops.engine_for(sales_source.settings.url)
    @event.listens_for(engine, "connect")
    def assume_role(connection, _):
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE " + role)  # Three fixed literals above, never user input.
        connection.commit()
    monkeypatch.setattr(ops, "engine_for", lambda _: engine)
    settings = ops.Settings(sales_source.settings.url, sales_source.settings.expected_host,
                            sales_source.settings.expected_database, False)
    before = snapshot(sales_source)
    if allowed:
        assert ops.execute(settings)["status"] == "applied"
        after = snapshot(sales_source)
        assert after["rows"] == before["rows"] and after["catalog"] == before["catalog"]
    else:
        with pytest.raises(ops.Stop, match="migrator_role_required"):
            ops.execute(settings)
        assert snapshot(sales_source) == before


def test_advisory_lock_refuses_a_second_operation_without_mutation(sales_source):
    before = snapshot(sales_source)
    with sales_source.engine.begin() as conn:
        assert conn.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ops.ADVISORY_LOCK}).scalar_one()
        with pytest.raises(ops.Stop, match="migration_already_running"):
            ops.execute(sales_source.settings)
    assert snapshot(sales_source) == before
