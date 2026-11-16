"""Run WhatsApp integration checks on newly-created loopback services only.

Uses pre-existing PostgreSQL/Redis executables, never a configured application
database. External messaging, model calls and provider connections are excluded.
The Redis workload exercises the production ingress Lua, not WhatsApp sockets.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def clean_environment() -> dict[str, str]:
    # Preserve OS/runtime discovery only; in particular never inherit production
    # databases, bridge credentials, provider tokens or application .env values.
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
               "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return result


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * quantile))], 3)


async def redis_workload(url: str, sizes: list[int]) -> list[dict]:
    """Measure actual Lua atomicity/isolation; no inferred provider capacity."""
    from redis.asyncio import from_url
    from app import qr_intake_revision as ingress
    from app.qr_channels import ChannelIdentity

    redis = from_url(url, socket_connect_timeout=3, socket_timeout=5, max_connections=128)
    results = []
    try:
        for tenant_count in sizes:
            latencies: list[float] = []
            identities = [ChannelIdentity(uuid4(), tenant, uuid4(), "baileys", 1, uuid4(), True)
                          for tenant in [uuid4() for _ in range(tenant_count)] for _ in range(2)]
            semaphore = asyncio.Semaphore(64)
            phone = "5567999999901"  # Synthetic; no provider is used.
            namespace_keys: list[str] = []

            async def conversation(identity, index):
                async with semaphore:
                    own = ingress.channel_keys(identity, phone)
                    namespace_keys.extend(own)
                    # Identical IDs across companies/numbers deliberately test
                    # that deduplication is scoped to the authenticated channel.
                    for number in range(6):
                        before = time.perf_counter()
                        values = await asyncio.gather(*(
                            ingress.register_channel(redis, identity, phone, f"shared-message-{number}")
                            for _ in range(2)))
                        latencies.append((time.perf_counter() - before) * 1000)
                        assert values == [number + 1, number + 1], values
                    last = f"exclusive-{identity.channel_id}"
                    assert await ingress.register_channel(redis, identity, phone, last) == 7
                    assert await redis.eval(ingress.VALIDATE_SCRIPT, 2, *own[:2], 7, last) == 1
                    foreign = identities[(index + 1) % len(identities)]
                    foreign_keys = ingress.channel_keys(foreign, phone)
                    assert own != foreign_keys
                    assert await redis.eval(ingress.VALIDATE_SCRIPT, 2, *foreign_keys[:2], 7, last) == 0
                    assert await redis.eval(ingress.VALIDATE_SCRIPT, 2, *own[:2], 6, "shared-message-5") == 0
                    assert 0 < await redis.ttl(own[0]) <= ingress.TTL

            started = time.perf_counter()
            try:
                outcomes = await asyncio.gather(*(conversation(identity, index)
                    for index, identity in enumerate(identities)), return_exceptions=True)
                failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
                if failures:
                    raise failures[0]
                duration = time.perf_counter() - started
                results.append({
                    "tenant_namespaces": tenant_count,
                    "channels_per_tenant": 2,
                    "logical_conversations": len(identities),
                    "unique_ingress_events": len(identities) * 7,
                    "duplicate_attempts": len(identities) * 6,
                    "concurrency_limit": 64,
                    "elapsed_seconds": round(duration, 3),
                    "unique_events_per_second": round(len(identities) * 7 / duration, 2),
                    "duplicate_pair_roundtrip_ms": {
                        "p50": percentile(latencies, .5), "p95": percentile(latencies, .95),
                        "p99": percentile(latencies, .99), "max": round(max(latencies), 3),
                    },
                    "assertions": ["atomic_duplicate_claim", "same_contact_separate_tenants_and_channels",
                                   "foreign_event_cannot_authorize_turn", "superseded_turn_rejected", "bounded_ttl"],
                    "actual_whatsapp_connections": 0,
                    "real_provider_delivery_verified": False,
                    "database_queue_or_ai_capacity_measured": False,
                })
            finally:
                # Remove only names created by this run, even on failed checks.
                for offset in range(0, len(namespace_keys), 256):
                    await redis.delete(*namespace_keys[offset:offset + 256])
    finally:
        await redis.aclose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    parser.add_argument("--redis-server", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-services", action="store_true",
                        help="Retain only this run's isolated services for another local verification")
    parser.add_argument("--test-timeout", type=int, default=3600,
                        help="Overall timeout for the selected real integration tests (seconds)")
    parser.add_argument("--load-tenants", type=int, nargs="*", default=[10, 100, 1000])
    parser.add_argument("--tests", nargs="*", default=[
        "tests/integration/test_qr_channels_postgres.py",
        "tests/integration/test_qr_channels_transaction_postgres.py",
        "tests/integration/test_qr_intake_revision_redis.py",
        "tests/integration/test_qr_single_connection_postgres.py",
        "tests/integration/test_qr_adopted_ingress_postgres.py",
        "tests/integration/test_subscription_devices_postgres.py",
        "tests/integration/test_subscription_chatbot_postgres.py",
        "tests/integration/test_subscription_chatbot_archive_postgres.py",
        "tests/integration/test_subscription_bulk_messages_postgres.py",
        "tests/integration/test_subscription_campaigns_postgres.py",
        "tests/integration/test_subscription_direct_messages_postgres.py",
        "tests/integration/test_subscription_scheduled_messages_postgres.py",
        "tests/integration/test_subscription_sent_messages_postgres.py",
        "tests/qualification/whatsapp_reliability_postgres.py",
    ])
    args = parser.parse_args()
    if any(count < 1 or count > 10000 for count in args.load_tenants):
        parser.error("load sizes must be between 1 and 10000 logical tenant namespaces")
    if not 30 <= args.test_timeout <= 7200:
        parser.error("test timeout must be between 30 and 7200 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="whatsapp-ephemeral-", dir=output))
    pgdata = work / "pg-data"
    redisdata = work / "redis-data"
    redisdata.mkdir()
    pgport, redisport = free_port(), free_port()
    while pgport == redisport:
        redisport = free_port()
    environment = clean_environment()
    environment.update({
        "APP_DATABASE_URL": f"postgresql+asyncpg://app_runtime:synthetic-runtime@127.0.0.1:{pgport}/whatsapp_qualification",
        "MIGRATION_DATABASE_URL": f"postgresql+psycopg://app_migrator:synthetic-migrator@127.0.0.1:{pgport}/whatsapp_qualification",
        "SUPPORT_DATABASE_URL": f"postgresql+asyncpg://app_support:synthetic-support@127.0.0.1:{pgport}/whatsapp_qualification",
        "REDIS_URL": f"redis://127.0.0.1:{redisport}/15",
        "ORION_INTAKE_TEST_REDIS_URL": f"redis://127.0.0.1:{redisport}/15",
        "REQUIRE_POSTGRES_TESTS": "1", "REQUIRE_REDIS_TESTS": "1", "ORION_REQUIRE_INTAKE_REDIS_TEST": "1",
        "SUPPORT_API_TOKEN": "synthetic-whatsapp-qualification-support-" + "x" * 40,
        "PATH": str(args.postgres_bin.resolve()) + os.pathsep + environment.get("PATH", ""),
    })

    def run(name: str, command: list[str], *, timeout=300, check=True) -> int:
        with (work / f"{name}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    creationflags=CREATE_NO_WINDOW)
        if check and result.returncode:
            raise RuntimeError(f"{name} failed ({result.returncode}); see {work / (name + '.log')}")
        return result.returncode

    pg = lambda name: str(args.postgres_bin.resolve() / (name + (".exe" if os.name == "nt" else "")))
    pg_started, redis_process = False, None
    summary = {"source": str(ROOT), "kind": "isolated_loopback_integration_and_real_redis_lua",
               "work_directory": str(work), "external_messages_sent": 0,
               "real_whatsapp_delivery_verified": False, "steps": {},
               "started_at_utc": datetime.now(timezone.utc).isoformat(),
               "python": sys.version, "platform": platform.platform()}
    try:
        run("initdb", [pg("initdb"), "-D", str(pgdata), "-U", "postgres", "-A", "trust",
                       "--encoding=UTF8", "--locale=C"], timeout=60)
        run("postgres-start", [pg("pg_ctl"), "-D", str(pgdata), "-l", str(work / "postgres.log"),
                               "-o", f"-h 127.0.0.1 -p {pgport}", "-w", "-t", "45", "start"], timeout=60)
        pg_started = True
        with (work / "redis.log").open("w", encoding="utf-8") as redis_log:
            redis_process = subprocess.Popen([str(args.redis_server.resolve()), "--bind", "127.0.0.1",
                "--port", str(redisport), "--databases", "16", "--appendonly", "no", "--save", "",
                "--dir", str(redisdata).replace("\\", "/")], cwd=args.redis_server.parent,
                env=environment, stdout=redis_log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        import redis
        cache = redis.from_url(environment["REDIS_URL"], socket_connect_timeout=1, socket_timeout=1)
        deadline = time.monotonic() + 15
        while True:
            try:
                cache.ping()
                break
            except redis.RedisError:
                if redis_process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"Ephemeral Redis did not start; see {work / 'redis.log'}") from None
                time.sleep(.1)
        cache.close()
        summary["test_services"] = {"postgres_port": pgport, "postgres_data": str(pgdata),
            "redis_port": redisport, "redis_process_id": redis_process.pid,
            "environment": {name: environment[name] for name in (
                "APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL", "REDIS_URL",
                "ORION_INTAKE_TEST_REDIS_URL", "REQUIRE_POSTGRES_TESTS", "REQUIRE_REDIS_TESTS",
                "ORION_REQUIRE_INTAKE_REDIS_TEST")}}
        (work / "services.json").write_text(json.dumps(summary["test_services"], indent=2), encoding="utf-8")
        base = [pg("psql"), "-X", "-h", "127.0.0.1", "-p", str(pgport), "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
        run("database-create", [*base, "-d", "postgres", "-c", "CREATE DATABASE whatsapp_qualification"])
        run("roles", [*base, "-d", "whatsapp_qualification", "-v", "db_name=whatsapp_qualification",
                      "-v", "runtime_password=synthetic-runtime", "-v", "migrator_password=synthetic-migrator",
                      "-v", "support_password=synthetic-support", "-f", str(ROOT / "infra/postgres/init/001-roles.sql")])
        run("migrations", [sys.executable, "-m", "alembic", "upgrade", "head"], timeout=300)
        summary["steps"]["migrations"] = "passed"
        if args.tests:
            code = run("integration", [sys.executable, "-m", "pytest", *args.tests, "-q", "-ra",
                       "--disable-warnings", "-o", f"cache_dir={work / 'pytest-cache'}",
                       f"--junitxml={work / 'integration.xml'}"], timeout=args.test_timeout, check=False)
            summary["steps"]["integration_exit_code"] = code
            if (work / "integration.xml").is_file():
                summary["integration_suites"] = [node.attrib for node in
                    ET.parse(work / "integration.xml").getroot().iter("testsuite")]
        # Import app only after the isolated environment is configured. Nothing
        # in this process receives the caller's production environment.
        previous = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(environment)
            sys.path.insert(0, str(ROOT))
            summary["redis_ingress_workloads"] = asyncio.run(redis_workload(environment["REDIS_URL"], args.load_tenants))
        finally:
            os.environ.clear()
            os.environ.update(previous)
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        keep = (args.keep_services and pg_started and redis_process is not None
                and redis_process.poll() is None)
        summary["isolated_services_retained"] = keep
        if redis_process is not None and redis_process.poll() is None and not keep:
            redis_process.terminate()
            try:
                redis_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                redis_process.kill()
                redis_process.wait(timeout=10)
        if pg_started and not keep:
            try:
                summary["steps"]["postgres_stop_exit_code"] = run("postgres-stop", [pg("pg_ctl"), "-D",
                    str(pgdata), "-m", "fast", "-w", "-t", "30", "stop"], timeout=45, check=False)
            except Exception as error:
                summary["stop_error"] = str(error)
        target = output / "summary.json"
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(summary.get("error") or summary.get("stop_error") or
                    summary["steps"].get("integration_exit_code", 0) or
                    summary["steps"].get("postgres_stop_exit_code", 0)))


if __name__ == "__main__":
    raise SystemExit(main())
