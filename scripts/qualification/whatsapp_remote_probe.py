"""Bounded read-only observation of the existing Railway WhatsApp bridge."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--recipient-check", help="Read-only check of an authorized test recipient; digits only")
    args = parser.parse_args()
    if not 1 <= args.samples <= 1441 or not 1 <= args.interval <= 3600:
        parser.error("Use 1..1441 samples with interval 1..3600 seconds")
    source = Path(__file__).with_name("whatsapp_runtime_probe.mjs").read_bytes()
    module = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
    command = "node --input-type=module -e " + shlex.quote("await import(" + json.dumps(module) + ")")
    if args.recipient_check:
        if not re.fullmatch(r"[1-9][0-9]{7,14}", args.recipient_check):
            parser.error("Recipient must contain country code and digits only")
        command = "ORION_PROBE_RECIPIENT=" + args.recipient_check + " " + command
    argv = [str(args.cli), "ssh", "--project", args.project, "--service", args.service,
            "--environment", args.environment, "--identity-file", str(args.identity), "--", command]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for sample in range(args.samples):
        record = {"observer_at": datetime.now(timezone.utc).isoformat(), "sample": sample + 1}
        try:
            result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=40)
            payloads = [line for line in result.stdout.splitlines() if line.startswith("{")]
            if result.returncode == 0 and payloads:
                record["runtime"] = json.loads(payloads[-1])
            else:
                # Never persist arbitrary remote stdout/stderr in case a
                # future CLI or application error starts including private data.
                record["observer_error"] = "remote_command_failed"
                record["returncode"] = result.returncode
        except (OSError, subprocess.TimeoutExpired, ValueError):
            record["observer_error"] = "remote_observation_unavailable"
        with args.output.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        state = record.get("runtime", {}).get("session", {}).get("status")
        print(json.dumps({"sample": sample + 1, "state": state,
                          "error": record.get("observer_error")}), flush=True)
        if sample + 1 < args.samples:
            time.sleep(max(0, started + (sample + 1) * args.interval - time.monotonic()))


if __name__ == "__main__":
    main()
