"""Build a verified Delivery overlay without replacing unrelated live Orion code."""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
BASE_IMAGE = "ghcr.io/qgbaltigo/orion-assistente-api@sha256:cd81d0a800ee331a6d9841b072d8dfdd5b88f458f85229badaad11b8d3147dd2"
BASE_SOURCE = "591fc0c57271880dc67aab616e0094b7a647a60e"
IMAGE_REPOSITORY = "ghcr.io/qgbaltigo/orion-assistente-api"
APP_ROOT = "/srv/app"
INHERITED_MIGRATIONS = {
    "alembic/versions/0099_admin_personal_password.py": "05ebee0f38b81acbdd7c363b3f30cf8184e98f61",
}
SHARED = frozenset({
    "app/commercial_service.py",
    "app/main.py",
    "app/media_api.py",
    "app/management_models.py",
    "app/onboarding_schemas.py",
    "app/plan_permissions.py",
    "app/static/orion-suite.js",
    "app/static/panel.html",
    "app/workspace_policy.py",
})
DELIVERY_CODE = re.compile(
    r"(?:app/delivery_[a-z0-9_]+\.py|app/static/(?:orion-delivery|delivery)[-_.a-z0-9]+\.(?:js|css|html|svg|json|webmanifest)|"
    r"app/static/delivery-products/[a-z0-9-]+\.webp|"
    r"alembic/versions/(?:0099|010[0-9]|0110)_delivery_[a-z0-9_]+\.py|scripts/backup_and_migrate_delivery\.py)"
)
REQUIRED = frozenset({"app/delivery_api.py", "alembic/versions/0104_delivery_returns.py",
                      "scripts/backup_and_migrate_delivery.py", *INHERITED_MIGRATIONS})


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def runtime_changes(release):
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        raise ValueError("Use the full Git revision of the tested commit")
    subprocess.run(["git", "merge-base", "--is-ancestor", BASE_SOURCE, release], cwd=ROOT, check=True)
    tokens = git("diff", "--name-status", "--no-renames", "-z", BASE_SOURCE, release).decode().split("\0")
    if len(tokens) % 2 != 1 or tokens.pop() != "":
        raise ValueError("Unexpected Git change list")
    changes = {}
    # Preserve the image's dependency lock, infrastructure, entrypoints and assets.
    for status, path in zip(tokens[::2], tokens[1::2], strict=True):
        if path.startswith((".github/", "deploy/", "docs/", "tests/", "ui-tests/")) or path == "scripts/package_delivery_release.py":
            continue
        if not ((status == "M" and path in SHARED)
                or (status == "A" and (DELIVERY_CODE.fullmatch(path) or path in INHERITED_MIGRATIONS))):
            raise ValueError(f"Release path needs explicit review: {status} {path}")
        mode = git("ls-tree", "--full-tree", "--format=%(objectmode)", release, "--", path).decode().strip()
        if mode != "100644":
            raise ValueError(f"Expected a regular source file: {path}")
        changes[path] = status
    if not REQUIRED <= changes.keys():
        raise ValueError("Incomplete Delivery release")
    return changes


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def prepare_overlay(container, release, stage, changes):
    overlay = stage / "overlay"
    overlay.mkdir()
    manifest = {}
    for path, status in changes.items():
        source = git("cat-file", "blob", f"{release}:{path}")
        source_hash = sha256(source)
        destination = overlay / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path in INHERITED_MIGRATIONS:
            # The image already contains this production migration. Guard its
            # identity on both sides without copying over the live file.
            blob = git("ls-tree", "--full-tree", "--format=%(objectname)", release, "--", path).decode().strip()
            if blob != INHERITED_MIGRATIONS[path]:
                raise ValueError(f"Unexpected inherited migration blob: {path}")
            inherited = stage / "inherited-migration"
            subprocess.run(["docker", "cp", f"{container}:{APP_ROOT}/{path}", str(inherited)], check=True)
            if inherited.is_symlink() or not inherited.is_file() or inherited.read_bytes() != source:
                raise ValueError(f"Inherited migration differs from the pinned production image: {path}")
            inherited.unlink()
            manifest[path] = {"live_before": source_hash, "source_before": source_hash}
        elif status == "A":
            destination.write_bytes(source)
            manifest[path] = {"live_before": None, "source_before": None}
        else:
            subprocess.run(["docker", "cp", f"{container}:{APP_ROOT}/{path}", str(destination)], check=True)
            if not destination.is_file() or destination.is_symlink():
                raise ValueError(f"Unexpected base-image file: {path}")
            predecessor = git("cat-file", "blob", f"{BASE_SOURCE}:{path}")
            live = destination.read_bytes()
            manifest[path] = {"live_before": sha256(live), "source_before": sha256(predecessor)}
            if live != predecessor:
                source = merge_live_delta(path, destination, predecessor, source, stage)
            destination.write_bytes(source)
        manifest[path].update(live_after=sha256(source), source_after=source_hash)

    return {"base_image": BASE_IMAGE, "base_source": BASE_SOURCE, "release": release, "files": manifest}


