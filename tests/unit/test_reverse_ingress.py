"""Synthetic lifecycle tests: no real processes, SSH, network, or application."""
from pathlib import Path
import signal
import stat
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import reverse_ingress as ingress
from scripts import start_api


@pytest.fixture
def environment():
    identity = Ed25519PrivateKey.generate()
    server = Ed25519PrivateKey.generate().public_key()
    return {
        "ORION_ORIGIN_SSH_HOST": "187.77.255.164",
        "ORION_ORIGIN_SSH_PRIVATE_KEY": identity.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption()).decode("ascii"),
        "ORION_ORIGIN_SSH_KNOWN_HOSTS": "187.77.255.164 " + server.public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii"),
        "PORT": "8080", "WEB_CONCURRENCY": "3", "APP_DATABASE_URL": "synthetic-runtime",
        "QR_BRIDGE_TOKEN": "preserve-session", "PATH": "must-not-pass-to-ssh",
    }


class FakeProcess:
    def __init__(self, name):
        self.name = name
        self.returncode = None
        self.sent = []
        self.reaped = 0
        self.pid = 123456  # never passed to a real OS operation

    def poll(self):
        return self.returncode

    def send_signal(self, number):
        self.sent.append(number)
        if number in (signal.SIGTERM, signal.SIGINT):
            self.returncode = -number

    def wait(self, timeout=None):
        assert self.returncode is not None, "A synthetic running child must be stopped first"
        self.reaped += 1
        return self.returncode


class Harness:
    def __init__(self):
        self.now = 0.0
        self.events = []
        self.calls = []
        self.api = FakeProcess("api")
        self.tunnels = []
        self.signals = ingress.Signals()
        self.on_sleep = lambda harness: setattr(harness.api, "returncode", 0) if harness.now >= 1 else None
        self.ready = True
        self.health_calls = 0
        self.paths = []
        self.stop_order = []

    def popen(self, arguments, **kwargs):
        self.calls.append((self.now, arguments, kwargs))
        if arguments[1:3] == ["-m", "uvicorn"]:
            assert not self.tunnels
            return self.api
        assert self.health_calls > 0
        identity = Path(arguments[arguments.index("-i") + 1])
        known_hosts = Path(next(value.split("=", 1)[1] for value in arguments if value.startswith("UserKnownHostsFile=")))
        assert identity.is_file() and known_hosts.is_file()
        self.paths = [identity, known_hosts]
        child = FakeProcess("ssh")
        self.tunnels.append(child)
        return child

    def health(self, port):
        self.health_calls += 1
        self.events.append(("health", self.now, port))
        return self.ready

    def sleep(self, duration):
        self.now += duration
        self.on_sleep(self)
        assert self.now < 100, "A fake lifecycle must end without a busy loop"

    def stop(self, process, number=signal.SIGTERM, *, timeout=ingress.SHUTDOWN_SECONDS):
        if process is not None:
            assert all(path.exists() for path in self.paths)
            self.stop_order.append(process.name)
        ingress.stop_process(process, number, timeout=timeout, kill_group=lambda _: pytest.fail("No real process group"))

    def run(self, environment, **overrides):
        arguments = dict(popen=self.popen, health=self.health, monotonic=lambda: self.now,
                         sleep=self.sleep, signals=self.signals, install_signals=False, stop=self.stop)
        arguments.update(overrides)
        return ingress.supervise(environment, **arguments)


def test_single_master_then_one_tunnel_preserves_workers_and_runtime_secrets(environment):
    harness = Harness()
    assert harness.run(environment) == 0
    assert len(harness.calls) == 2
    api = harness.calls[0]
    ssh = harness.calls[1]
    assert api[1] == ingress.api_arguments("8080")
    assert "--proxy-headers" in api[1] and "--no-access-log" in api[1]
    assert api[1][api[1].index("--forwarded-allow-ips") + 1] == "127.0.0.1,::1"
    assert api[2]["env"]["WEB_CONCURRENCY"] == "3"
    assert api[2]["env"]["QR_BRIDGE_TOKEN"] == "preserve-session"
    assert api[2]["env"]["APP_DATABASE_URL"] == "synthetic-runtime"
    assert not set(api[2]["env"]) & start_api.SSH_KEYS
    assert ssh[2]["env"] == {"PATH": "/usr/bin:/bin"}
    assert ssh[2]["stdin"] == ssh[2]["stdout"] == ssh[2]["stderr"] == subprocess.DEVNULL
    assert api[2]["start_new_session"] and ssh[2]["start_new_session"]
    assert harness.stop_order == ["api", "ssh"]
    assert harness.api.reaped == 1 and harness.tunnels[0].reaped == 1
    assert all(not path.exists() for path in harness.paths)
    for secret in (environment["ORION_ORIGIN_SSH_PRIVATE_KEY"], "preserve-session", "synthetic-runtime"):
        assert secret not in " ".join(ssh[1])


