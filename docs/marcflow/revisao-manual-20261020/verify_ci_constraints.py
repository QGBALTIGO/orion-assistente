"""Verify two pip application-install receipts in a GitHub CI job text log.

This reads files only. It neither imports the application nor runs pip, Docker,
the runtime, or a provider. Successful receipts prove what pip logged, not the
contents of an image after any later mutation. Download/metadata lines do not
qualify as install receipts. This is intentionally strict for the main job and
its BuildKit Docker build: one local-project receipt in each environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_]*\Z")
_PIN = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)\s*\Z")
_RECEIPT = re.compile(r"(?<!\S)Successfully installed\s+(.+?)\s*\Z")
_DOCKER_PREFIX = re.compile(r"(?:^|\s)#(?P<step>[0-9]+)\s+(?P<elapsed>[0-9]+(?:\.[0-9]+)?)\s+\Z")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_constraints(text: str, expected_count: int) -> dict[str, str]:
    pins: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None or _VERSION.fullmatch(match[2]) is None:
            raise ValueError(f"lock line {number}: requires a single exact name==version pin")
        name = normalize_name(match[1])
        if name in pins:
            raise ValueError(f"lock line {number}: duplicate normalized name {name}")
        pins[name] = match[2]
    if len(pins) != expected_count:
        raise ValueError(f"lock pin count {len(pins)} does not equal required {expected_count}")
    return dict(sorted(pins.items()))


def parse_installed(payload: str) -> dict[str, str]:
    installed: dict[str, str] = {}
    for token in payload.split():
        name, separator, version = token.rpartition("-")
        if not separator or _NAME.fullmatch(name) is None or _VERSION.fullmatch(version) is None:
            raise ValueError(f"invalid Successfully installed token: {token!r}")
        normalized = normalize_name(name)
        if normalized in installed:
            raise ValueError(f"duplicate installed package {normalized}")
        installed[normalized] = version
    return installed


def verify_log(
    pins: dict[str, str], log: str, local_project: str
) -> dict[str, Any]:
    project = normalize_name(local_project)
    if project in pins:
        raise ValueError("local project must be excluded from dependency constraints")
    if _NAME.fullmatch(local_project) is None:
        raise ValueError("invalid local-project package name")
    batches: list[dict[str, Any]] = []
    ignored_receipts: list[int] = []
    malformed_receipts: list[dict[str, Any]] = []
    receipt_count = 0
    for number, raw_line in enumerate(log.splitlines(), 1):
        line = _ANSI.sub("", raw_line)
        match = _RECEIPT.search(line)
        if match is None:
            continue
        receipt_count += 1
        try:
            installed = parse_installed(match[1])
        except ValueError as exc:
            # A malformed line cannot be admitted as installation evidence.
            malformed_receipts.append({"line_number": number, "error": str(exc)})
            continue
        if project not in installed:
            ignored_receipts.append(number)
            continue
        prefix = line[: match.start()]
        docker = _DOCKER_PREFIX.search(prefix)
        environment = "docker" if docker is not None else "main"
        missing = sorted(set(pins) - set(installed))
        mismatches = {
            name: {"expected": version, "installed": installed[name]}
            for name, version in pins.items()
            if name in installed and installed[name] != version
        }
        unexpected = {
            name: version
            for name, version in sorted(installed.items())
            if name not in pins and name != project
        }
        matches = {
            name: version for name, version in pins.items() if installed.get(name) == version
        }
        batches.append({
            "environment": environment,
            "line_number": number,
            "line_sha256": sha256(raw_line.encode("utf-8")),
            "docker_build_step": int(docker["step"]) if docker is not None else None,
            "installed_package_count": len(installed),
            "local_project": {"name": project, "version": installed[project]},
            "versions_matched": len(matches),
            "matched_versions": matches,
            "missing_pins": missing,
            "version_mismatches": mismatches,
            "unexpected_packages": unexpected,
            "exact_constraints_match": not (missing or mismatches or unexpected),
        })
    counts = {name: sum(item["environment"] == name for item in batches) for name in ("main", "docker")}
    two_environments = counts == {"main": 1, "docker": 1}
    return {
        "verified": two_environments and all(item["exact_constraints_match"] for item in batches)
        and not malformed_receipts,
        "two_environments": two_environments,
        "environment_receipt_counts": counts,
        "successfully_installed_line_count": receipt_count,
        "ignored_non_application_receipt_lines": ignored_receipts,
        "malformed_receipts": malformed_receipts,
        "application_install_batches": batches,
        "versions_matched": sum(item["versions_matched"] for item in batches),
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-pin-count", type=positive_int, default=52)
    parser.add_argument("--local-project", default="orion-barbearias")
    parser.add_argument("--source-commit", help="Caller-supplied provenance label; not independently verified")
    parser.add_argument("--ci-run-id", help="Caller-supplied provenance label; not independently verified")
    args = parser.parse_args(argv)
    result: dict[str, Any] = {
        "schema_version": 1,
        "verification_scope": "pip Successfully installed receipts in supplied text log only",
        "runtime_execution": False,
        "application_execution": False,
        "network_access": False,
        "verified": False,
        "caller_supplied_labels": {"source_commit": args.source_commit, "ci_run_id": args.ci_run_id},
        "limitations": [
            "The supplied log and caller labels are not independently authenticated.",
            "Receipts do not prove the final installed state after later environment mutations.",
            "No application, runtime, provider, Docker, or pip command was executed.",
        ],
    }
    try:
        lock_bytes = args.lock.read_bytes()
        log_bytes = args.log.read_bytes()
        result["inputs"] = {
            "lock": {"path": str(args.lock.resolve()), "sha256": sha256(lock_bytes), "size_bytes": len(lock_bytes)},
            "log": {"path": str(args.log.resolve()), "sha256": sha256(log_bytes), "size_bytes": len(log_bytes)},
        }
        pins = read_constraints(lock_bytes.decode("utf-8-sig"), args.expected_pin_count)
        result["expected_pin_count"] = len(pins)
        result["expected_versions"] = pins
        result.update(verify_log(pins, log_bytes.decode("utf-8-sig"), args.local_project))
    except (OSError, UnicodeError, ValueError) as exc:
        result["error"] = str(exc)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
