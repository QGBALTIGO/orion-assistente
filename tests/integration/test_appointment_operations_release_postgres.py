"""Actual PostgreSQL constraints, legacy defaults and read-only readiness."""
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.booking_service import protocol
from test_subscription_devices_release_postgres import isolated_settings  # noqa: F401
from scripts import backup_and_migrate_appointment_operations as release
from scripts import wait_for_appointment_operations_schema as barrier

pytestmark = pytest.mark.integration


def test_release_verifies_preserved_tenant_fks_rls_and_booking_constraints(isolated_settings):
    assert release.schema_ready(isolated_settings.migration_url)


async def test_runtime_can_verify_new_schema_without_migrator_or_revision_access(isolated_settings):
    assert await barrier.probe_schema(isolated_settings.runtime, 3)
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        result = await db.fetchrow("""SELECT current_user AS role,
          current_setting('default_transaction_read_only') AS readonly,
          has_column_privilege(current_user,'public.appointments','booking_mode','SELECT') AS mode,
          has_column_privilege(current_user,'public.appointments','created_by_account_id','INSERT') AS actor,
          has_column_privilege(current_user,'public.appointments','actual_duration_min','UPDATE') AS duration,
          has_column_privilege(current_user,'public.appointment_items','item_origin','SELECT') AS origin,
          has_column_privilege(current_user,'public.appointment_items','added_at','INSERT') AS added_at,
          has_table_privilege(current_user,'public.alembic_version','SELECT') AS revision
        """)
        assert dict(result) == {"role": "app_runtime", "readonly": "on", "mode": True,
            "actor": True, "duration": True, "origin": True, "added_at": True, "revision": False}
    finally:
        db.terminate()


