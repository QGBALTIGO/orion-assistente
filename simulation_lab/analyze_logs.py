"""Read-only local test-log synopsis; never mark tests passed without log evidence."""
import argparse
import json
import re
from pathlib import Path

def summarize_node(path):
    data = Path(path).read_bytes()
    text = data.decode("utf-16" if data.startswith(b"\xff\xfe") else "utf-8", errors="replace")
    successes, failed = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("✔ "):
            successes.append(line[2:])
        elif line.startswith("✖ "):
            failed.append(line[2:])
    # Node prints failures once as case status and again in failure details.
    distinct = list(dict.fromkeys(failed))
    return {"source": str(path), "passed_cases_seen": len(successes),
            "failing_cases_unique": len(distinct), "fail_titles": distinct[:100],
            "result_complete": "Process completed" not in text}

def summarize_pytest(path):
    data = Path(path).read_bytes()
    text = data.decode("utf-16" if data.startswith(b"\xff\xfe") else "utf-8", errors="replace")
    summary = re.findall(r"(\d+) (passed|failed|skipped|error|errors|warnings)", text)
    failures = re.findall(r"^FAILED (.*?)$", text, re.M)
    return {"source": str(path), "summary": summary[-15:],
            "fail_titles": failures[:80]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ui")
    p.add_argument("--pytest")
    p.add_argument("--out")
    args = p.parse_args()
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