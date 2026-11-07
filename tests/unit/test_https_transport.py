"""Synthetic three-child transport tests: no app, network, SSH or real process."""
import base64
from pathlib import Path
import signal
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
        "ORION_ORIGIN_HTTPS_FINGERPRINT": base64.b64encode(bytes(range(32))).decode("ascii"),
        "ORION_ORIGIN_HTTPS_AUTH": "origin-https:" + base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("="),
        "PORT": "8080", "WEB_CONCURRENCY": "3", "APP_DATABASE_URL": "synthetic-runtime",
        "QR_BRIDGE_TOKEN": "unchanged-session", "AUTH": "must-not-inherit",
        "CHISEL_WS_TIMEOUT": "must-not-inherit", "HTTPS_PROXY": "must-not-inherit",
        "PATH": "must-not-inherit",
    }


class Child:
    def __init__(self, name):
        self.name = name
        self.returncode = None
        self.sent = []
        self.reaped = 0
        self.pid = 123456  # Never supplied to a real OS process operation.

    def poll(self):
        return self.returncode

    def send_signal(self, number):
        self.sent.append(number)
        if number in (signal.SIGTERM, signal.SIGINT):
            self.returncode = -number

    def wait(self, timeout=None):
        assert self.returncode is not None
        self.reaped += 1
        return self.returncode


class Harness:
    def __init__(self):
        self.now = 0.0
        self.calls = []
        self.api = Child("api")
        self.https = []
        self.ssh = []
        self.paths = []
        self.stops = []
        self.health_calls = 0
        self.ready = True
        self.signals = ingress.Signals()
        self.on_sleep = lambda state: setattr(state.api, "returncode", 0) if state.now >= 1 else None

    def popen(self, arguments, **kwargs):
        self.calls.append((self.now, arguments, kwargs))
        assert kwargs["start_new_session"] is True
        if arguments[1:3] == ["-m", "uvicorn"]:
            assert not self.https and not self.ssh
            return self.api
        assert self.health_calls > 0
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        if arguments[0] == "/usr/local/bin/chisel":
            assert all(child.poll() is not None and child.reaped == 1 for child in self.https)
            child = Child("https")
            self.https.append(child)
            return child
        assert arguments[0] == "/usr/bin/ssh"
        assert self.https and self.https[-1].poll() is None
        assert all(child.poll() is not None and child.reaped == 1 for child in self.ssh)
        identity = Path(arguments[arguments.index("-i") + 1])
        known_hosts = Path(next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("UserKnownHostsFile=")))
        self.paths = [identity, known_hosts]
        assert all(path.is_file() for path in self.paths)
        assert known_hosts.read_bytes().startswith(b"[127.0.0.1]:18446 ssh-ed25519 ")
        child = Child("ssh")
        self.ssh.append(child)
        return child

    def health(self, port):
        assert port == "8080"
        self.health_calls += 1
        return self.ready

    def sleep(self, duration):
        self.now += duration
        self.on_sleep(self)
        assert self.now < 100, "A synthetic lifecycle must terminate"

    def stop(self, child, number=signal.SIGTERM, *, timeout=ingress.SHUTDOWN_SECONDS):
        if child is not None:
            assert all(path.exists() for path in self.paths)
            self.stops.append((child.name, timeout))
        ingress.stop_process(child, number, timeout=timeout,
                             kill_group=lambda _: pytest.fail("No real process group"))

    def run(self, environment, **overrides):
        options = dict(popen=self.popen, health=self.health, monotonic=lambda: self.now,
                       sleep=self.sleep, signals=self.signals, install_signals=False, stop=self.stop)
        options.update(overrides)
        return ingress.supervise(environment, **options)


