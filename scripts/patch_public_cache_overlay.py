"""Apply only reviewed public-site differences onto the exact deployed image.

The overlay build starts from the immutable currently-live image and refuses to
build when any edited block differs from the development baseline. This avoids
replacing newer production files or rebuilding unrelated application code.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

ROOT = Path("/srv/app")
BASE = Path("/tmp/orion-public-base")
EDITED = Path("/tmp/orion-public-edited")
FILES = (
    "app/static/orion-public.js",
    "app/static/orion-public.css",
    "app/public_site_api.py",
)


def patch_file(base: str, edited: str, current: str, path: str) -> tuple[str, int]:
    before = base.splitlines(keepends=True)
    after = edited.splitlines(keepends=True)
    operations = [
        (kind, i1, i2, j1, j2)
        for kind, i1, i2, j1, j2 in
        difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes()
        if kind != "equal"
    ]
    if not operations:
        raise RuntimeError(f"{path}: no feature changes found")

    # Process in reverse source order, so insertion anchors and before/after
    # blocks never overlap with an earlier change.
    for kind, i1, i2, j1, j2 in reversed(operations):
        old = "".join(before[i1:i2])
        new = "".join(after[j1:j2])
        if old:
            count = current.count(old)
            if count != 1:
                raise RuntimeError(
                    f"{path}:{i1+1}: refused {kind}: baseline block matched "
                    f"{count} times (production differs from expected source)"
                )
            current = current.replace(old, new, 1)
        else:
            # Insertions have no old body. Require unique context on both sides
            # to avoid inserting into an unrelated location in the live image.
            left = "".join(before[max(0, i1 - 3):i1])
            right = "".join(before[i1:i1 + 3])
            anchor = left + right
            count = current.count(anchor)
            if count != 1 or (not left and not right):
                raise RuntimeError(
                    f"{path}:{i1+1}: refused insertion; context matched {count}"
                )
            current = current.replace(anchor, left + new + right, 1)
    return current, len(operations)


def main():
    # Validate delta algorithm against its baseline before touching image files.
    output = {}
    for path in FILES:
        base = (BASE / path).read_text(encoding="utf-8")
        edited = (EDITED / path).read_text(encoding="utf-8")
        runtime = (ROOT / path).read_text(encoding="utf-8")
        verified, _ = patch_file(base, edited, base, path)
        if verified != edited:
            raise RuntimeError(f"{path}: delta not deterministic on baseline")
        patched, changes = patch_file(base, edited, runtime, path)
        print(f"{path}: {changes} guarded changes, {len(runtime)} -> {len(patched)} chars")
        output[path] = patched

    # All three files must pass guards before any write. Don't ship partial work.
    for path, patched in output.items():
        (ROOT / path).write_text(patched, encoding="utf-8")
    print("Orion public cache patch applied to production base image")


if __name__ == "__main__":
    main()