def test_ssh_is_pinned_noninteractive_and_has_only_fixed_remote_loopback(environment):
    arguments = ingress.ssh_arguments(ingress.configuration(environment), "/private/identity", "/private/pin")
    assert arguments[:7] == ["/usr/bin/ssh", "-N", "-T", "-F", "/dev/null", "-i", "/private/identity"]
    assert arguments[-3:] == ["-R", "127.0.0.1:18445:127.0.0.1:8080", "orion-origin@187.77.255.164"]
    for expected in ("IdentitiesOnly=yes", "IdentityAgent=none", "StrictHostKeyChecking=yes",
                     "UserKnownHostsFile=/private/pin", "GlobalKnownHostsFile=/dev/null",
                     "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "BatchMode=yes",
                     "ExitOnForwardFailure=yes", "ServerAliveInterval=15", "ServerAliveCountMax=3",
                     "ConnectTimeout=10"):
        assert expected in arguments
    assert "-A" not in arguments and "-D" not in arguments and "-L" not in arguments


def test_api_starts_without_waiting_for_local_health_or_remote_bind(environment):
    harness = Harness()
    harness.ready = False
    def advance(state):
        assert state.calls[0][1][1:3] == ["-m", "uvicorn"]
        if state.now < 0.6:
            assert not state.tunnels
        else:
            state.ready = True
        if state.now >= 1:
            state.api.returncode = 0
    harness.on_sleep = advance
    assert harness.run(environment) == 0
    assert len(harness.tunnels) == 1
    assert harness.calls[1][0] >= 0.6


def test_remote_bind_collision_reaps_and_retries_after_two_seconds_without_second_live_client(environment):
    harness = Harness()
    harness.ready = True
    def advance(state):
        if len(state.tunnels) == 1:
            state.tunnels[0].returncode = 255  # previous deployment still owns remote bind
        if len(state.tunnels) == 2:
            assert state.tunnels[0].reaped == 1
            state.api.returncode = 0
    harness.on_sleep = advance
    assert harness.run(environment) == 0
    assert len(harness.tunnels) == 2
    assert harness.calls[2][0] - 0.2 >= ingress.RETRY_SECONDS
    assert harness.api.sent == []  # health was not made dependent on tunnel readiness
    assert all(child.reaped == 1 for child in harness.tunnels)


@pytest.mark.parametrize("status,expected", [(7, 7), (-9, 137), (0, 0)])
def test_api_exit_stops_tunnel_and_propagates_status(environment, status, expected):
    harness = Harness()
    harness.on_sleep = lambda state: setattr(state.api, "returncode", status)
    assert harness.run(environment) == expected
    assert harness.tunnels[0].sent == [signal.SIGTERM]
    assert harness.stop_order == ["api", "ssh"]


def test_shutdown_terminates_api_first_and_keeps_credentials_until_both_are_reaped(environment):
    harness = Harness()
    harness.on_sleep = lambda state: state.signals.receive(signal.SIGTERM, None)
    assert harness.run(environment) == 0
    assert harness.api.sent == [signal.SIGTERM]
    assert harness.stop_order == ["api", "ssh"]
    assert all(not path.exists() for path in harness.paths)


def test_signal_before_spawn_does_not_start_children(environment):
    harness = Harness()
    harness.signals.receive(signal.SIGTERM, None)
    assert harness.run(environment) == 0
    assert not harness.calls and not harness.health_calls


def test_signal_during_health_does_not_start_tunnel(environment):
    harness = Harness()
    def health(_port):
        harness.signals.receive(signal.SIGTERM, None)
        return True
    assert harness.run(environment, health=health) == 0
    assert len(harness.calls) == 1
    assert harness.api.sent == [signal.SIGTERM]


