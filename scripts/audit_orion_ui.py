"""Verify migrated primitives and inventory legacy module CSS."""
import json
import re
import sys
from pathlib import Path
GROUPS = ("af-dashboard-periods", "af-dashboard-metric-tabs", "af-dashboard-publication-tabs",
          "af-offer-tabs", "af-auto-filters", "af-history-filters", "af-rule-presets")
def main(root, output):
    folder=Path(root)
    suite=(folder/"orion-suite.js").read_text()
    affiliate=(folder/"orion-affiliate-workspace.js").read_text()
    css=(folder/"orion-suite.css").read_text()
    assert 'os-pill-tabs os-ui-segments os-account-menu' in suite
    assert 'class="os-pill-tab" data-os-account-tab=' in suite
    for name in GROUPS:
        assert name+" os-pill-tabs os-ui-segments" in affiliate, name
    for name in ("af-dashboard-heading","af-offers-heading","af-auto-heading","af-history-heading"):
        assert name+" os-title-row os-ui-heading" in affiliate, name
    assert "--os-ui-segment-radius:100px" in css
    assert "grid-template-columns:minmax(0,1fr)" in css
    results=[]
    for path in sorted(folder.glob("orion-*.css")):
        if path.name in ("orion-suite.css","orion-design-system.css"):
            continue
        s=path.read_text()
        results.append({"file":path.name,
                        "button_selector_candidates":len(re.findall(r'[^{}]*(?:\bbutton\b|\.os-button|\.os-pill-tab)[^{}]*\{',s)),
                        "literal_hex_color_candidates":len(re.findall(r'#[0-9a-fA-F]{3,8}(?![\w-])',s)),
                        "radius_declarations":len(re.findall(r'\bborder-radius\s*:',s))})
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({"contract":"passed","migrated_groups":list(GROUPS),"legacy_review_candidates":results},indent=2))
    print("Orion UI contract passed; reviewed "+str(len(results))+" module CSS inventories.")
if __name__=="__main__":
    main(sys.argv[1],sys.argv[2])
