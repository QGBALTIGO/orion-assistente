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
        "ORION_EXPECTED_DATABASE_HOST", "ORION_EXPECTED_DATABASE_NAME",
    }
    source = {name: "synthetic-only" for name in start_api.ONE_TIME_KEYS | migration_keys}
    source.update(APP_DATABASE_URL="runtime-only", SUPPORT_DATABASE_URL="support-only",
                  QR_BRIDGE_TOKEN="existing-bridge-secret", PORT="8080")
    monkeypatch.setattr(start_api.os, "environ", source)
    executed = []
    monkeypatch.setattr(start_api.os, "execve", lambda *args: executed.append(args))
    start_api.main()
    executable, args, environment = executed[0]
    assert executable == start_api.sys.executable
    assert args[1:4] == ["-m", "uvicorn", "app.main:app"] and args[-1] == "8080"
    assert not set(environment) & start_api.ONE_TIME_KEYS
    assert not set(environment) & migration_keys
    assert environment["APP_DATABASE_URL"] == "runtime-only"
    assert environment["SUPPORT_DATABASE_URL"] == "support-only"
    assert environment["QR_BRIDGE_TOKEN"] == "existing-bridge-secret"
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
    with pytest.raises(RuntimeError, match="Invalid API port"):
        start_api.main()
