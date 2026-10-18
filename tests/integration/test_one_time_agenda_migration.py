"""Agenda migration rehearsals only in UUID-named loopback PostgreSQL databases."""
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from alembic import command, op
from alembic.config import Config
import pytest
from sqlalchemy import event, text

from scripts import one_time_agenda_migration as ops
from test_business_migration_ops_postgres import migration_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def agenda_source(migration_source):
    engine, previous = migration_source
    config = Config(str(ops.ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ops.ROOT / "alembic"))
    command.upgrade(config, ops.SOURCE)
    appointment, professional, history_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as conn:
        tenant, customer = conn.exec_driver_sql("SELECT tenant_id,id FROM customers LIMIT 1").one()
        conn.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Synthetic agenda professional')"),
                     {"id": professional, "tenant": tenant})
        conn.execute(text("""INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,
            idempotency_key,request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents)
            VALUES(:id,:tenant,:customer,:professional,'AG-TEST01','synthetic-agenda-migration',:fingerprint,
            '2030-04-05 12:00:00Z','2030-04-05 12:30:00Z','2030-04-05 12:00:00Z','2030-04-05 12:30:00Z','confirmed',5000)"""),
            {"id": appointment, "tenant": tenant, "customer": customer, "professional": professional, "fingerprint": "0" * 64})
        conn.execute(text("INSERT INTO appointment_history(id,tenant_id,appointment_id,event,operator) VALUES(:id,:tenant,:appointment,'confirmed','support')"),
                     {"id": history_id, "tenant": tenant, "appointment": appointment})
    return SimpleNamespace(engine=engine, settings=ops.Settings(previous.url, previous.url.host, previous.url.database, True),
                           appointment=appointment, tenant=tenant, history_id=history_id)


def snapshot(source, *, projected=False):
    with source.engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        conn.exec_driver_sql("SET LOCAL timezone='UTC'")
        data = {}
        for name in sorted(ops.TABLES - {"alembic_version"}):
            expression = "to_jsonb(t) - 'details'" if projected and name == "appointment_history" else "to_jsonb(t)"
            query = ('SELECT encode(sha256(convert_to((' + expression + ")::text,'UTF8')),'hex') FROM public.\"" + name + '\" t ORDER BY 1')
            digest = hashlib.sha256()
            for value in conn.exec_driver_sql(query).scalars():
                digest.update(value.encode("ascii") + b"\n")
            data[name] = digest.hexdigest()
        return {"catalog": ops.existing_catalog(conn, projected=projected), "data": data, "revision": ops.revision(conn)}


def test_additive_upgrade_preserves_existing_data_and_permissions(agenda_source):
    before = snapshot(agenda_source)
    result = ops.execute(agenda_source.settings)
    assert result == {"ok": True, "status": "applied", "source": ops.SOURCE, "revision": ops.TARGET}
    after = snapshot(agenda_source, projected=True)
    assert after["catalog"] == before["catalog"] and after["data"] == before["data"]
    with agenda_source.engine.begin() as conn:
        assert conn.execute(text("SELECT details FROM appointment_history WHERE id=:id"),
                            {"id": agenda_source.history_id}).scalar_one() == {}
        assert ops.revision(conn) == ops.TARGET


def test_target_replay_is_read_only_and_preserves_new_history(agenda_source):
    ops.execute(agenda_source.settings)
    with agenda_source.engine.begin() as conn:
        conn.execute(text("""INSERT INTO appointment_history(tenant_id,appointment_id,event,operator,details)
            VALUES(:tenant,:appointment,'cancelled','support',CAST(:details AS jsonb))"""),
            {"tenant": agenda_source.tenant, "appointment": agenda_source.appointment,
             "details": '{"version":1,"requested_by":"customer","reason":"Synthetic request"}'})
    before = snapshot(agenda_source)
    writes = []
    def capture(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith(("UPDATE ", "INSERT ", "ALTER ", "DELETE ", "CREATE ", "DROP ")):
            writes.append(statement.split(None, 1)[0])
    event.listen(agenda_source.engine, "before_cursor_execute", capture)
    # execute creates its own engine; wrap its construction to observe replay.
    original = ops.engine_for
    try:
        ops.engine_for = lambda _: agenda_source.engine
        result = ops.execute(agenda_source.settings)
    finally:
        ops.engine_for = original
        event.remove(agenda_source.engine, "before_cursor_execute", capture)
    assert result["status"] == "already_applied" and writes == []
    assert snapshot(agenda_source) == before


@pytest.mark.parametrize("alteration", [
    "UPDATE alembic_version SET version_num='0017_financial_settlements'",
    "ALTER TABLE appointment_history ADD COLUMN unreviewed text",
])
def test_incompatible_source_stops_before_upgrade(agenda_source, alteration):
    with agenda_source.engine.begin() as conn:
        conn.exec_driver_sql(alteration)
    before = snapshot(agenda_source)
    with pytest.raises(ops.Stop, match="unexpected_source_schema|unexpected_source_history"):
        ops.execute(agenda_source.settings)
    assert snapshot(agenda_source) == before


def test_existing_catalog_mutation_rolls_back_ddl(agenda_source, monkeypatch):
    module = ops.reviewed_migration()
    def upgrade():
        module.upgrade()
        op.execute("ALTER TABLE customers ADD COLUMN unreviewed text")
    monkeypatch.setattr(ops, "reviewed_migration", lambda: SimpleNamespace(upgrade=upgrade))
    before = snapshot(agenda_source)
    with pytest.raises(ops.Stop, match="existing_catalog_changed"):
        ops.execute(agenda_source.settings)
    assert snapshot(agenda_source) == before


def test_failure_after_revision_write_rolls_back_all_changes(agenda_source, monkeypatch):
    original = ops.existing_catalog
    projected_calls = 0
    def altered(conn, *, projected=False):
        nonlocal projected_calls
        result = original(conn, projected=projected)
        if projected:
            projected_calls += 1
            if projected_calls == 2:
                raise ops.Stop("synthetic_final_check_failed")
        return result
    before = snapshot(agenda_source)
    monkeypatch.setattr(ops, "existing_catalog", altered)
    with pytest.raises(ops.Stop, match="synthetic_final_check_failed"):
        ops.execute(agenda_source.settings)
    assert projected_calls == 2 and snapshot(agenda_source) == before


def test_revision_trigger_is_refused_before_any_migration(agenda_source):
    with agenda_source.engine.begin() as conn:
        conn.exec_driver_sql("""CREATE FUNCTION public.synthetic_agenda_trigger() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'must not run'; END $$""")
        conn.exec_driver_sql("CREATE TRIGGER synthetic_agenda_revision BEFORE UPDATE ON alembic_version FOR EACH ROW EXECUTE FUNCTION public.synthetic_agenda_trigger()")
    before = snapshot(agenda_source)
    with pytest.raises(ops.Stop, match="unreviewed_revision_programs"):
        ops.execute(agenda_source.settings)
    assert snapshot(agenda_source) == before


def test_pending_other_agenda_migration_does_not_retry_or_write(agenda_source):
    with agenda_source.engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ops.ADVISORY_LOCK})
        with pytest.raises(ops.Stop, match="migration_already_running"):
            ops.execute(agenda_source.settings)
    assert snapshot(agenda_source)["revision"] == ops.SOURCE
