"""First deployment of the isolated pilot on its own hostname and port."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


HOST = "orion-afiliados-piloto.187-77-255-164.sslip.io"
ORIGIN = "https://" + HOST
PORT = 18067
WA_PORT = 18068
NAME = "orion-afiliados-piloto"
ROOT = Path("/opt") / NAME
PRIVATE = Path("/root") / NAME
STAGED = Path("/root/manga-systematic-20261104/stage/orion-afiliados-20261124")
UNIT = Path("/etc/systemd/system") / f"{NAME}.service"
WA_NAME = f"{NAME}-whatsapp"
WA_UNIT = Path("/etc/systemd/system") / f"{WA_NAME}.service"
NGINX = Path("/etc/nginx/sites-available") / NAME
ENABLED = Path("/etc/nginx/sites-enabled") / NAME
ACME = Path("/var/www") / NAME
CERT = Path("/etc/letsencrypt/live") / HOST


def run(args, timeout=35, environment=None):
    process = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=environment)
    if process.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: " + process.stderr[-1300:])
    return process.stdout


def fetch(url, payload=None, headers=None):
    args = dict(headers=headers or {})
    if payload is not None:
        args.update(data=json.dumps(payload).encode(), method="POST")
        args["headers"].update({"Content-Type": "application/json", "Origin": ORIGIN})
    with urllib.request.urlopen(urllib.request.Request(url, **args), timeout=14) as response:
        return response.status, response.read(150_000), response.headers


def same_service(name):
    return {line.split("=", 1)[0]: line.split("=", 1)[1] for line in run([
        "systemctl", "show", name, "-p", "ActiveState,MainPID",
    ]).splitlines()}


assert socket.gethostbyname(HOST) == "187.77.255.164"
assert not any(path.exists() or path.is_symlink() for path in (ROOT, UNIT, WA_UNIT, NGINX, ENABLED))
assert not any(HOST in path.read_text(errors="replace") for path in
               Path("/etc/nginx/sites-enabled").iterdir() if path.is_file())
for port in (PORT, WA_PORT):
    with socket.socket() as available:
        available.bind(("127.0.0.1", port))
baseline = same_service("geekhunter-bot.service")
assert baseline["ActiveState"] == "active"
original_health = fetch("https://orionagenda.pro/health")
assert original_health[0] == 200
manifest = json.loads((STAGED / "manifest.json").read_text())
bundle = STAGED / "release.tar.gz"
assert hashlib.sha256(bundle.read_bytes()).hexdigest() == manifest["archive_sha256"]

PRIVATE.mkdir(mode=0o700, exist_ok=True)
ROOT.mkdir(mode=0o755)
release = ROOT / manifest["commit"]
release.mkdir(mode=0o755)
with tarfile.open(bundle) as src:
    src.extractall(release, filter="data")
assert (release / "affiliate_pilot/static/index.html").is_file()
print("Orion isolated release extracted", flush=True)
venv = ROOT / "venv"
run(["python3", "-m", "venv", str(venv)], 50)
run(["nice", "-n", "10", str(venv / "bin/pip"), "install", "--quiet", "--no-cache-dir",
     "-r", str(release / "affiliate_pilot/requirements.txt")], 200)
run([str(venv / "bin/python"), "-m", "compileall", "-q", str(release / "affiliate_pilot")], 30)
print("Isolated Python dependencies ready", flush=True)
run(["nice", "-n", "10", "npm", "ci", "--prefix", str(release / "qr-bridge"), "--omit=dev",
     "--ignore-scripts", "--no-audit", "--no-fund", "--fetch-retries=1"], 160, {
    **os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf",
    "GIT_CONFIG_VALUE_0": "ssh://git@github.com/", "GIT_TERMINAL_PROMPT": "0",
})
(release / "node_modules").symlink_to(release / "qr-bridge/node_modules")
run(["node", "--check", str(release / "affiliate_pilot/whatsapp.mjs")])
print("Dedicated WhatsApp dependencies ready", flush=True)

password = secrets.token_urlsafe(36)
key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
wa_token = secrets.token_urlsafe(44)
wa_runtime = PRIVATE / "whatsapp.env"
with wa_runtime.open("x") as file:
    os.fchmod(file.fileno(), 0o600)
    file.write(f"AFFILIATE_WA_TOKEN={wa_token}\nAFFILIATE_WA_PORT={WA_PORT}\n")
env = PRIVATE / "runtime.env"
with env.open("x") as file:
    os.fchmod(file.fileno(), 0o600)
    file.write(
        f"AFFILIATE_DATABASE=/var/lib/{NAME}/affiliates.sqlite3\n"
        f"AFFILIATE_MASTER_KEY={key}\nAFFILIATE_BOOTSTRAP_PASSWORD={password}\n"
        "AFFILIATE_BOOTSTRAP_USER=piloto\nAFFILIATE_COMPANY_NAME=GeekHunter\n"
        f"AFFILIATE_WA_TOKEN={wa_token}\nAFFILIATE_WA_PORT={WA_PORT}\n"
        f"AFFILIATE_PUBLIC_ORIGIN={ORIGIN}\nAFFILIATE_ENABLE_TEST_SEND=0\n"
    )
UNIT.write_text(f"""[Unit]
Description=Orion Afiliados isolated pilot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
DynamicUser=yes
StateDirectory={NAME}
StateDirectoryMode=0700
WorkingDirectory={release}
EnvironmentFile={env}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={venv}/bin/python -m uvicorn affiliate_pilot.main:app --host 127.0.0.1 --port {PORT} --no-access-log --no-proxy-headers
Restart=on-failure
RestartSec=5
CPUQuota=35%
MemoryMax=256M
TasksMax=28
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
""")
WA_UNIT.write_text(f"""[Unit]
Description=Orion Afiliados dedicated WhatsApp session
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
DynamicUser=yes
StateDirectory={WA_NAME}
StateDirectoryMode=0700
WorkingDirectory={release}
EnvironmentFile={wa_runtime}
ExecStart=/usr/bin/env node {release}/affiliate_pilot/whatsapp.mjs
Restart=on-failure
RestartSec=8
CPUQuota=20%
MemoryMax=200M
TasksMax=24
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
""")

acme_rules = f"""server {{
    listen 80;
    server_name {HOST};
    location ^~ /.well-known/acme-challenge/ {{ root {ACME}; try_files $uri =404; }}
    location / {{ return 301 https://$host$request_uri; }}
}}
"""
tls_rules = f"""server {{
    listen 443 ssl;
    server_name {HOST};
    ssl_certificate {CERT}/fullchain.pem;
    ssl_certificate_key {CERT}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 64k;
    add_header X-Robots-Tag "noindex, nofollow" always;
    location / {{
        proxy_pass http://127.0.0.1:{PORT};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Connection "";
        proxy_read_timeout 55s;
    }}
}}
"""
started = False
wa_started = False
nginx_installed = False
try:
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", WA_NAME])
    wa_started = True
    run(["systemctl", "enable", "--now", NAME])
    started = True
    for _ in range(40):
        try:
            _, body, _ = fetch(f"http://127.0.0.1:{PORT}/health")
            if json.loads(body)["worker"]:
                break
        except OSError:
            time.sleep(.4)
    else:
        raise RuntimeError("Pilot or worker did not start on its loopback port")
    print("Backend and worker healthy on loopback", flush=True)

    ACME.mkdir(mode=0o755, exist_ok=True)
    NGINX.write_text(acme_rules)
    ENABLED.symlink_to(NGINX)
    nginx_installed = True
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    if not (CERT / "fullchain.pem").exists():
        run(["certbot", "certonly", "--webroot", "-w", str(ACME), "-d", HOST, "--non-interactive", "--quiet"],
            150)
    NGINX.write_text(acme_rules + tls_rules)
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    print("Pilot HTTPS configured", flush=True)

    result = {}
    _, body, _ = fetch(ORIGIN + "/health")
    result["health"] = json.loads(body)
    assert result["health"]["worker"] and not result["health"]["external_sends_enabled"]
    assert "Orion Afiliados" in fetch(ORIGIN + "/")[1].decode()
    try:
        fetch(ORIGIN + "/api/state")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
        result["anonymous_blocked"] = True
    else:
        raise RuntimeError("Pilot backend was accessible anonymously")
    # Temporary verification session. The session cookie is never logged.
    _, _, headers = fetch(ORIGIN + "/api/login", {"username": "piloto", "password": password})
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    assert "Secure" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    _, body, _ = fetch(ORIGIN + "/api/state", headers={"Cookie": cookie})
    state = json.loads(body)
    assert state["company"] == "GeekHunter" and not state["sending"] and not state["jobs"]
    assert state["whatsapp_link_available"]
    _, body, _ = fetch(ORIGIN + "/api/connection/whatsapp", headers={"Cookie": cookie})
    assert json.loads(body)["status"] == "unlinked"
    result["whatsapp_isolated_unlinked"] = True
    fetch(ORIGIN + "/api/logout", {}, {"Cookie": cookie})
    assert same_service("geekhunter-bot.service") == baseline
    assert fetch("https://orionagenda.pro/health")[0] == 200
    access = PRIVATE / "acesso-piloto.txt"
    with access.open("x", encoding="utf8") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(f"Orion Afiliados — ambiente piloto\n{ORIGIN}/\n\nUsuário: piloto\nSenha: {password}\n")
    result.update({"url": ORIGIN + "/", "commit": manifest["commit"], "geekhunter_unchanged": True})
    (PRIVATE / "published.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
except BaseException:
    if nginx_installed:
        ENABLED.unlink(missing_ok=True)
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    if started:
        run(["systemctl", "disable", "--now", NAME])
    if wa_started:
        run(["systemctl", "disable", "--now", WA_NAME])
    raise
