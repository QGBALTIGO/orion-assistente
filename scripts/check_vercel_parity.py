"""Read-only check: Vercel must deliver the live Railway Orion frontend."""
import hashlib
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VERCEL = "https://orion-painel-iota.vercel.app"
RAILWAY = "https://orion-assistente-production.up.railway.app"
PATHS = (
    "/panel",
    "/panel-assets/orion-suite.js",
    "/panel-assets/orion-suite-live.js",
    "/panel-assets/orion-suite.css",
    "/public-assets/orion-public.js",
    "/public-assets/orion-public.css",
    "/landing-assets/orion-landing.js",
)


def get(url):
    request = Request(url, headers={
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "User-Agent": "Orion-Vercel-Parity/1.0",
    })
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP {response.status} at {url}")
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise RuntimeError(f"Unexpectedly large response at {url}")
        return data


def check():
    for path in PATHS:
        a = get(VERCEL + path)
        b = get(RAILWAY + path)
        if a != b:
            raise RuntimeError(
                f"Frontend diverged at {path}: Vercel={hashlib.sha256(a).hexdigest()[:16]} "
                f"Railway={hashlib.sha256(b).hexdigest()[:16]}"
            )
        print(f"OK {path} ({len(a)} bytes)")
    landing_vercel = get(VERCEL + "/")
    landing_railway = get(RAILWAY + "/")
    if landing_vercel.replace(VERCEL.encode(), RAILWAY.encode()) != landing_railway:
        raise RuntimeError("Homepage differs beyond fixed Vercel canonical/Open Graph metadata")
    print("OK / (same homepage with Vercel-specific canonical metadata)")


if __name__ == "__main__":
    for attempt in range(3):
        try:
            check()
            sys.exit(0)
        except (HTTPError, URLError, RuntimeError) as exc:
            print(f"Attempt {attempt + 1}/3: {exc}", file=sys.stderr)
            if attempt == 2:
                sys.exit(1)
            time.sleep(20)
