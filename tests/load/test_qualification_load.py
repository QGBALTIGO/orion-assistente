"""Harness correctness only: these mocks are never labelled real load evidence."""
import asyncio
import os
import json
import subprocess
from collections import Counter

import httpx
import pytest

from scripts import qualification_load as load


@pytest.mark.parametrize("value,options", [
    ("postgresql://postgres:p@production.example/postgres", {"role":"postgres","database":"postgres"}),
    ("postgresql://app_runtime:p@127.0.0.1/postgres", {"role":"postgres"}),
    ("postgresql://postgres:p@127.0.0.1/live", {"database":"postgres"}),
    ("postgresql://postgres:p@127.0.0.1/postgres?host=production.example", {}),
    ("redis://127.0.0.1/0", {"database":"15","redis_url":True}),
    ("rediss://production.example/15", {"database":"15","redis_url":True}),
])
def test_remote_ambiguous_or_nondedicated_targets_are_rejected(value,options):
    with pytest.raises(ValueError): load.local_url(value,**options)


def test_private_environment_excludes_providers_and_production_settings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY","do-not-copy")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET","do-not-copy")
    monkeypatch.setenv("APP_DATABASE_URL","do-not-copy")
    monkeypatch.setenv("HTTP_PROXY","do-not-copy")
    monkeypatch.setenv("ORION_DB_POOL_SIZE","20")
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW","20")
    result = load.clean_environment()
    assert not {"GROQ_API_KEY","GOOGLE_CLIENT_SECRET","APP_DATABASE_URL","HTTP_PROXY"}&result.keys()
    assert not {"ORION_DB_POOL_SIZE", "ORION_DB_MAX_OVERFLOW"}&result.keys()


@pytest.mark.parametrize("flag,value", [("--db-pool-size", "0"), ("--db-pool-size", "21"),
    ("--db-max-overflow", "-1"), ("--db-max-overflow", "21")])
def test_pool_bounds_reject_before_any_disposable_database(flag,value,tmp_path):
    with pytest.raises(SystemExit):
        load.main(["--output",str(tmp_path/"result.json"),flag,value])
    assert not list(tmp_path.iterdir())


def test_pool_declaration_precedes_database_and_matches_explicit_api_environment(monkeypatch,tmp_path):
    monkeypatch.setenv("ORION_QUALIFICATION_DISPOSABLE", "1")
    monkeypatch.setenv("ORION_QUALIFICATION_OWNER_URL", "postgresql://postgres:synthetic@127.0.0.1/postgres")
    monkeypatch.setenv("ORION_DB_POOL_SIZE", "20")
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", "20")
    path = tmp_path/"result.json"; declarations = []
    class NoDatabase:
        def __init__(self,*_):
            declarations.append(json.loads(path.read_text()))
            raise RuntimeError("No connection permitted by this harness test")
    monkeypatch.setattr(load, "DisposableDatabase", NoDatabase)
    arguments = ["--confirm-disposable-local", "--output",str(path),"--api-workers","3",
        "--db-pool-size","5","--db-max-overflow","5"]
    assert load.main(arguments) == 1
    declaration = declarations[0]
    assert declaration["database_pools"] == {"size_per_role_per_worker":5,
        "max_overflow_per_role_per_worker":5,"roles_per_worker":2,"theoretical_api_connection_ceiling":60}
    assert declaration["api_workers"] == 3 and declaration["targets_before_run"] == load.TARGETS
    assert declaration["offered_journey_rate_per_second"]["1500"] == 150
    args = load.parser().parse_args(arguments)
    assert load.api_pool_environment(args) == {"ORION_DB_POOL_SIZE":"5", "ORION_DB_MAX_OVERFLOW":"5"}
    defaults = load.parser().parse_args(["--output", "synthetic.json"])
    assert (defaults.api_workers,defaults.db_pool_size,defaults.db_max_overflow) == (2,5,10)


def test_start_requires_two_explicit_disposable_confirmations(monkeypatch,tmp_path):
    monkeypatch.delenv("ORION_QUALIFICATION_DISPOSABLE",raising=False)
    with pytest.raises(SystemExit): load.main(["--confirm-disposable-local","--output",str(tmp_path/"result.json")])
    monkeypatch.setenv("ORION_QUALIFICATION_DISPOSABLE","1")
    with pytest.raises(SystemExit): load.main(["--output",str(tmp_path/"result.json")])
    assert not list(tmp_path.iterdir())


