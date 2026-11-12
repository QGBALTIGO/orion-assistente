"""Read-only summaries of recorded test logs, never inferred successes."""
import argparse
import json
import re
from pathlib import Path


def read_text(path):
    content = Path(path).read_bytes()
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace")
    return content.decode("utf-8-sig", errors="replace")


def summarize_node(path):
    content = read_text(path)
    lines = content.splitlines()
    passing_titles = [line.strip() for line in lines if line.strip().startswith("✔ ")]
    # The footer uses "test at path:line:col" before the failing title; robust
    # against PowerShell Tee-Object rewriting Node's unicode symbols.
    fail_locations = [
        line.strip()[len("test at "):]
        for line in lines if line.strip().startswith("test at ")
    ]
    fail_titles = []
    for index, line in enumerate(lines):
        if line.strip().startswith("test at ") and index + 1 < len(lines):
            fail_titles.append(lines[index + 1].strip())
    pass_match = re.search(r"(?m)^[^\r\n]*\bpass\s+(\d+)\s*$", content)
    fail_match = re.search(r"(?m)^[^\r\n]*\bfail\s+(\d+)\s*$", content)
    total_match = re.search(r"(?m)^[^\r\n]*\btests\s+(\d+)\s*$", content)
    passing_count = int(pass_match.group(1)) if pass_match else len(passing_titles)
    failing_count = int(fail_match.group(1)) if fail_match else len(fail_locations)
    total_count = int(total_match.group(1)) if total_match else None
    return {
        "source": str(path), "suite": "node-ui",
        "reported_total": total_count, "reported_passed": passing_count,
        "reported_failed": failing_count,
        "failure_locations": fail_locations[:100], "failure_titles": fail_titles[:100],
        "count_consistent": failing_count == len(fail_locations),
        "note": "Preexisting UI tests. Failures require reproducing and root-cause classification.",
    }


def summarize_pytest(path):
    content = read_text(path)
    summary = re.findall(r"(\d+) (passed|failed|skipped|error|errors|warnings)", content)
    failures = [line.strip().removeprefix("FAILED ") for line in content.splitlines()
                if line.strip().startswith("FAILED ")]
    return {"source": str(path), "suite": "pytest-unit",
            "summary": summary[-15:], "failure_cases": failures[:100],
            "note": "May include Windows-only encoding or dependency errors."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui")
    parser.add_argument("--pytest")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = {}
    if args.ui:
        result["ui"] = summarize_node(args.ui)
    if args.pytest:
        result["pytest"] = summarize_pytest(args.pytest)
    blob = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(blob, encoding="utf-8")
    print(blob)


if __name__ == "__main__":
    main()