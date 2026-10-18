"""Client pooling changes neither offered work nor request/result semantics."""
import asyncio
import json
import ssl
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from scripts import qualification_load as load


class ObservedClient:
    def __init__(self, index, options):
        self.index, self.options, self.calls, self.closed = index, options, [], 0
        self.request_error = self.close_error = None

    async def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.request_error is not None:
            raise self.request_error
        return SimpleNamespace(client=self.index, status_code=200)

    async def aclose(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def factory_recording(clients):
    def factory(**options):
        client = ObservedClient(len(clients), options)
        clients.append(client)
        return client
    return factory


@pytest.mark.asyncio
async def test_all_twelve_real_pools_have_same_origin_timeout_and_aggregate_original_caps():
    async with load.QualificationHttpClient("http://127.0.0.1:12345") as client:
        assert len(client._clients) == 12
        limits = []
        contexts = set()
        for item in client._clients:
            assert item.base_url == httpx.URL("http://127.0.0.1:12345")
            assert item.timeout == httpx.Timeout(10)
            assert item.trust_env is False
            pool = item._transport._pool
            limits.append((pool._max_connections, pool._max_keepalive_connections))
            assert isinstance(pool._ssl_context, ssl.SSLContext)
            assert pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert pool._ssl_context.check_hostname is True
            contexts.add(id(pool._ssl_context))
            assert pool._retries == 0
        assert [limit[0] for limit in limits] == [25] * 12
        assert [limit[1] for limit in limits] == [13] * 6 + [12] * 6
        assert sum(limit[0] for limit in limits) == 300
        assert sum(limit[1] for limit in limits) == 150
        assert len(contexts) == 1
    assert all(item.is_closed for item in client._clients)


@pytest.mark.asyncio
async def test_session_hash_is_stable_across_clients_and_header_case_without_exposing_cookies():
    clients = []
    async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory_recording(clients)) as client:
        seen = set()
        for index in range(1500):
            token = "synthetic-session-" + str(index)
            first = await client.request("GET", "/private", headers={"Cookie": "__Host-orion_session=" + token})
            second = await client.request("POST", "/private", headers=httpx.Headers({"cookie": "other=1; __Host-orion_session=" + token + "; trailing=2"}))
            assert first.client == second.client
            assert first.client == load.QualificationHttpClient._pool_index({"COOKIE": "__Host-orion_session=" + token})
            seen.add(first.client)
        assert seen == set(range(12))
        assert (await client.get("/health")).client == 0
        assert (await client.get("/health", headers={"Accept": "application/json"})).client == 0
    assert all(item.closed == 1 for item in clients)
    declaration = json.dumps(load.http_pool_configuration())
    assert "synthetic-session" not in declaration


@pytest.mark.asyncio
async def test_wrapper_preserves_method_path_json_headers_timeout_and_response_identity():
    calls, responses = [], []
    async def handler(request):
        calls.append(request)
        response = httpx.Response(409, json={"detail": "synthetic conflict"}, headers={"x-test": "preserved"})
        responses.append(response)
        return response
    def factory(**options):
        return httpx.AsyncClient(**options, transport=httpx.MockTransport(handler))
    async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory) as client:
        body = {"expected_version": 7, "idempotency_key": "synthetic-once", "data": {"modality": "online"}}
        headers = {"Cookie": "__Host-orion_session=synthetic-only", "x-orion-csrf": "synthetic-csrf"}
        response = await client.request("PUT", "/api/v1/tenants/synthetic/onboarding/steps/location?x=1",
            json=body, headers=headers)
        assert response is responses[0]
        assert response.status_code == 409 and response.json() == {"detail": "synthetic conflict"}
        assert response.headers["x-test"] == "preserved"
    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "http://127.0.0.1:12345/api/v1/tenants/synthetic/onboarding/steps/location?x=1"
    assert request.method == "PUT" and json.loads(request.content) == body
    assert request.headers["Cookie"] == headers["Cookie"]
    assert request.headers["x-orion-csrf"] == headers["x-orion-csrf"]
    assert request.extensions["timeout"] == {"connect": 10, "read": 10, "write": 10, "pool": 10}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [httpx.ReadTimeout, httpx.ConnectError, httpx.PoolTimeout])
