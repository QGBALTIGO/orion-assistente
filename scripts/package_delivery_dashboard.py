"""Build the Dashboard revision on the exact currently deployed API image.

No database migration, WhatsApp change, dependency rebuild or unrelated
production file can enter this overlay. Patch conflicts fail the build.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "f7ebcbbf47446e77f11d3aebadd581f9b0c634f0"
BASE = "ghcr.io/qgbaltigo/orion-assistente-api@sha256:ca9b8be591f7d180f69b6cc158991266eded13cf8a3aaeaed400b305b121dee9"
FILES = (
    "app/delivery_orders.py",
    "app/delivery_performance.py",
    "app/static/orion-delivery-dashboard.js",
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
        with TemporaryDirectory(prefix="orion-dashboard-") as tmp:
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
                 "from pathlib import Path; from app import delivery_orders, delivery_performance; "
                 "[compile(Path(p).read_bytes(),p,'exec') for p in ('app/delivery_orders.py','app/delivery_performance.py')]")
    finally:
        call("docker", "rm", container)


if __name__ == "__main__":
    build(*sys.argv[1:])
