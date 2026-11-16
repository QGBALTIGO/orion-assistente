"""One-time, tenant-pinned import of customer profiles and read-only old history.

Run only with an explicitly captured private source snapshot. Dry-run by default.
It does not create appointments, financial entries, notifications or messages.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

TENANT_ID = UUID("26694b4b-be15-4850-bcb9-69b184897e79")
SOURCE = "minhaagenda.digital/Peixoto.Barbearia"
REVISION = "0078_customer_legacy_history"


def phone_e164(raw):
    digits = re.sub(r"\D", "", raw)
    if len(digits) not in (10, 11):
        raise ValueError("unexpected_source_phone")
    return "+55" + digits


def checked_snapshot(path):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    if snapshot.get("source") != "https://minhaagenda.digital/Peixoto.Barbearia/admin/clientes/":
        raise ValueError("unexpected_source")
    people = snapshot.get("customers")
    if not isinstance(people, list) or len(people) != 324:
        raise ValueError("unexpected_customer_count")
    phones = [phone_e164(p["source_phone"]) for p in people]
    if len(set(phones)) != len(phones):
        raise ValueError("duplicate_source_phone")
    errors = Counter()
    for row in people:
        if not isinstance(row.get("name"), str) or not row["name"].strip() or len(row["name"]) > 120:
            raise ValueError("invalid_source_name")
        count, spent, ticket = (row[k] for k in
            ("source_appointments", "source_spent_cents", "source_ticket_cents"))
        if any(type(v) is not int or v < 0 for v in (count, spent, ticket)):
            raise ValueError("invalid_source_summary")
        history = row.get("history")
        if history is None:
            errors[row.get("history_error") or "unknown"] += 1
        elif (not isinstance(history, list) or len(history) != count or
              sum(item["price_cents"] for item in history) != spent):
            raise ValueError("source_history_summary_mismatch")
    if sum(p["source_appointments"] for p in people) != 1882 or sum(
            p["source_spent_cents"] for p in people) != 8086000:
        raise ValueError("source_totals_changed")
    if errors != {"http_500": 1}:
        raise ValueError("unexpected_history_errors")
    return people


def digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def run(snapshot_path, *, apply=False):
    people = checked_snapshot(snapshot_path)
    dsn = os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        raise RuntimeError("database_connection_unavailable")
    with psycopg.connect(dsn, connect_timeout=10) as db:
        db.execute("SET ROLE app_migrator")
        db.commit()
        with db.transaction():
            if not apply:
                db.execute("SET TRANSACTION READ ONLY")
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            if revision != REVISION and (apply or revision != "0077_commercial_demos"):
                raise RuntimeError("schema_revision_mismatch")
            tenant = db.execute("SELECT public.resolve_published_site(%s)",
                ("barbearia-peixoto",)).fetchone()[0]
            if tenant != TENANT_ID:
                raise RuntimeError("tenant_identity_mismatch")
            if apply:
                db.execute("SELECT pg_advisory_xact_lock(1330792783, 178)")
            before = db.execute("SELECT count(*) FROM appointments WHERE tenant_id=%s", (TENANT_ID,)).fetchone()[0]
            existing = db.execute("""SELECT id,whatsapp_e164,deleted_at FROM customers
                WHERE tenant_id=%s AND whatsapp_e164 IS NOT NULL""", (TENANT_ID,)).fetchall()
            by_phone = {phone: (cid, deleted) for cid, phone, deleted in existing}
            prior = db.execute("""SELECT source_phone,source_sha256 FROM customer_legacy_history
                WHERE tenant_id=%s AND source_system=%s""", (TENANT_ID, SOURCE)).fetchall() if revision == REVISION else []
            prior_hash = dict(prior)
            if len(prior_hash) not in (0, len(people)):
                raise RuntimeError("partial_prior_import")
            to_create = 0
            to_archive = 0
            for row in people:
                source_phone = row["source_phone"]
                phone = phone_e164(source_phone)
                current = by_phone.get(phone)
                if current and current[1] is not None:
                    raise RuntimeError("source_matches_deleted_customer")
                checksum = digest(row)
                if source_phone in prior_hash:
                    if prior_hash[source_phone].strip() != checksum or current is None:
                        raise RuntimeError("existing_import_changed")
                    continue
                if current is None:
                    to_create += 1
                    if apply:
                        cid = db.execute("""INSERT INTO customers(tenant_id,name,whatsapp_e164)
                            VALUES (%s,%s,%s) RETURNING id""",
                            (TENANT_ID, row["name"].strip(), phone)).fetchone()[0]
                        by_phone[phone] = (cid, None)
                if apply:
                    db.execute("""INSERT INTO customer_legacy_history(
                        tenant_id,customer_id,source_system,source_name,source_phone,
                        reported_appointments,reported_spent_cents,reported_ticket_cents,
                        history,history_complete,source_sha256)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (TENANT_ID, by_phone[phone][0], SOURCE, row["name"], source_phone,
                         row["source_appointments"], row["source_spent_cents"],
                         row["source_ticket_cents"], Jsonb(row["history"] or []),
                         row["history"] is not None, checksum))
                to_archive += 1
            if apply:
                after = db.execute("SELECT count(*) FROM appointments WHERE tenant_id=%s", (TENANT_ID,)).fetchone()[0]
                totals = db.execute("""SELECT count(*),sum(reported_appointments),
                    sum(reported_spent_cents),sum(jsonb_array_length(history)),
                    count(*) FILTER (WHERE NOT history_complete)
                    FROM customer_legacy_history WHERE tenant_id=%s AND source_system=%s""",
                    (TENANT_ID, SOURCE)).fetchone()
                if after != before or totals != (324, 1882, 8086000, 1866, 1):
                    raise RuntimeError("post_import_invariant_failed")
            print(json.dumps({"mode": "applied" if apply else "dry_run",
                "source_customers": len(people), "profiles_to_create": to_create,
                "histories_to_archive": to_archive, "existing_target_customers": len(existing),
                "appointments_untouched": before, "missing_detailed_histories": 1}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        run(args.snapshot, apply=args.apply)
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
            "error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "database_failure"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
