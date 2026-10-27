"""One reviewed merge, real PostgreSQL, synthetic people and private-store fake."""
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.booking_service import book_appointment, fingerprint, verify_customer_snapshot
from app.customer_names import remember_whatsapp_name
from app.database import tenant_transaction
from app.public_site_api import confirmed_public_replay
from scripts import repair_customer_identity_20261027 as repair
from test_management import management_db, customer, appointment, entry

pytestmark = pytest.mark.integration
PHONE64 = "+5511999999964"
PHONE94 = "+5511999999994"
BACKUP = {"key": repair.PREFIX + "synthetic-private.dump", "sha256": "a" * 64, "size": 1024}


@pytest_asyncio.fixture
async def repair_db(management_db):
    d = management_db
    primary = await customer(d, name="Kayky", phone=PHONE64, birth_date="1990-05-17", notes="Anotação sintética preservada")
    secondary = await customer(d, name="Kayky", phone=PHONE94)
    scope = repair.Scope(d["a"], UUID(primary["id"]), UUID(secondary["id"]))
    appointments = []
    for index in range(18):
        appointments.append(await appointment(d, primary["id"], start_at=d["now"] - timedelta(days=20-index), status="completed"))
    source_start = d["now"] - timedelta(hours=3)
    moved = await appointment(d, secondary["id"], start_at=source_start)
    for index in range(3):
        await entry(d, customer_id=primary["id"], appointment_id=appointments[index], amount_cents=1000 + index)
    await entry(d, customer_id=secondary["id"], appointment_id=moved, amount_cents=2500)
    key = "public:" + uuid4().hex
    original_hash = fingerprint(d["pro"], [d["service"]], source_start, PHONE94)
    async with d["migrator"].begin() as db:
        for identity, created in zip((scope.primary, scope.secondary), repair.CREATED):
            await db.execute(text("UPDATE customers SET created_at=:stamp WHERE tenant_id=:tenant AND id=:id"),
                             {"tenant": d["a"], "id": identity, "stamp": created})
        await db.execute(text("UPDATE appointments SET request_fingerprint=:hash,idempotency_key=:key WHERE id=:id"),
                         {"id": UUID(moved), "hash": original_hash, "key": key})
        await db.execute(text("""INSERT INTO appointment_history(tenant_id,appointment_id,event,operator,details)
            VALUES(:tenant,:id,'confirmed','support',CAST(:details AS jsonb))"""),
            {"tenant": d["a"], "id": UUID(moved), "details": json.dumps({"v": 1, "actor": {"role": "customer", "customer_id": secondary["id"]}, "source": "public_site"})})
        await db.execute(text("INSERT INTO public_booking_receipts(tenant_id,appointment_id) VALUES(:tenant,:id)"),
                         {"tenant": d["a"], "id": UUID(moved)})
        await db.execute(text("INSERT INTO calendar_outbox(tenant_id,appointment_id,action,status) VALUES(:tenant,:id,'create','sent')"),
                         {"tenant": d["a"], "id": UUID(moved)})
        await db.execute(text("INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in) VALUES(:tenant,:phone,false)"),
                         {"tenant": d["a"], "phone": PHONE94})
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql+psycopg://", 1))
    try:
        yield {**d, "scope": scope, "sync_engine": engine, "moved": UUID(moved), "start": source_start,
               "original_hash": original_hash, "key": key, "factory": async_sessionmaker(d["runtime"], expire_on_commit=False)}
    finally:
        engine.dispose()
        async with d["migrator"].begin() as db:
            for table in ("communication_pending", "public_booking_receipts", "contact_consents"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": d["a"]})


class PrivateSnapshot:
    def __init__(self, folder):
        self.folder = folder
        self.calls = 0
        self.snapshot = None

    def __call__(self, snapshot):
        self.calls += 1
        body = repair.encoded(snapshot)
        self.snapshot = json.loads(body)
        sha = hashlib.sha256(body).hexdigest()
        path = self.folder / (sha + ".json")
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(body)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        return repair.PREFIX + "snapshot-" + sha + ".json"


async def current(d):
    def read():
        with d["sync_engine"].begin() as db:
            return repair.inspect(db, d["scope"])
    return await asyncio.to_thread(read)


async def apply(d, snapshot):
    def run():
        with d["sync_engine"].begin() as db:
            return repair.repair(db, BACKUP, snapshot, d["scope"])
    return await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_specific_repair_preserves_all_history_and_is_idempotent_without_phone_alias(repair_db, tmp_path):
    d = repair_db; scope = d["scope"]
    before = await current(d)
    primary_before, secondary_before = repair.people(before, scope)
    snapshot = PrivateSnapshot(tmp_path)
    result = await apply(d, snapshot)
    assert result["status"] == "applied" and result["primary_appointments"] == 19
    assert result["primary_financial_entries"] == 4 and result["secondary_references"] == 0
    assert result["secondary_archived"] and result["historical_facts_preserved"]
    assert all(value not in json.dumps(result) for value in ("Kayky", PHONE64, PHONE94, "1990-05-17"))
    after = await current(d)
    primary, secondary = repair.people(after, scope)
    assert all(primary[key] == primary_before[key] for key in ("name", "birth_date", "created_at", "whatsapp_e164", "notes"))
    assert secondary["whatsapp_e164"] is None and not secondary["active"] and secondary["deleted_at"] is not None
    assert secondary["created_at"] == secondary_before["created_at"]
    assert repair.digest(repair.unchanged_facts(before)) == repair.digest(repair.unchanged_facts(after))
    old_requests = [row for row in before["management_requests"] if row["entity_id"] == scope.secondary]
    assert old_requests
    for row in old_requests:
        new = next(item for item in after["management_requests"] if item["idempotency_key"] == row["idempotency_key"])
        assert new == {**row, "entity_id": scope.primary}
    original = next(row for row in snapshot.snapshot["before"]["appointments"] if row["id"] == str(d["moved"]))
    moved = next(row for row in after["appointments"] if row["id"] == d["moved"])
    assert original["request_fingerprint"] == d["original_hash"]
    assert moved["request_fingerprint"] != d["original_hash"] and moved["idempotency_key"] == d["key"]
    assert moved["updated_at"] > next(row for row in before["appointments"] if row["id"] == d["moved"])["updated_at"]
    again = await apply(d, snapshot)
    assert again["status"] == "already_applied" and snapshot.calls == 1
    assert await current(d) == after
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            await verify_customer_snapshot(db, d["a"], moved, PHONE64, None)
            with pytest.raises(HTTPException) as denied:
                await verify_customer_snapshot(db, d["a"], moved, PHONE94, None)
            assert denied.value.status_code == 404
    # The discarded number is not an alias, even when WhatsApp sees it again.
    assert await remember_whatsapp_name(d["a"], PHONE94, "Contato distinto") is True
    async with d["migrator"].connect() as db:
        owner = (await db.execute(text("SELECT id FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                                  {"tenant": d["a"], "phone": PHONE94})).scalar_one()
        assert owner not in (scope.primary, scope.secondary)
        consent = (await db.execute(text("SELECT contact_e164,marketing_opt_in FROM contact_consents WHERE tenant_id=:tenant"),
                                    {"tenant": d["a"]})).one()
        assert tuple(consent) == (PHONE94, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["snapshot_failure", "postcondition_failure", "new_reference", "conflicting_notes", "indirect_contact"])
async def test_repair_aborts_atomically_when_preconditions_or_protected_snapshot_fail(repair_db, tmp_path, monkeypatch, mode):
    d = repair_db
    if mode == "new_reference":
        await appointment(d, str(d["scope"].secondary), start_at=d["now"] - timedelta(days=25))
    if mode == "conflicting_notes":
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE customers SET notes='Informação conflitante sintética' WHERE id=:id"), {"id": d["scope"].secondary})
    if mode == "indirect_contact":
        async with d["migrator"].begin() as db:
            await db.execute(text("""INSERT INTO communication_pending(tenant_id,contact_e164,event_key,event,reason,appointment_id)
                VALUES(:tenant,:phone,'synthetic-review','booking_confirmed','human_requested',:id)"""),
                {"tenant": d["a"], "phone": PHONE94, "id": d["moved"]})
    before = await current(d)
    writer = PrivateSnapshot(tmp_path)
    if mode == "snapshot_failure":
        def writer(_):
            raise RuntimeError("simulated_private_store_unavailable")
    if mode == "postcondition_failure":
        real_digest = repair.digest
        def rejecting_digest(value):
            result = real_digest(value)
            if isinstance(value, dict) and "appointments" in value and "customers" not in value:
                return str(uuid4())  # Mismatched verification after DML must roll back every row.
            return result
        monkeypatch.setattr(repair, "digest", rejecting_digest)
    with pytest.raises((ValueError, RuntimeError)):
        await apply(d, writer)
    assert await current(d) == before


@pytest.mark.asyncio
async def test_old_phone_replay_denied_by_revocation_and_by_guard_even_with_original_hash(repair_db, tmp_path):
    d = repair_db
    await apply(d, PrivateSnapshot(tmp_path))
    async def replay():
        async with d["factory"]() as db:
            return await book_appointment(db, d["a"], d["pro"], [d["service"]], PHONE94, "Kayky", d["start"], d["key"])
    with pytest.raises(HTTPException) as rejected:
        await replay()
    assert rejected.value.status_code == 409
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            with pytest.raises(HTTPException) as public_rejection:
                await confirmed_public_replay(db, d["a"], {"nonce": d["key"].split(":", 1)[1], "professional": d["pro"]},
                                              SimpleNamespace(start_at=d["start"], customer_phone=PHONE94))
            assert public_rejection.value.status_code == 409
    # Model the runtime guard on its own, without the one-time hash revocation.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET request_fingerprint=:hash WHERE id=:id"),
                         {"hash": d["original_hash"], "id": d["moved"]})
    with pytest.raises(HTTPException) as rejected:
        await replay()
    assert rejected.value.status_code == 409 and PHONE64 not in str(rejected.value.detail)


@pytest.mark.asyncio
async def test_repair_refuses_busy_professional_and_does_not_broaden_tenant_scope(repair_db, tmp_path):
    d = repair_db
    other = await customer(d, name="Kayky", phone=PHONE94, other=True)
    before = await current(d)
    async with d["runtime"].begin() as held:
        await held.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                           {"scope": str(d["a"]) + ":" + str(d["pro"])})
        with pytest.raises(ValueError, match="professional_busy_retry_later"):
            await apply(d, PrivateSnapshot(tmp_path))
    assert await current(d) == before
    await apply(d, PrivateSnapshot(tmp_path))
    async with d["migrator"].connect() as db:
        foreign = (await db.execute(text("SELECT name,whatsapp_e164,active,deleted_at FROM customers WHERE tenant_id=:tenant AND id=:id"),
                                    {"tenant": d["b"], "id": UUID(other["id"])})).one()
        assert tuple(foreign) == ("Kayky", PHONE94, True, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read_only", "missing_scope", "backup_failure", "verified_backup"])
async def test_operational_cli_defaults_to_read_only_and_requires_verified_backup_before_writing(repair_db, tmp_path, monkeypatch, capsys, mode):
    d = repair_db
    before = await current(d)
    monkeypatch.setattr(repair, "APPROVED", d["scope"])
    raw = os.environ["MIGRATION_DATABASE_URL"]
    monkeypatch.setattr(repair, "connection_values", lambda: (raw, make_url(raw)))
    events = []
    monkeypatch.setattr(repair, "storage_values", lambda: {"synthetic": True})
    def backup(_url, _settings, destination, **kwargs):
        events.append("backup")
        assert kwargs == {"backup_context": "customer-merge-20261027", "schema_revision": repair.SCHEMA, "preserve_ownership": True}
        if mode == "backup_failure":
            raise RuntimeError("synthetic_backup_failed")
        destination.write_bytes(b"Synthetic already-verified private backup. " * 20)
        return BACKUP["key"]
    monkeypatch.setattr(repair, "perform_backup", backup)
    snapshot = PrivateSnapshot(tmp_path)
    def writer(_settings):
        def seal(value):
            events.append("snapshot")
            return snapshot(value)
        return seal
    monkeypatch.setattr(repair, "snapshot_writer", writer)
    arguments = [] if mode == "read_only" else ["--apply"]
    if mode in {"backup_failure", "verified_backup"}:
        arguments += ["--tenant-id", str(d["scope"].tenant), "--primary-id", str(d["scope"].primary),
                      "--secondary-id", str(d["scope"].secondary), "--keep-phone-suffix", "64"]
    code = await asyncio.to_thread(repair.main, arguments)
    output = capsys.readouterr()
    assert all(value not in output.out + output.err for value in (PHONE64, PHONE94, "Kayky", "1990-05-17"))
    if mode == "verified_backup":
        assert code == 0 and events == ["backup", "snapshot"]
        assert json.loads(output.out)["status"] == "applied"
    else:
        assert await current(d) == before
        assert code == {"read_only": 0, "missing_scope": 2, "backup_failure": 3}[mode]
        assert events == (["backup"] if mode == "backup_failure" else [])
