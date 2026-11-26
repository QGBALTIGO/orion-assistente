"""Overlay the tested template editor onto the current API image only.

The VPS renderer must advertise image_templates_v1 before advanced controls
are enabled. Old engines still support changing the existing brand and layout.
"""
import hashlib
import re
import sys
from pathlib import Path

def patch(root,source):
    root,source=Path(root),Path(source)
    static=root/"app/static"
    js=static/"orion-affiliate-workspace.js"
    original=js.read_bytes()
    blob=hashlib.sha1(b"blob "+str(len(original)).encode()+b"\0"+original).hexdigest()
    if blob!="af7c841b1561a7fed4b2be23450ae4d6986cb742":
        raise RuntimeError("Production affiliate UI changed; review and rebase the editor")
    suite=static/"orion-suite.js"
    s=suite.read_text()
    for asset in ("workspace.css","workspace.js"):
        pattern=r"(afiliados-assets/"+re.escape(asset)+r")(?:\?v=[^']+)?'"
        s,n=re.subn(pattern,r"\1?v=affiliate-image-editor-20261126'",s,count=1)
        if n!=1:
            raise RuntimeError("Missing asset version: "+asset)
    proxy=root/"app/affiliate_workspace.py"
    p=proxy.read_text()
    old='    "POST": (r"offers|offers/demo|'
    if p.count(old)!=1:
        raise RuntimeError("Production API allowlist changed")
    p=p.replace(old,'    "POST": (r"templates/preview-image|templates/(?:default|[a-f0-9]{32})/default|offers|offers/demo|',1)
    js.write_bytes((source/"app/static/orion-affiliate-workspace.js").read_bytes())
    css=static/"orion-affiliate-workspace.css"
    css.write_text(css.read_text().rstrip()+"\n\n"+(source/"app/static/orion-affiliate-image-editor.css").read_text())
    suite.write_text(s);proxy.write_text(p)

if __name__=="__main__":
    patch(sys.argv[1],sys.argv[2])
