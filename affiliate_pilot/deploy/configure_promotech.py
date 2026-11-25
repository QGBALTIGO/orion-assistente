"""Configure the explicitly approved recurring PromoTech rule in the isolated pilot."""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


ROOT = Path("/root/orion-afiliados-piloto")
BINDING = ROOT / "promotech.env"
DESTINATION = "f5fc94167f0142569e7f74dea6dd0648"
CONFIG = dict(line.split("=", 1) for line in (ROOT / "runtime.env").read_text().splitlines())
ORIGIN = CONFIG["AFFILIATE_PUBLIC_ORIGIN"]
assert CONFIG["AFFILIATE_ENABLE_TEST_SEND"] == "0"


def request(url, headers=None, method="GET", data=None, timeout=32):
    headers = {**(headers or {}), "Origin": ORIGIN}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response), response.headers


def run(action):
    _, login = request(ORIGIN + "/api/login", method="POST", data={
        "username": CONFIG["AFFILIATE_BOOTSTRAP_USER"],
        "password": CONFIG["AFFILIATE_BOOTSTRAP_PASSWORD"],
    })
    headers = {"Cookie": login["Set-Cookie"].split(";", 1)[0]}

    def api(path, method="GET", data=None):
        return request(ORIGIN + "/api/" + path, headers, method, data)[0]

    try:
        state = api("state")
        dest = next(item for item in state["destination"] if item["id"] == DESTINATION)
        assert dest["name"].casefold() == "promotech - qg baltigo" and dest["mode"] == "whatsapp_channel"
        assert dest["verified"] and dest["role"] in {"ADMIN", "OWNER"}
        invite = re.fullmatch(r"https://(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9]{20,32})/?(?:\?[^#]*)?",
                              dest["address"])
        assert invite
        verified = api(f"destinations/{DESTINATION}/verify-channel", "POST", {})
        assert all(verified[name] == dest[name] for name in ("channel_jid", "connection_version", "verified"))
        with sqlite3.connect("file:" + CONFIG["AFFILIATE_DATABASE"] + "?mode=ro", uri=True) as db:
            company, = db.execute("SELECT id FROM company WHERE username=?", (CONFIG["AFFILIATE_BOOTSTRAP_USER"],)).fetchone()

        if action == "bind":
            assert not BINDING.exists()
            binding = {"company": company, "destination": DESTINATION, "rule": uuid4().hex,
                       "jid": dest["channel_jid"], "version": dest["connection_version"], "invite": invite[1]}
            os.umask(0o077)
            with BINDING.open("x") as file:
                file.write("AFFILIATE_PROMOTECH_BINDING='" + json.dumps(binding, separators=(",", ":")) + "'\n")
            for unit in ("orion-afiliados-piloto", "orion-afiliados-piloto-whatsapp"):
                dropin = Path("/etc/systemd/system") / (unit + ".service.d") / "37-promotech.conf"
                with dropin.open("x") as file:
                    file.write(f"[Service]\nEnvironmentFile={BINDING}\n")
            print(json.dumps({"dedicated_channel_binding_created": True, "automatic_rule_active": False}))
            return

        assert request(ORIGIN + "/health")[0]["promotech_configured"]
        assert state["promotech_automation"] and not state["sending"]
        assert len(state["niches"]) == 29
        binding = json.loads(BINDING.read_text().partition("=")[2].strip().strip("'"))
        assert binding["company"] == company
        reference = binding["rule"]
        if action == "activate":
            existing = next((entry for entry in state["rule"] if entry["id"] == reference), None)
            assert existing is None or not existing["active"]
            rule = {"name": "PromoTech • Shopee a cada 30 min", "keyword": "",
                    "niches": [entry["id"] for entry in state["niches"]], "sort": 2, "blocked": [],
                    "template_id": state["deal_template"], "destinations": [DESTINATION],
                    "days": list(range(7)), "opens": "00:00", "closes": "00:00",
                    "timezone": "America/Cuiaba", "interval": 30, "daily": 48,
                    "repeat_hours": 48, "mode": "automatic"}
            api("rules/" + reference, "PUT", rule)
            api("rules/" + reference + "/activate", "POST", {"active": True})
        until = time.monotonic() + (100 if action == "activate" else 10)
        while True:
            state = api("state")
            rule = next(entry for entry in state["rule"] if entry["id"] == reference)
            jobs = [job for job in state["jobs"] if (job["doc"].get("rule") or {}).get("id") == reference]
            if any(job["status"] in {"published", "unknown", "failed", "paused"} for job in jobs) or time.monotonic() >= until:
                break
            time.sleep(3)

        assert rule["active"] and rule["interval"] == 30 and len(rule["niches"]) == 29
        next_local = datetime.fromtimestamp(rule["next_at"], ZoneInfo(rule["timezone"]))
        print(json.dumps({"active": rule["active"], "niches": len(rule["niches"]), "interval": rule["interval"],
                          "next_cuiaba": next_local.isoformat(), "posts": [
                              {"status": job["status"], "receipt": job["receipt"], "product": job["doc"]["offer"]["title"],
                               "affiliate_link": job["doc"]["offer"].get("affiliate_url"), "error": job["error"]}
                              for job in jobs[:5]], "decisions": state["decision"][:2]}, ensure_ascii=False), flush=True)
    finally:
        api("logout", "POST", {})


if __name__ == "__main__" and sys.argv[1:] in (["bind"], ["activate"], ["verify"]):
    run(sys.argv[1])
