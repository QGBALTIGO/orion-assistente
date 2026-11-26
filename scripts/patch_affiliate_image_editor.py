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
    # Merge only reviewed changes into the shipped module. Both source versions
    # are repository files, never generated from production state or secrets.
    from difflib import SequenceMatcher
    baseline=(source/"affiliate-workspace-baseline.js").read_text().splitlines(keepends=True)
    replacement=(source/"app/static/orion-affiliate-workspace.js").read_text().splitlines(keepends=True)
    live=js.read_text()
    groups=SequenceMatcher(None,baseline,replacement,autojunk=False).get_grouped_opcodes(2)
    for group in groups:
        old="".join(baseline[group[0][1]:group[-1][2]])
        new="".join(replacement[group[0][3]:group[-1][4]])
        if old==new:
            continue
        if live.count(old)!=1:
            # Only the non-sensitive frontend anchor is displayed for review.
            raise RuntimeError("Production affiliate UI anchor changed: "+old[:240])
        live=live.replace(old,new,1)
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
    js.write_text(live)
    css=static/"orion-affiliate-workspace.css"
    css.write_text(css.read_text().rstrip()+"\n\n"+(source/"app/static/orion-affiliate-image-editor.css").read_text())
    suite.write_text(s);proxy.write_text(p)

if __name__=="__main__":
    patch(sys.argv[1],sys.argv[2])
