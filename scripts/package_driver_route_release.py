"""Build an immutable overlay on the exact live production image."""
from pathlib import Path
import hashlib
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
BEFORE="cb02ce983b6a27a586ff162377c9eab122c8967a"
BASE="ghcr.io/qgbaltigo/orion-assistente-api@sha256:5b416c05c61f2284b5301a951d93a6ddb6aad07fd740816009729912478f0b37"
FILES=(
    "app/delivery_driver.py","app/delivery_driver_api.py","app/delivery_logistics.py",
    "app/delivery_orders.py","app/delivery_route_batches.py",
    "app/static/orion-delivery-driver.html","app/static/orion-delivery-driver.css",
    "app/static/orion-delivery-driver.js","app/static/orion-delivery-logistics.js",
    "app/static/orion-delivery-orders.js","alembic/versions/0118_delivery_route_batches.py",
    "scripts/backup_and_migrate_delivery.py",
)
NEW={"app/delivery_route_batches.py","alembic/versions/0118_delivery_route_batches.py"}
def call(*args):
    subprocess.run(args,cwd=ROOT,check=True)
def out(*args):
    return subprocess.check_output(args,cwd=ROOT,text=True).strip()
def git_bytes(ref,file):
    return subprocess.check_output(["git","show",f"{ref}:{file}"],cwd=ROOT)
def manifest():
    return "".join(f"{hashlib.sha256((ROOT/f).read_bytes()).hexdigest()}  {f}\n" for f in FILES)
def prepare():
    head=out("git","rev-parse","HEAD")
    reviewed={f:git_bytes(head,f) for f in FILES}
    call("docker","pull","--platform","linux/amd64",BASE)
    cid=out("docker","create","--platform","linux/amd64",BASE)
    try:
        assert out("docker","inspect","--format","{{.Config.WorkingDir}}",cid)=="/srv/app"
        with TemporaryDirectory(prefix="orion-driver-base-") as tmp:
            for i,f in enumerate(FILES):
                path=Path(tmp)/str(i)
                if f in NEW:
                    missing=subprocess.run(["docker","cp",f"{cid}:/srv/app/{f}",str(path)],capture_output=True)
                    assert missing.returncode!=0 and not path.exists(),f"Unexpected file in live image: {f}"
                else:
                    call("docker","cp",f"{cid}:/srv/app/{f}",str(path))
                    assert path.is_file() and not path.is_symlink()
                    assert path.read_bytes()==git_bytes(BEFORE,f),f"Unexpected production divergence: {f}"
            for directory in ("app","alembic","scripts"):
                call("docker","cp",f"{cid}:/srv/app/{directory}/.",str(ROOT/directory))
            for f,data in reviewed.items():
                target=ROOT/f;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        (ROOT/"driver-route-manifest.txt").write_text(manifest(),encoding="utf-8")
    finally:
        call("docker","rm",cid)
def build(tag,head):
    assert out("git","rev-parse","HEAD")==head
    assert manifest()==(ROOT/"driver-route-manifest.txt").read_text(encoding="utf-8"),"Candidate changed since QA"
    with TemporaryDirectory(prefix="orion-driver-build-") as tmp:
        folder=Path(tmp)
        for f in FILES:
            dest=folder/"source"/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/f).read_bytes())
        (folder/"Dockerfile").write_text(f"FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision=\"{head}\"\n",encoding="utf-8")
        call("docker","build","--platform","linux/amd64","-t",tag,str(folder))
        call("docker","run","--rm","--network","none","--entrypoint","python",tag,"-B","-c",
             "from app import delivery_driver, delivery_driver_api, delivery_route_batches, delivery_orders; from scripts.backup_and_migrate_delivery import approved_chain; assert approved_chain()[-1]=='0118_delivery_route_batches'")
if __name__=="__main__":
    if sys.argv[1]=="prepare": prepare()
    else: build(*sys.argv[1:])
