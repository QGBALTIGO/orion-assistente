"""Finite, synthetic HTTP/PG/Redis qualification. Never accepts an API target URL.

Creates a random disposable local database and owns the API processes it loads.
Importing this module has no side effects. Credentials never enter the report.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import redis.asyncio as redis
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
LEVELS = (10, 100, 500, 1000, 1500)
TARGETS = {"read": {"p95_ms": 800, "p99_ms": 1500},
           "write": {"p95_ms": 2000, "p99_ms": 4000}, "unexpected_error_rate": .001}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ROLE_ENV = {"APP_DATABASE_URL": "app_runtime", "MIGRATION_DATABASE_URL": "app_migrator",
            "SUPPORT_DATABASE_URL": "app_support"}


def local_url(value, *, role=None, database=None, redis_url=False):
    url = make_url(value)
    valid_scheme = url.drivername == "redis" if redis_url else url.drivername in {
        "postgresql", "postgresql+psycopg", "postgresql+asyncpg"}
    if (not valid_scheme or url.host not in LOOPBACK or url.query
            or role and url.username != role or database and url.database != database):
        raise ValueError("Disposable loopback service contract rejected")
    return url


def clean_environment():
    # Do not inherit provider credentials, service URLs, feature flags or proxies.
    return {key: value for key, value in os.environ.items() if key in {
        "PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL",
        "LD_LIBRARY_PATH", "VIRTUAL_ENV", "PYTHONUTF8"}}


class DisposableDatabase:
    """Only this object's random, newly created database can be removed."""
    def __init__(self, owner_url, role_urls):
        self.owner = local_url(owner_url, role="postgres", database="postgres").set(drivername="postgresql+psycopg")
        self.roles = {key: local_url(role_urls[key], role=role) for key, role in ROLE_ENV.items()}
        for url in self.roles.values():
            if (url.host, url.port or 5432) != (self.owner.host, self.owner.port or 5432):
                raise ValueError("All roles must use the same disposable PostgreSQL server")
        self.name = "orion_qualification_" + uuid4().hex
        self.oid = None
        options = {"connect_timeout": 5, "options": "-c statement_timeout=15000"}
        self.admin = create_engine(self.owner, poolclass=NullPool, connect_args=options)
        self.engine = create_engine(self.owner.set(database=self.name), poolclass=NullPool, connect_args=options)

    def __enter__(self):
        with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
            if not str(db.exec_driver_sql("SHOW server_version_num").scalar_one()).startswith("16"):
                raise ValueError("PostgreSQL 16 required for the declared qualification")
            db.exec_driver_sql(f'CREATE DATABASE "{self.name}" TEMPLATE template0')
            self.oid = db.execute(text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": self.name}).scalar_one()
            db.exec_driver_sql(f'REVOKE CONNECT ON DATABASE "{self.name}" FROM PUBLIC')
            db.exec_driver_sql(f'GRANT CONNECT,CREATE ON DATABASE "{self.name}" TO app_migrator')
            db.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{self.name}" TO app_runtime,app_support')
        with self.engine.begin() as db:
            db.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            db.exec_driver_sql("GRANT USAGE ON SCHEMA public TO app_runtime,app_support,app_migrator")
            db.exec_driver_sql("GRANT CREATE ON SCHEMA public TO app_migrator")
        return self

    def environment(self):
        env = clean_environment()
        for key, url in self.roles.items():
            driver = "postgresql+psycopg" if key == "MIGRATION_DATABASE_URL" else "postgresql+asyncpg"
            env[key] = url.set(drivername=driver, database=self.name).render_as_string(hide_password=False)
        return env

    def migrate(self, revision="head"):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=ROOT, env=self.environment(), capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError("Synthetic Alembic upgrade failed (no credential-bearing output copied)")

    def __exit__(self, *_):
        self.engine.dispose()
        try:
            if self.oid is not None:
                with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
                    row = db.execute(text("SELECT oid,pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname=:name"), {"name": self.name}).one()
                    if row.oid != self.oid or row.owner != "postgres" or not re.fullmatch(r"orion_qualification_[0-9a-f]{32}", self.name):
                        raise RuntimeError("Disposable database identity changed; refusing cleanup")
                    # Never FORCE, terminate other sessions, or remove an existing database.
                    db.exec_driver_sql(f'DROP DATABASE "{self.name}"')
        finally:
            self.admin.dispose()


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * p) - 1)], 3)


