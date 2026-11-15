"""Read-only post-migration inspection; never logs credentials or message bodies.

Run inside the existing worker image with its existing environment. Every SQL
transaction is read-only. S3 and bridge operations are GET/list operations.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

def emit(section, value):
    print("orion_migration_check=" + json.dumps(
        {"section": section, "at": datetime.now(timezone.utc).isoformat(), **value},
        default=str, sort_keys=True), flush=True)


def database():
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    raw = os.environ["MIGRATION_DATABASE_URL"]
    url = make_url(raw).set(drivername="postgresql+psycopg")
    assert url.host == os.environ["ORION_EXPECTED_DATABASE_HOST"]
    assert url.database == os.environ["ORION_EXPECTED_DATABASE_NAME"]
    engine = create_engine(url, poolclass=NullPool, connect_args={
        "connect_timeout": 10,
        "options": "-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000",
    })
    try:
        with engine.connect() as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            db.execute(text("SET ROLE app_migrator"))
            assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            version = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            tables = db.execute(text("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""")).all()
            counts, inaccessible = {}, []
            for name, _, _ in tables:
                quoted = engine.dialect.identifier_preparer.quote(name)
                try:
                    with db.begin_nested():
                        counts[name] = db.execute(text(f"SELECT count(*) FROM public.{quoted}")).scalar_one()
                except Exception as exc:
                    inaccessible.append({"table": name, "error_type": type(exc).__name__})
            emit("database", {"read_only": True, "schema_revision": version,
                "table_count": len(tables), "counts": counts, "inaccessible": inaccessible,
                "unvalidated_constraints": db.execute(text("""SELECT conname FROM pg_constraint
                    WHERE connamespace='public'::regnamespace AND NOT convalidated ORDER BY conname""")).scalars().all(),
                "non_rls_tables": [r[0] for r in tables if not r[1]]})
            roles = db.execute(text("""SELECT rolname,rolsuper,rolbypassrls,rolcreaterole
                FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator') ORDER BY rolname""")).all()
            emit("database_roles", {"roles": [dict(zip(
                ("name", "superuser", "bypass_rls", "create_role"), r)) for r in roles]})
            channels = db.execute(text("SELECT row_to_json(c) FROM qr_channels c ORDER BY id")).scalars().all()
            fields = {"id", "tenant_id", "provider", "enabled", "assistant_enabled", "lease_owner",
                "lease_expires_at", "revision", "fence", "created_at", "updated_at"}
            emit("channels", {"items": [{k: v for k, v in r.items() if k in fields} for r in channels]})
            columns = db.execute(text("""SELECT table_name,column_name FROM information_schema.columns
                WHERE table_schema='public' AND (table_name LIKE '%notification%'
                OR table_name LIKE '%sent_message%' OR table_name LIKE '%receipt%'
                OR table_name LIKE '%migration%') ORDER BY table_name,ordinal_position""")).all()
            emit("operational_schema", {"columns": [list(r) for r in columns]})
            if inaccessible:
                raise RuntimeError("database_inspection_incomplete")
    finally:
        engine.dispose()