def test_https_transport_has_three_separate_children_with_narrow_environments(environment):
    harness = Harness()
    assert harness.run(environment) == 0
    assert len(harness.calls) == 3
    api, https, ssh = harness.calls
    assert api[1] == ingress.api_arguments("8080")
    assert api[2]["env"]["WEB_CONCURRENCY"] == "3"
    assert api[2]["env"]["APP_DATABASE_URL"] == "synthetic-runtime"
    assert api[2]["env"]["QR_BRIDGE_TOKEN"] == "unchanged-session"
    assert not set(api[2]["env"]) & start_api.SSH_KEYS
    assert https[2]["env"] == {
        "PATH": "/usr/bin:/bin", "AUTH": environment["ORION_ORIGIN_HTTPS_AUTH"],
        "CHISEL_WS_TIMEOUT": "10s", "CHISEL_SSH_TIMEOUT": "10s", "CHISEL_SSH_WAIT": "10s",
    }
    assert ssh[2]["env"] == {"PATH": "/usr/bin:/bin"}
    for child in (https, ssh):
        args = " ".join(child[1])
        assert environment["ORION_ORIGIN_HTTPS_AUTH"] not in args
        assert environment["ORION_ORIGIN_SSH_PRIVATE_KEY"] not in args
        assert "synthetic-runtime" not in args and "unchanged-session" not in args
    assert harness.stops == [("api", 20.0), ("ssh", 3.0), ("https", 3.0)]
    assert harness.api.reaped == harness.https[0].reaped == harness.ssh[0].reaped == 1
    assert all(not path.exists() for path in harness.paths)


def test_https_arguments_use_exact_pins_sni_path_and_one_normal_fixed_tcp_mapping(environment):
    config = ingress.configuration(environment)
    assert ingress.https_arguments(config) == [
        "/usr/local/bin/chisel", "client", "--fingerprint", environment["ORION_ORIGIN_HTTPS_FINGERPRINT"],
        "--hostname", "orionagenda.pro", "--sni", "orionagenda.pro", "--keepalive", "15s",
        "--min-retry-interval", "1s", "--max-retry-interval", "2s",
        "https://187.77.255.164:443/_orion-origin-https", "127.0.0.1:18446:127.0.0.1:22",
    ]
    arguments = ingress.ssh_arguments(config, "/private/key", "/private/pin")
    assert arguments[-5:] == ["-p", "18446", "-R", "127.0.0.1:18445:127.0.0.1:8080", "orion-origin@127.0.0.1"]
    assert "StrictHostKeyChecking=yes" in arguments and "UserKnownHostsFile=/private/pin" in arguments
    for forbidden in ("ProxyCommand", "ProxyJump", "-A", "-D", "-L", "-f"):
        assert not any(forbidden in arg for arg in arguments)
    assert config.known_hosts == (environment["ORION_ORIGIN_SSH_KNOWN_HOSTS"] + "\n").encode("ascii")
    assert environment["ORION_ORIGIN_HTTPS_AUTH"] not in repr(config)
    assert "PRIVATE KEY" not in repr(config)


def test_https_temporary_pin_changes_only_the_endpoint_and_not_the_server_key(environment):
    config = ingress.configuration(environment)
    with ingress.credential_files(config) as (identity, known_hosts):
        assert Path(identity).read_bytes() == config.private_key
        assert Path(known_hosts).read_bytes() == b"[127.0.0.1]:18446 " + config.known_hosts.split(b" ", 1)[1]
        assert b"187.77.255.164" not in Path(known_hosts).read_bytes()
    assert not Path(identity).parent.exists()


def test_unready_api_does_not_start_either_transport(environment):
    harness = Harness()
    harness.ready = False
    def advance(state):
        if state.now < 0.6:
            assert len(state.calls) == 1
        else:
            state.ready = True
        if state.now >= 1:
            state.api.returncode = 0
    harness.on_sleep = advance
    assert harness.run(environment) == 0
    assert len(harness.https) == len(harness.ssh) == 1
    assert harness.calls[1][0] >= 0.6


def test_https_crash_reaps_existing_ssh_before_recreating_both_transports(environment):
    harness = Harness()
    def advance(state):
        if len(state.https) == 1:
            state.https[0].returncode = 9
        if len(state.https) == 2:
            assert state.https[0].reaped == 1
            assert state.ssh[0].reaped == 1
            assert state.ssh[0].sent == [signal.SIGTERM]
            state.api.returncode = 0
    harness.on_sleep = advance
    assert harness.run(environment) == 0
    assert len(harness.https) == len(harness.ssh) == 2
    https_calls = [call for call in harness.calls if call[1][0] == "/usr/local/bin/chisel"]
    assert https_calls[1][0] - 0.2 >= ingress.RETRY_SECONDS
    assert harness.api.sent == []
    assert all(child.reaped == 1 for child in harness.https + harness.ssh)


