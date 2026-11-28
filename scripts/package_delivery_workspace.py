"""Build the Delivery workspace revision on the exact currently deployed API image.

No database migration, WhatsApp change, dependency rebuild or unrelated
production file can enter this overlay. Patch conflicts fail the build.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "662b396be508df1bc7235e45dbe788e5132b0406"
BASE = "ghcr.io/qgbaltigo/orion-assistente-api@sha256:fe051029fda6cb2ddd7dacc189582765d907ea0eb7c9c62201fa78dbb248eaef"
FILES = (
    "app/delivery_orders.py",
    "app/delivery_api.py",
    "app/delivery_catalog.py",
    "app/static/orion-delivery-orders.js",
    "app/static/orion-delivery-catalog.js",
    "app/static/orion-delivery-store-admin.js",
    "app/static/orion-suite.js",
    "app/static/orion-delivery-dashboard.css",
)


def call(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def build(tag, head):
    assert output("git", "rev-parse", "HEAD", cwd=ROOT) == head
    changed = output("git", "diff", "--name-status", BEFORE, head, "--", *FILES, cwd=ROOT)
    assert dict((name, kind) for kind, name in (line.split("\t") for line in changed.splitlines())) == dict.fromkeys(FILES, "M")
    patch = subprocess.check_output(
        ["git", "diff", "--binary", "--full-index", BEFORE, head, "--", *FILES], cwd=ROOT
    )
    call("docker", "pull", "--platform", "linux/amd64", BASE)
    container = output("docker", "create", "--platform", "linux/amd64", BASE)
    try:
        assert output("docker", "inspect", "--format", "{{.Config.WorkingDir}}", container) == "/srv/app"
        with TemporaryDirectory(prefix="orion-delivery-workspace-") as tmp:
            bundle = Path(tmp)
            source = bundle / "source"
            source.mkdir()
            for file in FILES:
                p = source / file
                p.parent.mkdir(parents=True, exist_ok=True)
                call("docker", "cp", f"{container}:/srv/app/{file}", str(p))
                assert p.is_file() and not p.is_symlink()
                # Strict base match: never replace an unknown live file.
                assert p.read_bytes() == subprocess.check_output(["git","show",f"{BEFORE}:{file}"],cwd=ROOT)
            (bundle / "change.patch").write_bytes(patch)
            call("git", "apply", "--check", str(bundle / "change.patch"), cwd=source)
            call("git", "apply", str(bundle / "change.patch"), cwd=source)
            for file in FILES:
                assert (source/file).read_bytes() == (ROOT/file).read_bytes()
            (bundle / "Dockerfile").write_text(
                f"FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision=\"{head}\"\n", encoding="utf-8"
            )
            call("docker", "build", "--platform", "linux/amd64", "-t", tag, str(bundle))
            call("docker", "run", "--rm", "--network", "none", "--entrypoint", "python", tag, "-B", "-c",
                 "from pathlib import Path; from app import delivery_orders, delivery_api, delivery_catalog; "
                 "[compile(Path(p).read_bytes(),p,'exec') for p in ('app/delivery_orders.py','app/delivery_api.py','app/delivery_catalog.py')]")
    finally:
        call("docker", "rm", container)


if __name__ == "__main__":
    build(*sys.argv[1:])