class Measurements:
    def __init__(self):
        self.latencies = defaultdict(list); self.statuses = defaultdict(Counter)
        self.failures = Counter(); self.offered = Counter(); self.dropped = Counter()
        self.violations = Counter(); self.committed = set(); self.verified_cancellations = set()

    def record(self, stage, operation, elapsed, status, expected):
        key = (stage, operation)
        self.latencies[key].append(elapsed * 1000)
        self.statuses[key][str(status)] += 1
        if status not in expected: self.failures[f"{stage}/{operation}/{status}"] += 1

    def report(self, durations):
        groups = []
        for (stage, operation), values in sorted(self.latencies.items()):
            kind = "write" if operation.startswith(("hold", "confirm", "cancel", "setup_save")) else "read"
            p95, p99 = percentile(values, .95), percentile(values, .99)
            target = TARGETS[kind]
            groups.append({"stage_users": stage, "operation": operation, "kind": kind, "count": len(values),
                "p95_ms": p95, "p99_ms": p99, "maximum_ms": round(max(values), 3),
                "statuses": dict(self.statuses[(stage, operation)]),
                "observed_http_rps": round(len(values) / durations[stage], 3),
                "latency_target_pass": p95 <= target["p95_ms"] and p99 <= target["p99_ms"]})
        total = sum(len(v) for v in self.latencies.values())
        errors = sum(self.failures.values()) + sum(self.dropped.values())
        denominator = total + sum(self.dropped.values())
        rate = errors / max(1, denominator)
        return {"operations": groups, "offered_journeys": dict(self.offered), "dropped_arrivals": dict(self.dropped),
            "unexpected": dict(self.failures), "unexpected_error_rate": rate,
            "http_requests": total, "violations": dict(self.violations),
            "pass": bool(total) and not self.violations and rate < TARGETS["unexpected_error_rate"]
                    and all(g["latency_target_pass"] for g in groups)}


def distribute(index, companies, wide=False):
    # Every company is represented; then 40% of the additional users concentrate
    # on five companies. Wide distribution has 1–2 operators per company.
    if index < companies or wide: return index % companies
    return index % min(5, companies) if index % 5 < 2 else index % companies


