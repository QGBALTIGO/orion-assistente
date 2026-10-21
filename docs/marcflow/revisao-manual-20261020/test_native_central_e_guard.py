"""Only local fakes execute the prepared phase-E hook; no service is contacted."""
import ast
import importlib.metadata
import json
from pathlib import Path
import re
import shlex
import subprocess
from types import SimpleNamespace

import httpx
import pytest

BASE = Path(__file__).resolve().parent
REPO = BASE.parent / "orion-marcflow-20261020"
PREPARED = json.loads((BASE / "native-central-E-prepared.json").read_text(encoding="utf-8"))
TRUE_FIELDS = ("configured", "ready", "connected", "session_preserved")
KEY = "synthetic-private-bridge-secret-" + "X" * 32
VALID = {name: True for name in TRUE_FIELDS}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_URL", "http://synthetic-qr.railway.internal:3000")
    monkeypatch.setenv("QR_BRIDGE_TOKEN", KEY)
    state = SimpleNamespace(status=200, chunks=[json.dumps(VALID).encode()], error=None,
                            events=[], versions=[], requests=[], client_args=None, closed=False)

    class Response:
        @property
        def status_code(self):
            return state.status

        def iter_bytes(self):
            if state.error == "body":
                raise RuntimeError("Private failure " + KEY)
            yield from state.chunks

    class Stream:
        def __enter__(self):
            if state.error == "stream":
                raise RuntimeError("Private failure " + KEY)
            return Response()

        def __exit__(self, *_args):
            state.events.append("stream_closed")

    class Client:
        def __init__(self, **kwargs):
            state.client_args = kwargs
            if state.error == "client":
                raise RuntimeError("Private failure " + KEY)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            state.closed = True

        def stream(self, method, url, **kwargs):
            state.requests.append((method, url, kwargs))
            state.events.append("readiness_GET")
            assert method == "GET"
            assert url == "http://synthetic-qr.railway.internal:3000/internal/legacy-central/status"
            assert kwargs == {"headers": {"X-Orion-Bridge-Key": KEY}}
            return Stream()

    monkeypatch.setattr(httpx, "Client", Client)
    namespace = {}
    exec(compile(PREPARED["guard_code"], "prepared_readiness_guard", "exec"), namespace)
    state.guard = namespace["require_legacy_central_ready"]
    return state


def refusal(fake, capsys):
    with pytest.raises(SystemExit) as stopped:
        fake.guard()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert output.out == "legacy_central_preflight_failed\n" and not output.err
    assert KEY not in output.out + output.err


