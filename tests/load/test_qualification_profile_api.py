"""CPU diagnostics guards/lifecycle, not evidence of database load capacity."""
import asyncio
import builtins
import json
import os
from pathlib import Path
import pstats
import socket
import subprocess
import time
from types import SimpleNamespace

import httpx
import pytest

from scripts import qualification_load as load
from scripts import qualification_profile_api as cpu


@pytest.fixture
def environment(tmp_path):
    directory = tmp_path / "profiles"; directory.mkdir()
    database = "orion_qualification_" + "a" * 32
    result = {"ORION_QUALIFICATION_DISPOSABLE": "1", "ORION_QUALIFICATION_PROFILE_API": "1",
        "ORION_QUALIFICATION_PROFILE_DIRECTORY": str(directory), "REDIS_URL": "redis://127.0.0.1/15"}
    for key, role in load.ROLE_ENV.items():
        result[key] = f"postgresql://{role}:synthetic-only@127.0.0.1/{database}"
    return result


@pytest.mark.parametrize("key,value", [
    ("ORION_QUALIFICATION_DISPOSABLE", "0"), ("ORION_QUALIFICATION_PROFILE_API", "0"),
    ("APP_DATABASE_URL", "postgresql://app_runtime:p@production.example/live"),
    ("APP_DATABASE_URL", "postgresql://postgres:p@127.0.0.1/orion_qualification_" + "a" * 32),
    ("APP_DATABASE_URL", "postgresql://app_runtime:p@127.0.0.1/existing_database"),
    ("SUPPORT_DATABASE_URL", "postgresql://app_support:p@127.0.0.1/orion_qualification_" + "b" * 32),
    ("REDIS_URL", "redis://127.0.0.1/0"), ("REDIS_URL", "redis://production.example/15"),
    ("MARKETING_EXECUTION_ENABLED", "1"), ("MARKETING_RECONCILIATION_ENABLED", "1"),
    ("QR_CHANNELS_ENABLED", "1"), ("QR_AI_ENABLED", "1"), ("GOOGLE_LIVE_SYNC", "1"),
    ("GROQ_PROBE_ON_START", "1"), ("ORION_QUALIFICATION_PROFILE_DIRECTORY", "relative/profiles"),
])
def test_profiling_rejects_nonowned_or_effectful_startup(environment, key, value):
    environment[key] = value
    with pytest.raises((ValueError, RuntimeError)):
        cpu.Settings.from_environment(environment)
    assert not list(Path(environment.get("ORION_QUALIFICATION_PROFILE_DIRECTORY", ".")).glob("*.prof"))


def test_factory_checks_optin_before_importing_application(monkeypatch):
    monkeypatch.delenv("ORION_QUALIFICATION_PROFILE_API", raising=False)
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        assert name != "app.main", "Invalid profiling target imported the real API"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", checked)
    with pytest.raises(RuntimeError, match="explicit opt-in"): cpu.create_app()


def test_cli_default_keeps_real_app_and_all_qualification_targets(tmp_path):
    args = load.parser().parse_args(["--output", str(tmp_path / "load.json")])
    assert not args.profile_api
    assert args.stage_seconds == 60 and args.sustain_seconds == 600
    command = load.api_command(args, 32100)
    assert "app.main:app" in command and "--factory" not in command
    assert command[command.index("--host") + 1] == "127.0.0.1"
    env = {"APP_DATABASE_URL": "unchanged"}
    assert load.configure_api_profile(args, env) is None and env == {"APP_DATABASE_URL": "unchanged"}
    assert load.LEVELS == (10, 100, 500, 1000, 1500)
    assert load.TARGETS == {"read": {"p95_ms": 800, "p99_ms": 1500},
        "write": {"p95_ms": 2000, "p99_ms": 4000}, "unexpected_error_rate": .001}


def test_profile_runner_adds_optin_only_for_its_new_owned_output(tmp_path, environment):
    args = load.parser().parse_args(["--output", str(tmp_path / "load.json"), "--profile-api"])
    directory = load.configure_api_profile(args, environment)
    assert directory.parent == args.output.parent and directory.name == "load.profiles"
    assert cpu.Settings.from_environment(environment).directory == directory
    command = load.api_command(args, 32100)
    assert "scripts.qualification_profile_api:create_app" in command and "--factory" in command
    with pytest.raises(ValueError, match="new owned"): load.configure_api_profile(args, environment)


async def lifecycle(application, during=None):
    incoming = asyncio.Queue(); ready = asyncio.Event(); events = []
    async def send(message):
        events.append(message["type"])
        if message["type"] == "lifespan.startup.complete": ready.set()
    running = asyncio.create_task(application({"type": "lifespan", "asgi": {"version": "3.0"}}, incoming.get, send))
    await incoming.put({"type": "lifespan.startup"})
    try:
        await asyncio.wait_for(ready.wait(), 5)
        if during: await during()
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(running, 5)
    return events


