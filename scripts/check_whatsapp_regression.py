"""Compare complete Node TAP suites; never label inherited failures as green."""
from __future__ import annotations
import collections
import json
import re
import sys
from pathlib import Path


def report(file: str) -> dict:
    text = Path(file).read_text(encoding="utf-8")
    failures = collections.Counter(re.findall(r"^not ok \d+ - (.+)$", text, re.M))
    result = {}
    for name in ("tests", "pass", "fail", "cancelled", "skipped"):
        matches = re.findall(rf"^# {name} (\d+)$", text, re.M)
        if len(matches) != 1:
            raise RuntimeError(f"Incomplete or ambiguous TAP report: {file}: {name}")
        result[name] = int(matches[0])
    if result["cancelled"] or sum(failures.values()) != result["fail"]:
        raise RuntimeError(f"Cancelled or incomplete tests: {file}")
    result["failures"] = failures
    return result


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: check_whatsapp_regression.py BASELINE CANDIDATE REPORT.json")
    baseline, candidate = map(report, sys.argv[1:3])
    extra = candidate["failures"] - baseline["failures"]
    result = {"baseline": baseline, "candidate": candidate, "new_failures": extra,
              "resolved_failures": baseline["failures"] - candidate["failures"],
              "global_suite_passed": candidate["fail"] == 0}
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in ("baseline", "candidate")}, ensure_ascii=False))
    if extra or candidate["tests"] < baseline["tests"] or candidate["skipped"] > baseline["skipped"]:
        raise SystemExit("Transport regression gate failed; see the saved full reports")
