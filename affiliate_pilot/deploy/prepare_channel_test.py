"""Prepare one owner-approved PromoTech post without publishing it."""
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from PIL import Image

from affiliate_pilot.offers import caption, card_png, price_label, safe_product_photo, validate_length
from affiliate_pilot.providers import product_image
from affiliate_pilot.store import DEFAULT_TEMPLATE


ROOT = Path("/root/orion-afiliados-piloto")
GRANT = ROOT / "promotech-channel-grant.json"
DROPIN = Path("/etc/systemd/system/orion-afiliados-piloto-whatsapp.service.d/35-channel-test.conf")
TARGET = "f5fc94167f0142569e7f74dea6dd0648"
os.umask(0o077)
if GRANT.exists() or DROPIN.exists():
    raise RuntimeError("Já existe uma autorização de postagem para esta operação.")

config = dict(line.split("=", 1) for line in (ROOT / "runtime.env").read_text().splitlines())
origin = config["AFFILIATE_PUBLIC_ORIGIN"]
cookie = None


def api(path, method="GET", payload=None):
    headers = {"Origin": origin}
    data = None
    if cookie:
        headers["Cookie"] = cookie
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(origin + "/api/" + path, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=55) as response:
        return json.load(response), response.headers


_, headers = api("login", "POST", {
    "username": config["AFFILIATE_BOOTSTRAP_USER"], "password": config["AFFILIATE_BOOTSTRAP_PASSWORD"],
})
cookie = headers["Set-Cookie"].split(";", 1)[0]
try:
    state, _ = api("state")
    assert not state["sending"]
    dest = next(row for row in state["destination"] if row["id"] == TARGET)
    assert dest["mode"] == "whatsapp_channel" and dest["name"] == "PromoTech - QG BAlTIGO"
    proof, _ = api(f"destinations/{TARGET}/verify-channel", "POST", {})
    assert proof["verified"] and proof["role"] in {"OWNER", "ADMIN"}
    assert proof["channel_jid"] == dest["channel_jid"] and proof["connection_version"] == dest["connection_version"]
    invite = re.fullmatch(r"https://(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9]{20,32})/?(?:\?[^#]*)?",
                          proof["address"])
    assert invite
    draft = next(row for row in state["offer"] if not row.get("demo") and row.get("catalog_url"))
    offer = draft
    if time.time() - draft.get("fetched_at", 0) > 60:
        offer, _ = api("offers", "POST", {"url": draft["catalog_url"]})
    assert not offer.get("demo") and offer["price"] and offer.get("image") and offer.get("affiliate_url")
    assert offer["identity"] == draft["identity"]
    photo = asyncio.run(product_image(offer["image"]))
    assert safe_product_photo(photo) is not None
    template = {**DEFAULT_TEMPLATE, "brand": "PromoTech"}
    text = "Postagem de teste do Orion\n\n" + caption(offer, template, dest["name"])
    validate_length(text, "card")
    assert offer["affiliate_url"] in text.splitlines()
    card = io.BytesIO()
    thumbnail = io.BytesIO()
    with Image.open(io.BytesIO(card_png(offer, template, photo))) as image:
        image.save(card, format="JPEG", quality=92, optimize=True)
        image.thumbnail((144, 144))
        image.save(thumbnail, format="JPEG", quality=78)
    photo_bytes = card.getvalue()
    assert len(photo_bytes) < 400_000 and 0 < thumbnail.tell() <= 20_000
    grant = {
        "id": uuid4().hex,
        "invite": invite[1],
        "address": proof["channel_jid"],
        "version": proof["connection_version"],
        "expires": int(time.time() * 1000) + 900_000,
        "caption": text,
        "link": offer["affiliate_url"],
        "jpeg": base64.b64encode(photo_bytes).decode(),
        "thumbnail": base64.b64encode(thumbnail.getvalue()).decode(),
        "sha256": hashlib.sha256(photo_bytes).hexdigest(),
        "offer_title": offer["title"],
        "price": price_label(offer),
    }
    with GRANT.open("x") as file:
        json.dump(grant, file, separators=(",", ":"))
    with DROPIN.open("x") as file:
        file.write(f"[Service]\nLoadCredential=orion-channel-test:{GRANT}\n")
    print(json.dumps({"channel_verified": True, "product": grant["offer_title"], "price": grant["price"],
                      "card_bytes": len(photo_bytes), "single_post_prepared": True}, ensure_ascii=False), flush=True)
finally:
    api("logout", "POST", {})