def test_guard_success_is_readonly_bounded_and_preserves_client_safety(fake, capsys):
    fake.guard()
    assert capsys.readouterr().out == "legacy_central_preflight_ready\n"
    assert len(fake.requests) == 1 and fake.closed
    assert fake.client_args["trust_env"] is False and fake.client_args["follow_redirects"] is False
    timeout = fake.client_args["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (3, 5, 5, 5)


@pytest.mark.parametrize("field", TRUE_FIELDS)
@pytest.mark.parametrize("mode", ["missing", "false"])
def test_guard_refuses_missing_or_false_readiness(fake, capsys, field, mode):
    value = dict(VALID)
    if mode == "missing":
        value.pop(field)
    else:
        value[field] = False
    fake.chunks = [json.dumps(value).encode()]
    refusal(fake, capsys)


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_guard_requires_actual_booleans(fake, capsys, value):
    fake.chunks = [json.dumps({**VALID, "ready": value}).encode()]
    refusal(fake, capsys)


@pytest.mark.parametrize("value", [[], True, None, "ready"])
def test_guard_refuses_non_dictionary(fake, capsys, value):
    fake.chunks = [json.dumps(value).encode()]
    refusal(fake, capsys)


@pytest.mark.parametrize("status", [204, 301, 302, 401, 500])
def test_guard_refuses_status_and_never_follows_redirect(fake, capsys, status):
    fake.status = status
    refusal(fake, capsys)
    assert len(fake.requests) == 1 and fake.closed


@pytest.mark.parametrize("body", [b"{", b"\xff", b""])
def test_guard_refuses_invalid_body(fake, capsys, body):
    fake.chunks = [body]
    refusal(fake, capsys)


def test_guard_accepts_exact_size_boundary(fake):
    body = json.dumps(VALID).encode()
    fake.chunks = [body, b" " * (64000 - len(body))]
    fake.guard()


def test_guard_refuses_oversize_stream_before_parsing(fake, capsys):
    fake.chunks = [b" " * 32000, b" " * 32001]
    refusal(fake, capsys)
    assert fake.closed


@pytest.mark.parametrize("stage", ["client", "stream", "body"])
def test_guard_exception_never_prints_private_error(fake, capsys, stage):
    fake.error = stage
    refusal(fake, capsys)


@pytest.mark.parametrize("url", ["https://public.example:3000", "http://other.railway.internal:8000",
                               "http://user:pass@synthetic-qr.railway.internal:3000",
                               "http://synthetic-qr.railway.internal:3000/path"])
def test_guard_rejects_unsafe_origin_before_attaching_key(fake, capsys, monkeypatch, url):
    monkeypatch.setenv("QR_BRIDGE_URL", url)
    refusal(fake, capsys)
    assert not fake.requests


def install_hook_fakes(fake, monkeypatch):
    expected = PREPARED["static_validation"]["runtime_versions_unchanged_from_C"]

    def version(name):
        fake.versions.append(name)
        return expected[name]

    def run(argv, *, check):
        assert check is True and argv[1] == "-m"
        fake.events.append(argv[2])

    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setattr(subprocess, "run", run)


def test_complete_hook_checks_guard_before_adoption_and_preserves_gate_order(fake, monkeypatch, capsys):
    install_hook_fakes(fake, monkeypatch)
    exec(compile(PREPARED["code"], "prepared_phase_E_hook", "exec"), {})
    assert len(fake.versions) == 18
    assert [event for event in fake.events if event != "stream_closed"] == [
        "scripts.wait_for_admin_identity_schema", "readiness_GET",
        "scripts.one_time_legacy_adoption", "scripts.probe_qr_bridge"]
    assert KEY not in capsys.readouterr().out


def test_complete_hook_failure_stops_before_any_adoption(fake, monkeypatch, capsys):
    install_hook_fakes(fake, monkeypatch)
    fake.chunks = [json.dumps({**VALID, "ready": False}).encode()]
    with pytest.raises(SystemExit) as stopped:
        exec(compile(PREPARED["code"], "prepared_phase_E_hook", "exec"), {})
    assert stopped.value.code == 1
    assert fake.events == ["scripts.wait_for_admin_identity_schema", "readiness_GET", "stream_closed"]
    assert KEY not in capsys.readouterr().out


def test_prepared_posix_hook_ast_versions_and_adoption_target():
    assert shlex.split(PREPARED["hook"], posix=True) == ["python", "-c", PREPARED["code"]]
    ast.parse(PREPARED["code"])
    ast.parse(PREPARED["guard_code"])
    pins = {}
    for line in (REPO / "requirements.lock").read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if match:
            pins[re.sub(r"[-_.]+", "-", match[1]).lower()] = match[2]
    expected = PREPARED["static_validation"]["runtime_versions_unchanged_from_C"]
    assert len(pins) == 52 and len(expected) == 18
    assert all(pins[re.sub(r"[-_.]+", "-", name).lower()] == value for name, value in expected.items())
    assert PREPARED["variables"] == {"ORION_RUN_LEGACY_ADOPTION": "1",
        "ORION_LEGACY_ADOPTION_RUN_ID": "0a1fa3b2-4d80-46cb-99f7-1c31900f9113",
        "ORION_LEGACY_ADOPTION_PHASE": "activate", "QR_LEGACY_CENTRAL_ENABLED": "1"}
    assert PREPARED["required_preserved_runtime_flags"]["PANEL_LEGACY_ADMIN_IDENTITY_ENABLED"] == "1"
    assert PREPARED["required_preserved_runtime_flags"]["INBOX_CONTROL_ENABLED"] == "0"
    assert PREPARED["required_preserved_runtime_flags"]["INBOX_SEND_ENABLED"] == "0"
