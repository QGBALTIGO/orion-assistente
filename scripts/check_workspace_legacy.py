"""Prove the reviewed Delivery release does not add legacy-niche failures.

Run the same full cross-niche tests twice, against the exact live files and
against the reconciled candidate. Existing test incompatibilities remain
visible in both reports and are not counted as successful tests.
"""
from pathlib import Path
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT/"workspace-production-baseline"
OUT=ROOT/"artifacts/workspace-legacy"
OUT.mkdir(parents=True,exist_ok=True)
FILES=[p.relative_to(BASELINE) for p in BASELINE.rglob("*") if p.is_file()]
assert len(FILES)==12

def run(label):
    result=subprocess.run(
        ["node","--test","--test-reporter=./scripts/workspace-test-reporter.mjs",*sys.argv[1:]],
        cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (OUT/f"{label}.log").write_text(result.stdout,encoding="utf-8")
    print(result.stdout,flush=True)
    events=[json.loads(line[8:]) for line in result.stdout.splitlines() if line.startswith("ORIONQA ")]
    summaries=[e for e in events if e["kind"]=="summary"]
    assert len(summaries)==1,f"Missing test completion: {label}"
    stats=summaries[0]
    failures=[e for e in events if e["kind"]=="fail"]
    assert stats["failed"]==len(failures),(label,stats,failures)
    assert stats["tests"]>0 and stats["skipped"]==0 and stats["cancelled"]==0
    assert result.returncode in (0,1)
    assert (result.returncode!=0)==bool(failures)
    return stats,{(f["file"],f["name"]):f["error"] for f in failures}

candidate={path:(ROOT/path).read_bytes() for path in FILES}
try:
    for path in FILES:
        (ROOT/path).write_bytes((BASELINE/path).read_bytes())
    baseline_stats,baseline_failures=run("baseline")
finally:
    for path,data in candidate.items():
        (ROOT/path).write_bytes(data)
review_stats,review_failures=run("candidate")
assert baseline_stats["tests"]==review_stats["tests"],"Cross-niche test count changed"
new={key:value for key,value in review_failures.items() if baseline_failures.get(key)!=value}
report={"baseline":baseline_stats,"candidate":review_stats,
        "preexisting_failures":[{"file":key[0],"test":key[1],"error":value}
                                for key,value in review_failures.items() if baseline_failures.get(key)==value],
        "new_or_changed_failures":[{"file":key[0],"test":key[1],"error":value} for key,value in new.items()]}
(OUT/"comparison.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("LEGACY_COMPARISON="+json.dumps(report,ensure_ascii=False),flush=True)
assert not new,"New or changed failures in other niches"
