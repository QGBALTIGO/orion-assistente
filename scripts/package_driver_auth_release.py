"""Publish only the three courier portal assets over the exact live image."""
from pathlib import Path
import hashlib
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
BEFORE="28a8f54b91b9cf85b79ec84a70c00dfba378bc91"
BASE="ghcr.io/qgbaltigo/orion-assistente-api@sha256:b6405fc717f7ce30d97dfb90840a049a97c1d8072454e4c39ba38f70adb038a5"
FILES=("app/static/orion-delivery-driver.html","app/static/orion-delivery-driver.css","app/static/orion-delivery-driver.js")
def call(*args):subprocess.run(args,cwd=ROOT,check=True)
def out(*args):return subprocess.check_output(args,cwd=ROOT,text=True).strip()
def git_bytes(ref,f):return subprocess.check_output(["git","show",f"{ref}:{f}"],cwd=ROOT)
def manifest():return "".join(f"{hashlib.sha256((ROOT/f).read_bytes()).hexdigest()}  {f}\n" for f in FILES)
def prepare():
    head=out("git","rev-parse","HEAD")
    reviewed={f:git_bytes(head,f) for f in FILES}
    call("docker","pull","--platform","linux/amd64",BASE)
    cid=out("docker","create","--platform","linux/amd64",BASE)
    try:
        assert out("docker","inspect","--format","{{.Config.WorkingDir}}",cid)=="/srv/app"
        with TemporaryDirectory(prefix="orion-driver-auth-") as tmp:
            for i,f in enumerate(FILES):
                path=Path(tmp)/str(i)
                call("docker","cp",f"{cid}:/srv/app/{f}",str(path))
                assert path.read_bytes()==git_bytes(BEFORE,f),f"Unexpected production divergence: {f}"
            call("docker","cp",f"{cid}:/srv/app/app/static/.",str(ROOT/"app/static"))
            for f,data in reviewed.items():(ROOT/f).write_bytes(data)
        (ROOT/"driver-auth-manifest.txt").write_text(manifest())
    finally:call("docker","rm",cid)
def build(tag,head):
    assert out("git","rev-parse","HEAD")==head
    assert manifest()==(ROOT/"driver-auth-manifest.txt").read_text(),"Candidate changed since QA"
    with TemporaryDirectory(prefix="orion-auth-build-") as tmp:
        folder=Path(tmp)
        for f in FILES:
            dest=folder/"source"/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/f).read_bytes())
        (folder/"Dockerfile").write_text(f'FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision="{head}"\n')
        call("docker","build","--platform","linux/amd64","-t",tag,str(folder))
if __name__=="__main__":
    if sys.argv[1]=="prepare":prepare()
    else:build(*sys.argv[1:])
