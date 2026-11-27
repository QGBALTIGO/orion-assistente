"""Overlay the reviewed Delivery button change on the exact deployed images.

Only the selected changes are applied. Conflicting production changes fail the
build; no unrelated files are copied or dependencies rebuilt.
"""
import hashlib
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "4879bf3e061a23c9889ef46ed350430ec75208ec"
REVIEW = "be952d3b9f8e7dc8f535f2ee15a1a3cca65d3893"
SURFACES = {
    "api": {
        "base": "ghcr.io/qgbaltigo/orion-assistente-api@sha256:6bec942197e7db9d265e35d735f21c0fb631088041014b66c80b96d4f9c2827d",
        "workdir": "/srv/app",
        "files": {"app/delivery_whatsapp.py": "M", "app/subscription_devices.py": "M"},
        "prefix": "",
    },
    "qr": {
        "base": "ghcr.io/qgbaltigo/orion-assistente-qr-bridge@sha256:b7e1a1d00cc7b473f5cd0e68c9c06c0cd35db58346030fb81094e98922bb1e22",
        "workdir": "/srv/qr",
        "files": {
            "qr-bridge/src/channel-session.mjs": "M",
            "qr-bridge/src/device-runtime.mjs": "M",
            "qr-bridge/src/delivery-confirmation.mjs": "A",
        },
        "prefix": "qr-bridge/",
    },
}


def output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def call(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def build(surface, tag):
    cfg = SURFACES[surface]
    base = cfg["base"]
    files = cfg["files"]
    changed = output("git", "diff", "--name-status", "--no-renames", BEFORE, REVIEW, "--", *files, cwd=ROOT)
    assert {path: kind for kind, path in (line.split("\t") for line in changed.splitlines())} == files
    patch = subprocess.check_output(
        ["git", "diff", "--binary", "--full-index", "--no-renames", BEFORE, REVIEW, "--", *files],
        cwd=ROOT,
    )
    call("docker", "pull", "--platform", "linux/amd64", base)
    container = output("docker", "create", "--platform", "linux/amd64", base)
    try:
        assert output("docker", "inspect", "--format", "{{.Config.WorkingDir}}", container) == cfg["workdir"]
        with TemporaryDirectory(prefix="orion-delivery-buttons-") as temp:
            bundle = Path(temp)
            source = bundle / "source"
            overlay = bundle / "overlay"
            source.mkdir()
            overlay.mkdir()
            for path, kind in files.items():
                relative = path.removeprefix(cfg["prefix"])
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                if kind == "M":
                    call("docker", "cp", f"{container}:{cfg['workdir']}/{relative}", str(target))
                    assert target.is_file() and not target.is_symlink()
                    print(f"Original {path} sha256={hashlib.sha256(target.read_bytes()).hexdigest()}", flush=True)
                else:
                    # An unexpected pre-existing module is never replaced.
                    call("docker", "run", "--rm", "--network", "none", "--entrypoint", "test",
                         base, "!", "-e", f"{cfg['workdir']}/{relative}")
            patch_path = bundle / "reviewed.patch"
            patch_path.write_bytes(patch)
            call("git", "apply", "--check", str(patch_path), cwd=source)
            call("git", "apply", str(patch_path), cwd=source)
            for path in files:
                relative = path.removeprefix(cfg["prefix"])
                target = overlay / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / path).read_bytes())
                print(f"Updated {path} sha256={hashlib.sha256(target.read_bytes()).hexdigest()}", flush=True)
            (bundle / "Dockerfile").write_text(
                f"FROM {base}\nCOPY overlay/ {cfg['workdir']}/\n"
                f'LABEL org.opencontainers.image.revision="{REVIEW}"\n', encoding="utf-8"
            )
            call("docker", "build", "--platform", "linux/amd64", "-t", tag, str(bundle))
    finally:
        call("docker", "rm", container)


if __name__ == "__main__":
    build(*sys.argv[1:])
