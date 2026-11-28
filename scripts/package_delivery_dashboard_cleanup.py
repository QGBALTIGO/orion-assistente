"""Validate the exact live static assets plus a two-file Delivery dashboard cleanup."""
from pathlib import Path
import hashlib
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
BEFORE="cb02ce983b6a27a586ff162377c9eab122c8967a"
BASE="ghcr.io/qgbaltigo/orion-assistente-api@sha256:5b416c05c61f2284b5301a951d93a6ddb6aad07fd740816009729912478f0b37"
FILES=(
    "app/static/orion-delivery-dashboard.css",
    "app/static/orion-delivery-dashboard.js",
)

def call(*args):
    subprocess.run(args,cwd=ROOT,check=True)

def output(*args):
    return subprocess.check_output(args,cwd=ROOT,text=True).strip()

def git_bytes(ref,file):
    return subprocess.check_output(["git","show",f"{ref}:{file}"],cwd=ROOT)

def manifest():
    return "".join(f"{hashlib.sha256((ROOT/file).read_bytes()).hexdigest()}  {file}\n" for file in FILES)

def prepare():
    head=output("git","rev-parse","HEAD")
    call("docker","pull","--platform","linux/amd64",BASE)
    container=output("docker","create","--platform","linux/amd64",BASE)
    try:
        assert output("docker","inspect","--format","{{.Config.WorkingDir}}",container)=="/srv/app"
        with TemporaryDirectory(prefix="orion-dashboard-cleanup-") as tmp:
            for i,file in enumerate(FILES):
                live=Path(tmp)/str(i)
                call("docker","cp",f"{container}:/srv/app/{file}",str(live))
                assert live.is_file() and not live.is_symlink()
                assert live.read_bytes()==git_bytes(BEFORE,file),f"Unexpected production divergence: {file}"
            # Tests use the actual shared CSS, JS and panel HTML served by the base.
            call("docker","cp",f"{container}:/srv/app/app/static/.",str(ROOT/"app/static"))
            for file in FILES:
                reviewed=git_bytes(head,file)
                assert reviewed!=git_bytes(BEFORE,file),file
                (ROOT/file).write_bytes(reviewed)
        (ROOT/"dashboard-cleanup-qa-manifest.txt").write_text(manifest(),encoding="utf-8")
    finally:
        call("docker","rm",container)

def build(tag,head):
    assert output("git","rev-parse","HEAD")==head
    assert manifest()==(ROOT/"dashboard-cleanup-qa-manifest.txt").read_text(encoding="utf-8"),"Files differ from tested sources"
    with TemporaryDirectory(prefix="orion-dashboard-cleanup-build-") as tmp:
        folder=Path(tmp)
        for file in FILES:
            dest=folder/"source"/file
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes((ROOT/file).read_bytes())
        (folder/"Dockerfile").write_text(
            f"FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision=\"{head}\"\n",
            encoding="utf-8")
        call("docker","build","--platform","linux/amd64","-t",tag,str(folder))

if __name__=="__main__":
    if sys.argv[1]=="prepare":
        prepare()
    else:
        build(*sys.argv[1:])
