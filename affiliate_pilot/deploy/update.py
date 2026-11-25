"""Deploy a committed pilot-only release while retaining the live linked device."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


NAME = "orion-afiliados-piloto"
SERVICES = (NAME, NAME + "-whatsapp")
APP_ONLY = sys.argv[1:] == ["--app-only"]
assert sys.argv[1:] in ([], ["--app-only"])
ROOT = Path("/opt") / NAME
PRIVATE = Path("/root") / NAME
STAGED = Path("/root/manga-systematic-20261104/stage/orion-afiliados-channel")
os.umask(0o077)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=40).stdout.strip()


def settings(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


config = settings(PRIVATE / "runtime.env")
origin = config["AFFILIATE_PUBLIC_ORIGIN"]
wa_origin = f"http://127.0.0.1:{config['AFFILIATE_WA_PORT']}"
wa_headers = {"Authorization": "Bearer " + config["AFFILIATE_WA_TOKEN"]}


def fetch(url, *, method="GET", payload=None, headers=None):
    headers = {**(headers or {}), "Origin": origin}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers, data=data, method=method), timeout=32) as res:
        raw = res.read(1_000_000)
        return raw, res.headers


def read_json(url, **opts):
    return json.loads(fetch(url, **opts)[0])


def until(check, seconds=45):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if candidate := check():
                return candidate
        except OSError:
            pass
        time.sleep(1.2)
    raise RuntimeError("A validação do serviço após a troca de versão expirou.")


def saved_companion_version(bridge_source):
    directory = Path("/var/lib") / SERVICES[1] / "whatsapp-auth"
    probe = r"""
