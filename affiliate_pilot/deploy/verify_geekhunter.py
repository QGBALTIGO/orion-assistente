"""Validate the published flow without copying credentials off the VPS."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path("/root/orion-afiliados-piloto")
published = json.loads((ROOT / "published.json").read_text())
origin = published["url"].rstrip("/")
settings = dict(line.split("=", 1) for line in (ROOT / "runtime.env").read_text().splitlines())
pid = int(subprocess.check_output(["systemctl", "show", "geekhunter-bot.service", "-P", "MainPID"], text=True))
environment = dict(line.split(b"=", 1) for line in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
                   if b"=" in line)
cookie = None


def call(path, method="GET", body=None, raw=False):
    payload = json.dumps(body if body is not None else {}).encode() if method in {"PUT", "POST"} else None
    headers = {"Origin": origin, "Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(origin + "/api/" + path, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(800_000)
        return (data if raw else json.loads(data)), response.headers


try:
    _, headers = call("login", "POST", {"username": settings["AFFILIATE_BOOTSTRAP_USER"],
                                       "password": settings["AFFILIATE_BOOTSTRAP_PASSWORD"]})
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    connected, _ = call("platforms/shopee", "PUT", {
        "app_id": environment[b"SHOPEE_AFFILIATE_APP_ID"].decode(),
        "key": environment[b"SHOPEE_AFFILIATE_SECRET"].decode(),
    })
    found, _ = call("catalog?keyword=mouse")
    sample, _ = call("offers", "POST", {"url": found[0]["catalog_url"]})
    preview, _ = call("offers/" + sample["id"] + "/preview")
    png, image_headers = call("offers/" + sample["id"] + "/card.png", raw=True)
    state, _ = call("state")
    assert connected["status"] == "connected"
    assert preview["offer"]["identity"] == found[0]["identity"]
    assert preview["offer"]["affiliate_url"] in preview["text"]
    assert image_headers.get_content_type() == "image/png" and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert state["jobs"] == [] and not state["sending"]
    result = {"published_shopee_connection_verified": True, "search_count": len(found),
              "canonical_product_matches": True, "generated_affiliate_link": True,
              "returned_shopee_image_url": bool(sample.get("image")), "card_png": True,
              "publication_attempts": len(state["jobs"])}
    (ROOT / "shopee-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)

    wa, _ = call("connection/whatsapp")
    assert wa["status"] == "unlinked"
    try:
        call("connection/whatsapp", "POST", {"active": True})
        deadline = time.monotonic() + 38
        while time.monotonic() < deadline:
            wa, _ = call("connection/whatsapp")
            if wa["status"] in {"qr_ready", "error", "logged_out"}:
                break
            time.sleep(1.6)
        result = {"whatsapp_status": wa["status"],
                  "qr_generated": wa.get("image", "").startswith("data:image/png;base64,"),
                  "account_linked": False, "messages_sent": 0}
        print(json.dumps(result), flush=True)
    finally:
        after, _ = call("connection/whatsapp", "DELETE")
        assert after["status"] == "unlinked"
finally:
    if cookie:
        call("logout", "POST")
