"""Supervise one API master and one opt-in, pinned OpenSSH reverse tunnel.

Only scripts.start_api should enter this module, after its execve credential
boundary. A read-only schema probe precedes supervision; there are no application
imports, provider calls, or tunnel per worker.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import http.client
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from cryptography.hazmat.primitives import serialization

from scripts.start_api import ONE_TIME_KEYS, SSH_KEYS, runtime_environment, wait_for_campaigns_schema

RETRY_SECONDS = 2.0
POLL_SECONDS = 0.2
STARTUP_SECONDS = 90.0
SHUTDOWN_SECONDS = 20.0


@dataclass(frozen=True)
class TunnelConfig:
    host: str
    port: str
    private_key: bytes = field(repr=False)
    known_hosts: bytes = field(repr=False)


def configuration(environment):
    """Validate without including secret input or parser errors in failures."""
    values = [environment.get(name, "") for name in (
        "ORION_ORIGIN_SSH_HOST", "ORION_ORIGIN_SSH_PRIVATE_KEY", "ORION_ORIGIN_SSH_KNOWN_HOSTS")]
    if not all(values):
        raise RuntimeError("Reverse ingress requires all three SSH settings")
    host, private, known = values
    port = environment.get("PORT", "8000")
    try:
        address = ipaddress.IPv4Address(host)
        if str(address) != host or not address.is_global or address.is_multicast:
            raise ValueError
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError
        if len(private) > 32768 or len(known) > 16384:
            raise ValueError
        key = serialization.load_ssh_private_key(private.encode("ascii"), password=None)
        normalized_private = key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.OpenSSH,
                                               serialization.NoEncryption())
        # One exact host/key, with no wildcard, @cert-authority, hashed host,
        # alternate port, or host-list that could silently widen this pin.
        lines = [line for line in known.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError
        fields = lines[0].split()
        if len(fields) != 3 or fields[0] != host:
            raise ValueError
        public = serialization.load_ssh_public_key(" ".join(fields[1:]).encode("ascii"))
        normalized_public = public.public_bytes(serialization.Encoding.OpenSSH,
                                               serialization.PublicFormat.OpenSSH)
        if normalized_public != " ".join(fields[1:]).encode("ascii"):
            raise ValueError
        normalized_known = host.encode("ascii") + b" " + normalized_public + b"\n"
    except Exception:
        raise RuntimeError("Invalid reverse ingress SSH configuration") from None
    return TunnelConfig(host, port, normalized_private, normalized_known)


@contextmanager
def credential_files(config):
    """Temporary credentials are never placed in argv, logs, or child env."""
    with tempfile.TemporaryDirectory(prefix="orion-origin-") as directory:
        os.chmod(directory, 0o700)
        paths = []
        for name, data in (("identity", config.private_key), ("known_hosts", config.known_hosts)):
            path = Path(directory) / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
            os.chmod(path, 0o600)
            paths.append(str(path))
        yield tuple(paths)


def ssh_arguments(config, identity, known_hosts):
    return [
        "/usr/bin/ssh", "-N", "-T", "-F", "/dev/null", "-i", identity,
        "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no", "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10",
        "-R", f"127.0.0.1:18445:127.0.0.1:{config.port}", f"orion-origin@{config.host}",
    ]


def api_arguments(port):
    # The authenticated SSH forward connects to API loopback. Trust only this
    # local proxy, whose Nginx overwrites scheme/IP headers from the real peer.
    # Public Railway requests retain the ordinary launcher's loopback trust.
    return [sys.executable, "-m", "uvicorn", "app.main:app", "--no-access-log",
            "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,::1",
            "--host", "0.0.0.0", "--port", port]


def local_health(port):
    """Only loopback, no proxy env or redirects and no DB/provider operation."""
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=0.4)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read(256)) == {"status": "ok"}
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()


class Signals:
    def __init__(self):
        self.stop = None
        self.forward = []
        self.previous = {}

    def receive(self, number, _frame):
        if number in (signal.SIGINT, signal.SIGTERM):
            self.stop = number
        else:
            self.forward.append(number)

    def install(self):
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGTTIN", "SIGTTOU"):
            number = getattr(signal, name, None)
            if number is not None:
                self.previous[number] = signal.signal(number, self.receive)

    def restore(self):
        for number, handler in self.previous.items():
            signal.signal(number, handler)


def kill_process_group(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()


def stop_process(process, number=signal.SIGTERM, *, kill_group=kill_process_group):
    if process is None:
        return
    if process.poll() is None:
        try:
            process.send_signal(number)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=SHUTDOWN_SECONDS)
    except subprocess.TimeoutExpired:
        kill_group(process)
        process.wait()


def supervise(environment, *, popen=None, health=None, monotonic=None, sleep=None,
              signals=None, install_signals=True, stop=None):
    """Lifecycle dependencies are injectable; unit tests never spawn or connect."""
    # Defense in depth for direct invocation: entrypoint below re-execs first;
    # do not silently keep owner credentials in an unexpected supervisor.
    if set(environment) & ONE_TIME_KEYS:
        raise RuntimeError("Reverse ingress requires the sanitized API launcher")
    config = configuration(environment)
    popen = popen or subprocess.Popen
    health = health or local_health
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    signals = signals or Signals()
    stop = stop or stop_process
    child_environment = {key: value for key, value in environment.items() if key not in SSH_KEYS}
    if install_signals:
        signals.install()
    try:
        with credential_files(config) as (identity, known_hosts):
            return supervise_children(config, identity, known_hosts, child_environment,
                                      popen, health, monotonic, sleep, signals, stop)
    except OSError:
        # Do not print exception details: they can include a credential path.
        print("Reverse ingress could not start its required process", file=sys.stderr)
        return 1
    finally:
        if install_signals:
            signals.restore()


def supervise_children(config, identity, known_hosts, child_environment,
                       popen, health, monotonic, sleep, signals, stop):
    api = tunnel = None
    try:
        # A signal can arrive after handler installation but before spawn.
        if signals.stop is not None:
            return 0
        api = popen(api_arguments(config.port), env=child_environment, start_new_session=True)
        deadline = monotonic() + STARTUP_SECONDS
        ready = False
        retry_at = monotonic()
        while signals.stop is None:
            status = api.poll()
            if status is not None:
                return status if status >= 0 else 128 - status
            while signals.forward:
                number = signals.forward.pop(0)
                try:
                    api.send_signal(number)  # master only, never its workers' group
                except ProcessLookupError:
                    pass
            if signals.stop is not None:
                break
            if not ready:
                ready = health(config.port)
                if not ready and monotonic() >= deadline:
                    print("Reverse ingress API readiness timed out", file=sys.stderr)
                    return 1
            if tunnel is not None and tunnel.poll() is not None:
                tunnel.wait()
                tunnel = None
                retry_at = monotonic() + RETRY_SECONDS
            # Do not gate API health on the remote bind: an older container
            # may own it until Railway drains that deployment.
            if ready and tunnel is None and monotonic() >= retry_at and signals.stop is None:
                tunnel = popen(ssh_arguments(config, identity, known_hosts),
                               env={"PATH": "/usr/bin:/bin"}, start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            sleep(POLL_SECONDS)
        return 0
    finally:
        # This runs inside credential_files: drain/reap both children before
        # removing the identity, including when a spawn or readiness call fails.
        stop(api, signals.stop or signal.SIGTERM)
        stop(tunnel)


def main():
    environment = runtime_environment(os.environ)
    if set(os.environ) & ONE_TIME_KEYS:
        # Also preserve the exec barrier if explicitly invoked outside start_api.
        os.execve(sys.executable, [sys.executable, "-m", "scripts.reverse_ingress"], environment)
        return
    try:
        # Validate the opt-in settings before any child. The schema probe runs
        # after the clean exec boundary and receives neither owner nor SSH keys.
        configuration(environment)
        wait_for_campaigns_schema({key: value for key, value in environment.items() if key not in SSH_KEYS})
        result = supervise(environment)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        print("Reverse ingress schema readiness failed", file=sys.stderr)
        result = 1
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