def test_ssh_failure_retries_without_restarting_a_live_https_client(environment):
    harness = Harness()
    def advance(state):
        if len(state.ssh) == 1:
            state.ssh[0].returncode = 255
        if len(state.ssh) == 2:
            state.api.returncode = 0
    harness.on_sleep = advance
    assert harness.run(environment) == 0
    assert len(harness.https) == 1 and len(harness.ssh) == 2
    ssh_calls = [call for call in harness.calls if call[1][0] == "/usr/bin/ssh"]
    assert ssh_calls[1][0] - 0.2 >= ingress.RETRY_SECONDS
    assert all(child.reaped == 1 for child in harness.https + harness.ssh)


def test_signal_shutdown_drains_api_then_reaps_both_transports_within_configured_budget(environment):
    harness = Harness()
    harness.on_sleep = lambda state: state.signals.receive(signal.SIGTERM, None)
    assert harness.run(environment) == 0
    assert harness.stops == [("api", 20.0), ("ssh", 3.0), ("https", 3.0)]
    assert sum(timeout for _, timeout in harness.stops) == 26.0
    assert all(child.sent == [signal.SIGTERM] and child.reaped == 1
               for child in [harness.api, *harness.ssh, *harness.https])


@pytest.mark.parametrize("binary", ["/usr/local/bin/chisel", "/usr/bin/ssh"])
def test_failed_transport_spawn_reaps_every_started_child_without_leaking_errors(environment, binary, capsys):
    harness = Harness()
    def popen(arguments, **kwargs):
        if arguments[0] == binary:
            raise FileNotFoundError("sensitive credential data must never be logged")
        return harness.popen(arguments, **kwargs)
    assert harness.run(environment, popen=popen) == 1
    assert harness.api.reaped == 1 and harness.api.sent == [signal.SIGTERM]
    assert all(child.reaped == 1 for child in harness.https)
    assert not harness.ssh
    assert capsys.readouterr().err == "Reverse ingress could not start its required process\n"


def test_signal_during_health_cannot_start_https_or_ssh(environment):
    harness = Harness()
    def health(_port):
        harness.signals.receive(signal.SIGTERM, None)
        return True
    assert harness.run(environment, health=health) == 0
    assert len(harness.calls) == 1
    assert not harness.https and not harness.ssh


def test_signal_between_https_and_ssh_spawns_does_not_leave_an_extra_child(environment):
    harness = Harness()
    def popen(arguments, **kwargs):
        child = harness.popen(arguments, **kwargs)
        if arguments[0] == "/usr/local/bin/chisel":
            harness.signals.receive(signal.SIGTERM, None)
        return child
    assert harness.run(environment, popen=popen) == 0
    assert len(harness.https) == 1 and not harness.ssh
    assert harness.stops == [("api", 20.0), ("https", 3.0)]
    assert harness.api.reaped == harness.https[0].reaped == 1


def test_an_https_child_that_exits_at_spawn_is_reaped_without_starting_ssh(environment):
    harness = Harness()
    def popen(arguments, **kwargs):
        child = harness.popen(arguments, **kwargs)
        if arguments[0] == "/usr/local/bin/chisel":
            child.returncode = 9
        return child
    assert harness.run(environment, popen=popen) == 0
    assert len(harness.https) == 1 and not harness.ssh
    assert harness.https[0].reaped == 1


@pytest.mark.parametrize("status,expected", [(7, 7), (-9, 137)])
def test_api_exit_status_is_preserved_while_both_transports_are_reaped(environment, status, expected):
    harness = Harness()
    harness.on_sleep = lambda state: setattr(state.api, "returncode", status)
    assert harness.run(environment) == expected
    assert harness.stops == [("api", 20.0), ("ssh", 3.0), ("https", 3.0)]
    assert all(child.reaped == 1 for child in [harness.api, *harness.ssh, *harness.https])


