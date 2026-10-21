"""Isolated readiness-guard checks with all socket/DNS access denied."""
from pathlib import Path
import json
import os
import socket
import sys

OUT = Path(__file__).resolve().parent
for key in list(os.environ):
    if any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "DATABASE_URL", "REDIS_URL")) or key.startswith(("QR_", "ORION_", "APP_", "PANEL_", "INBOX_", "META_", "GROQ_", "MARKETING_", "WHATSAPP_")):
        os.environ.pop(key, None)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
attempts = []


def denied(*_args, **_kwargs):
    attempts.append("network_or_dns_attempt_denied")
    raise RuntimeError("external_network_denied_for_guard_tests")


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied
import pytest

result = pytest.main([str(OUT / "test_native_central_e_guard.py"), "-q", "-p", "no:cacheprovider",
                      "--junitxml=" + str(OUT / "native-central-E-guard.xml")])
(OUT / "native-central-E-guard-network.json").write_text(json.dumps({"exit_code": int(result),
    "network_attempts": attempts, "scope": "readiness GET and whole prepared hook with local HTTP, subprocess and version fakes; no production call"}, indent=2), encoding="utf-8")
raise SystemExit(result)