def seed_database(sandbox, users, companies, wide=False, *, simulated_channels=False):
    tenants = [uuid4() for _ in range(companies)]
    services = [uuid4() for _ in tenants]
    variants = [uuid4() for _ in tenants]
    day = (datetime.now(timezone.utc) + timedelta(days=7)).date()
    people = []
    with sandbox.engine.begin() as db:
        for n, tenant in enumerate(tenants):
            db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:id,:name,:slug,'UTC')"),
                {"id": tenant, "name": f"Synthetic qualification company {n}", "slug": "qualification-" + tenant.hex})
            db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:tenant,'Synthetic appointment',30,3500)"),
                {"id": services[n], "tenant": tenant})
            product = uuid4()
            db.execute(text("INSERT INTO commercial_products(id,tenant_id,name,active) VALUES(:id,:t,'Synthetic product',true)"), {"id": product,"t":tenant})
            db.execute(text("""INSERT INTO commercial_variants(id,tenant_id,product_id,name,price_kind,unit_price_cents,active)
                VALUES(:id,:t,:p,'Synthetic variant','fixed',2500,true)"""), {"id":variants[n],"t":tenant,"p":product})
            db.execute(text("INSERT INTO commercial_inventory(tenant_id,variant_id) VALUES(:t,:v)"), {"t":tenant,"v":variants[n]})
        for n in range(users):
            company = distribute(n, companies, wide); tenant = tenants[company]
            account, professional = uuid4(), uuid4()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,verified_at)
                VALUES(:id,'Synthetic operator',:email,'not-a-login-password',now())"""),
                {"id": account, "email": f"qualification-{account.hex}@example.test"})
            db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(:tenant,:account,'owner',true)"), {"tenant": tenant, "account": account})
            db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Synthetic professional')"), {"id": professional, "tenant": tenant})
            db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:tenant,:pro,:service)"), {"tenant": tenant, "pro": professional, "service": services[company]})
            db.execute(text("""INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time)
                VALUES(:tenant,:pro,:weekday,'08:00','18:00')"""), {"tenant": tenant, "pro": professional, "weekday": day.weekday()})
            people.append({"tenant": str(tenant), "other": str(tenants[(company + 1) % companies]),
                "service": str(services[company]), "variant": str(variants[company]), "professional": str(professional), "account": str(account),
                "token": token, "csrf": csrf, "phone": "+55679" + f"{n:08d}", "day": day.isoformat()})
        # A populated operational history, not just empty catalog tables.
        cipher = Fernet(Fernet.generate_key())
        for n, tenant in enumerate(tenants):
            person = people[n]; customer = uuid4()
            if simulated_channels:
                for _ in range(2):
                    credential = secrets.token_urlsafe(32)
                    db.execute(text("""INSERT INTO qr_channels(tenant_id,owner_account_id,provider,label,credential_hash,
                        credential_encrypted,allowed_senders,idempotency_key,request_fingerprint)
                        VALUES(:t,:a,'baileys','Synthetic disabled registry',:digest,:encrypted,CAST(:senders AS jsonb),:key,:fp)"""),
                        {"t":tenant,"a":person["account"],"digest":hashlib.sha256(credential.encode()).hexdigest(),
                         "encrypted":cipher.encrypt(credential.encode()).decode(),"senders":json.dumps([person["phone"].removeprefix("+")]),
                         "key":uuid4(),"fp":"0"*64})
            db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164) VALUES(:id,:t,'Synthetic historic customer',:phone)"),
                {"id": customer, "t": tenant, "phone": "+55678" + f"{n:08d}"})
            for j in range(10):
                start = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=j+1)
                appointment = uuid4()
                db.execute(text("""INSERT INTO appointments(id,tenant_id,professional_id,customer_id,protocol,idempotency_key,request_fingerprint,
                    start_at,end_at,occupied_from,occupied_until,status,total_cents)
                    VALUES(:id,:t,:p,:c,:protocol,:key,:fp,:start,:end,:start,:end,'completed',3500)"""),
                    {"id": appointment, "t": tenant, "p": person["professional"], "c": customer,
                     "protocol": f"Q-{n:04}-{j:02}", "key": "qualification-history-"+appointment.hex,
                     "fp": "0"*64, "start": start, "end": start+timedelta(minutes=30)})
                db.execute(text("""INSERT INTO appointment_items(tenant_id,appointment_id,service_id,name_snapshot,duration_snapshot_min,
                    price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min,item_order)
                    VALUES(:t,:a,:s,'Synthetic appointment',30,3500,0,0,0)"""), {"t": tenant, "a": appointment, "s": services[n]})
                db.execute(text("INSERT INTO appointment_history(tenant_id,appointment_id,event,operator) VALUES(:t,:a,'confirmed','system')"), {"t": tenant, "a": appointment})
    return people, {"companies": companies, "operators": users, "professionals": users,
                    "services": companies, "products": companies, "historical_appointments": companies*10,
                    "simulated_disabled_channel_records":companies*2 if simulated_channels else 0,
                    "actual_whatsapp_sessions": 0, "audio_percent": 0, "external_provider_requests": 0}


async def request(client, metrics, stage, person, operation, method, path, *, body=None, expected=(200,), due=None):
    started = time.monotonic() if due is None else due
    status = "transport_error"; data = None
    try:
        response = await client.request(method, path, json=body,
            headers={"Cookie": "__Host-orion_session="+person["token"], "x-orion-csrf": person["csrf"]})
        status = response.status_code
        if response.headers.get("content-type", "").startswith("application/json"): data = response.json()
    except (httpx.HTTPError, ValueError):
        pass
    metrics.record(stage, operation, time.monotonic()-started, status, expected)
    return status, data


async def booking_journey(client, m, stage, p, key, *, due=None, conflict=False):
    base = "/api/v1/tenants/" + p["tenant"]
    payload = {"professional_id": p["professional"], "service_ids": [p["service"]],
        "customer_name": "Synthetic qualification customer", "customer_phone": p["phone"],
        "start_at": p["day"]+"T09:00:00+00:00", "idempotency_key": key}
    status, result = await request(client, m, stage, p, "hold", "POST", base+"/appointments/hold", body=payload,
                                   expected=(201, 409) if conflict else (201,), due=due)
    if status != 201: return
    appointment = result["id"]; m.committed.add(appointment)
    status, duplicate = await request(client, m, stage, p, "hold_replay", "POST", base+"/appointments/hold", body=payload, expected=(201,))
    if status == 201 and duplicate.get("id") != appointment: m.violations["duplicate_hold"] += 1
    status, confirmed = await request(client, m, stage, p, "confirm", "POST", base+f"/appointments/{appointment}/confirm", body={"idempotency_key": key+"-confirm"})
    if status == 200 and confirmed.get("status") != "confirmed": m.violations["false_confirmation"] += 1
    status, cancelled = await request(client, m, stage, p, "cancel", "POST", base+f"/appointments/{appointment}/cancel", body={"idempotency_key": key+"-cancel", "requested_by": "customer"})
    if status == 200:
        if cancelled.get("status") != "cancelled": m.violations["false_cancellation"] += 1
        else: m.verified_cancellations.add(appointment)


async def journey(client, m, stage, p, seq, index, due):
    base = "/api/v1/tenants/"+p["tenant"]
    choice = (seq*7+index) % 20
    if choice < 2:
        await request(client, m, stage, p, "product_catalog", "GET", base+"/commerce/products", due=due)
    elif choice < 6:
        await request(client, m, stage, p, "catalog", "GET", base+"/services", due=due)
    elif choice < 10:
        await request(client, m, stage, p, "availability", "GET", base+f'/availability?professional_id={p["professional"]}&day={p["day"]}&service_ids={p["service"]}', due=due)
    elif choice < 13:
        await request(client, m, stage, p, "appointments", "GET", base+f'/appointments?day={p["day"]}&paged=true&limit=20', due=due)
    elif choice < 17:
        status, state = await request(client, m, stage, p, "setup_read", "GET", base+"/onboarding", due=due)
        if choice >= 15 and status == 200:
            body = {"expected_version": state["version"], "idempotency_key": f"qualification-{index}-{stage}-{seq}", "data": {"modality": "online"}}
            status, updated = await request(client, m, stage, p, "setup_save", "PUT", base+"/onboarding/steps/location", body=body, expected=(200,409))
            if status == 200:
                status, replay = await request(client, m, stage, p, "setup_save_replay", "PUT", base+"/onboarding/steps/location", body=body)
                # Other operators may legitimately advance the visible version.
                if status == 200 and replay["version"] < updated["version"]: m.violations["setup_version_regression"] += 1
    else:
        await booking_journey(client, m, stage, p, f"qualification-{index}-{stage}-{seq}", due=due)


async def run_stage(client, metrics, people, active, duration, period, seed, max_pending):
    rng = random.Random(seed + active); start = time.monotonic(); stop = start+duration
    arrivals = [(start+rng.random()*period, i, 0) for i in range(active)]
    heapq.heapify(arrivals); pending = set(); busy = set()
    async def run(index, seq, due):
        try: await journey(client, metrics, active, people[index], seq, index, due)
        except Exception as exc: metrics.failures[f"{active}/journey/{type(exc).__name__}"] += 1
        finally: busy.discard(index)
    try:
        while arrivals:
            due, index, seq = heapq.heappop(arrivals)
            if due >= stop: break
            await asyncio.sleep(max(0, due-time.monotonic()))
            metrics.offered[active] += 1
            if index in busy or len(pending) >= max_pending:
                metrics.dropped[active] += 1
            else:
                busy.add(index); task = asyncio.create_task(run(index, seq, due)); pending.add(task)
                task.add_done_callback(pending.discard)
            heapq.heappush(arrivals, (due+period, index, seq+1))
        await asyncio.sleep(max(0, stop-time.monotonic()))
        if pending: await asyncio.gather(*pending)
    finally:
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending,return_exceptions=True)
    return time.monotonic()-start


def invariants(sandbox, metrics, expected):
    with sandbox.engine.connect() as db:
        checks = {
            "overlapping_live_appointments": """SELECT count(*) FROM appointments a JOIN appointments b ON a.tenant_id=b.tenant_id
                AND a.professional_id=b.professional_id AND a.id<b.id AND a.status IN ('hold','confirmed') AND b.status IN ('hold','confirmed')
                AND tstzrange(a.occupied_from,a.occupied_until,'[)') && tstzrange(b.occupied_from,b.occupied_until,'[)')""",
            "duplicate_idempotency_keys": "SELECT count(*) FROM (SELECT tenant_id,idempotency_key FROM appointments GROUP BY 1,2 HAVING count(*)>1) x",
            "cross_tenant_customers": "SELECT count(*) FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.tenant_id<>c.tenant_id",
            "cross_tenant_professionals": "SELECT count(*) FROM appointments a JOIN professionals p ON p.id=a.professional_id WHERE a.tenant_id<>p.tenant_id",
            "unexpected_live_appointments": "SELECT count(*) FROM appointments WHERE status IN ('hold','confirmed')",
            "unexpected_delivery_jobs": "SELECT count(*) FROM automation_jobs",
        }
        result = {name: db.exec_driver_sql(sql).scalar_one() for name, sql in checks.items()}
        result["historical_rows_lost"] = abs(db.exec_driver_sql("SELECT count(*) FROM appointments WHERE status='completed'").scalar_one()-expected["historical_appointments"])
        result["unexpected_new_appointments"] = abs(db.exec_driver_sql("SELECT count(*) FROM appointments WHERE status<>'completed'").scalar_one()-len(metrics.committed))
        result["unverified_cancellations"] = len(metrics.committed-metrics.verified_cancellations)
        result["unsafe_runtime_role"] = db.exec_driver_sql("SELECT count(*) FROM pg_roles WHERE rolname IN ('app_runtime','app_support','app_migrator') AND (rolsuper OR rolbypassrls)").scalar_one()
        for name, count in result.items():
            if count: metrics.violations[name] += count
        return result


async def contention(client, people, seed):
    # Independent report for each seed. Shared real slot across twenty sessions.
    p = people[0]; rng = random.Random(seed); m = Measurements()
    challengers = [dict(p, phone="+55677"+f"{seed*100+i:08d}") for i in range(20)]
    rng.shuffle(challengers)
    # Keep the winner's hold until every competing request has completed.
    base = "/api/v1/tenants/"+p["tenant"]
    async def hold(i, candidate):
        return await request(client, m, 20, candidate, "hold", "POST", base+"/appointments/hold", expected=(201,409),
            body={"professional_id": p["professional"], "service_ids": [p["service"]], "customer_name": "Synthetic contender",
                  "customer_phone": candidate["phone"], "start_at": p["day"]+"T11:00:00+00:00", "idempotency_key": f"qualification-race-{seed}-{i}"})
    results = await asyncio.gather(*(hold(i, c) for i, c in enumerate(challengers)))
    winners = [result for status, result in results if status == 201]
    if len(winners) != 1: m.violations["contention_winner_count"] += 1
    for winner in winners:
        m.committed.add(winner["id"])
        status, result = await request(client, m, 20, p, "cancel", "POST", base+f'/appointments/{winner["id"]}/cancel', body={"idempotency_key": f"qualification-race-{seed}-cancel"})
        if status == 200 and result.get("status") == "cancelled": m.verified_cancellations.add(winner["id"])
        else: m.violations["contention_cleanup"] += 1
    return m, {"seed": seed, "attempts": 20, "winners": len(winners), "expected_conflicts": sum(status==409 for status,_ in results),
               "unexpected": dict(m.failures), "violations": dict(m.violations), "pass": not m.failures and not m.violations}


async def isolation_preflight(client, people):
    metrics = Measurements(); slots = asyncio.Semaphore(20)
    async def inspect(p):
        async with slots:
            for tenant, expected in ((p["tenant"],200),(p["other"],403)):
                status,_ = await request(client,metrics,1500,p,"isolation_probe","GET",f"/api/v1/tenants/{tenant}/services",expected=(expected,))
                if status != expected: metrics.violations["tenant_isolation_or_authentication"] += 1
    await asyncio.gather(*(inspect(p) for p in people))
    return {"synthetic_sessions_checked":len(people),"companies_checked":len({p["tenant"] for p in people}),
        "own_expected_200":len(people),"foreign_expected_403":len(people),"unexpected":dict(metrics.failures),
        "violations":dict(metrics.violations),"pass":not metrics.failures and not metrics.violations}


def process_metrics(parent_pid):
    if not Path("/proc").is_dir(): return {"available": False}
    rows = {}
    for path in Path("/proc").glob("[0-9]*"):
        try:
            fields = (path/"stat").read_text().rsplit(")",1)[1].split()
            rows[int(path.name)] = (int(fields[1]), int(fields[11])+int(fields[12]), int(fields[21]), path)
        except (OSError, ValueError, IndexError): continue
    selected = {parent_pid}
    for _ in range(3): selected.update(pid for pid, row in rows.items() if row[0] in selected)
    records = [rows[pid] for pid in selected if pid in rows]
    return {"available": True, "processes": len(records), "cpu_seconds": sum(r[1] for r in records)/os.sysconf("SC_CLK_TCK"),
        "rss_bytes": sum(r[2] for r in records)*os.sysconf("SC_PAGE_SIZE"),
        "open_fds": sum(len(list((r[3]/"fd").iterdir())) for r in records)}


async def monitor(sandbox, cache, pid, samples, stop, max_memory_mb):
    previous = None
    while not stop.is_set():
        def database():
            with sandbox.engine.connect() as db:
                row = dict(db.execute(text("""SELECT count(*) AS connections,
                    count(*) FILTER(WHERE wait_event_type='Lock') AS lock_waiters
                    FROM pg_stat_activity WHERE datname=:name"""), {"name": sandbox.name}).mappings().one())
                row["calendar_pending"] = db.exec_driver_sql("SELECT count(*) FROM calendar_outbox WHERE status='pending'").scalar_one()
                return row
        row = await asyncio.to_thread(database); row.update(process_metrics(pid)); row["at_monotonic"] = time.monotonic()
        info = await cache.info("memory"); row["redis_used_memory"] = info["used_memory"]
        row["disk_free_bytes"] = shutil.disk_usage(ROOT).free
        if previous and row.get("available"):
            row["api_cpu_percent_one_core"] = max(0, (row["cpu_seconds"]-previous["cpu_seconds"])/(row["at_monotonic"]-previous["at_monotonic"])*100)
        samples.append(row); previous = row
        if row.get("rss_bytes",0) > max_memory_mb*1024**2 or row["connections"] > 80 or row["disk_free_bytes"] < 512*1024**2:
            raise RuntimeError("Qualification resource stop: memory, connections or free disk")
        try: await asyncio.wait_for(stop.wait(), 5)
        except asyncio.TimeoutError: pass


async def execute(args, sandbox, report):
    cache_url = local_url(os.environ["REDIS_URL"], database="15", redis_url=True)
    cache = redis.from_url(cache_url.render_as_string(hide_password=False), decode_responses=True)
    lock = "orion:qualification:exclusive"; owner = secrets.token_urlsafe(32); keys = []; api = None; monitor_task = None
    stop = asyncio.Event(); report["resource_samples"] = []
    try:
        if await cache.dbsize() != 0 or not await cache.set(lock, owner, nx=True, ex=7200):
            raise RuntimeError("Qualification requires an empty, dedicated Redis database 15")
        keys.append(lock)
        report["phase"] = "synthetic_fixture"
        people, inventory = await asyncio.to_thread(seed_database, sandbox, 1500, args.companies, args.profile=="wide",simulated_channels=True)
        report["fixture"] = inventory
        pipe = cache.pipeline(transaction=False)
        for p in people:
            key = "orion:session:"+hashlib.sha256(p["token"].encode()).hexdigest(); keys.append(key)
            pipe.set(key, json.dumps({"role": "member", "account_id": p["account"], "auth_version": 1, "csrf": p["csrf"]}), ex=7200)
        await pipe.execute()
        env = sandbox.environment(); env.update({"REDIS_URL": cache_url.render_as_string(hide_password=False),
            "SUPPORT_API_TOKEN": secrets.token_urlsafe(48), "BUSINESS_PROFILES_ENABLED": "1", "ONBOARDING_ENABLED": "1",
            "COMMERCIAL_CATALOG_ENABLED": "1", "MARKETING_EXECUTION_ENABLED": "0", "QR_CHANNELS_ENABLED": "0", "QR_AI_ENABLED": "0", "GROQ_PROBE_ON_START": "0"})
        with socket.socket() as probe: probe.bind(("127.0.0.1",0)); port = probe.getsockname()[1]
        with args.output.with_suffix(".api.log").open("wb") as log:
            report["phase"] = "local_api_start"
            api = subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),
                "--workers",str(args.api_workers),"--no-access-log"],cwd=ROOT,env=env,stdout=log,stderr=log)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",timeout=10,
                limits=httpx.Limits(max_connections=300,max_keepalive_connections=150),trust_env=False) as client:
                for _ in range(90):
                    if api.poll() is not None: raise RuntimeError("Qualification API exited before readiness")
                    try:
                        if (await client.get("/health")).status_code == 200: break
                    except httpx.HTTPError: pass
                    await asyncio.sleep(.5)
                else: raise RuntimeError("Qualification API readiness timeout")
                metrics = Measurements(); reports = []
                report["phase"] = "all_sessions_isolation"
                report["isolation"] = await isolation_preflight(client,people)
                if not report["isolation"]["pass"]: raise RuntimeError("Synthetic session isolation preflight failed")
                report["phase"] = "three_seed_contention"
                for seed in (17,29,43):
                    race, result = await contention(client, people, seed); reports.append(result)
                    metrics.committed.update(race.committed); metrics.verified_cancellations.update(race.verified_cancellations)
                    metrics.violations.update(race.violations)
                report["contention"] = reports
                for p in people[:2]:
                    code, _ = await request(client, metrics, 10, p, "cross_tenant_rejected", "GET", f'/api/v1/tenants/{p["other"]}/services', expected=(403,))
                    if code != 403: metrics.violations["cross_tenant_http_access"] += 1
                # Warmup is reported separately, excluded explicitly from measured percentiles.
                warmup = Measurements()
                await run_stage(client,warmup,people,10,args.warmup_seconds,args.period_seconds,7,300)
                metrics.committed.update(warmup.committed); metrics.verified_cancellations.update(warmup.verified_cancellations)
                report["warmup_unexpected"] = dict(warmup.failures); metrics.violations.update(warmup.violations)
                monitor_task = asyncio.create_task(monitor(sandbox,cache,api.pid,report["resource_samples"],stop,args.max_memory_mb))
                durations = {}
                for active in LEVELS:
                    report["phase"] = f"load_{active}"
                    stage_task = asyncio.create_task(run_stage(client,metrics,people,active,
                        args.sustain_seconds if active==1500 else args.stage_seconds,args.period_seconds,args.seed,300))
                    done, _ = await asyncio.wait({monitor_task,stage_task},return_when=asyncio.FIRST_COMPLETED)
                    if monitor_task in done:
                        stage_task.cancel(); await asyncio.gather(stage_task,return_exceptions=True)
                        await monitor_task
                        raise RuntimeError("Resource monitor stopped unexpectedly")
                    durations[active] = await stage_task
                recovery = Measurements()
                recovery_elapsed = await run_stage(client,recovery,people,10,30,args.period_seconds,args.seed,300)
                metrics.committed.update(recovery.committed); metrics.verified_cancellations.update(recovery.verified_cancellations)
                metrics.violations.update(recovery.violations)
                report["recovery"] = recovery.report({10: recovery_elapsed})
                report["invariants"] = await asyncio.to_thread(invariants,sandbox,metrics,inventory)
                report["measurements"] = metrics.report(durations)
                report["actual_stage_seconds"] = durations
                if monitor_task.done(): await monitor_task
                report["pass"] = report["measurements"]["pass"] and all(r["pass"] for r in reports) and not warmup.failures and report["recovery"]["pass"]
                report["phase"] = "completed"
    finally:
        stop.set()
        if monitor_task:
            await asyncio.gather(monitor_task,return_exceptions=True)
        if api and api.poll() is None:
            api.terminate()
            try: await asyncio.to_thread(api.wait,15)
            except subprocess.TimeoutExpired:
                api.kill(); await asyncio.to_thread(api.wait,5)
        if keys and await cache.get(lock)==owner:
            for offset in range(0,len(keys),100): await cache.delete(*keys[offset:offset+100])
        await cache.aclose()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--confirm-disposable-local", action="store_true")
    result.add_argument("--output",type=Path,required=True)
    result.add_argument("--profile",choices=("concentrated","wide"),default="concentrated")
    result.add_argument("--stage-seconds",type=int,default=60)
    result.add_argument("--sustain-seconds",type=int,default=600)
    result.add_argument("--warmup-seconds",type=int,default=30)
    result.add_argument("--period-seconds",type=float,default=10)
    result.add_argument("--api-workers",type=int,choices=(1,2),default=2)
    result.add_argument("--max-memory-mb",type=int,default=1536)
    result.add_argument("--seed",type=int,default=20261018)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.confirm_disposable_local or os.getenv("ORION_QUALIFICATION_DISPOSABLE")!="1":
        raise SystemExit("Explicit disposable-local confirmation required")
    if (not 1 <= args.stage_seconds <= 300 or not 1 <= args.sustain_seconds <= 3600
            or not 0 <= args.warmup_seconds <= 120 or not 1 <= args.period_seconds <= 30
            or not 256 <= args.max_memory_mb <= 4096):
        raise SystemExit("Qualification bounds rejected")
    args.companies = 1000 if args.profile=="wide" else 100
    args.output.parent.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    report = {"schema":1,"synthetic_only":True,"profile":args.profile,"levels":LEVELS,"targets_before_run":TARGETS,
        "arrival_model":"open-loop fixed virtual-user cadence; busy/queue arrivals are counted as dropped errors",
        "offered_journey_rate_per_second":{n:n/args.period_seconds for n in LEVELS},
        "period_seconds":args.period_seconds,"api_workers":args.api_workers,"pass":False,
        "planned_stage_seconds":args.stage_seconds,"planned_sustain_seconds":args.sustain_seconds,
        "run_kind":"finite_qualification" if args.sustain_seconds>=600 and args.stage_seconds>=60 else "short_smoke_only",
        "twelve_hour_window_completed":False,"external_delivery_qualified":False,
        "limits":["No real WhatsApp, audio, model provider, worker recovery or financial integration",
                  "No production capacity claim; API workers and PostgreSQL pools are real",
                  "No 12-hour endurance test; 30/60-minute profiles require separate execution"],
        "machine":{"platform":platform.platform(),"python":platform.python_version(),"logical_cpus":os.cpu_count()}}
    source = subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,timeout=5)
    report["source_commit"] = source.stdout.strip() if source.returncode==0 else None
    # Persist the immutable target declaration before any measurement.
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    try:
        report["phase"] = "disposable_database_creation"
        with DisposableDatabase(os.environ["ORION_QUALIFICATION_OWNER_URL"],os.environ) as sandbox:
            report["phase"] = "migration_to_head"
            sandbox.migrate()
            with sandbox.engine.connect() as db: report["migration_revision"] = db.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            asyncio.run(execute(args,sandbox,report))
    except Exception as exc:
        report["pass"] = False; report["stopped_error_class"] = type(exc).__name__
        original = getattr(exc,"orig",None)
        report["sqlstate"] = getattr(original,"sqlstate",None)
        diagnostic = getattr(original,"diag",None)
        if diagnostic:
            report["sql_structure"] = {key:getattr(diagnostic,key,None) for key in ("schema_name","table_name","column_name","constraint_name")}
    report["elapsed_seconds"] = round(time.monotonic()-started,3)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"pass":report["pass"],"elapsed_seconds":report["elapsed_seconds"],"error_class":report.get("stopped_error_class")}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
