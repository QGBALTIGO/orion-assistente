"""Apply a reviewed analytics release without restarting the WhatsApp session.

Run on the authorized VPS with its existing venv and a private JSON manifest.
The manifest contains a pinned commit, hashes for every overlay source file,
the expected live directory, and hashes of its original pilot files. It contains
no credentials. Source archives are downloaded from the project's GitHub repo.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

from cryptography.fernet import Fernet


NAME = "orion-afiliados-piloto"
ROOT = Path("/opt") / NAME
PRIVATE = Path("/root") / NAME
PYTHON = ROOT / "venv/bin/python"


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=45).stdout.strip()


def prop(service, field):
    return run("systemctl", "show", service, "-P", field)


def read_json(path, headers=None):
    req = urllib.request.Request("http://127.0.0.1:18067" + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as response:
        assert response.headers.get_content_type() == "application/json"
        return json.load(response)


def wait_for(check, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("O serviço não passou pela verificação após a atualização.")


def main(manifest_path):
    os.umask(0o077)
    manifest = json.loads(Path(manifest_path).read_text())
    commit = manifest["commit"]
    assert re.fullmatch(r"[a-f0-9]{40}", commit)
    old = Path(prop(NAME, "WorkingDirectory"))
    assert str(old) == manifest["base_directory"] and old.parent == ROOT
    assert prop(NAME, "ActiveState") == "active"
    release = ROOT / commit
    assert not release.exists(), "A versão de destino já existe. Inspecione antes de tentar novamente."
    for name, digest in manifest["base_hashes"].items():
        path = old / name
        assert path.resolve().is_relative_to(old) and hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    before = read_json("/health")
    assert before["worker"] and not before["external_sends_enabled"]
    companions = {service: prop(service, "MainPID") for service in
                  (NAME + "-whatsapp", "geekhunter-bot.service")}
    assert all(pid != "0" for pid in companions.values())
    runtime = dict(entry.split(b"=", 1) for entry in Path("/proc", prop(NAME, "MainPID"), "environ").read_bytes().split(b"\0") if b"=" in entry)
    database = runtime[b"AFFILIATE_DATABASE"].decode()
    signer = Fernet(runtime[b"ORION_AFFILIATE_WORKSPACE_KEY"])
    tenant = manifest["verify_tenant"]
    assert re.fullmatch(r"[a-f0-9]{32}", tenant)
    with sqlite3.connect("file:" + database + "?mode=ro", uri=True) as db:
        company = db.execute("SELECT name FROM company WHERE username=?", ("orion-" + tenant,)).fetchone()
        assert company is not None
        with sqlite3.connect(PRIVATE / ("before-analytics-" + commit[:12] + ".sqlite3")) as backup:
            db.backup(backup)
    url = "https://api.github.com/repos/QGBALTIGO/orion-assistente/tarball/" + commit
    req = urllib.request.Request(url, headers={"User-Agent": "Orion-analytics-release"})
    with urllib.request.urlopen(req, timeout=45) as response:
        bundle = response.read(64 * 1024 * 1024 + 1)
    assert len(bundle) <= 64 * 1024 * 1024
    overlay = {}
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        for member in archive:
            name = member.name.partition("/")[2]
            if name not in manifest["files"]:
                continue
            assert member.isfile() and not member.issym() and member.size <= 2 * 1024 * 1024
            data = archive.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == manifest["files"][name], name
            overlay[name] = data
    assert set(overlay) == set(manifest["files"])
    assert "app/affiliate_analytics.py" in overlay and "affiliate_pilot/analytics.py" in overlay
    previous_umask = os.umask(0o022)
    try:
        shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
        dependency_dir = (old / "node_modules").resolve(strict=True)
        assert dependency_dir.is_relative_to(ROOT)
        (release / "node_modules").symlink_to(dependency_dir)
        for name, data in overlay.items():
            path = release / name
            assert path.resolve().is_relative_to(release)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
        release.chmod(0o755)
    finally:
        os.umask(previous_umask)
    run(str(PYTHON), "-m", "compileall", "-q", str(release / "affiliate_pilot"))
    run(str(PYTHON), "-c", "from affiliate_pilot.main import app; from app.affiliate_analytics import analytics_report_valid; assert any(r.path == '/api/analytics' for r in app.routes)", cwd=release)
    override = Path("/etc/systemd/system") / (NAME + ".service.d/20-release.conf")
    original = override.read_bytes() if override.exists() else None
    try:
        override.parent.mkdir(mode=0o755, exist_ok=True)
        override.write_text("[Service]\nWorkingDirectory=" + str(release) + "\n")
        override.chmod(0o644)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", NAME)
        health = wait_for(lambda: state if (state := read_json("/health")).get("worker") else None)
        assert not health["external_sends_enabled"]
        assert health["promotech_configured"] == before["promotech_configured"]
        for service, pid in companions.items():
            assert prop(service, "MainPID") == pid and prop(service, "ActiveState") == "active"
        try:
            read_json("/api/analytics")
            raise AssertionError("O relatório aceitou uma chamada anônima")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        claim = {"tenant": tenant, "name": company[0], "method": "GET", "path": "/api/analytics"}
        token = signer.encrypt(json.dumps(claim).encode()).decode()
        report = read_json("/api/analytics?period=30d&timezone=America%2FCampo_Grande",
                           {"X-Orion-Affiliate-Access": token})
        sys.path.insert(0, str(release))
        from app.affiliate_analytics import analytics_report_valid
        assert analytics_report_valid(report, period="30d")
        published_path = PRIVATE / "published.json"
        published = json.loads(published_path.read_text())
        published_path.write_text(json.dumps({**published, "commit": commit, "health": health}, indent=2))
        receipt = {"commit": commit, "health": health, "same_whatsapp_process": True,
                   "same_geekhunter_process": True, "anonymous_blocked": True,
                   "report_status": report["sources"]["sales"]["status"],
                   "period": report["period"], "summary": report["summary"]}
        (PRIVATE / ("analytics-release-" + commit[:12] + ".json")).write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt, ensure_ascii=True), flush=True)
    except BaseException:
        if original is None:
            override.unlink(missing_ok=True)
        else:
            override.write_bytes(original)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", NAME)
        # Additive columns/tables remain compatible with the preceding source.
        # Never rewind a live queue to the backup and lose intervening events.
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Use analytics_update.py /caminho/manifest.json")
    main(sys.argv[1])
