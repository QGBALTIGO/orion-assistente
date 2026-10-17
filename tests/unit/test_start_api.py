"""The public API process must never inherit one-time owner/migration access."""
import pytest

from scripts import start_api


def test_api_exec_has_runtime_roles_but_no_owner_migration_environment(monkeypatch):
    source = {name: "synthetic-only" for name in start_api.ONE_TIME_KEYS}
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
    assert environment["APP_DATABASE_URL"] == "runtime-only"
    assert environment["SUPPORT_DATABASE_URL"] == "support-only"
    assert environment["QR_BRIDGE_TOKEN"] == "existing-bridge-secret"
    assert "MIGRATION_DATABASE_URL" in source  # Do not mutate the deployment helper's environment.


@pytest.mark.parametrize("value", ["0", "65536", "8000 --reload", "", "abc"])
def test_invalid_port_cannot_become_a_startup_argument(monkeypatch, value):
    monkeypatch.setattr(start_api.os, "environ", {"PORT": value})
    monkeypatch.setattr(start_api.os, "execve", lambda *_: pytest.fail("Must not execute invalid port"))
    with pytest.raises(RuntimeError, match="Invalid API port"):
        start_api.main()
