"""Harness correctness only: these mocks are never labelled real load evidence."""
import asyncio
import os

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
    result = load.clean_environment()
    assert not {"GROQ_API_KEY","GOOGLE_CLIENT_SECRET","APP_DATABASE_URL","HTTP_PROXY"}&result.keys()


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


@pytest.mark.asyncio
async def test_open_loop_keeps_offering_while_responses_stall(monkeypatch):
    calls=[]
    async def slow(*args): calls.append(1); await asyncio.sleep(.11)
    monkeypatch.setattr(load,"journey",slow)
    metrics=load.Measurements()
    await load.run_stage(None,metrics,[{}],1,.10,.01,17,300)
    assert metrics.offered[1]>=9 and metrics.dropped[1]>=8 and len(calls)==1


@pytest.mark.asyncio
async def test_cancellation_drains_children_instead_of_leaving_load_running(monkeypatch):
    entered=asyncio.Event(); stopped=asyncio.Event()
    async def slow(*args):
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