async def test_transport_error_is_never_retried_and_all_clients_close(kind):
    clients = []; failure = kind("synthetic transport error")
    with pytest.raises(kind) as result:
        async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory_recording(clients)) as client:
            for item in clients: item.request_error = failure
            await client.request("POST", "/write", json={"idempotency_key": "synthetic"}, headers={"Cookie": "__Host-orion_session=synthetic"})
    assert result.value is failure
    assert sum(len(item.calls) for item in clients) == 1
    assert len(clients) == 12 and all(item.closed == 1 for item in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("body_error", [False, True])
async def test_shutdown_attempts_every_client_even_when_one_close_fails(body_error):
    clients = []; close_error = RuntimeError("synthetic close failure"); request_error = httpx.ReadTimeout("synthetic request failure")
    with pytest.raises(httpx.ReadTimeout if body_error else RuntimeError) as error:
        async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory_recording(clients)):
            clients[0].close_error = close_error
            clients[5].close_error = RuntimeError("other close failure")
            if body_error: raise request_error
    assert error.value is (request_error if body_error else close_error)
    assert len(clients) == 12 and all(item.closed == 1 for item in clients)


@pytest.mark.asyncio
async def test_partial_initialization_failure_closes_every_owned_client_and_preserves_error():
    clients = []; failure = RuntimeError("synthetic initialization failure")
    def factory(**options):
        if len(clients) == 5: raise failure
        return factory_recording(clients)(**options)
    with pytest.raises(RuntimeError) as error:
        async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory):
            pytest.fail("Initialization should not enter workload")
    assert error.value is failure
    assert len(clients) == 5 and all(item.closed == 1 for item in clients)


@pytest.mark.asyncio
async def test_twenty_same_cookie_contention_requests_are_not_serialized_by_the_wrapper():
    clients, entered = [], []
    release = asyncio.Event()
    class WaitingClient(ObservedClient):
        async def request(self, *args, **kwargs):
            entered.append(self.index)
            await release.wait()
            return await super().request(*args, **kwargs)
    def factory(**options):
        item = WaitingClient(len(clients), options); clients.append(item); return item
    async with load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory) as client:
        tasks = [asyncio.create_task(client.request("POST", "/hold", headers={"Cookie": "__Host-orion_session=same-synthetic-session"})) for _ in range(20)]
        try:
            async with asyncio.timeout(2):
                while len(entered) < 20: await asyncio.sleep(0)
            assert len(set(entered)) == 1
            assert clients[entered[0]].options["limits"].max_connections == 25
        finally:
            release.set()
            await asyncio.gather(*tasks)
    assert sum(len(item.calls) for item in clients) == 20


@pytest.mark.asyncio
async def test_closed_or_unentered_wrapper_cannot_send_or_reopen():
    clients = []
    client = load.QualificationHttpClient("http://127.0.0.1:12345", client_factory=factory_recording(clients))
    with pytest.raises(RuntimeError): await client.get("/health")
    async with client: pass
    with pytest.raises(RuntimeError): await client.get("/health")
    with pytest.raises(RuntimeError): await client.__aenter__()
    assert not any(item.calls for item in clients)


def test_timing_only_is_full_qualification_without_cpu_profiler_and_declares_pools_before_io(monkeypatch, tmp_path):
    monkeypatch.setenv("ORION_QUALIFICATION_DISPOSABLE", "1")
    monkeypatch.setenv("ORION_QUALIFICATION_OWNER_URL", "synthetic-unused")
    monkeypatch.setattr(load.platform, "platform", lambda: "synthetic-platform")
    monkeypatch.setattr(load.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="synthetic-commit"))
    monkeypatch.setattr(load.cProfile, "Profile", lambda *args, **kwargs: pytest.fail("Timing-only created cProfile"))
    target = tmp_path / "result.json"
    class Sandbox:
        def __init__(self, *args):
            declared = json.loads(target.read_text())
            assert declared["timing_diagnostics"] is True
            assert declared["http_client_pools"] == load.http_pool_configuration()
            assert declared["run_kind"] == "finite_qualification"
            assert declared["planned_stage_seconds"] == 60 and declared["planned_sustain_seconds"] == 600
            assert declared["targets_before_run"] == load.TARGETS
            self.engine = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def migrate(self): pass
        def connect(self): return self
        def exec_driver_sql(self, *args): return self
        def scalar_one(self): return "synthetic"
    async def execute(args, sandbox, report):
        assert args.timing_diagnostics and not args.profile_api and not args.profile_generator
        report["pass"] = True
    monkeypatch.setattr(load, "DisposableDatabase", Sandbox)
    monkeypatch.setattr(load, "execute", execute)
    assert load.main(["--confirm-disposable-local", "--timing-diagnostics", "--output", str(target)]) == 0
    actual = json.loads(target.read_text())
    assert actual["pass"] and actual["run_kind"] == "finite_qualification"
    assert "cpu_profiles" not in actual and "generator_profile" not in actual
    assert not target.with_suffix(".generator-profiles").exists()


