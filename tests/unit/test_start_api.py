"""The public API process must never inherit one-time owner/migration access."""
import json
from pathlib import Path

import pytest

from scripts import start_api


def test_api_exec_has_runtime_roles_but_no_owner_migration_environment(monkeypatch):
    # Keep this list independent of the implementation so accidentally removing
    # a sensitive key from ONE_TIME_KEYS cannot make the test pass silently.
    migration_keys = {
        "MIGRATION_DATABASE_URL", "ORION_RUN_BUSINESS_MIGRATION", "ORION_RUN_ACCOUNT_MIGRATION",
        "ORION_MIGRATION_SWITCH_ROLE", "ORION_BUSINESS_MIGRATION_RUN_ID",
        "ORION_RUN_MANAGEMENT_MIGRATION", "ORION_MANAGEMENT_MIGRATION_RUN_ID",
        "ORION_RUN_SALES_REQUESTS_MIGRATION", "ORION_RUN_AGENDA_MIGRATION",
        "ORION_EXPECTED_DATABASE_HOST", "ORION_EXPECTED_DATABASE_NAME",
        "ORION_RUN_ONBOARDING_MIGRATION", "ORION_ONBOARDING_MIGRATION_RUN_ID",
        "ORION_RUN_ADMIN_IDENTITY_MIGRATION", "ORION_ADMIN_IDENTITY_MIGRATION_RUN_ID",
        "ORION_RUN_ADMIN_DEMO_SETUP", "ORION_ADMIN_DEMO_SETUP_RUN_ID", "ORION_ADMIN_DEMO_SETUP_PHASE",
        "ORION_ADMIN_DEMO_TENANT_ID", "ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256",
    }
    source = {name: "synthetic-only" for name in start_api.ONE_TIME_KEYS | migration_keys}
    source.update(APP_DATABASE_URL="runtime-only", SUPPORT_DATABASE_URL="support-only",
                  QR_BRIDGE_TOKEN="existing-bridge-secret", PORT="8080")
    monkeypatch.setattr(start_api.os, "environ", source)
    executed = []
    barriers = []

    def wait(command, **kwargs):
        assert command == [start_api.sys.executable, "-m", "scripts.wait_for_subscription_campaigns_schema"]
        assert kwargs["check"] is True and kwargs["timeout"] == 95
        assert not set(kwargs["env"]) & migration_keys
        assert not set(kwargs["env"]) & start_api.ONE_TIME_KEYS
        assert kwargs["env"]["APP_DATABASE_URL"] == "runtime-only"
        assert executed == []
        barriers.append(kwargs["env"])

    def execute(*args):
        assert len(barriers) == 1
        executed.append(args)

    monkeypatch.setattr(start_api.subprocess, "run", wait)
    monkeypatch.setattr(start_api.os, "execve", execute)
    start_api.main()
    executable, args, environment = executed[0]
    assert executable == start_api.sys.executable
    assert args[1:4] == ["-m", "uvicorn", "app.main:app"] and args[-1] == "8080"
    # A Google callback includes code/state in its query. The production child
    # must disable Uvicorn's complete-target access log, while retaining errors.
    assert "--no-access-log" in args and "--log-level" not in args
    assert not set(environment) & start_api.ONE_TIME_KEYS
    assert not set(environment) & migration_keys
    assert environment["APP_DATABASE_URL"] == "runtime-only"
    assert environment["SUPPORT_DATABASE_URL"] == "support-only"
    assert environment["QR_BRIDGE_TOKEN"] == "existing-bridge-secret"
    assert barriers == [environment]
    assert "MIGRATION_DATABASE_URL" in source  # Do not mutate the deployment helper's environment.


def test_docker_and_railway_start_the_environment_sanitizing_launcher():
    root = Path(__file__).resolve().parents[2]
    # Source CI checks both manifests. The runtime image intentionally ships
    # neither manifests nor .git; its separate execve test above still runs.
    if not any((root / name).exists() for name in (".git", "Dockerfile", "railway.json")):
        pytest.skip("Deployment manifests are checked in source CI, not shipped in the runtime image")
    config = json.loads((root / "railway.json").read_text(encoding="utf-8"))
    assert config["deploy"]["startCommand"] == "python -m scripts.start_api"
    commands = [line.removeprefix("CMD ") for line in (root / "Dockerfile").read_text(encoding="utf-8").splitlines()
                if line.startswith("CMD ")]
    assert commands and json.loads(commands[-1]) == ["python", "-m", "scripts.start_api"]


@pytest.mark.parametrize("value", ["0", "65536", "8000 --reload", "", "abc"])
def test_invalid_port_cannot_become_a_startup_argument(monkeypatch, value):
    monkeypatch.setattr(start_api.os, "environ", {"PORT": value})
    monkeypatch.setattr(start_api.os, "execve", lambda *_: pytest.fail("Must not execute invalid port"))
    monkeypatch.setattr(start_api.subprocess, "run", lambda *_a, **_k: pytest.fail("Invalid port must fail before the catalog probe"))
    with pytest.raises(RuntimeError, match="Invalid API port"):
        start_api.main()


@pytest.mark.parametrize("timed_out", [False, True])
def test_api_never_serves_when_the_campaign_or_predecessor_catalog_is_not_ready(monkeypatch, timed_out):
    monkeypatch.setattr(start_api.os, "environ", {"PORT": "8080", "APP_DATABASE_URL": "synthetic-runtime"})
    monkeypatch.setattr(start_api.os, "execve", lambda *_: pytest.fail("API cannot serve before the read-only schema barrier"))

    def fail(command, **kwargs):
        if timed_out:
            raise start_api.subprocess.TimeoutExpired(command, kwargs["timeout"])
        raise start_api.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(start_api.subprocess, "run", fail)
    expected = start_api.subprocess.TimeoutExpired if timed_out else start_api.subprocess.CalledProcessError
    with pytest.raises(expected):
        start_api.main()
