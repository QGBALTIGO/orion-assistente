"""One-time, fail-closed backup and 0033 commission migration.

This entrypoint is intended only for a short-lived Railway release job, never
for ordinary API/worker startup. It checks the reviewed origin, saves a private
S3 backup of the complete Postgres cluster database, verifies the object and
archive, and only then invokes the guarded single-revision migration.
No credentials, SQL rows or dump bytes are printed to logs.
"""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from botocore.config import Config
from sqlalchemy.engine import make_url

from scripts.apply_professional_commission_migration import (
    PREVIOUS, TARGET, main as apply_commission_migration, migrated_version,
)


def connection_values():
    raw = os.getenv("MIGRATION_DATABASE_URL", "")
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        url = make_url(raw)
    except Exception as exc:
        raise ValueError("migration_database_url_invalid") from exc
    expected_host = os.getenv("ORION_EXPECTED_DATABASE_HOST")
    expected_db = os.getenv("ORION_EXPECTED_DATABASE_NAME")
    if not expected_host or not expected_db:
        raise ValueError("migration_database_scope_missing")
    if (url.drivername != "postgresql+psycopg" or url.host != expected_host
            or url.database != expected_db or not url.username or not url.password):
        raise ValueError("migration_database_scope_mismatch")
    return raw, url


def storage_values():
    keys = {
        "endpoint": "ORION_BACKUP_S3_ENDPOINT",
        "bucket": "ORION_BACKUP_S3_BUCKET",
        "region": "ORION_BACKUP_S3_REGION",
        "access": "ORION_BACKUP_S3_ACCESS_KEY_ID",
        "secret": "ORION_BACKUP_S3_SECRET_ACCESS_KEY",
        "expected": "ORION_BACKUP_EXPECTED_BUCKET",
    }
    values = {key: os.getenv(value, "") for key, value in keys.items()}
    if not all(values.values()):
        raise ValueError("private_backup_storage_missing")
    if values["bucket"] != values["expected"]:
        raise ValueError("private_backup_bucket_mismatch")
    if not values["endpoint"].startswith("https://"):
        raise ValueError("private_backup_endpoint_must_be_https")
    if values["region"] not in {"iad", "sjc", "ams", "sin", "auto"}:
        raise ValueError("private_backup_region_invalid")
    values["style"] = os.getenv("ORION_BACKUP_S3_URL_STYLE", "path")
    if values["style"] not in {"path", "virtual"}:
        raise ValueError("private_backup_url_style_invalid")
    return values


def perform_backup(url, settings, destination: Path):
    environment = dict(os.environ)
    environment["PGPASSWORD"] = url.password
    # Explicit arguments do not contain passwords; credentials remain in env.
    commands = [
        "pg_dump", "--host", url.host, "--port", str(url.port or 5432),
        "--username", url.username, "--dbname", url.database,
        "--format=custom", "--no-owner", "--no-acl",
        "--file", str(destination), "--lock-wait-timeout=10000",
    ]
    # A failure aborts the migration; do not log stderr with internal host/rows.
    dump = subprocess.run(commands, env=environment, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=600, check=False)
    if dump.returncode != 0 or not destination.is_file() or destination.stat().st_size < 100:
        raise RuntimeError("private_database_dump_failed")
    verify = subprocess.run(["pg_restore", "--list", str(destination)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=60, check=False)
    if verify.returncode != 0:
        raise RuntimeError("private_database_backup_archive_invalid")

    sha = hashlib.sha256()
    with destination.open("rb") as stream:
        while block := stream.read(1048576):
            sha.update(block)
    size = destination.stat().st_size
    import boto3
    client = boto3.client("s3", endpoint_url=settings["endpoint"],
                          aws_access_key_id=settings["access"],
                          aws_secret_access_key=settings["secret"],
                          region_name=settings["region"],
                          config=Config(s3={"addressing_style": settings["style"]},
                                        connect_timeout=20, read_timeout=120, retries={"max_attempts":3}))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"production/commission-0033/pre-schema-0032-{stamp}-{uuid4().hex}.dump"
    client.upload_file(str(destination), settings["bucket"], key,
                       ExtraArgs={"Metadata": {"sha256": sha.hexdigest(),
                                               "schema": PREVIOUS}})
    head = client.head_object(Bucket=settings["bucket"], Key=key)
    if (int(head["ContentLength"]) != size
            or head.get("Metadata", {}).get("sha256") != sha.hexdigest()):
        raise RuntimeError("private_database_backup_remote_verification_failed")
    print("private_backup_archive_verified", flush=True)
    print("private_backup_object_key="+key, flush=True)
    return key


def main():
    try:
        raw, url = connection_values()
        current = migrated_version(raw)
        if current == TARGET:
            print("commission_schema_already_ready", flush=True)
            return 0
        if current != PREVIOUS:
            print("commission_migration_source_revision_not_approved", file=sys.stderr)
            return 2
        settings = storage_values()
        with tempfile.TemporaryDirectory(prefix="orion-commission-release-") as folder:
            archive = Path(folder) / "backup.dump"
            perform_backup(url, settings, archive)
        # This runner will never attempt the migration if private backup
        # integrity has not been verified and uploaded successfully.
        outcome = apply_commission_migration()
        if outcome:
            return outcome
        if migrated_version(raw) != TARGET:
            print("commission_schema_not_verified", file=sys.stderr)
            return 3
        print("commission_migration_0033_published_after_backup", flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        # Print a predefined safe marker, never internal errors/credentials.
        print("commission_release_preflight_or_backup_failed", file=sys.stderr)
        return 3
    except Exception:
        print("commission_release_unexpected_failure", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
