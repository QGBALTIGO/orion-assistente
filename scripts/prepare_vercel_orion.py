"""Build the policy-only Vercel facade, never a frozen HTML or JavaScript snapshot.

The Railway backend serves all live UI and APIs through the fixed allowlisted
Vercel proxy. Regular Orion changes require no Vercel deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

SOURCE_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ORIGIN = "https://orion-painel-iota.vercel.app"
UPSTREAM_ORIGIN = "https://orion-assistente-production.up.railway.app"
SOURCE_ALLOWLIST = (
    ("deploy/vercel-orion/403.html", "public/403.html"),
    ("deploy/vercel-orion/404.html", "public/404.html"),
    ("deploy/vercel-orion/google-origin.json", "public/google-origin.json"),
    ("deploy/vercel-orion/vercel.json", "vercel.json"),
)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _source_bytes(relative: str) -> bytes:
    root = SOURCE_ROOT.resolve()
    source = root / relative
    if source.is_symlink() or source.resolve() != source.absolute():
        raise ValueError(f"Source must be a regular checkout file: {relative}")
    if not source.is_file():
        raise ValueError(f"Missing allowlisted source: {relative}")
    return source.read_bytes()


def _check_proxy_policy(raw: bytes) -> None:
    try:
        config = json.loads(raw)
        routes = config["routes"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid Vercel facade routing configuration") from exc
    if not isinstance(routes, list) or not routes:
        raise ValueError("Vercel facade routes missing")
    for route in routes:
        destination = route.get("dest", "")
        parsed = urlsplit(destination)
        if parsed.netloc and (parsed.scheme != "https" or parsed.netloc != urlsplit(UPSTREAM_ORIGIN).netloc):
            raise ValueError("External proxy destination not allowlisted")
        if destination in {"/index.html", "/orion-suite-vercel.js"}:
            raise ValueError("Version-pinned frontend copies are forbidden")
    if not any(r.get("src") == "/" and r.get("dest") == UPSTREAM_ORIGIN + "/" for r in routes):
        raise ValueError("Public homepage must be served from Railway")
    if not any(r.get("methods") == ["GET", "HEAD"] and "panel-assets" in r.get("src", "")
               and r.get("dest", "").startswith(UPSTREAM_ORIGIN) for r in routes):
        raise ValueError("Live panel assets must be routed to Railway")


def prepare_bundle(output: Path) -> dict:
    """Create four static security files and a private manifest, not a copy of the UI."""
    root = SOURCE_ROOT.resolve()
    destination = output.expanduser().resolve()
    if destination == root or destination.is_relative_to(root):
        raise ValueError("Output must be outside the repository")
    if destination.exists():
        raise ValueError("Output already exists; choose a new directory")
    if not destination.parent.is_dir():
        raise ValueError("Output parent must already exist")

    prepared = []
    for source, target in SOURCE_ALLOWLIST:
        content = _source_bytes(source)
        if source.endswith("vercel.json"):
            _check_proxy_policy(content)
        prepared.append((target, content, {
            "source": source,
            "source_sha256": sha256(content),
            "source_sha256_lf": sha256(content.replace(b"\r\n", b"\n")),
            "output": target,
            "output_sha256": sha256(content),
            "output_bytes": len(content),
            "transformation": "byte_identical_copy",
        }))

    manifest = {
        "format_version": 2,
        "mode": "live_railway_proxy",
        "public_origin": PUBLIC_ORIGIN,
        "upstream_origin": UPSTREAM_ORIGIN,
        "files": [record for _, _, record in prepared],
        "network_requests": 0,
        "runtime_execution": False,
        "production_changes": False,
        "manifest_public": False,
    }
    with tempfile.TemporaryDirectory(prefix=".orion-vercel-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        for relative, content, _ in prepared:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (staging / "bundle-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        staging.rename(destination)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New directory outside the repository")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_bundle(args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output.expanduser().resolve()), "files": len(manifest["files"]),
                      "manifest": "bundle-manifest.json", "network_requests": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
