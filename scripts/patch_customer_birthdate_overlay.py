"""Overlay only reviewed changes on the exact live customer-area image."""
from pathlib import Path
from patch_public_cache_overlay import patch_file
ROOT = Path("/srv/app")
BASE = Path("/tmp/orion-birth-base")
EDITED = Path("/tmp/orion-birth-edited")
FILES = ("app/public_customer_area.py", "app/public_site_api.py",
         "app/static/orion-public.js", "app/static/orion-customer-area.js")
def main():
    output = {}
    for path in FILES:
        base = (BASE/path).read_text(encoding="utf-8")
        edited = (EDITED/path).read_text(encoding="utf-8")
        current = (ROOT/path).read_text(encoding="utf-8")
        if current == base:
            output[path] = edited
        else:
            verified, _ = patch_file(base, edited, base, path)
            if verified != edited:
                raise RuntimeError(f"{path}: non-deterministic baseline delta")
            output[path], _ = patch_file(base, edited, current, path)
        print(f"{path}: reviewed overlay verified")
    for path, content in output.items():
        (ROOT/path).write_text(content, encoding="utf-8")
    print("Birthdate portal overlay applied without unrelated changes")
if __name__ == "__main__":
    main()
