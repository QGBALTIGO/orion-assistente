"""Reviewed one-off customer repair: keep the confirmed phone ending in 64.

Read-only by default. Execution requires the exact reviewed IDs, a verified
private full backup, and a verified private snapshot made under transaction
locks. No names, full phones, birthdays or SQL errors are printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts.backup_and_migrate_commissions import (
    connection_values, migrated_version, perform_backup, storage_values,
)

SCHEMA = "0037_recurring_goals"
OPERATION = "customer_merge_20261027"
PREFIX = "production/customer-merge-20261027/"
FK_TABLES = {"appointments", "financial_entries", "loyalty_transactions",
             "loyalty_coupons", "automation_jobs", "inbox_customer_labels"}
# The 0047 migration added two *separate* customer FKs; allow exactly the
# historical and the expanded reviewed layouts, never an unrecognized FK.
NEW_FK_TABLES = {"appointment_access", "appointment_reviews"}
EXPECTED_COUNTS = {"appointments": (18, 1), "financial_entries": (3, 1),
                   **{table: (0, 0) for table in (FK_TABLES | NEW_FK_TABLES)
                      - {"appointments", "financial_entries"}}}
CREATED = (datetime.fromisoformat("2026-10-17T17:09:31.606497+00:00"),
           datetime.fromisoformat("2026-10-26T20:43:31.324782+00:00"))


@dataclass(frozen=True)
class Scope:
    tenant: UUID
    primary: UUID
    secondary: UUID

    @property
    def parameters(self):
        return {"tenant": self.tenant, "primary": self.primary, "secondary": self.secondary}

    @property
    def receipt_key(self):
        return "keep64:" + str(self.secondary)


APPROVED = Scope(UUID("82552b25-d0c8-5338-8c42-49f11de59e45"),
                 UUID("bcebf5d3-d066-4011-b2d9-1deca2f2a8ee"),
                 UUID("82bf0a1d-8fd7-4767-93bc-ef0fd7ec6665"))


def require(condition, marker):
    if not condition:
        raise ValueError(marker)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def rows(db, sql, parameters):
    result = [dict(row) for row in db.execute(text(sql), parameters).mappings().all()]
    return sorted(result, key=lambda row: encoded(row))


def verify_foreign_keys(db):
    actual = rows(db, """SELECT n.nspname AS namespace,c.relname AS table_name,
        ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(attnum,position)
              JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum ORDER BY x.position) AS columns
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE k.contype='f' AND k.confrelid='public.customers'::regclass""", {})
    tables = {row["table_name"] for row in actual}
    allowed = (FK_TABLES, FK_TABLES | NEW_FK_TABLES)
    require(any(len(actual) == len(group) and tables == group for group in allowed)
            and all(row["namespace"] == "public" and row["columns"] == ["tenant_id", "customer_id"] for row in actual),
            "customer_foreign_keys_changed")
    return tables


def inspect(db, scope=APPROVED):
    fk_tables = verify_foreign_keys(db)
    p = scope.parameters
    people = rows(db, "SELECT * FROM customers WHERE tenant_id=:tenant AND id IN (:primary,:secondary)", p)
    require(len(people) == 2, "reviewed_customers_missing")
    state = {"customers": people}
    for table in sorted(fk_tables):
        state[table] = rows(db, f"SELECT * FROM {table} WHERE tenant_id=:tenant AND customer_id IN (:primary,:secondary)", p)
    state["management_requests"] = rows(db, """SELECT * FROM management_requests WHERE tenant_id=:tenant
        AND entity_id IN (:primary,:secondary)""", p)
    appointment_ids = [row["id"] for row in state["appointments"]]
    ids = {**p, "appointments": appointment_ids, "entries": [row["id"] for row in state["financial_entries"]]}
    state["financial_other_links"] = rows(db, """SELECT * FROM financial_entries WHERE tenant_id=:tenant
        AND appointment_id=ANY(CAST(:appointments AS uuid[]))
        AND (customer_id IS NULL OR customer_id NOT IN (:primary,:secondary))""", ids)
    for table, column in (("appointment_items", "appointment_id"), ("appointment_history", "appointment_id"),
                          ("public_booking_receipts", "appointment_id"), ("calendar_outbox", "appointment_id"),
                          ("commercial_quotes", "pickup_appointment_id"), ("communication_pending", "appointment_id")):
        state[table] = rows(db, f"SELECT * FROM {table} WHERE tenant_id=:tenant AND {column}=ANY(CAST(:appointments AS uuid[]))", ids)
    state["commission_settlements"] = rows(db, """SELECT * FROM commission_settlements WHERE tenant_id=:tenant
        AND payment_entry_id=ANY(CAST(:entries AS uuid[]))""", ids)
    state["commission_advance_allocations"] = rows(db, """SELECT * FROM commission_advance_allocations WHERE tenant_id=:tenant
        AND advance_entry_id=ANY(CAST(:entries AS uuid[]))""", ids)
    return state


def people(state, scope):
    index = {row["id"]: row for row in state["customers"]}
    return index[scope.primary], index[scope.secondary]


def counts(state, scope):
    return {table: tuple(sum(row["customer_id"] == identity for row in state[table])
                         for identity in (scope.primary, scope.secondary))
            for table in sorted((FK_TABLES | NEW_FK_TABLES) & state.keys())}


def validate_before(state, scope):
    primary, secondary = people(state, scope)
    require(all(row["active"] and row["deleted_at"] is None for row in (primary, secondary)), "customer_state_changed")
    require((primary["created_at"], secondary["created_at"]) == CREATED, "registration_changed")
    require(" ".join(primary["name"].casefold().split()) == "kayky"
            and " ".join(secondary["name"].casefold().split()) == "kayky", "customer_name_changed")
    require(all(isinstance(row["whatsapp_e164"], str) and re.fullmatch(r"\+55[1-9][0-9]9[0-9]{8}", row["whatsapp_e164"])
                and row["whatsapp_e164"].endswith(suffix) for row, suffix in ((primary, "64"), (secondary, "94"))),
            "reviewed_phone_changed")
    require(primary["birth_date"] is not None and secondary["birth_date"] is None
            and secondary["birth_month"] is None and secondary["birth_day"] is None, "birthday_state_changed")
    require(counts(state, scope) == {table: EXPECTED_COUNTS[table]
                                      for table in counts(state, scope)},
            "reviewed_reference_counts_changed")
    for field in ("instagram", "email", "city", "neighborhood", "notes"):
        require(not (primary.get(field) and secondary.get(field) and primary[field] != secondary[field]),
                "customer_fields_conflict")
    require(all(row["operation"] == "customers" for row in state["management_requests"]), "unexpected_customer_receipt")
    moved_ids = {row["id"] for row in state["appointments"] if row["customer_id"] == scope.secondary}
    require(not any(row["pickup_appointment_id"] in moved_ids for row in state["commercial_quotes"])
            and not any(row["appointment_id"] in moved_ids for row in state["communication_pending"]),
            "appointment_contact_dependency_requires_review")
    owners = {row["id"]: row["customer_id"] for row in state["appointments"]}
    require(all(row["customer_id"] is None for row in state["financial_other_links"]), "financial_appointment_owner_conflict")
    require(all(row["appointment_id"] is None or owners.get(row["appointment_id"]) == row["customer_id"]
                for row in state["financial_entries"]), "financial_appointment_owner_conflict")


def unchanged_facts(state):
    result = {}
    for table, values in state.items():
        if table in {"customers", "management_requests"}:
            continue
        ignored = {"customer_id", "updated_at", "request_fingerprint"} if table == "appointments" else (
            {"customer_id", "version", "updated_at"} if table == "financial_entries" else set())
        result[table] = sorted(({key: value for key, value in row.items() if key not in ignored} for row in values), key=encoded)
    return result


def masked_result(state, scope, status, **extra):
    primary, secondary = people(state, scope)
    return {"status": status, "kept_phone_suffix": "64", "archived_phone_suffix": "94",
            "primary_appointments": counts(state, scope)["appointments"][0],
            "primary_financial_entries": counts(state, scope)["financial_entries"][0],
            "secondary_references": sum(values[1] for values in counts(state, scope).values()),
            "secondary_archived": not secondary["active"] and secondary["deleted_at"] is not None,
            "primary_birth_date_present": primary["birth_date"] is not None, **extra}


def repair(db, backup, seal_snapshot, scope=APPROVED):
    """Caller commits the transaction; seal_snapshot must verify private readback."""
    db.execute(text("SET LOCAL lock_timeout='3000ms'"))
    db.execute(text("SET LOCAL statement_timeout='15000ms'"))
    locked = db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                        {"key": OPERATION + ":" + str(scope.tenant)}).scalar_one()
    require(locked, "customer_repair_busy")
    receipt = db.execute(text("""SELECT fingerprint,entity_id FROM management_requests
        WHERE tenant_id=:tenant AND operation=:operation AND idempotency_key=:key"""),
        {"tenant": scope.tenant, "operation": OPERATION, "key": scope.receipt_key}).mappings().one_or_none()
    if receipt:
        current = inspect(db, scope)
        _, secondary = people(current, scope)
        require(receipt["entity_id"] == scope.primary and not secondary["active"] and secondary["deleted_at"] is not None
                and secondary["whatsapp_e164"] is None and not any(value[1] for value in counts(current, scope).values()),
                "completed_repair_state_changed")
        return masked_result(current, scope, "already_applied", snapshot_sha256=receipt["fingerprint"])
    professionals = [row["id"] for row in rows(db, "SELECT id FROM professionals WHERE tenant_id=:tenant", scope.parameters)]
    for identity in sorted(professionals, key=str):
        locked = db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:scope,0))"),
                            {"scope": str(scope.tenant) + ":" + str(identity)}).scalar_one()
        require(locked, "professional_busy_retry_later")
    # Same professional locks as booking/completion/finance, then customer locks.
    # Locking the tenant also prevents new professional/FK inserts during the repair.
    db.execute(text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), scope.parameters).scalar_one()
    require({row["id"] for row in rows(db, "SELECT id FROM professionals WHERE tenant_id=:tenant", scope.parameters)} == set(professionals),
            "professional_scope_changed")
    db.execute(text("SELECT id FROM customers WHERE tenant_id=:tenant AND id IN (:primary,:secondary) ORDER BY id FOR UPDATE"), scope.parameters).all()
    for table in ("appointments", "financial_entries"):
        db.execute(text(f"SELECT id FROM {table} WHERE tenant_id=:tenant AND customer_id IN (:primary,:secondary) ORDER BY id FOR UPDATE"), scope.parameters).all()
    before = inspect(db, scope)
    validate_before(before, scope)
    require(isinstance(backup, dict) and backup.get("key", "").startswith(PREFIX) and backup["key"].endswith(".dump")
            and re.fullmatch(r"[a-f0-9]{64}", backup.get("sha256", "")) and backup.get("size", 0) > 100,
            "verified_private_backup_required")
    snapshot = {"schema": SCHEMA, "operation": OPERATION, "scope": scope.parameters,
                "backup": backup, "before": before,
                "reason": "Confirmed customer correction; keep 64, archive 94 without alias; revoke old booking retry"}
    snapshot_sha = digest(snapshot)
    snapshot_key = seal_snapshot(snapshot)
    require(snapshot_key == PREFIX + "snapshot-" + snapshot_sha + ".json", "private_snapshot_not_verified")
    primary, secondary = people(before, scope)
    primary_fields = {field: secondary[field] for field in ("instagram", "email", "city", "neighborhood", "notes")
                      if not primary.get(field) and secondary.get(field)}
    p = scope.parameters
    db.execute(text("""UPDATE customers SET active=false,deleted_at=clock_timestamp(),whatsapp_e164=NULL,
        version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:tenant AND id=:secondary"""), p)
    assignments = "".join("," + key + "=:" + key for key in primary_fields)
    db.execute(text("UPDATE customers SET version=version+1,updated_at=clock_timestamp()" + assignments +
                    " WHERE tenant_id=:tenant AND id=:primary"), {**p, **primary_fields})
    for appointment in before["appointments"]:
        if appointment["customer_id"] != scope.secondary:
            continue
        revoked = digest({"repair": OPERATION, "appointment": appointment["id"], "original": appointment["request_fingerprint"]})
        result = db.execute(text("""UPDATE appointments SET customer_id=:primary,request_fingerprint=:revoked,
            updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
            WHERE tenant_id=:tenant AND id=:id AND customer_id=:secondary AND request_fingerprint=:original"""),
            {**p, "id": appointment["id"], "revoked": revoked, "original": appointment["request_fingerprint"]})
        require(result.rowcount == 1, "appointment_changed_during_repair")
    result = db.execute(text("""UPDATE financial_entries SET customer_id=:primary,version=version+1,
        updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
        WHERE tenant_id=:tenant AND customer_id=:secondary"""), p)
    require(result.rowcount == 1, "financial_changed_during_repair")
    db.execute(text("""UPDATE management_requests SET entity_id=:primary WHERE tenant_id=:tenant
        AND operation='customers' AND entity_id=:secondary"""), p)
    after = inspect(db, scope)
    require(digest(unchanged_facts(before)) == digest(unchanged_facts(after)), "historical_facts_changed")
    actual_primary, actual_secondary = people(after, scope)
    require(all(actual_primary[key] == primary[key] for key in ("id", "name", "whatsapp_e164", "birth_date", "birth_month", "birth_day", "created_at")),
            "primary_identity_changed")
    require(counts(after, scope) == {table: (sum(values), 0)
                                      for table, values in counts(before, scope).items()}
            and actual_secondary["whatsapp_e164"] is None and not actual_secondary["active"] and actual_secondary["deleted_at"] is not None,
            "repair_postconditions_failed")
    db.execute(text("""INSERT INTO management_requests(tenant_id,operation,idempotency_key,fingerprint,entity_id)
        VALUES(:tenant,:operation,:key,:fingerprint,:primary)"""),
        {**p, "operation": OPERATION, "key": scope.receipt_key, "fingerprint": snapshot_sha})
    db.execute(text("INSERT INTO tenant_audit(tenant_id,action,entity,entity_id) VALUES(:tenant,:operation,'customers',:primary)"),
               {**p, "operation": OPERATION})
    return masked_result(after, scope, "applied", snapshot_key=snapshot_key, snapshot_sha256=snapshot_sha,
                         historical_facts_preserved=True, booking_retry_revoked=True)


def snapshot_writer(settings):
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", endpoint_url=settings["endpoint"], region_name=settings["region"],
                          aws_access_key_id=settings["access"], aws_secret_access_key=settings["secret"],
                          config=Config(s3={"addressing_style": settings["style"]}, connect_timeout=10,
                                        read_timeout=20, retries={"max_attempts": 2}))

    def seal(snapshot):
        body = encoded(snapshot)
        sha = hashlib.sha256(body).hexdigest()
        key = PREFIX + "snapshot-" + sha + ".json"
        client.put_object(Bucket=settings["bucket"], Key=key, Body=body, ContentType="application/json",
                          Metadata={"sha256": sha, "schema": SCHEMA})
        result = client.get_object(Bucket=settings["bucket"], Key=key)
        stream = result["Body"]
        try:
            actual = stream.read(len(body) + 1)
        finally:
            stream.close()
        require(len(actual) == len(body) and hashlib.sha256(actual).hexdigest() == sha
                and result.get("Metadata", {}).get("sha256") == sha, "private_snapshot_readback_failed")
        return key

    return seal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--tenant-id")
    parser.add_argument("--primary-id")
    parser.add_argument("--secondary-id")
    parser.add_argument("--keep-phone-suffix")
    args = parser.parse_args(argv)
    engine = None
    try:
        supplied = (args.tenant_id, args.primary_id, args.secondary_id, args.keep_phone_suffix)
        approved = (str(APPROVED.tenant), str(APPROVED.primary), str(APPROVED.secondary), "64")
        require(supplied == approved if args.apply or any(supplied) else True, "explicit_repair_scope_required")
        print("orion_customer_repair_started", flush=True)
        raw, url = connection_values()
        require(migrated_version(raw) == SCHEMA, "customer_repair_schema_not_reviewed")
        engine = create_engine(raw, poolclass=NullPool)
        with engine.begin() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text("SET LOCAL statement_timeout='15000ms'"))
            receipt = db.execute(text("""SELECT fingerprint,entity_id FROM management_requests WHERE tenant_id=:tenant
                AND operation=:operation AND idempotency_key=:key"""),
                {"tenant": APPROVED.tenant, "operation": OPERATION, "key": APPROVED.receipt_key}).mappings().one_or_none()
            current = inspect(db, APPROVED)
            if receipt:
                primary, secondary = people(current, APPROVED)
                require(receipt["entity_id"] == APPROVED.primary and not secondary["active"] and secondary["deleted_at"] is not None and secondary["whatsapp_e164"] is None
                        and not any(value[1] for value in counts(current, APPROVED).values()), "completed_repair_state_changed")
                print(json.dumps(masked_result(current, APPROVED, "already_applied", snapshot_sha256=receipt["fingerprint"])), flush=True)
                return 0
            validate_before(current, APPROVED)
            if not args.apply:
                print(json.dumps(masked_result(current, APPROVED, "read_only_ready", read_only=True)), flush=True)
                return 0
        settings = storage_values()
        with tempfile.TemporaryDirectory(prefix="orion-customer-repair-") as folder:
            archive = Path(folder) / "backup.dump"
            key = perform_backup(url, settings, archive, backup_context="customer-merge-20261027",
                                 schema_revision=SCHEMA, preserve_ownership=True)
            with archive.open("rb") as stream:
                backup_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            backup = {"key": key, "sha256": backup_sha, "size": archive.stat().st_size}
        with engine.begin() as db:
            result = repair(db, backup, snapshot_writer(settings), APPROVED)
        print(json.dumps(result), flush=True)
        return 0
    except ValueError as exc:
        marker = str(exc)
        print(json.dumps({"status": "not_applied", "reason": marker if re.fullmatch(r"[a-z_]+", marker) else "review_failed"}), file=sys.stderr, flush=True)
        return 2
    except Exception:
        print(json.dumps({"status": "not_applied", "reason": "backup_or_transaction_failed"}), file=sys.stderr, flush=True)
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
