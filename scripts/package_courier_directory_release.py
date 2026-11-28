"""Layer only the reviewed courier-management changes on the exact deployed image."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
BEFORE="c202588ef347463ed56fac3b0cec8de2e95b9700"
BASE="ghcr.io/qgbaltigo/orion-assistente-api@sha256:fba4b0b1c040d783a1b3609773d4b97c4c221c4162fad0c4a75fcd4ac631e47b"
SUITE="app/static/orion-suite.js"
FILES=(SUITE,"app/static/orion-delivery-logistics.js","app/delivery_driver.py","app/delivery_api.py",
       "app/static/orion-delivery-driver.html","app/static/orion-delivery-driver.js","app/static/orion-delivery-driver.css")
def call(*args):subprocess.run(args,cwd=ROOT,check=True)
def out(*args):return subprocess.check_output(args,cwd=ROOT,text=True).strip()
def git_bytes(ref,f):return subprocess.check_output(["git","show",f"{ref}:{f}"],cwd=ROOT)
def manifest():
    return "".join(f"{hashlib.sha256((ROOT/f).read_bytes()).hexdigest()}  {f}\n" for f in FILES)
def patch_suite(raw):
    value=raw.decode("utf-8")
    patches=json.loads((ROOT/"scripts/courier_directory_suite_patch.json").read_text())
    for before,after in patches:
        assert value.count(before)==1, f"Unreviewed suite divergence: {before[:80]}"
        value=value.replace(before,after,1)
    return value.encode("utf-8")
def prepare():
    head=out("git","rev-parse","HEAD")
    reviewed={f:git_bytes(head,f) for f in FILES}
    assert patch_suite(git_bytes(BEFORE,SUITE))==reviewed[SUITE],"Git source must match exact reviewed route patch"
    call("docker","pull","--platform","linux/amd64",BASE)
    cid=out("docker","create","--platform","linux/amd64",BASE)
    try:
        assert out("docker","inspect","--format","{{.Config.WorkingDir}}",cid)=="/srv/app"
        with TemporaryDirectory(prefix="orion-couriers-base-") as tmp:
            for i,f in enumerate(FILES):
                path=Path(tmp)/str(i)
                call("docker","cp",f"{cid}:/srv/app/{f}",str(path))
                live=path.read_bytes()
                if f==SUITE:
                    reviewed[f]=patch_suite(live)
                else:
                    assert live==git_bytes(BEFORE,f),f"Unexpected production divergence: {f}"
            for directory in ("app","alembic","scripts"):
                call("docker","cp",f"{cid}:/srv/app/{directory}/.",str(ROOT/directory))
            for f,data in reviewed.items():(ROOT/f).write_bytes(data)
        (ROOT/"courier-directory-manifest.txt").write_text(manifest())
    finally:call("docker","rm",cid)
def build(tag,head):
    assert out("git","rev-parse","HEAD")==head
    assert manifest()==(ROOT/"courier-directory-manifest.txt").read_text(),"Candidate changed since QA"
    with TemporaryDirectory(prefix="orion-courier-build-") as tmp:
        folder=Path(tmp)
        for f in FILES:
            dest=folder/"source"/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/f).read_bytes())
        (folder/"Dockerfile").write_text(f'FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision="{head}"\n')
        call("docker","build","--platform","linux/amd64","-t",tag,str(folder))
        call("docker","run","--rm","--network","none","--entrypoint","python",tag,"-B","-c",
             "from app.delivery_driver import Enroll,access_directory,enroll; from app.delivery_api import router; assert any(r.path.endswith('/couriers/enroll') for r in router.routes)")
if __name__=="__main__":
    if sys.argv[1]=="prepare":prepare()
    else:build(*sys.argv[1:])