def test_master_control_signal_is_forwarded_once_only_to_api(environment):
    if not hasattr(signal, "SIGHUP"):
        pytest.skip("POSIX worker control signal is unavailable on Windows")
    harness = Harness()
    harness.signals.forward.append(signal.SIGHUP)
    assert harness.run(environment) == 0
    assert harness.api.sent == [signal.SIGHUP]
    assert signal.SIGHUP not in harness.tunnels[0].sent


def test_unready_api_times_out_without_starting_tunnel(environment, monkeypatch, capsys):
    harness = Harness()
    harness.ready = False
    harness.on_sleep = lambda state: None
    monkeypatch.setattr(ingress, "STARTUP_SECONDS", 0.6)
    assert harness.run(environment) == 1
    assert len(harness.calls) == 1 and not harness.tunnels
    assert harness.api.sent == [signal.SIGTERM]
    assert "readiness timed out" in capsys.readouterr().err


def test_missing_ssh_binary_stops_api_before_removing_credentials(environment, capsys):
    harness = Harness()
    def popen(arguments, **kwargs):
        if arguments[0] == "/usr/bin/ssh":
            identity = Path(arguments[arguments.index("-i") + 1])
            harness.paths = [identity]
            raise FileNotFoundError("sensitive path should not appear")
        return harness.popen(arguments, **kwargs)
    assert harness.run(environment, popen=popen) == 1
    assert harness.api.sent == [signal.SIGTERM]
    assert harness.api.reaped == 1 and harness.stop_order == ["api"]
    assert not harness.paths[0].exists()
    assert capsys.readouterr().err == "Reverse ingress could not start its required process\n"


def test_bounded_shutdown_kills_and_reaps_unresponsive_group():
    process = FakeProcess("api")
    killed = []
    process.send_signal = lambda number: process.sent.append(number)
    def wait(timeout=None):
        if timeout is not None:
            assert timeout == ingress.SHUTDOWN_SECONDS
            raise subprocess.TimeoutExpired("synthetic", timeout)
        assert killed == [process]
        process.reaped += 1
    process.wait = wait
    ingress.stop_process(process, kill_group=killed.append)
    assert process.sent == [signal.SIGTERM] and process.reaped == 1


def test_config_and_tempfiles_never_expose_private_material_in_repr(environment):
    config = ingress.configuration(environment)
    assert "PRIVATE KEY" not in repr(config)
    with ingress.credential_files(config) as (identity, known_hosts):
        assert Path(identity).read_bytes() == config.private_key
        assert Path(known_hosts).read_bytes() == config.known_hosts
        if ingress.os.name == "posix":
            assert stat.S_IMODE(Path(identity).stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(known_hosts).stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(identity).parent.stat().st_mode) == 0o700
    assert not Path(identity).parent.exists()


@pytest.mark.parametrize("name", ["ORION_ORIGIN_SSH_HOST", "ORION_ORIGIN_SSH_PRIVATE_KEY", "ORION_ORIGIN_SSH_KNOWN_HOSTS"])
def test_partial_config_fails_before_any_process(environment, name):
    environment.pop(name)
    with pytest.raises(RuntimeError, match="all three SSH settings"):
        Harness().run(environment)


@pytest.mark.parametrize("host", ["", "127.0.0.1", "0.0.0.0", "10.1.2.3", "::1", "example.com",
                                   "187.77.255.164:22", "user@187.77.255.164", "187.77.255.164\n",
                                   "187.077.255.164", "224.0.0.1"])
def test_invalid_ssh_host_fails_closed(environment, host):
    environment["ORION_ORIGIN_SSH_HOST"] = host
    with pytest.raises(RuntimeError):
        Harness().run(environment)


@pytest.mark.parametrize("port", ["0", "65536", "", "8080 -Rbad", "８０８０"])
def test_invalid_local_port_fails_closed(environment, port):
    environment["PORT"] = port
    with pytest.raises(RuntimeError, match="Invalid reverse ingress"):
        Harness().run(environment)


@pytest.mark.parametrize("kind", ["different-host", "hash", "wildcard", "host-list", "port", "ca",
                                   "extra-line", "missing-key", "bad-key", "comment", "unicode"])