def test_master_control_signal_is_sent_only_to_api_not_either_transport(environment):
    harness = Harness()
    control = getattr(signal, "SIGHUP", signal.SIGABRT)
    harness.signals.forward.append(control)
    assert harness.run(environment) == 0
    assert harness.api.sent == [control]
    assert all(control not in child.sent for child in harness.https + harness.ssh)


def test_https_internal_reconnect_does_not_create_duplicate_live_clients(environment):
    # Chisel owns its retry loop while its PID remains alive. The supervisor
    # must not spawn extra clients merely because network readiness is unknown.
    harness = Harness()
    harness.on_sleep = lambda state: setattr(state.api, "returncode", 0) if state.now >= 6 else None
    assert harness.run(environment) == 0
    assert len(harness.https) == len(harness.ssh) == 1


@pytest.mark.parametrize("name", ["ORION_ORIGIN_HTTPS_FINGERPRINT", "ORION_ORIGIN_HTTPS_AUTH"])
def test_partial_https_configuration_is_rejected_before_any_child(environment, name):
    environment.pop(name)
    harness = Harness()
    with pytest.raises(RuntimeError, match="both HTTPS settings"):
        harness.run(environment)
    assert not harness.calls


@pytest.mark.parametrize("fingerprint", [
    "", "not-a-fingerprint", "A" * 43, "A" * 44, "A" * 42 + "B=",
    "A" * 43 + "=\n", "A" * 43 + "==", "A" * 43 + "= ",
    base64.urlsafe_b64encode(b"\xff" * 32).decode("ascii"), "é" * 44,
], ids=["empty", "garbage", "unpadded", "wrong-size", "noncanonical-padding-bits",
        "newline", "extra-padding", "space", "urlsafe-alphabet", "non-ascii"])
def test_https_fingerprint_must_be_canonical_standard_base64_sha256(environment, fingerprint):
    environment["ORION_ORIGIN_HTTPS_FINGERPRINT"] = fingerprint
    with pytest.raises(RuntimeError) as error:
        Harness().run(environment)
    assert str(error.value) in ("Reverse ingress requires both HTTPS settings", "Invalid reverse ingress HTTPS configuration")
    assert fingerprint not in str(error.value) if fingerprint else True


@pytest.mark.parametrize("auth", [
    "", "wrong-user:" + "A" * 43, "origin-https:" + "A" * 42,
    "origin-https:" + "A" * 44, "origin-https:" + "A" * 42 + "B",
    "origin-https:" + "A" * 43 + "=", "origin-https:" + "/" * 43,
    "origin-https:" + "A" * 43 + "\n", "origin-https:é", "origin-https:origin-https:" + "A" * 43,
], ids=["empty", "wrong-user", "short-token", "long-token", "noncanonical-padding-bits",
        "padded-token", "wrong-alphabet", "newline", "non-ascii", "duplicate-prefix"])
def test_https_auth_is_fixed_user_and_canonical_urlsafe_32byte_token(environment, auth):
    environment["ORION_ORIGIN_HTTPS_AUTH"] = auth
    with pytest.raises(RuntimeError) as error:
        Harness().run(environment)
    assert str(error.value) in ("Reverse ingress requires both HTTPS settings", "Invalid reverse ingress HTTPS configuration")
    assert auth not in str(error.value) if auth else True


def test_https_transport_does_not_relax_original_vps_known_host_validation(environment):
    key = environment["ORION_ORIGIN_SSH_KNOWN_HOSTS"].split(" ", 1)[1]
    environment["ORION_ORIGIN_SSH_KNOWN_HOSTS"] = "[127.0.0.1]:18446 " + key
    with pytest.raises(RuntimeError, match="Invalid reverse ingress SSH configuration"):
        Harness().run(environment)


def test_short_transport_shutdown_timeout_kills_and_reaps_unresponsive_group():
    child = Child("https")
    killed = []
    child.send_signal = child.sent.append
    def wait(timeout=None):
        if timeout is not None:
            assert timeout == 3.0
            raise subprocess.TimeoutExpired("synthetic", timeout)
        assert killed == [child]
        child.reaped += 1
    child.wait = wait
    ingress.stop_process(child, timeout=ingress.TRANSPORT_SHUTDOWN_SECONDS, kill_group=killed.append)
    assert child.sent == [signal.SIGTERM] and child.reaped == 1
