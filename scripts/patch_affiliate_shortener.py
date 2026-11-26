"""Patch only the integration anchors of the existing Orion runtime."""
from pathlib import Path

def replace_once(source, old, new):
    if new in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError("Unexpected integration anchor: " + old[:100])
    return source.replace(old, new, 1)

def patch(root: Path):
    workspace = root / "app/affiliate_workspace.py"
    source = workspace.read_text()
    source = replace_once(source, "router = APIRouter()\n",
                          "router = APIRouter()\nfrom app.affiliate_shortener import router as shortener_router\nrouter.include_router(shortener_router)\n")
    source = replace_once(source, '    "workspace.js": ("orion-affiliate-workspace.js", "text/javascript"),',
                          '    "workspace.js": ("orion-affiliate-workspace.js", "text/javascript"),\n    "shortener.js": ("orion-affiliate-shortener.js", "text/javascript"),')
    workspace.write_text(source)
    suite = root / "app/static/orion-suite.js"
    source = suite.read_text()
    source = replace_once(source, "['afiliados-ofertas','Ofertas','box']",
                          "['afiliados-ofertas','Ofertas','box'],['afiliados-links','Encurtador de links','link']")
    source = replace_once(source, "affiliateMounted.whatsappAvailable===whatsappAvailable){",
                          "affiliateMounted.whatsappAvailable===whatsappAvailable&&\n        (affiliateMounted.view==='links')===(view==='links')){")
    source = replace_once(source, "affiliateMounted.controller.setView(view);",
                          "affiliateMounted.controller.setView(view);\n      affiliateMounted.view=view;")
    source = replace_once(source, "const workspace=await loadAffiliateAssets();",
                          "const nativeWorkspace=await loadAffiliateAssets();\n      const workspace=view==='links'?await import('/api/v1/afiliados-assets/shortener.js?v=1'):nativeWorkspace;")
    source = replace_once(source, "affiliateMounted={host,controller,tenantId:c.tenantId,whatsappAvailable};",
                          "affiliateMounted={host,controller,tenantId:c.tenantId,whatsappAvailable,view};")
    if "workspace.css?v=" in source:
        import re
        source = re.sub(r"workspace\.css\?v=[^']+", "workspace.css?v=af-shortener-20261126", source, count=1)
    else:
        source = replace_once(source, "afiliados-assets/workspace.css'", "afiliados-assets/workspace.css?v=af-shortener-20261126'")
    suite.write_text(source)
    css = root / "app/static/orion-affiliate-workspace.css"
    addition = (root / "app/static/orion-affiliate-shortener.css").read_text()
    original = css.read_text()
    if addition not in original:
        css.write_text(original.rstrip()+"\n\n"+addition)

if __name__ == "__main__":
    import sys
    patch(Path(sys.argv[1] if len(sys.argv)>1 else "."))