def test_known_host_pin_cannot_widen_to_other_hosts_or_unknown_keys(environment, kind):
    original = environment["ORION_ORIGIN_SSH_KNOWN_HOSTS"]
    key = original.split(" ", 1)[1]
    variants = {
        "different-host": "1.1.1.1 " + key, "hash": "|1|hashed|host " + key,
        "wildcard": "* " + key, "host-list": "187.77.255.164,example.com " + key,
        "port": "[187.77.255.164]:22 " + key, "ca": "@cert-authority " + original,
        "extra-line": original + "\n" + original, "missing-key": "187.77.255.164",
        "bad-key": "187.77.255.164 ssh-ed25519 invalid", "comment": original + " comment",
        "unicode": original + " K",
    }
    environment["ORION_ORIGIN_SSH_KNOWN_HOSTS"] = variants[kind]
    with pytest.raises(RuntimeError, match="Invalid reverse ingress SSH configuration"):
        Harness().run(environment)


@pytest.mark.parametrize("key", ["not-a-private-key", "secret\nOTHER", "é", "x" * 32769],
                         ids=["garbage", "multiline", "non-ascii", "oversized"])
def test_invalid_private_key_fails_without_echoing_input(environment, key):
    environment["ORION_ORIGIN_SSH_PRIVATE_KEY"] = key
    with pytest.raises(RuntimeError) as error:
        Harness().run(environment)
    assert str(error.value) == "Invalid reverse ingress SSH configuration"


def test_encrypted_private_key_is_rejected_without_prompt(environment):
    # Valid encrypted OpenSSH test material, never authorized on any server.
    # Loading must fail before a prompt; generating it at test time would add
    # an optional bcrypt dependency that the application does not require.
    environment["ORION_ORIGIN_SSH_PRIVATE_KEY"] = (
        Path(__file__).parents[1] / "fixtures" / "origin-encrypted-test.key"
    ).read_text(encoding="ascii")
    with pytest.raises(RuntimeError, match="Invalid reverse ingress"):
        Harness().run(environment)


def test_direct_supervisor_with_owner_env_is_rejected_before_child(environment):
    environment["MIGRATION_DATABASE_URL"] = "owner-secret"
    with pytest.raises(RuntimeError, match="sanitized API launcher"):
        Harness().run(environment)


def test_direct_module_invocation_reexecs_before_processing_configuration(monkeypatch):
    source = {key: "owner-secret" for key in start_api.ONE_TIME_KEYS}
    source.update(ORION_ORIGIN_SSH_PRIVATE_KEY="synthetic", PORT="8080")
    monkeypatch.setattr(ingress.os, "environ", source)
    executed = []
    monkeypatch.setattr(ingress.os, "execve", lambda *args: executed.append(args))
    monkeypatch.setattr(ingress, "supervise", lambda _: pytest.fail("Must exec before supervising"))
    ingress.main()
    assert executed[0][1] == [ingress.sys.executable, "-m", "scripts.reverse_ingress"]
    assert not set(executed[0][2]) & start_api.ONE_TIME_KEYS
    assert "MIGRATION_DATABASE_URL" in source


def test_signal_handlers_are_restored_after_install(environment, monkeypatch):
    previous = object()
    calls = []
    monkeypatch.setattr(ingress.signal, "signal", lambda *args: calls.append(args) or previous)
    harness = Harness()
    assert harness.run(environment, install_signals=True) == 0
    installed = len(calls) // 2
    assert installed >= 2
    assert all(handler is previous for _, handler in calls[installed:])


@pytest.mark.parametrize("status,body,expected", [(200, b'{"status":"ok"}', True),
                                                  (503, b'{"status":"ok"}', False),
                                                  (302, b"", False), (200, b"invalid", False)])
def test_local_health_uses_only_loopback_without_redirect_or_proxy(monkeypatch, status, body, expected):
    calls = []
    class Connection:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
        def request(self, *args):
            assert args == ("GET", "/health")
        def getresponse(self):
            return type("Response", (), {"status": status, "read": lambda self, size: body})()
        def close(self):
            calls.append("closed")
    monkeypatch.setattr(ingress.http.client, "HTTPConnection", Connection)
    assert ingress.local_health("8080") is expected
    assert calls[0] == (("127.0.0.1", 8080), {"timeout": 0.4})
    assert calls[-1] == "closed"
