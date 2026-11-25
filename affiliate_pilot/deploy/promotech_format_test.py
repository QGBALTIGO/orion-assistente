"""Two owner-approved category posts, spaced by ten real minutes on PromoTech."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

from PIL import Image

from affiliate_pilot.niches import NICHES
from affiliate_pilot.offers import caption, card_png, price_label, safe_product_photo, validate_length
from affiliate_pilot.providers import ProviderError, product_image
from affiliate_pilot.store import DEAL_TEMPLATE


ROOT = Path("/root/orion-afiliados-piloto")
STATE = Path("/var/lib/orion-afiliados-piloto-whatsapp")
DROPIN = Path("/etc/systemd/system/orion-afiliados-piloto-whatsapp.service.d/36-format-test.conf")
MANIFEST = ROOT / "promotech-format-test.json"
TARGET = "f5fc94167f0142569e7f74dea6dd0648"
EXAMPLES = {
    "casa": "Uma opção para preparar as refeições do dia a dia com mais praticidade.",
    "eletronicos": "Confira esta opção para ouvir música, podcasts e chamadas na sua rotina.",
}


def settings(file):
    return dict(line.split("=", 1) for line in (ROOT / file).read_text().splitlines())


def request(url, headers, method="GET", data=None, timeout=40):
    headers = headers.copy()
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response), response.headers


def media_bytes(offer, photo):
    with Image.open(io.BytesIO(card_png(offer, DEAL_TEMPLATE, photo))) as image:
        card, thumbnail = io.BytesIO(), io.BytesIO()
        image.save(card, format="JPEG", quality=89, optimize=True)
        image.thumbnail((140, 140))
        image.save(thumbnail, format="JPEG", quality=78)
    return card.getvalue(), thumbnail.getvalue()


def prepare():
    config = settings("runtime.env")
    origin = config["AFFILIATE_PUBLIC_ORIGIN"]
    headers = {"Origin": origin}
    paths = [ROOT / f"promotech-format-{index}.json" for index in range(len(EXAMPLES))]
    if any(path.exists() for path in [DROPIN, MANIFEST, *paths]):
        raise RuntimeError("Já existe um teste de formato preparado.")
    _, login = request(origin + "/api/login", headers, "POST",
        {"username": config["AFFILIATE_BOOTSTRAP_USER"], "password": config["AFFILIATE_BOOTSTRAP_PASSWORD"]})
    headers["Cookie"] = login["Set-Cookie"].split(";", 1)[0]

    def api(path, method="GET", data=None):
        return request(origin + "/api/" + path, headers, method, data)[0]

    try:
        state = api("state")
        assert not state["sending"]
        dest = next(row for row in state["destination"] if row["id"] == TARGET)
        assert dest["name"] == "PromoTech - QG BAlTIGO" and dest["mode"] == "whatsapp_channel"
        proof = api(f"destinations/{TARGET}/verify-channel", "POST", {})
        assert proof["verified"] and proof["role"] in {"OWNER", "ADMIN"}
        assert proof["channel_jid"] == dest["channel_jid"] and proof["connection_version"] == dest["connection_version"]
        invite = re.fullmatch(r"https://(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9]{20,32})/?(?:\?[^#]*)?",
                              dest["address"])
        assert invite
        grants = []
        for niche, alternate in EXAMPLES.items():
            candidates = api("catalog?" + urllib.parse.urlencode({"niche": niche, "sort": "1"}))
            assert candidates and all(NICHES[niche]["category"] in row["categories"] for row in candidates)
            for candidate in candidates[:5]:
                try:
                    offer = api("offers", "POST", {"url": candidate["catalog_url"]})
                    if offer["identity"] != candidate["identity"] or not offer.get("price") or not offer.get("image"):
                        continue
                    image = asyncio.run(product_image(offer["image"]))
                    if safe_product_photo(image) is not None:
                        break
                except (urllib.error.HTTPError, ProviderError):
                    continue
            else:
                raise RuntimeError("Nenhuma oferta com foto confirmada no nicho " + niche)
            for key in ("sales", "rating", "discount_rate"):
                if not offer.get(key):
                    offer[key] = candidate.get(key)
            draft = api("offers/" + offer["id"] + "/phrase", "POST")
            source = draft["phrase_source"]
            if source != "groq":
                draft = api("offers/" + offer["id"] + "/phrase", "PUT", {"text": alternate})
                source = "frase editorial do teste"
            offer.update(niche=niche, phrase=draft["phrase"])
            text = caption(offer, DEAL_TEMPLATE, dest["name"])
            validate_length(text, "card")
            card, thumbnail = media_bytes(offer, image)
            assert 0 < len(card) < 400_000 and 0 < len(thumbnail) <= 20_000
            grants.append({
                "id": uuid4().hex, "after": grants[-1]["id"] if grants else None, "invite": invite[1],
                "address": proof["channel_jid"], "version": proof["connection_version"],
                "expires": int(time.time() * 1000) + 1_800_000, "caption": text,
                "link": offer["affiliate_url"], "sha256": hashlib.sha256(card).hexdigest(),
                "jpeg": base64.b64encode(card).decode(), "thumbnail": base64.b64encode(thumbnail).decode(),
                "product": offer["title"], "price": price_label(offer), "niche": niche, "phrase_source": source,
            })
        for path, grant in zip(paths, grants):
            with path.open("x") as file:
                json.dump(grant, file)
        with MANIFEST.open("x") as file:
            json.dump({"interval_seconds": 600, "offers": [
                {key: grant[key] for key in ("id", "product", "price", "niche", "phrase_source", "link")}
                for grant in grants]}, file, indent=2)
        with DROPIN.open("x") as file:
            file.write("[Service]\n" + "".join(f"LoadCredential=orion-channel-test-{i}:{path}\n"
                       for i, path in enumerate(paths)))
        print(json.dumps({"ready": [{key: grant[key] for key in ("niche", "product", "price", "phrase_source")}
                                   for grant in grants], "interval_seconds": 600}, ensure_ascii=False), flush=True)
    finally:
        api("logout", "POST", {})


def run():
    config = settings("whatsapp.env")
    headers = {"Authorization": "Bearer " + config["AFFILIATE_WA_TOKEN"]}
    base = f"http://127.0.0.1:{config['AFFILIATE_WA_PORT']}"
    manifest = json.loads(MANIFEST.read_text())
    for i, offer in enumerate(manifest["offers"]):
        journal = STATE / f"channel-test-{offer['id']}.json"
        if i:
            first = json.loads((STATE / f"channel-test-{manifest['offers'][0]['id']}.json").read_text())
            if first["status"] == "provider_rejected":
                raise RuntimeError("O WhatsApp recusou o primeiro teste.")
            due = first["started"] / 1000 + manifest["interval_seconds"]
            while remaining := due - time.time():
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 20))
        url = f"{base}/channel-test?slot={i}"
        result = request(url, headers, "GET" if journal.exists() else "POST", timeout=55)[0]
        assert journal.exists()
        print(json.dumps({**offer, **result}, ensure_ascii=False), flush=True)
    if manifest["offers"]:
        first, second = [json.loads((STATE / f"channel-test-{item['id']}.json").read_text())
                         for item in manifest["offers"]]
        actual = (second["started"] - first["started"]) / 1000
        (ROOT / "promotech-format-result.json").write_text(json.dumps(
            {"seconds_between_attempts": actual, "attempts": [first, second], **manifest}, indent=2))
        print(json.dumps({"attempts": 2, "seconds_between_attempts": actual}), flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:] == ["prepare"]:
        prepare()
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise SystemExit("Selecione prepare ou run.")
