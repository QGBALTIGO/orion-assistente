"""Replay synthetic data through real Orion pure booking engine functions.

This is actual Python application-code execution, NOT API, AI, PostgreSQL, RLS,
booking persistence, WhatsApp delivery or production load.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.booking_service import amounts, bounds, validate_local
from simulation_lab.orion_lab import BASE_DATE, SERVICES, make_report, read_jsonl_gz

ELIGIBLE_AREAS = {
    "agenda", "professionals_services_commissions", "recurrence",
}
SERVICE_BY_NAME = {name: (minutes, cents) for name, minutes, cents in SERVICES}
TZ = ZoneInfo("America/Campo_Grande")


def replay(out: Path):
    file = out / "scenarios.jsonl.gz"
    if not file.exists():
        raise FileNotFoundError("Generate synthetic corpus before the replay")
    checked = 0
    by_area = Counter()
    failures = []
    timings = []
    started = time.perf_counter()
    for case in read_jsonl_gz(file):
        if case["area"] not in ELIGIBLE_AREAS:
            continue
        tick = time.perf_counter()
        state = case["state"]
        items = []
        for name in state["service_names"]:
            duration, cents = SERVICE_BY_NAME[name]
            items.append({"duration_min": duration, "price_cents": cents,
                          "prep_min": 0, "cleanup_min": 0})
        expected_minutes = sum(item["duration_min"] for item in items)
        expected_price = sum(item["price_cents"] for item in items)
        start = datetime.combine(
            BASE_DATE + timedelta(days=state["lead_days"]),
            datetime.min.time(), tzinfo=TZ
        ) + timedelta(minutes=state["start_min"])
        try:
            validated = validate_local(start, TZ)
            total_minutes, prep, cleanup, price = amounts(items)
            result = bounds(validated, items)
            conditions = (
                total_minutes == expected_minutes,
                price == expected_price,
                prep == 0,
                cleanup == 0,
                result["total_cents"] == expected_price,
                result["start_at"] == validated.astimezone(result["start_at"].tzinfo),
                (result["end_at"] - result["start_at"]).total_seconds() == expected_minutes * 60,
                (result["occupied_until"] - result["occupied_from"]).total_seconds()
                    == expected_minutes * 60,
            )
            if not all(conditions):
                failures.append({"id": case["scenario_id"], "error": "invariant mismatch",
                                 "failed_conditions": [i for i, passed in enumerate(conditions)
                                                       if not passed]})
        except Exception as exc:
            failures.append({"id": case["scenario_id"],
                             "error_type": type(exc).__name__,
                             "error": str(exc)[:250]})
        timings.append((time.perf_counter() - tick) * 1000)
        checked += 1
        by_area[case["area"]] += 1
    timings.sort()
    def pct(n):
        return round(timings[int((len(timings) - 1) * n)], 5) if timings else None
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    receipt = {
        "corpus_sha256": manifest["files"]["scenarios.jsonl.gz"]["sha256"],
        "kind": "real_orion_pure_booking_functions",
        "app_code_executed": True,
        "end_to_end_executions": 0,
        "postgres_transactions": 0,
        "ai_calls": 0,
        "whatsapp_sends": 0,
        "notes": "Called the real Orion amounts, bounds and validate_local with isolated synthetic input. This does not exercise database concurrency or reservation persistence.",
        "function_scenarios_executed": checked,
        "passed": checked - len(failures),
        "failed": len(failures),
        "by_area": dict(by_area),
        "function_latency_ms": {"p50": pct(.5), "p95": pct(.95), "p99": pct(.99)},
        "duration_seconds": round(time.perf_counter() - started, 3),
        "failures": failures[:100],
    }
    (out / "real_engine_results.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    reference_path = out / "reference_results.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else {}
    make_report(out, manifest, reference)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="simulation_lab/artifacts")
    args = parser.parse_args()
    print(json.dumps(replay(Path(args.out)), ensure_ascii=False))