@pytest.mark.parametrize("guard", [
    "ck_appointment_booking_mode", "ck_appointment_actual_duration",
    "ck_appointment_item_origin", "ck_appointment_item_added_duration",
    "ck_appointment_item_extra_metadata", "ck_history_event", "appointments_no_overlap",
])
async def test_missing_guard_prevents_startup_without_mutating_schema(isolated_settings, guard):
    db = await asyncpg.connect(isolated_settings.runtime.dsn, timeout=3, command_timeout=3,
        server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog"})
    try:
        query = barrier.SCHEMA_QUERY.replace(guard, "missing_appointment_guard")
        assert query != barrier.SCHEMA_QUERY
        assert (await db.fetchrow(query))["schema_ready"] is False
    finally:
        db.terminate()


@pytest_asyncio.fixture
async def legacy_rows(isolated_settings):
    """Uncommitted synthetic rows, using only columns known before 0066."""
    dsn = isolated_settings.migration_url.replace("postgresql+psycopg://", "postgresql://", 1)
    db = await asyncpg.connect(dsn, timeout=3, command_timeout=5)
    transaction = db.transaction()
    await transaction.start()
    tenant, professional, customer, service, appointment, item = [uuid4() for _ in range(6)]
    try:
        await db.execute("INSERT INTO tenants(id,name,slug) VALUES($1,'Empresa sintética',$2)",
                         tenant, "appointment-release-" + str(tenant))
        await db.execute("INSERT INTO professionals(id,tenant_id,name) VALUES($1,$2,'Profissional sintético')",
                         professional, tenant)
        await db.execute("INSERT INTO customers(id,tenant_id,name) VALUES($1,$2,'Cliente avulso sintético')",
                         customer, tenant)
        await db.execute("""INSERT INTO services(id,tenant_id,name,duration_min,price_cents)
                            VALUES($1,$2,'Serviço sintético',30,1000)""", service, tenant)
        await db.execute("""INSERT INTO appointments(id,tenant_id,customer_id,professional_id,
          protocol,idempotency_key,request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents)
          VALUES($1,$2,$3,$4,$5,$6,$7,'2020-01-01 12:00Z','2020-01-01 12:30Z',
                 '2020-01-01 12:00Z','2020-01-01 12:30Z','completed',1000)""",
          appointment, tenant, customer, professional, protocol(),
          "release-" + str(appointment), "a" * 64)
        await db.execute("""INSERT INTO appointment_items(id,tenant_id,appointment_id,service_id,
          name_snapshot,duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order)
          VALUES($1,$2,$3,$4,'Serviço sintético',30,1000,0,0,0)""", item, tenant, appointment, service)
        yield db, {"tenant": tenant, "appointment": appointment, "item": item}
    finally:
        try:
            await transaction.rollback()
        finally:
            db.terminate()


async def test_legacy_inserts_keep_booked_defaults_and_unknown_metadata(legacy_rows):
    db, ids = legacy_rows
    appointment = await db.fetchrow("""SELECT booking_mode,created_by_account_id,actual_duration_min,
                                      status,total_cents FROM appointments WHERE id=$1""", ids["appointment"])
    assert dict(appointment) == {"booking_mode": "standard", "created_by_account_id": None,
        "actual_duration_min": None, "status": "completed", "total_cents": 1000}
    item = await db.fetchrow("""SELECT item_origin,added_at,added_by_account_id,added_duration_min,
                               duration_snapshot_min,price_snapshot_cents FROM appointment_items WHERE id=$1""", ids["item"])
    assert dict(item) == {"item_origin": "booked", "added_at": None, "added_by_account_id": None,
        "added_duration_min": None, "duration_snapshot_min": 30, "price_snapshot_cents": 1000}


@pytest.mark.parametrize("assignment,constraint", [
    ("booking_mode='untrusted'", "ck_appointment_booking_mode"),
    ("actual_duration_min=0", "ck_appointment_actual_duration"),
    ("actual_duration_min=1441", "ck_appointment_actual_duration"),
])
async def test_invalid_booking_metadata_rejected_by_database(legacy_rows, assignment, constraint):
    db, ids = legacy_rows
    with pytest.raises(asyncpg.CheckViolationError) as error:
        async with db.transaction():
            await db.execute("UPDATE appointments SET " + assignment + " WHERE id=$1", ids["appointment"])
    assert error.value.constraint_name == constraint


@pytest.mark.parametrize("assignment,constraint", [
    ("item_origin='unknown'", "ck_appointment_item_origin"),
    ("added_duration_min=-1", "ck_appointment_item_added_duration"),
    ("added_duration_min=1441", "ck_appointment_item_added_duration"),
    ("item_origin='extra'", "ck_appointment_item_extra_metadata"),
    ("item_origin='extra',added_at=clock_timestamp()", "ck_appointment_item_extra_metadata"),
    ("item_origin='extra',added_duration_min=0", "ck_appointment_item_extra_metadata"),
])
async def test_invalid_or_incomplete_extra_metadata_rejected(legacy_rows, assignment, constraint):
    db, ids = legacy_rows
    with pytest.raises(asyncpg.CheckViolationError) as error:
        async with db.transaction():
            await db.execute("UPDATE appointment_items SET " + assignment + " WHERE id=$1", ids["item"])
    assert error.value.constraint_name == constraint


async def test_zero_added_time_preserves_positive_service_snapshot_and_support_actor(legacy_rows):
    db, ids = legacy_rows
    await db.execute("""UPDATE appointment_items SET item_origin='extra',added_at=clock_timestamp(),
                        added_duration_min=0 WHERE id=$1""", ids["item"])
    item = await db.fetchrow("""SELECT item_origin,added_duration_min,added_by_account_id,
                               duration_snapshot_min FROM appointment_items WHERE id=$1""", ids["item"])
    assert dict(item) == {"item_origin": "extra", "added_duration_min": 0,
                         "added_by_account_id": None, "duration_snapshot_min": 30}
    with pytest.raises(asyncpg.CheckViolationError) as error:
        async with db.transaction():
            await db.execute("UPDATE appointment_items SET duration_snapshot_min=0 WHERE id=$1", ids["item"])
    assert error.value.constraint_name == "ck_item_snapshot"


async def test_history_accepts_existing_and_new_events_and_rejects_unknown(legacy_rows):
    db, ids = legacy_rows
    events = ("hold_created", "hold_expired", "confirmed", "cancelled", "rescheduled",
              "completed", "no_show", "exceptional_created", "retroactive_created", "services_added")
    for event in events:
        await db.execute("""INSERT INTO appointment_history(tenant_id,appointment_id,event,operator)
                            VALUES($1,$2,$3,'support')""", ids["tenant"], ids["appointment"], event)
    assert await db.fetchval("SELECT count(*) FROM appointment_history WHERE appointment_id=$1",
                            ids["appointment"]) == len(events)
    with pytest.raises(asyncpg.CheckViolationError) as error:
        async with db.transaction():
            await db.execute("""INSERT INTO appointment_history(tenant_id,appointment_id,event,operator)
                                VALUES($1,$2,'unknown_event','support')""", ids["tenant"], ids["appointment"])
    assert error.value.constraint_name == "ck_history_event"
