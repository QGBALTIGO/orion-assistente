"""Build a minimal verified overlay for one active Orion image."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "orion-plan-cycle" / "sources.json"


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def blob_hash(path):
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def build(surface, tag, revision):
    config = json.loads(MANIFEST.read_text(encoding="utf-8"))[surface]
    base = config["image"]
    run("docker", "pull", base)
    container = run("docker", "create", base)
    try:
        workdir = run("docker", "inspect", "--format", "{{.Config.WorkingDir}}", container).strip("/")
        if not workdir or any(part in {"", ".", ".."} for part in workdir.split("/")):
            raise ValueError("Unexpected base-image working directory")
        with TemporaryDirectory(prefix="orion-plan-cycle-image-") as temporary:
            bundle = Path(temporary)
            appdir = bundle / workdir
            appdir.mkdir(parents=True)
            for path, sha in config["originals"].items():
                target = appdir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                run("docker", "cp", f"{container}:/{workdir}/{path}", str(target))
                if blob_hash(target) != sha:
                    raise RuntimeError(f"Baseline changed for {surface}/{path}")
            patch = config.get("patch")
            if patch:
                path = MANIFEST.parent / patch
                run("git", "apply", "--check", str(path), cwd=appdir)
                run("git", "apply", str(path), cwd=appdir)
            for source, destination in config["copy"].items():
                target = appdir / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / source, target)
            subprocess.run(["docker", "build", "--file", str(ROOT / "deploy/Dockerfile.orion-plan-cycle"),
                            "--build-arg", f"ORION_BASE={base}", "--build-arg", f"ORION_RELEASE={revision}",
                            "--tag", tag, str(bundle)], check=True)
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    build(*sys.argv[1:])