def test_percentiles_include_slow_requests_and_do_not_reclassify_failures():
    metrics = load.Measurements()
    for _ in range(98): metrics.record(10,"catalog",.01,200,(200,))
    metrics.record(10,"catalog",3,200,(200,)); metrics.record(10,"catalog",4,500,(200,))
    metrics.offered[10]=100
    report = metrics.report({10:10})
    assert report["operations"][0]["p99_ms"]==3000
    assert report["unexpected_error_rate"]==.01 and not report["pass"]
    assert report["unexpected"]["10/catalog/500"]==1


def test_correct_conflicts_are_counted_and_not_removed_from_latency():
    metrics = load.Measurements()
    metrics.record(10,"hold",.01,201,(201,409)); metrics.record(10,"hold",5,409,(201,409))
    report = metrics.report({10:10})
    assert report["operations"][0]["statuses"]=={"201":1,"409":1}
    assert report["unexpected_error_rate"]==0 and report["operations"][0]["p99_ms"]==5000
    assert not report["pass"]


def test_dropped_arrivals_fail_even_when_every_executed_request_is_fast():
    metrics=load.Measurements(); metrics.record(1500,"catalog",.01,200,(200,))
    metrics.dropped[1500]=9
    report=metrics.report({1500:1})
    assert report["unexpected_error_rate"]==.9 and not report["pass"]


def test_profile_distribution_is_not_users_equal_to_rps():
    counts={}
    for user in range(1500):
        company=load.distribute(user,100); counts[company]=counts.get(company,0)+1
    assert len(counts)==100 and max(counts.values())>min(counts.values())*5
    wide={}
    for user in range(1500):
        company=load.distribute(user,1000,True); wide[company]=wide.get(company,0)+1
    assert len(wide)==1000 and set(wide.values())=={1,2}
    assert 1500/load.parser().get_default("period_seconds")==150


def test_forty_percent_additional_concentration_covers_five_balanced_companies():
    additional = range(100, 1500)
    hot = Counter(load.distribute(index, 100) for index in additional if index % 5 < 2)
    assert set(hot) == set(range(5))
    assert set(hot.values()) == {112} and sum(hot.values()) == 560
    counts = Counter(load.distribute(index, 100) for index in range(1500))
    assert len(counts) == 100 and sum(counts.values()) == 1500
    assert min(counts[index] for index in range(5)) > 100
    assert max(counts[index] for index in range(5, 100)) <= 15
    assert max(counts[index] for index in range(5)) - min(counts[index] for index in range(5)) <= 15


@pytest.mark.asyncio
async def test_open_loop_keeps_offering_while_responses_stall(monkeypatch):
    calls=[]
    async def slow(*args, **kwargs): calls.append(1); await asyncio.sleep(.11)
    monkeypatch.setattr(load,"journey",slow)
    metrics=load.Measurements()
    await load.run_stage(None,metrics,[{}],1,.10,.01,17,300)
    assert metrics.offered[1]>=9 and metrics.dropped[1]>=8 and len(calls)==1


@pytest.mark.asyncio
async def test_cancellation_drains_children_instead_of_leaving_load_running(monkeypatch):
    entered=asyncio.Event(); stopped=asyncio.Event()
    async def slow(*args, **kwargs):
        entered.set()
        try: await asyncio.sleep(30)
        finally: stopped.set()
    monkeypatch.setattr(load,"journey",slow)
    running=asyncio.create_task(load.run_stage(None,load.Measurements(),[{}],1,30,.01,17,300))
    await entered.wait(); running.cancel()
    await asyncio.gather(running,return_exceptions=True)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_http_error_is_preserved_without_logging_body_or_credentials():
    async def handler(request): return httpx.Response(503,json={"secret":"must-not-enter-report"})
    metrics=load.Measurements()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url="http://127.0.0.1") as client:
        status,_=await load.request(client,metrics,10,{"token":"private","csrf":"private"},"catalog","GET","/synthetic")
    report=metrics.report({10:1})
    assert status==503 and not report["pass"] and "must-not-enter-report" not in str(report) and "private" not in str(report)


@pytest.mark.asyncio
async def test_duplicate_response_id_is_an_invariant_failure():
    calls=0
    async def handler(request):
        nonlocal calls
        calls+=1
        if calls==1: return httpx.Response(201,json={"id":"a"})
        if calls==2: return httpx.Response(201,json={"id":"different"})
        return httpx.Response(200,json={"status":"confirmed" if calls==3 else "cancelled"})
    p={"tenant":"synthetic","token":"private","csrf":"private","professional":"p","service":"s","phone":"synthetic","day":"2030-01-01"}
    metrics=load.Measurements()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url="http://127.0.0.1") as client:
        await load.booking_journey(client,metrics,10,p,"synthetic-key")
    assert metrics.violations["duplicate_hold"]==1 and not metrics.report({10:1})["pass"]


