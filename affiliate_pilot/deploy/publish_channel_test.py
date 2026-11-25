"""Publish or reconcile the explicitly approved pilot post over loopback."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path("/root/orion-afiliados-piloto")
config = dict(line.split("=", 1) for line in (ROOT / "whatsapp.env").read_text().splitlines())
grant = json.loads((ROOT / "promotech-channel-grant.json").read_text())
url = f"http://127.0.0.1:{config['AFFILIATE_WA_PORT']}/channel-test"
headers = {"Authorization": "Bearer " + config["AFFILIATE_WA_TOKEN"]}
state = Path("/var/lib/orion-afiliados-piloto-whatsapp") / f"channel-test-{grant['id']}.json"
os.umask(0o077)


def invoke(method):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=headers), timeout=106) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, **json.load(exc)}
    except OSError:
        return {"status": "result_unknown"}


if state.exists():
    receipt = invoke("GET")
else:
    receipt = invoke("POST")  # One dispatch trigger. All subsequent checks are reads.
for _ in range(3):
    if not state.exists() or receipt.get("status") == "confirmed":
        break
    time.sleep(3)
    receipt = invoke("GET")
if state.exists():
    receipt = json.loads(state.read_text())
result = {**receipt, "product": grant["offer_title"], "price": grant["price"],
          "dispatch_reserved": state.exists(), "authorization_id": grant["id"]}
(ROOT / "promotech-channel-result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(json.dumps(result, ensure_ascii=False), flush=True)
