"""Merge reviewed files onto production, test the merged sources, then package."""
from pathlib import Path
import hashlib
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
    "app/delivery_crm.py",
    "app/static/orion-delivery-orders.js",
    "app/static/orion-delivery-catalog.js",
    "app/static/orion-delivery-store-admin.js",
    "app/static/orion-delivery-containers.js",
    "app/static/orion-delivery-reports.js",
    "app/static/orion-delivery-logistics.js",
    "app/static/orion-suite.js",
    "app/static/orion-delivery-dashboard.css",
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
        with TemporaryDirectory(prefix="orion-workspace-merge-") as tmp:
            folder=Path(tmp)
            for i,file in enumerate(FILES):
                live,base,review=(folder/f"{i}.{kind}" for kind in ("live","base","review"))
                call("docker","cp",f"{container}:/srv/app/{file}",str(live))
                assert live.is_file() and not live.is_symlink()
                base.write_bytes(git_bytes(BEFORE,file))
                review.write_bytes(git_bytes(head,file))
                assert review.read_bytes()!=base.read_bytes(),file
                if live.read_bytes()==base.read_bytes():
                    result=review.read_bytes()
                else:
                    print(f"Preservando divergência de produção: {file}",flush=True)
                    merged=subprocess.run(["git","merge-file","-p","--diff3",
                        "-L","production","-L","review-base","-L","review",str(live),str(base),str(review)],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    if merged.returncode:
                        raise RuntimeError(f"Conflito na base publicada: {file}; revisão manual obrigatória")
                    result=merged.stdout
                (ROOT/file).write_bytes(result)
                print(f"{file}: {hashlib.sha256(result).hexdigest()}",flush=True)
    finally:
        call("docker","rm",container)

def build(tag,head):
    assert output("git","rev-parse","HEAD")==head
    expected=(ROOT/"workspace-qa-manifest.txt").read_text(encoding="utf-8")
    assert manifest()==expected,"Arquivos diferentes dos homologados"
    with TemporaryDirectory(prefix="orion-workspace-build-") as tmp:
        bundle=Path(tmp)
        for file in FILES:
            dest=bundle/"source"/file
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes((ROOT/file).read_bytes())
        (bundle/"Dockerfile").write_text(
            f"FROM {BASE}\nCOPY source/ /srv/app/\nLABEL org.opencontainers.image.revision=\"{head}\"\n",
            encoding="utf-8")
        call("docker","build","--platform","linux/amd64","-t",tag,str(bundle))
        call("docker","run","--rm","--network","none","--entrypoint","python",tag,"-B","-c",
            "from app import delivery_orders, delivery_api, delivery_catalog, delivery_crm")

if __name__=="__main__":
    if sys.argv[1]=="prepare":
        prepare()
        (ROOT/"workspace-qa-manifest.txt").write_text(manifest(),encoding="utf-8")
    else:
        build(*sys.argv[1:])
