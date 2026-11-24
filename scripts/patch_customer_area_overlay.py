"""Apply only reviewed customer-area edits to the exact deployed API image."""
from pathlib import Path
from patch_public_cache_overlay import patch_file

ROOT = Path("/srv/app")
BASE = Path("/tmp/orion-area-base")
EDITED = Path("/tmp/orion-area-edited")
FILES = (
    "app/main.py",
    "app/public_site_api.py",
    "app/static/orion-public.html",
    "app/static/orion-public.js",
)

def main():
    output = {}
    for path in FILES:
        base = (BASE / path).read_text(encoding="utf-8")
        edited = (EDITED / path).read_text(encoding="utf-8")
        current = (ROOT / path).read_text(encoding="utf-8")
        verified, _ = patch_file(base, edited, base, path)
        if verified != edited:
            raise RuntimeError(f"{path}: reviewed patch is not deterministic")
        updated, count = patch_file(base, edited, current, path)
        print(f"{path}: {count} guarded edits")
        output[path] = updated
    for path, content in output.items():
        (ROOT / path).write_text(content, encoding="utf-8")
    print("Customer area overlay verified: no unrelated deployed files replaced")

if __name__ == "__main__":
    main()