@pytest.mark.asyncio
async def test_warmup_measurement_and_recovery_have_distinct_operation_keys_but_replay_within_each():
    records, seen = {}, []
    async def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/hold"):
            key = body["idempotency_key"]; seen.append(key)
            records.setdefault(key, {"id": str(len(records)), "status": "hold"})
            return httpx.Response(201, json=records[key])
        identity = request.url.path.split("/")[-2]
        record = next(row for row in records.values() if row["id"] == identity)
        if request.url.path.endswith("/confirm") and record["status"] == "hold": record["status"] = "confirmed"
        if request.url.path.endswith("/cancel"): record["status"] = "cancelled"
        # A replay returns its current persisted state, including cancelled.
        return httpx.Response(200, json=record)
    person = {"tenant":"synthetic", "token":"private", "csrf":"private", "professional":"p",
        "service":"s", "phone":"synthetic", "day":"2030-01-01"}
    metrics = load.Measurements()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1") as client:
        for phase in ("warmup", "measured", "recovery"):
            await load.journey(client, metrics, 10, person, 0, 17, None, phase=phase)
    assert len(records) == 3 and len(metrics.verified_cancellations) == 3
    assert not metrics.violations and len(metrics.hold_attempts) == 3
    assert len(seen) == 6 and all(seen[i] == seen[i+1] for i in (0,2,4))
    assert len({seen[i] for i in (0,2,4)}) == 3


def test_lost_hold_response_is_uncertain_not_a_proven_duplicate_and_still_fails():
    metrics = load.Measurements(); metrics.hold_attempts.add(("tenant", "attempt"))
    metrics.record(1500, "hold", 10, "transport_error", (201,))
    integrity, uncertainty = load.reconcile_booking_observations([
        {"id":"committed", "tenant_id":"tenant", "idempotency_key":"attempt", "status":"hold"}], metrics)
    assert not any(integrity.values())
    assert uncertainty == {"committed_hold_without_observed_response":1, "unfinished_booking_journeys":1,
        "cancellation_response_not_observed":0}
    metrics.observation_uncertainty.update({k:v for k,v in uncertainty.items() if v})
    report = metrics.report({1500:10})
    assert not report["pass"] and report["unexpected_error_rate"] == 1
    assert report["http_requests"] == 1 and report["operations"][0]["p99_ms"] == 10000


def test_reconciliation_keeps_missing_rows_unrequested_rows_and_false_cancellation_as_data_failures():
    metrics = load.Measurements(); metrics.committed.update({"missing", "cancel-ack"})
    metrics.verified_cancellations.add("cancel-ack"); metrics.hold_attempts.add(("tenant", "known"))
    integrity, uncertainty = load.reconcile_booking_observations([
        {"id":"cancel-ack", "tenant_id":"tenant", "idempotency_key":"known", "status":"confirmed"},
        {"id":"unrequested", "tenant_id":"tenant", "idempotency_key":"never-attempted", "status":"cancelled"}], metrics)
    assert integrity == {"appointment_without_recorded_attempt":1, "observed_hold_missing_from_database":1,
        "verified_cancellation_not_persisted":1}
    assert uncertainty["unfinished_booking_journeys"] == 1


@pytest.mark.parametrize("workers", [2,3,4])
def test_diagnostic_worker_count_preserves_offer_timeout_and_declared_targets(workers):
    args = load.parser().parse_args(["--output","synthetic.json","--api-workers",str(workers)])
    assert args.api_workers == workers and args.period_seconds == 10
    assert args.max_memory_mb == 1536
    assert load.TARGETS == {"read":{"p95_ms":800,"p99_ms":1500},
        "write":{"p95_ms":2000,"p99_ms":4000},"unexpected_error_rate":.001}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [0, 1, "timeout"])
async def test_final_snapshot_requires_owned_api_to_finish_inflight_writes_before_read(outcome):
    class Api:
        def __init__(self): self.events = []
        def poll(self): self.events.append("poll"); return None
        def terminate(self): self.events.append("terminate")
        def wait(self, timeout):
            assert timeout == 30; self.events.append("wait")
            if outcome == "timeout": raise subprocess.TimeoutExpired("synthetic", timeout)
            return outcome
    api = Api()
    if outcome == 0:
        result = await load.stop_api_for_reconciliation(api)
        assert result["clean_shutdown"] and result["final_database_read_after_shutdown"]
    else:
        with pytest.raises(RuntimeError): await load.stop_api_for_reconciliation(api)
    assert api.events == ["poll", "terminate", "wait"]
