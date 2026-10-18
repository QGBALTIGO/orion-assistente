"""Bounded deployment barrier tests: fake clock/connection, no network or DDL."""
import asyncio
import json

import asyncpg
import pytest

from scripts import wait_for_onboarding_schema as gate


class Clock:
    now = 0.0
    def __init__(self): self.sleeps = []
    def __call__(self): return self.now
    async def sleep(self, seconds):
        self.sleeps.append(seconds); self.now += seconds


class Connection:
    def __init__(self, **changes):
        self.row = {"runtime_role": "app_runtime", "authenticated_role": "app_runtime", "restricted_role": True,
            "tables_ready": True, "functions_ready": True, "consent_ready": True, "agenda_ready": True, **changes}
        self.queries, self.terminated = [], False
    async def fetchrow(self, query, *args):
        self.queries.append((query, args)); return self.row
    def terminate(self): self.terminated = True


async def test_waits_for_committed_complete_metadata_then_releases_without_extra_probe():
    clock = Clock(); outcomes = [False, False, True]; budgets = []
    async def probe(settings, timeout): budgets.append(timeout); return outcomes.pop(0)
    assert await gate.wait_for_schema(gate.Settings("private"), clock=clock, sleep=clock.sleep, probe=probe) == {
        "ok": True, "status": "onboarding_schema_ready", "schema_marker": "0023_automation_notice_guard"}
    assert budgets == [3, 3, 3] and clock.sleeps == [1, 1]


async def test_absent_metadata_times_out_at_300_seconds_without_real_sleep():
    clock = Clock(); budgets = []
    async def probe(settings, timeout): budgets.append(timeout); return False
    with pytest.raises(gate.Stop, match="^onboarding_schema_timeout$"):
        await gate.wait_for_schema(gate.Settings("private"), clock=clock, sleep=clock.sleep, probe=probe)
    assert clock.now == 300 and len(budgets) == 300 and budgets[-1] == 1
    assert max(budgets) == 3 and sum(clock.sleeps) == 300


async def test_retryable_network_failure_and_slow_attempt_share_the_original_deadline():
    clock = Clock(); outcomes = [OSError("private-host/password"), TimeoutError("private"), True]
    async def probe(settings, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception): clock.now += timeout; raise outcome
        return outcome
    assert (await gate.wait_for_schema(gate.Settings("private"), clock=clock, sleep=clock.sleep, probe=probe))["ok"]
    assert clock.now == 8 and clock.sleeps == [1, 1]


@pytest.mark.parametrize("error,code", [
    (gate.Stop("runtime_role_required"), "runtime_role_required"),
    (asyncpg.InvalidPasswordError("private"), "runtime_authentication_failed"),
    (asyncpg.InvalidCatalogNameError("private"), "runtime_authentication_failed"),
    (asyncpg.InsufficientPrivilegeError("private"), "runtime_catalog_unavailable"),
])
async def test_auth_and_role_fail_closed_without_retry(error, code):
    clock = Clock()
    async def probe(settings, timeout): raise error
    with pytest.raises(gate.Stop, match="^" + code + "$"):
        await gate.wait_for_schema(gate.Settings("private"), clock=clock, sleep=clock.sleep, probe=probe)
    assert clock.now == 0 and not clock.sleeps


@pytest.mark.parametrize("changes,ready,denied", [
    ({}, True, False),
    ({"tables_ready": False}, False, False),
    ({"functions_ready": False}, False, False),
    ({"consent_ready": False}, False, False),
    ({"agenda_ready": False}, False, False),
    ({"functions_ready": None}, False, False),
    ({"runtime_role": "app_migrator"}, False, True),
    ({"authenticated_role": "postgres"}, False, True),
    ({"restricted_role": False}, False, True),
])
async def test_every_component_and_both_roles_are_required_with_readonly_connection(monkeypatch, changes, ready, denied):
    connection = Connection(**changes)
    async def connect(dsn, **kwargs):
        assert dsn == "private"
        assert kwargs == {"timeout": 3, "command_timeout": 3, "server_settings": {
            "default_transaction_read_only": "on", "search_path": "pg_catalog", "statement_timeout": "3000",
            "lock_timeout": "3000", "application_name": "orion-onboarding-schema-wait"}}
        return connection
    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    if denied:
        with pytest.raises(gate.Stop, match="^runtime_role_required$"):
            await gate.probe_schema(gate.Settings("private"), 3)
    else:
        assert await gate.probe_schema(gate.Settings("private"), 3) is ready
    assert connection.terminated and connection.queries == [(gate.SCHEMA_QUERY, (list(gate.TABLES), list(gate.FUNCTIONS)))]


async def test_hung_catalog_read_is_cancelled_and_connection_terminated(monkeypatch):
    connection, clock = Connection(), Clock()
    async def connect(*args, **kwargs): return connection
    async def blocked(*args):
        connection.queries.append("pending")
        await asyncio.Future()
    connection.fetchrow = blocked
    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    monkeypatch.setattr(gate, "ATTEMPT_SECONDS", 0)
    monkeypatch.setattr(gate, "WAIT_SECONDS", 1)
    with pytest.raises(gate.Stop, match="^onboarding_schema_timeout$"):
        await gate.wait_for_schema(gate.Settings("private"), clock=clock, sleep=clock.sleep)
    assert connection.terminated and connection.queries == ["pending"] and clock.now == 1


@pytest.mark.parametrize("url,code", [
    (None, "invalid_runtime_configuration"),
    ("postgresql://postgres:private@db/orion", "runtime_role_required"),
    ("postgresql://app_migrator:private@db/orion", "runtime_role_required"),
    ("postgresql://app_runtime:private@db/orion?options=-c+role%3Dpostgres", "invalid_runtime_configuration"),
])
def test_configuration_never_falls_back_to_migration_credentials(monkeypatch, capsys, url, code):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://postgres:private-owner@db/orion")
    if url is None: monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    else: monkeypatch.setenv("APP_DATABASE_URL", url)
    async def forbidden(*args, **kwargs): pytest.fail("Invalid runtime configuration must not connect")
    monkeypatch.setattr(gate.asyncpg, "connect", forbidden)
    assert gate.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": code} and output.err == ""


@pytest.mark.parametrize("error,expected", [
    (gate.Stop("onboarding_schema_timeout"), "onboarding_schema_timeout"),
    (RuntimeError("postgresql://secret:password@private"), "onboarding_schema_check_failed"),
    (gate.Stop("secret-password"), "onboarding_schema_check_failed"),
])
def test_cli_emits_only_finite_codes_never_database_errors(monkeypatch, capsys, error, expected):
    monkeypatch.setattr(gate.Settings, "from_environment", lambda: gate.Settings("private"))
    async def fail(settings): raise error
    monkeypatch.setattr(gate, "wait_for_schema", fail)
    assert gate.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": expected} and output.err == ""


def test_cli_success_contains_no_identity_or_connection(monkeypatch, capsys):
    monkeypatch.setattr(gate.Settings, "from_environment", lambda: gate.Settings("private"))
    async def ready(settings): return {"ok": True, "status": "onboarding_schema_ready", "schema_marker": gate.SCHEMA_MARKER}
    monkeypatch.setattr(gate, "wait_for_schema", ready)
    assert gate.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": True, "status": "onboarding_schema_ready", "schema_marker": gate.SCHEMA_MARKER}
    assert output.err == ""