def test_diagnostics_are_opt_in_and_do_not_change_default_duration_or_targets(tmp_path):
    args = load.parser().parse_args(["--output", str(tmp_path / "result.json")])
    assert not args.timing_diagnostics and not args.profile_generator and not args.profile_api
    assert (args.stage_seconds, args.sustain_seconds, args.period_seconds) == (60, 600, 10)
    assert load.http_pool_configuration()["max_connections_total"] == 300
    assert load.http_pool_configuration()["max_keepalive_total"] == 150


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", [False, True])
async def test_actual_execute_wires_timing_into_all_phases_and_monitor_without_changing_offers(monkeypatch, tmp_path, timing):
    """Only infrastructure is replaced; run execute's actual phase orchestration."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1/15")
    args = load.parser().parse_args(["--output", str(tmp_path / "result.json")] + (["--timing-diagnostics"] if timing else []))
    args.companies = 100
    phases, monitoring, clients, api = [], [], [], SimpleNamespace(pid=1234, code=None)
    class Cache:
        def __init__(self): self.values = {}; self.closed = False
        async def dbsize(self): return 0
        async def set(self, key, value, **kwargs): self.values[key] = value; return True
        async def get(self, key): return self.values.get(key)
        async def delete(self, *keys):
            for key in keys: self.values.pop(key, None)
        async def aclose(self): self.closed = True
        def pipeline(self, **kwargs):
            cache = self
            class Pipeline:
                def set(self, key, value, **options): cache.values[key] = value
                async def execute(self): pass
            return Pipeline()
    cache = Cache()
    monkeypatch.setattr(load.redis, "from_url", lambda *args, **kwargs: cache)
    person = {"token": "synthetic", "csrf": "synthetic", "account": "synthetic", "tenant": "synthetic-a", "other": "synthetic-b"}
    monkeypatch.setattr(load, "seed_database", lambda *args, **kwargs: ([person], {}))
    api.poll = lambda: api.code
    api.terminate = lambda: setattr(api, "code", 0)
    api.wait = lambda *args: 0
    monkeypatch.setattr(load.subprocess, "Popen", lambda *args, **kwargs: api)
    class LocalSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, *args): pass
        def getsockname(self): return ("127.0.0.1", 12345)
    monkeypatch.setattr(load.socket, "socket", LocalSocket)
    class Client:
        def __init__(self, **kwargs): self.options = kwargs; self.closed = False; clients.append(self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.closed = True
        async def get(self, path): return httpx.Response(200)
        async def request(self, *args, **kwargs): return httpx.Response(403, json={"detail": "synthetic denied"})
    monkeypatch.setattr(load, "QualificationHttpClient", Client)
    async def isolation(*args): return {"pass": True}
    async def contention(*args): return load.Measurements(), {"pass": True}
    async def stage(client, metrics, people, active, duration, period, seed, max_pending, *, phase="measured"):
        phases.append({"phase": phase, "active": active, "seconds": duration, "period": period,
            "max_pending": max_pending, "diagnostics": metrics.diagnostics})
        metrics.record(active, "catalog", .001, 200, (200,))
        await asyncio.sleep(0)
        return duration
    async def monitor(sandbox, cache, pid, samples, stop, max_memory_mb, *, diagnostics=False, phase=None):
        monitoring.append({"diagnostics": diagnostics, "memory_guard": max_memory_mb})
        await stop.wait()
    monkeypatch.setattr(load, "isolation_preflight", isolation)
    monkeypatch.setattr(load, "contention", contention)
    monkeypatch.setattr(load, "run_stage", stage)
    monkeypatch.setattr(load, "monitor", monitor)
    monkeypatch.setattr(load, "invariants", lambda *args: {"pass": True})
    report = {}
    await load.execute(args, SimpleNamespace(environment=lambda: {}), report)
    assert [row["phase"] for row in phases] == ["warmup"] + ["measured"] * 5 + ["recovery"]
    assert [row["active"] for row in phases] == [10, 10, 100, 500, 1000, 1500, 10]
    assert [row["seconds"] for row in phases] == [30, 60, 60, 60, 60, 600, 30]
    assert all(row["max_pending"] == 300 and row["period"] == 10 for row in phases)
    assert all(row["diagnostics"] is timing for row in phases)
    assert monitoring == [{"diagnostics": timing, "memory_guard": 1536}]
    assert ("diagnostics" in report["measurements"]) is timing
    assert ("warmup_diagnostics" in report) is timing
    assert ("diagnostics" in report["recovery"]) is timing
    assert "generator_profile" not in report and "cpu_profiles" not in report
    assert report["pass"] and report["final_drain"]["clean_shutdown"]
    assert len(clients) == 1 and clients[0].closed
    assert clients[0].options == {"base_url": "http://127.0.0.1:12345"}
    assert cache.closed and not cache.values
