"""Bounded, read-only public-product probe; never loads app state or credentials.

Run from the checkout with a Python environment containing httpx:
    python -m affiliate_pilot.tests.probe_retailers --output /tmp/retailer-probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from affiliate_pilot import marketplaces
from affiliate_pilot.providers import ProviderError, resolve_product


async def probe(rows):
    semaphore = asyncio.Semaphore(3)

    async def one(row):
        async with semaphore:
            started = time.monotonic()
            result = {"retailer": row["retailer"], "url": row["url"]}
            try:
                offer = await marketplaces.extract_offer(row["url"])
                result.update(status=offer["extraction"]["status"], method=offer["extraction"]["method"],
                              reason=offer["extraction"]["reason"], code=offer["extraction"].get("code"),
                              title=offer.get("title"), price=offer.get("price"), currency=offer.get("currency"),
                              rating=offer.get("rating"), rating_scale=offer.get("rating_scale"),
                              image_count=len(offer.get("images") or []), missing=offer["extraction"]["missing"])
            except ProviderError as exc:
                result.update(status="error", code=exc.code, reason=exc.message)
            except Exception as exc:
                # Keep the exception class for diagnostics without response
                # bodies, credential-bearing URLs or arbitrary traceback data.
                result.update(status="error", code=type(exc).__name__)
            result["seconds"] = round(time.monotonic() - started, 2)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

    results = await asyncio.gather(*(one(row) for row in rows))
    started = time.monotonic()
    short = {"url": "https://s.shopee.com.br/9AQFRAtKYz"}
    try:
        short["shop"], short["item"] = await resolve_product(short["url"])
        short["status"] = "resolved"
    except ProviderError as exc:
        short.update(status="error", code=exc.code, reason=exc.message)
    short["seconds"] = round(time.monotonic() - started, 2)
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "counts": dict(Counter(row["status"] for row in results)),
            "shortlink": short, "retailers": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(__file__).with_name("retailer_links.json").read_text())
    report = asyncio.run(probe(rows))
    package = Path(marketplaces.__file__).parent
    report["source_sha256"] = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                               for name in ("marketplaces.py", "providers.py")}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"counts": report["counts"], "shortlink": report["shortlink"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
