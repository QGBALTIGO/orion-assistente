"""Compare complete JUnit identities without printing giant synthetic payloads.

UUID-valued parameters are normalized. For oversized or runtime-varying
parametrized names, use the source-defined parameter ordinal only when that test
module is byte-for-byte unchanged and both collections have the same number of
cases for that function. Runtime-varying names include generated dates and IDs
whose display changes with the platform's text encoding.
Raw identity hashes and the original XML remain the evidence for every case.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


UUID = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])")


def read(file: Path) -> tuple[list[dict], list[dict]]:
    root = ET.parse(file).getroot()
    cases = []
    for node in root.iter("testcase"):
        failures, errors, skips = node.findall("failure"), node.findall("error"), node.findall("skipped")
        name = node.attrib["name"]
        cases.append({"class": node.attrib["classname"], "name": name,
            "base": name.split("[", 1)[0], "state": "failed" if failures or errors else "skipped" if skips else "passed",
            "failures": len(failures), "errors": len(errors),
            "messages": [UUID.sub("{uuid}", child.attrib.get("message", ""))[:700]
                         for child in [*failures, *errors, *skips]],
            "raw_name_sha256": hashlib.sha256(name.encode()).hexdigest()})
    return cases, [node.attrib for node in root.iter("testsuite")]


def compare(baseline_file: Path, candidate_file: Path, baseline_root: Path, candidate_root: Path) -> dict:
    baseline, baseline_suites = read(baseline_file)
    candidate, candidate_suites = read(candidate_file)
    counts = [Counter((case["class"], case["base"]) for case in cases) for cases in (baseline, candidate)]
    large_functions = {(case["class"], case["base"]) for case in baseline + candidate if len(case["name"]) > 1024}
    names = []
    for cases in (baseline, candidate):
        groups = defaultdict(Counter)
        for case in cases:
            groups[(case["class"], case["base"])].update([UUID.sub("{uuid}", case["name"])])
        names.append(groups)
    variable_functions = {identity for identity in names[0].keys() & names[1].keys()
                          if names[0][identity] != names[1][identity]}
    safe_source_parameters = set()
    for identity in large_functions | variable_functions:
        module = Path(*identity[0].split(".")).with_suffix(".py")
        old, new = baseline_root / module, candidate_root / module
        if old.is_file() and new.is_file() and old.read_bytes() == new.read_bytes() and counts[0][identity] == counts[1][identity]:
            safe_source_parameters.add(identity)

    def index(cases):
        indexed, ordinals, duplicates = {}, defaultdict(int), defaultdict(int)
        for original in cases:
            case = dict(original)
            family = (case["class"], case["base"])
            ordinal = ordinals[family]
            ordinals[family] += 1
            if family in safe_source_parameters:
                normalized = f"{case['base']}[unchanged-source-param-{ordinal}]"
                case["identity_normalization"] = "unchanged_source_parameter_ordinal"
            else:
                normalized = UUID.sub("{uuid}", case["name"])
                case["identity_normalization"] = "uuid_parameters_only"
            duplicate = duplicates[(case["class"], normalized)]
            duplicates[(case["class"], normalized)] += 1
            key = hashlib.sha256(f"{case['class']}\0{normalized}\0{duplicate}".encode()).hexdigest()
            case["name_prefix"] = case.pop("name")[:160]
            case["identity"] = key
            case["normalized_name_prefix"] = normalized[:160]
            indexed[key] = case
        return indexed

    old, new = index(baseline), index(candidate)
    new_failures, inherited, changed, resolved, added, removed, regressed_skips = [], [], [], [], [], [], []
    for identity, current in new.items():
        previous = old.get(identity)
        if previous is None:
            added.append(current)
        if current["state"] == "failed":
            if previous is None or previous["state"] != "failed":
                new_failures.append(current)
            else:
                inherited.append(current)
                if current["messages"] != previous["messages"]:
                    changed.append({"baseline": previous, "candidate": current})
        elif previous and previous["state"] == "failed":
            resolved.append({"baseline": previous, "candidate": current})
        if previous and previous["state"] == "passed" and current["state"] == "skipped":
            regressed_skips.append(current)
    for identity, previous in old.items():
        if identity not in new:
            removed.append(previous)
    return {"baseline_suites": baseline_suites, "candidate_suites": candidate_suites,
        "normalization": {"uuid_parameters": True,
                          "oversized_functions_safely_matched": len(large_functions & safe_source_parameters),
                          "runtime_varying_functions_safely_matched": [list(item) for item in sorted(variable_functions & safe_source_parameters)],
                          "oversized_functions_not_safe_to_normalize": [list(item) for item in sorted(large_functions-safe_source_parameters)]},
        "new_failures": new_failures, "inherited_failed_cases": inherited,
        "changed_inherited_failure_messages": changed, "resolved_failed_cases": resolved,
        "added_cases": added, "removed_cases": removed, "regressed_skips": regressed_skips,
        "regression_gate_passed": not (new_failures or removed or regressed_skips),
        "entire_candidate_suite_passed": not any(case["state"] == "failed" for case in candidate)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.baseline, args.candidate, args.baseline_root, args.candidate_root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: value if not isinstance(value, list) else len(value) for name, value in result.items()
                      if name not in {"baseline_suites", "candidate_suites"}}, ensure_ascii=False, indent=2))
    return int(not result["regression_gate_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