@pytest.mark.asyncio
async def test_actual_api_startup_health_and_shutdown_dump_function_metadata_only(environment, monkeypatch):
    for key, value in environment.items(): monkeypatch.setenv(key, value)
    for key in ("GROQ_PROBE_ON_START", "QR_AI_ENABLED", "QR_CHANNELS_ENABLED", "GOOGLE_LIVE_SYNC",
                "MARKETING_EXECUTION_ENABLED", "MARKETING_RECONCILIATION_ENABLED"):
        monkeypatch.setenv(key, "0")
    application = cpu.create_app()
    private = "sensitive-request-content-never-profiled"
    async def health():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1") as client:
            response = await client.get("/health", headers={"x-synthetic-secret": private})
            assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert await lifecycle(application, health) == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    directory = Path(environment["ORION_QUALIFICATION_PROFILE_DIRECTORY"])
    binary = next(directory.glob("*.prof")); summary = next(directory.glob("*.json"))
    payload = json.loads(summary.read_text())
    assert payload["clock"] == "time.process_time" and payload["diagnostic_only"] is True
    assert any(item["function"].endswith(":health") for item in payload["functions"])
    assert all(set(item) == {"function", "primitive_calls", "calls", "tottime", "cumtime"} for item in payload["functions"])
    assert private not in summary.read_text() and private.encode() not in binary.read_bytes()
    assert "synthetic-only" not in summary.read_text() and not list(directory.glob("*.tmp"))
    assert pstats.Stats(str(binary)).stats
    assert load.collect_api_profiles(directory, 1)["complete"]
    assert not load.collect_api_profiles(directory, 2)["complete"]


@pytest.mark.asyncio
async def test_failed_shutdown_still_saves_profile_and_does_not_hide_original_error(environment):
    async def broken(scope, receive, send):
        await receive(); await send({"type": "lifespan.startup.complete"})
        await receive()
        raise RuntimeError("synthetic shutdown failure")
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        await lifecycle(cpu.ProfiledApi(broken, cpu.Settings.from_environment(environment)))
    directory = Path(environment["ORION_QUALIFICATION_PROFILE_DIRECTORY"])
    assert len(list(directory.glob("*.prof"))) == len(list(directory.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_separate_worker_pids_never_overwrite_each_other(environment, monkeypatch):
    async def app(scope, receive, send):
        await receive(); await send({"type": "lifespan.startup.complete"})
        await receive(); await send({"type": "lifespan.shutdown.complete"})
    for pid in (1001, 1002):
        monkeypatch.setattr(cpu.os, "getpid", lambda value=pid: value)
        await lifecycle(cpu.ProfiledApi(app, cpu.Settings.from_environment(environment)))
    directory = Path(environment["ORION_QUALIFICATION_PROFILE_DIRECTORY"])
    report = load.collect_api_profiles(directory, 2)
    assert report["complete"] and report["complete_workers"] == 2
    assert report["files"] == ["worker-1001.json", "worker-1001.prof", "worker-1002.json", "worker-1002.prof"]


@pytest.mark.parametrize("measured_pass", [False, True])
def test_diagnostic_never_claims_capacity_or_hides_failed_exit(tmp_path, monkeypatch, measured_pass):
    monkeypatch.setenv("ORION_QUALIFICATION_DISPOSABLE", "1")
    monkeypatch.setenv("ORION_QUALIFICATION_OWNER_URL", "synthetic-not-used")
    class Sandbox:
        def __init__(self, *args): self.engine = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def migrate(self): pass
        def connect(self): return self
        def exec_driver_sql(self, *args): return self
        def scalar_one(self): return "synthetic"
    async def execute(args, sandbox, report): report["pass"] = measured_pass
    monkeypatch.setattr(load, "DisposableDatabase", Sandbox)
    monkeypatch.setattr(load, "execute", execute)
    monkeypatch.setattr(load.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="synthetic-commit"))
    target = tmp_path / "load.json"
    code = load.main(["--confirm-disposable-local", "--profile-api", "--output", str(target)])
    result = json.loads(target.read_text())
    assert result["run_kind"] == "cpu_profile_diagnostic" and result["capacity_qualified"] is False
    assert result["targets_before_run"] == load.TARGETS and result["pass"] == measured_pass
    assert code == (0 if measured_pass else 1)


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate kills a process; production Uvicorn graceful SIGTERM is POSIX")
def test_real_two_worker_uvicorn_sigterm_flushes_each_cpu_profile(environment, tmp_path):
    args = load.parser().parse_args(["--output", str(tmp_path / "diagnostic.json"), "--profile-api", "--api-workers", "2"])
    child = load.clean_environment(); child.update(environment)
    for key in ("GROQ_PROBE_ON_START", "QR_AI_ENABLED", "QR_CHANNELS_ENABLED", "GOOGLE_LIVE_SYNC",
                "MARKETING_EXECUTION_ENABLED", "MARKETING_RECONCILIATION_ENABLED"):
        child[key] = "0"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    logpath = tmp_path / "uvicorn.log"
    with logpath.open("wb") as log:
        api = subprocess.Popen(load.api_command(args, port), cwd=load.ROOT, env=child, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=.5, trust_env=False) as client:
                while time.monotonic() < deadline:
                    assert api.poll() is None, "Owned diagnostic API exited before startup"
                    if logpath.read_text().count("Application startup complete.") == 2:
                        assert client.get("/health").status_code == 200
                        break
                    time.sleep(.1)
                else: pytest.fail("Owned diagnostic workers did not start")
            api.terminate()
            assert api.wait(timeout=25) == 0
        finally:
            if api.poll() is None:
                api.kill(); api.wait(timeout=5)
    directory = Path(environment["ORION_QUALIFICATION_PROFILE_DIRECTORY"])
    assert load.collect_api_profiles(directory, 2)["complete"]
    for path in directory.glob("*.json"):
        payload = json.loads(path.read_text())
        assert payload["clock"] == "time.process_time" and payload["functions"]