def merge_live_delta(path, live_file, predecessor, desired, stage):
    before = stage / "merge-predecessor"
    after = stage / "merge-delivery"
    before.write_bytes(predecessor)
    after.write_bytes(desired)
    # The pinned production file is the current side. A clean three-way
    # merge permits adjacent edits and preserves independent live changes.
    result = subprocess.run(
        ["git", "merge-file", "--stdout", "--diff3", str(live_file), str(before), str(after)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    live = live_file.read_bytes()
    merged = result.stdout if result.returncode == 0 else merge_disjoint_lines(predecessor, live, desired)
    if merged is not None:
        print(f"Merged Delivery without replacing the existing live delta in {path}", flush=True)
        return merged
    if path == "app/static/orion-suite.js":
        # Only this browser-delivered source is eligible for a bounded conflict
        # excerpt. Do not expose arbitrary image files or environment values.
        conflicts = re.findall(rb"^<{7} .*?^>{7} [^\n]*$", result.stdout, re.MULTILINE | re.DOTALL)
        for line in b"\n".join(conflicts).decode("utf-8", "replace").splitlines()[:80]:
            print("Static browser source conflict: " + line[:500], flush=True)
    raise RuntimeError(
        f"Live and Delivery changes overlap in {path}; need a reviewed merge "
        f"(live={sha256(live)}, baseline={sha256(predecessor)}, "
        f"delivery={sha256(desired)}, status={result.returncode})"
    )


def merge_disjoint_lines(predecessor, live, desired):
    """Join strictly disjoint edits, including adjacent lines Git treats as a conflict."""
    base_lines = predecessor.splitlines(keepends=True)

    def changes(source):
        lines = source.splitlines(keepends=True)
        diff = SequenceMatcher(None, base_lines, lines, autojunk=False)
        return {(start, end, tuple(lines[first:last]))
                for action, start, end, first, last in diff.get_opcodes() if action != "equal"}

    cursor = 0
    insertions = set()
    selected = []
    for start, end, replacements in sorted(changes(live) | changes(desired)):
        if start < cursor or (start == end and start in insertions):
            return None
        if start == end:
            insertions.add(start)
        selected.extend(base_lines[cursor:start])
        selected.extend(replacements)
        cursor = end
    selected.extend(base_lines[cursor:])
    return b"".join(selected)


def build(release, manifest_path):
    tag = f"{IMAGE_REPOSITORY}:delivery-{release}"
    changes = runtime_changes(release)
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", BASE_IMAGE], check=True)
    container = subprocess.check_output(
        ["docker", "create", "--platform", "linux/amd64", BASE_IMAGE], text=True,
    ).strip()
    try:
        workdir = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.Config.WorkingDir}}", container], text=True,
        ).strip()
        if workdir != APP_ROOT:
            raise ValueError("Unexpected working directory for the live Orion image")
        with TemporaryDirectory(prefix="orion-delivery-image-") as directory:
            stage = Path(directory)
            manifest = prepare_overlay(container, release, stage, changes)
            encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            (stage / "delivery-release.json").write_text(encoded, encoding="utf-8")
            subprocess.run([
                "docker", "build", "--platform", "linux/amd64", "--file", str(ROOT / "deploy/Dockerfile.delivery-release"),
                "--build-arg", f"BASE_IMAGE={BASE_IMAGE}", "--build-arg", f"RELEASE_REVISION={release}",
                "--tag", tag, str(stage),
            ], check=True)
        manifest_path.write_text(encoded, encoding="utf-8")
    finally:
        subprocess.run(["docker", "rm", container], stdout=subprocess.DEVNULL, check=True)
    print(tag, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    build(args.revision, args.manifest)
