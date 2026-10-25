"""Prepare the reviewed, static Orion facade without copying the repository.

This command only creates a new directory outside the checkout. It does not
contact Vercel, Railway, databases, authentication providers, or messaging APIs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile


SOURCE_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ORIGIN = "https://orion-painel-iota.vercel.app"
UPSTREAM_ORIGIN = "https://orion-assistente-production.up.railway.app"

# LF hashes make the reviewed content check independent of Git autocrlf.
# Output transformations preserve the bytes/newlines of the actual checkout.
REVIEWED_SOURCE_SHA256_LF = {
    "landing/index.html": "f627dce1f7c28d94f3210c602498192a487e0821e4444b769ac086f6f35747b4",
    "app/static/orion-suite.js": "44e2837dd4438c896fd671791e8bb593340afa037d5a0ae717da5c3b82eee3f4",
}
SOURCE_ALLOWLIST = (
    ("landing/index.html", "public/index.html"),
    ("app/static/orion-suite.js", "public/orion-suite-vercel.js"),
    ("deploy/vercel-orion/403.html", "public/403.html"),
    ("deploy/vercel-orion/404.html", "public/404.html"),
    ("deploy/vercel-orion/google-origin.json", "public/google-origin.json"),
    ("deploy/vercel-orion/vercel.json", "vercel.json"),
)

GOOGLE_ACTION_GUARD = (
    "  function showGoogleAction(action){\n"
    "    const capability={connect:'can_connect',revoke:'can_disconnect',retry:'can_retry'}[action];if(!googleCan(capability))return;\n"
)
GOOGLE_CONNECT_INSERTION = (
    "    if(action==='connect'){\n"
    "      const originalPanel='https://orion-assistente-production.up.railway.app/panel?aba=agenda&empresa='+encodeURIComponent(state.tenantId);\n"
    "      showDialog('google-original-panel','Conectar Google Agenda','<p>Para conectar o Google Agenda, continue no painel original do Orion. Talvez seja necessário entrar novamente.</p><div class=\"os-form-actions\">'+button('Cancelar','close-dialog','os-outline')+'<a class=\"os-button\" href=\"'+esc(originalPanel)+'\">Continuar no painel original</a></div>',{capability});\n"
    "      return;\n"
    "    }\n"
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
    content = source.read_bytes()
    reviewed_hash = REVIEWED_SOURCE_SHA256_LF.get(relative)
    if reviewed_hash and sha256(content.replace(b"\r\n", b"\n")) != reviewed_hash:
        raise ValueError(f"Reviewed source hash mismatch: {relative}")
    return content


def _landing(content: bytes) -> bytes:
    if b"__SALES_CONTACT_LINK__" in content:
        raise ValueError("Unresolved sales contact placeholder in landing")
    for attribute in (
        'property="og:image" content="{origin}/landing-assets/orion-social.png"',
        'property="og:url" content="{origin}/"',
        'rel="canonical" href="{origin}/"',
    ):
        before = attribute.format(origin=UPSTREAM_ORIGIN).encode("utf-8")
        after = attribute.format(origin=PUBLIC_ORIGIN).encode("utf-8")
        if content.count(before) != 1:
            raise ValueError("Expected exactly one reviewed landing metadata tag")
        content = content.replace(before, after, 1)
    return content


def _suite(content: bytes) -> bytes:
    newline = b"\r\n" if b"\r\n" in content else b"\n"
    guard = GOOGLE_ACTION_GUARD.encode("utf-8").replace(b"\n", newline)
    insertion = GOOGLE_CONNECT_INSERTION.encode("utf-8").replace(b"\n", newline)
    if content.count(guard) != 1:
        raise ValueError("Expected exactly one reviewed Google capability guard")
    return content.replace(guard, guard + insertion, 1)


def prepare_bundle(output: Path) -> dict:
    """Create six allowlisted files plus a provenance manifest, without IO online."""
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
        original = _source_bytes(source)
        content = _landing(original) if source == "landing/index.html" else (
            _suite(original) if source == "app/static/orion-suite.js" else original
        )
        prepared.append((target, content, {
            "source": source,
            "source_sha256": sha256(original),
            "source_sha256_lf": sha256(original.replace(b"\r\n", b"\n")),
            "reviewed_source_sha256_lf": REVIEWED_SOURCE_SHA256_LF.get(source),
            "output": target,
            "output_sha256": sha256(content),
            "output_bytes": len(content),
            "transformation": (
                "three_origin_metadata_values" if source == "landing/index.html" else
                "google_connect_dialog_insertion_after_capability_guard" if source == "app/static/orion-suite.js" else
                "byte_identical_copy"
            ),
        }))

    manifest = {
        "format_version": 1,
        "public_origin": PUBLIC_ORIGIN,
        "upstream_origin": UPSTREAM_ORIGIN,
        "files": [record for _, _, record in prepared],
        "network_requests": 0,
        "runtime_execution": False,
        "production_changes": False,
        "manifest_public": False,
    }
    # All validation precedes filesystem writes. The temporary directory is
    # created by us under the resolved external parent and renamed only once.
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