def s3(prefix):
    import boto3
    from botocore.config import Config

    endpoint = os.environ[prefix + "ENDPOINT"]
    assert endpoint.startswith("https://")
    style = os.getenv(prefix + "URL_STYLE", "path")
    assert style in {"path", "virtual", "auto"}
    return boto3.client("s3", endpoint_url=endpoint,
        aws_access_key_id=os.environ[prefix + "ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ[prefix + "SECRET_ACCESS_KEY"],
        region_name=os.environ[prefix + "REGION"],
        config=Config(s3={"addressing_style": style}, connect_timeout=10,
            read_timeout=30, retries={"max_attempts": 2})), os.environ[prefix + "BUCKET"]


def objects(client, bucket):
    found = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for row in page.get("Contents", []):
            found[row["Key"]] = {"size": row["Size"], "modified": row["LastModified"].isoformat()}
    return found


def digest_object(client, bucket, key):
    stream = client.get_object(Bucket=bucket, Key=key)["Body"]
    checksum = hashlib.sha256()
    size = 0
    try:
        for chunk in stream.iter_chunks(chunk_size=1048576):
            checksum.update(chunk)
            size += len(chunk)
    finally:
        stream.close()
    return size, checksum.hexdigest()


def storage():
    invalid = False
    for kind in ("MEDIA", "BACKUP"):
        src, src_bucket = s3("MIGRATION_SOURCE_" + kind + "_")
        dst, dst_bucket = s3("ORION_" + kind + "_S3_")
        before, after = objects(src, src_bucket), objects(dst, dst_bucket)
        missing, mismatched, matched, excluded = [], [], 0, 0
        exclusion = os.getenv("MIGRATION_EXCLUDE_PREFIX", "")
        for key in sorted(before):
            if exclusion and key.startswith(exclusion):
                excluded += 1
                continue
            if key not in after:
                missing.append(key)
                continue
            if digest_object(src, src_bucket, key) != digest_object(dst, dst_bucket, key):
                mismatched.append(key)
            else:
                matched += 1
        emit("storage_" + kind.lower(), {"source_objects": len(before),
            "target_objects": len(after), "matched_by_sha256": matched,
            "excluded_objects": excluded,
            "missing": missing, "mismatched": mismatched,
            "same_bucket": src_bucket == dst_bucket,
            "recent_objects": sorted(after.items(), key=lambda r: r[1]["modified"])[-8:]})
        invalid = invalid or bool(missing or mismatched)
    if invalid:
        raise RuntimeError("storage_integrity_mismatch")


def safe_bridge_result(data):
    """Allow only known transport flags, counts, and status enums in logs."""
    result = {}
    states = {"starting", "connecting", "connected", "reconnecting", "degraded", "needs_attention",
        "logged_out", "pair_failed", "pairing", "pair_expired", "stopped", "error", "qr_ready",
        "qr_expired", "qr_delayed", "disconnecting"}
    if data.get("status") in states:
        result["status"] = data["status"]
    booleans = {"configured", "owned", "ready", "connected", "session_preserved", "messagesEnabled",
        "automaticRepliesEnabled"}
    numbers = {"disconnectCode", "waitingSeconds", "retainedMessages", "connectedSince", "lastInboundAt", "observed_at"}
    for key in booleans:
        if type(data.get(key)) is bool:
            result[key] = data[key]
    for key in numbers:
        if type(data.get(key)) in {int, float} or key in data and data[key] is None:
            result[key] = data[key]
    health = data.get("connection_health")
    if isinstance(health, dict):
        clean = {}
        if health.get("state") in states:
            clean["state"] = health["state"]
        for key in ("disconnect_code", "reconnect_attempts", "last_activity_at", "connected_at",
                    "interrupted_at", "recovered_at", "reconnect_at"):
            if type(health.get(key)) in {int, float} or key in health and health[key] is None:
                clean[key] = health[key]
        for key in ("sync_pending", "transport_observed", "ingress_storage_unavailable"):
            if type(health.get(key)) is bool:
                clean[key] = health[key]
        for key, allowed in (("reason", {"logged_out", "forbidden", "multidevice_mismatch", "connection_replaced",
                "session_recovery_exhausted", "reconnect_exhausted", "session_recovery", "restart_required",
                "network_timeout", "connection_closed", "provider_unavailable", "transport_interrupted",
                "ingress_storage_unavailable", "sync_stalled", "transport_unresponsive", "socket_not_open"}),
                ("action", {"reauthenticate", "retry_connection", "inspect_session_state", "inspect_session_ownership",
                "inspect_provider_restriction", "inspect_client_compatibility", "inspect_ingress_storage"})):
            if health.get(key) in allowed or health.get(key) is None:
                clean[key] = health.get(key)
        result["connection_health"] = clean
    counters = data.get("deliveryDiagnostics")
    if isinstance(counters, dict):
        result["deliveryDiagnostics"] = {k: v for k, v in counters.items()
            if k in {"prepared", "relayAccepted", "delivered", "read", "pending", "failed", "retries"}
            and type(v) is int and v >= 0}
    return result


def bridge():
    import httpx
    base = os.environ["QR_BRIDGE_URL"].rstrip("/")
    parsed = urlsplit(base)
    assert parsed.scheme == "http" and parsed.hostname.endswith(".railway.internal")
    assert parsed.port == 3000 and parsed.path == "" and not parsed.username and not parsed.query
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        for path in ("/internal/status", "/internal/legacy-central/status"):
            r = client.get(base + path, headers={"X-Orion-Bridge-Key": os.environ["QR_BRIDGE_TOKEN"]})
            if r.status_code != 200:
                emit("bridge", {"path": path, "http_status": r.status_code})
                continue
            data = r.json()
            emit("bridge", {"path": path, "http_status": r.status_code,
                "result": safe_bridge_result(data)})


def main():
    failed = []
    for name, function in (("bridge", bridge), ("database", database), ("storage", storage)):
        try:
            function()
        except Exception as exc:
            failed.append(name)
            emit(name, {"error_type": type(exc).__name__})
    emit("complete", {"failed_sections": failed})
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
