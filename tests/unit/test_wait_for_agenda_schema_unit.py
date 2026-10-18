"""Bounded worker readiness checks, with no network or wall-clock sleeps."""
import asyncio
import json

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from scripts import wait_for_agenda_schema as gate


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("results,expected_sleeps", [([True], []), ([False, False, True], [1, 1])])
async def test_ready_or_later_migration_uses_only_bounded_polls(results, expected_sleeps):
    clock = Clock()
    attempts = []
    async def probe(settings, timeout):
        attempts.append(timeout)
        return results.pop(0)
    result = await gate.wait_for_schema(gate.Settings("synthetic"), clock=clock, sleep=clock.sleep, probe=probe)
    assert result == {"ok": True, "status": "agenda_schema_ready"}
    assert clock.sleeps == expected_sleeps
    assert attempts == [3.0] * (len(expected_sleeps) + 1)


async def test_absent_schema_stops_at_fixed_deadline_without_real_sleep():
    clock = Clock()
    attempts = []
    async def probe(settings, timeout):
        attempts.append(timeout)
        return False
    with pytest.raises(gate.Stop, match="^agenda_schema_timeout$"):
        await gate.wait_for_schema(gate.Settings("synthetic"), clock=clock, sleep=clock.sleep, probe=probe)
    assert clock.now == 90.0 and len(attempts) == 90
    assert max(attempts) == 3.0 and attempts[-1] == 1.0
    assert clock.sleeps == [1.0] * 90


async def test_retryable_connection_failures_share_the_same_deadline():
    clock = Clock()
    outcomes = [OSError("synthetic-password@private-host"), TimeoutError("synthetic-password"), True]
    async def probe(settings, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            clock.now += timeout
            raise outcome
        return outcome
    result = await gate.wait_for_schema(gate.Settings("synthetic"), clock=clock, sleep=clock.sleep, probe=probe)
    assert result["ok"] is True and clock.now == 8.0 and clock.sleeps == [1.0, 1.0]


@pytest.mark.parametrize("error,code", [
    (gate.Stop("runtime_role_required"), "runtime_role_required"),
    (asyncpg.InvalidPasswordError("synthetic-password"), "runtime_authentication_failed"),
    (asyncpg.InsufficientPrivilegeError("synthetic-password"), "runtime_catalog_unavailable"),
])
async def test_role_and_privilege_failures_do_not_wait(error, code):
    clock = Clock()
    async def probe(settings, timeout):
        raise error
    with pytest.raises(gate.Stop, match="^" + code + "$"):
        await gate.wait_for_schema(gate.Settings("synthetic"), clock=clock, sleep=clock.sleep, probe=probe)
    assert clock.now == 0 and clock.sleeps == []


class Connection:
    def __init__(self, *, role="app_runtime", ready=True):
        self.row = {"runtime_role": role, "ready": ready}
        self.queries = []
        self.terminated = False

    async def fetchrow(self, query):
        self.queries.append(query)
        return self.row

    def terminate(self):
        self.terminated = True


@pytest.mark.parametrize("role", ["app_runtime", "postgres"])
async def test_probe_is_read_only_checks_effective_role_and_always_closes(monkeypatch, role):
    connection = Connection(role=role)
    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["timeout"] == kwargs["command_timeout"] == 3.0
        assert kwargs["server_settings"] == {
            "default_transaction_read_only": "on", "search_path": "pg_catalog",
            "statement_timeout": "3000", "lock_timeout": "3000",
            "application_name": "orion-agenda-schema-wait",
        }
        return connection
    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    if role == "app_runtime":
        assert await gate.probe_schema(gate.Settings("synthetic"), 3.0) is True
    else:
        with pytest.raises(gate.Stop, match="^runtime_role_required$"):
            await gate.probe_schema(gate.Settings("synthetic"), 3.0)
    assert connection.queries == [gate.SCHEMA_QUERY] and connection.terminated


async def test_attempt_deadline_cancels_pending_query_and_closes_connection(monkeypatch):
    clock = Clock()
    connection = Connection()
    pending = []
    async def fetchrow(query):
        pending.append(query)
        await asyncio.Future()
    async def connect(*args, **kwargs):
        return connection
    connection.fetchrow = fetchrow
    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    # An immediate event-loop deadline proves cancellation without sleeping.
    monkeypatch.setattr(gate, "ATTEMPT_SECONDS", 0.0)
    monkeypatch.setattr(gate, "WAIT_SECONDS", 1.0)
    with pytest.raises(gate.Stop, match="^agenda_schema_timeout$"):
        await gate.wait_for_schema(gate.Settings("synthetic"), clock=clock, sleep=clock.sleep)
    assert pending == [gate.SCHEMA_QUERY] and connection.terminated
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize("url,code", [
    (None, "invalid_runtime_configuration"),
    ("sqlite:///synthetic-password", "invalid_runtime_configuration"),
    ("postgresql://app_runtime:synthetic-password@/orion", "invalid_runtime_configuration"),
    ("postgresql://postgres:synthetic-password@db/orion", "runtime_role_required"),
    ("postgresql://app_runtime:synthetic-password@db/orion?options=-c+role%3Dpostgres", "invalid_runtime_configuration"),
    ("postgresql://app_runtime:synthetic-password@db/orion?sslmode=unexpected", "invalid_runtime_configuration"),
])
def test_invalid_runtime_config_fails_before_connection_and_hides_secrets(monkeypatch, capsys, url, code):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://postgres:synthetic-owner-password@db/orion")
    if url is None:
        monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("APP_DATABASE_URL", url)
    async def must_not_connect(*args, **kwargs):
        pytest.fail("Invalid configuration must not open a connection or use the migrator URL")
    monkeypatch.setattr(gate.asyncpg, "connect", must_not_connect)
    assert gate.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": code} and output.err == ""
    assert "synthetic-password" not in output.out and "postgresql" not in output.out


def test_asyncpg_url_and_tls_are_preserved_without_exposing_settings(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://app_runtime:synthetic%40password@db:5432/orion?ssl=require")
    settings = gate.Settings.from_environment()
    url = make_url(settings.dsn)
    assert url.drivername == "postgresql" and url.username == "app_runtime"
    assert url.password == "synthetic@password" and url.query == {"sslmode": "require"}
    assert "synthetic" not in repr(settings) and "postgresql" not in repr(settings)


@pytest.mark.parametrize("error,expected_code", [
    (gate.Stop("agenda_schema_timeout"), "agenda_schema_timeout"),
    (RuntimeError("postgresql://app_runtime:synthetic-password@private-host/orion"), "agenda_schema_check_failed"),
    (gate.Stop("synthetic-password@private-host"), "agenda_schema_check_failed"),
])
def test_runtime_errors_emit_only_finite_safe_codes(monkeypatch, capsys, error, expected_code):
    monkeypatch.setattr(gate.Settings, "from_environment", lambda: gate.Settings("synthetic-password"))
    async def fail(settings):
        raise error
    monkeypatch.setattr(gate, "wait_for_schema", fail)
    assert gate.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": expected_code} and output.err == ""


def test_main_returns_success_without_configuration_or_identity_in_output(monkeypatch, capsys):
    monkeypatch.setattr(gate.Settings, "from_environment", lambda: gate.Settings("synthetic-password"))
    async def ready(settings):
        return {"ok": True, "status": "agenda_schema_ready"}
    monkeypatch.setattr(gate, "wait_for_schema", ready)
    assert gate.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": True, "status": "agenda_schema_ready"} and output.err == ""