import fs from "node:fs";
import {createHmac} from "node:crypto";
import {BufferJSON} from "@whiskeysockets/baileys";
import {decodeAuthRecord} from "./qr-bridge/src/durable-auth.mjs";
const directory = process.env.PILOT_AUTH_DIRECTORY;
const secret = process.env.AFFILIATE_WA_TOKEN;
const {value} = decodeAuthRecord(fs.readFileSync(directory + "/creds.json"), {
    directory, secret, name: "creds.json", deserialize: data => JSON.parse(data, BufferJSON.reviver),
});
if (!value.account?.accountSignature || !/^[1-9]\d{7,14}:\d+@s\.whatsapp\.net$/.test(value.me?.id)) process.exit(1);
process.stdout.write(createHmac("sha256", secret).update(value.me.id.replace(/:\d+@/, "@")).digest("hex"));
"""
    result = subprocess.run(["node", "--input-type=module", "-"], cwd=bridge_source, input=probe,
                            env={**os.environ, "AFFILIATE_WA_TOKEN": config["AFFILIATE_WA_TOKEN"],
                                 "PILOT_AUTH_DIRECTORY": str(directory)}, text=True, capture_output=True, timeout=12)
    if result.returncode != 0 or not re.fullmatch(r"[a-f0-9]{64}", result.stdout):
        raise RuntimeError("Não foi possível confirmar a identidade da sessão preservada do piloto.")
    return result.stdout


manifest = json.loads((STAGED / "manifest.json").read_text())
new = manifest["commit"]
assert re.fullmatch(r"[a-f0-9]{40}", new)
bundle = STAGED / "release.tar.gz"
assert hashlib.sha256(bundle.read_bytes()).hexdigest() == manifest["archive_sha256"]
assert config["AFFILIATE_ENABLE_TEST_SEND"] == "0"
old_dir = Path(run("systemctl", "show", SERVICES[0], "-P", "WorkingDirectory"))
bridge_dir = Path(run("systemctl", "show", SERVICES[1], "-P", "WorkingDirectory"))
assert old_dir.parent == bridge_dir.parent == ROOT
assert (bridge_dir / "node_modules/@whiskeysockets/baileys/package.json").exists()
release = ROOT / new
assert not release.exists()
geek_pid = run("systemctl", "show", "geekhunter-bot.service", "-P", "MainPID")
assert geek_pid != "0"
assert fetch("https://orionagenda.pro/health")[0]
before = read_json(wa_origin + "/status", headers=wa_headers)
expected_version = before.get("version") or saved_companion_version(bridge_dir)
bridge_pid = run("systemctl", "show", SERVICES[1], "-P", "MainPID")
starting_health = read_json(origin + "/health")
assert not starting_health["external_sends_enabled"]
if APP_ONLY:
    assert before.get("status") == "linked" and bridge_pid != "0"

release.mkdir(mode=0o755)
release.chmod(0o755)
# Git contains only source and static assets. The dynamic service user needs
# to traverse extracted directories while backups and runtime files stay private.
previous_umask = os.umask(0o022)
try:
    with tarfile.open(bundle) as archive:
        archive.extractall(release, filter="data")
finally:
    os.umask(previous_umask)
assert (release / "affiliate_pilot").stat().st_mode & 0o001
assert (release / "affiliate_pilot/whatsapp.mjs").stat().st_mode & 0o004
dependencies = (bridge_dir / "node_modules").resolve(strict=True)
assert dependencies.is_relative_to(ROOT)
(release / "node_modules").symlink_to(dependencies)
run("node", "--check", str(release / "affiliate_pilot/whatsapp.mjs"))
run(str(ROOT / "venv/bin/python"), "-m", "compileall", "-q", str(release / "affiliate_pilot"))
if APP_ONLY:
    bridge_paths = [Path("affiliate_pilot") / name for name in
                    ("whatsapp.mjs", "promotech-publish.mjs", "channel-test.mjs")]
    bridge_paths += [Path("qr-bridge") / name for name in ("package.json", "package-lock.json")]
    # Earlier archives contained only the bridge modules used by this service.
    bridge_paths.extend(path.relative_to(bridge_dir) for path in (bridge_dir / "qr-bridge/src").rglob("*") if path.is_file())
    assert all((bridge_dir / path).read_bytes() == (release / path).read_bytes()
               for path in bridge_paths), "Há alterações no conector que exigem uma atualização completa."
with sqlite3.connect("file:" + config["AFFILIATE_DATABASE"] + "?mode=ro", uri=True) as live:
    with sqlite3.connect(PRIVATE / f"before-{new[:10]}.sqlite3") as backup:
        live.backup(backup)

overrides = {service: Path("/etc/systemd/system") / (service + ".service.d") / "20-release.conf"
             for service in (SERVICES[:1] if APP_ONLY else SERVICES)}
previous = {service: path.read_bytes() if path.exists() else None for service, path in overrides.items()}
activated = []
restarted = []
try:
    for service, path in overrides.items():
        path.parent.mkdir(mode=0o755, exist_ok=True)
        lines = f"[Service]\nWorkingDirectory={release}\n"
        if service == SERVICES[1]:
            lines += f"ExecStart=\nExecStart=/usr/bin/env node {release}/affiliate_pilot/whatsapp.mjs\n"
        path.write_text(lines)
        activated.append(service)
    run("systemctl", "daemon-reload")
    if not APP_ONLY:
        # Stop the original before the replacement uses the same encrypted session.
        restarted.append(SERVICES[1])
        run("systemctl", "restart", SERVICES[1])
    after = until(lambda: (state if (state := read_json(wa_origin + "/status", headers=wa_headers)).get("status")
                           == "linked" else None))
    assert after["version"] == expected_version
    restarted.append(SERVICES[0])
    run("systemctl", "restart", SERVICES[0])
    health = until(lambda: (state if (state := read_json(origin + "/health"))["worker"] else None), 25)
    assert not health["external_sends_enabled"]
    assert health["promotech_configured"] == starting_health["promotech_configured"]
    assert b"/verify-channel" in fetch(origin + "/assets/pilot.js")[0]
    if APP_ONLY:
        for name in ("orion-auth.css", "orion-subscription-dashboard.css"):
            asset, headers = fetch(origin + "/design/" + name)
            assert headers.get_content_type() == "text/css" and asset == (release / "app" / "static" / name).read_bytes()
        assert run("systemctl", "show", SERVICES[1], "-P", "MainPID") == bridge_pid
        assert read_json(wa_origin + "/status", headers=wa_headers)["version"] == expected_version
    assert run("systemctl", "show", "geekhunter-bot.service", "-P", "MainPID") == geek_pid
    assert fetch("https://orionagenda.pro/health")[0]
    record_file = PRIVATE / "published.json"
    published = json.loads(record_file.read_text())
    record_file.write_text(json.dumps({**published, "commit": new, "health": health}, indent=2))
    print(json.dumps({"pilot_commit": new, "same_whatsapp_session": True, "whatsapp_restarted": not APP_ONLY, "worker": True,
                      "geekhunter_same_process": True, "external_sends_enabled": False}), flush=True)
except BaseException:
    for service in activated:
        path, previous_value = overrides[service], previous[service]
        if previous_value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous_value)
    run("systemctl", "daemon-reload")
    for service in restarted:
        run("systemctl", "restart", service)
    raise
