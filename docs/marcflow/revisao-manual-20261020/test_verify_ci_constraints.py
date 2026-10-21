"""Synthetic CLI regressions; no application, database, or network execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).with_name("verify_ci_constraints.py")


class VerifyCIConstraintsTests(unittest.TestCase):
    def exercise(self, change: str | None = None) -> tuple[int, dict]:
        pins = {f"synthetic-package-{index:02d}": f"1.{index}.0" for index in range(52)}
        lock = "# Exactly 52 synthetic constraints\n" + "\n".join(
            f"{name}=={version}" for name, version in pins.items()
        ) + "\n"
        main_tokens = [f"{name.upper().replace('-', '_')}-{version}" for name, version in pins.items()]
        docker_tokens = [f"{name}-{version}" for name, version in pins.items()]
        if change == "mismatch":
            docker_tokens[21] = "synthetic-package-21-9.9.9"
        if change == "missing":
            docker_tokens.pop(21)
        main_tokens.append("orion_barbearias-0.8.0")
        docker_tokens.append("orion-barbearias-0.8.0")
        full = " ".join([f"{name}-{version}" for name, version in pins.items()] + ["orion-barbearias-0.8.0"])
        log = "\n".join([
            "2026-10-20T12:34:56.0000000Z Successfully installed setuptools-80.0.0 wheel-0.45.1",
            # Complete download and metadata lines must never rescue a missing installation pin.
            "2026-10-20T12:34:57.0000000Z Downloading " + full,
            "2026-10-20T12:34:58.0000000Z Downloading metadata for " + full,
            "2026-10-20T12:35:00.0000000Z Successfully installed " + " ".join(main_tokens),
            "2026-10-20T12:36:00.0000000Z #17 22.123 Successfully installed " + " ".join(docker_tokens),
        ]) + "\n"
        with tempfile.TemporaryDirectory(prefix="orion-ci-constraints-") as folder:
            directory = Path(folder)
            lock_path, log_path, output_path = (directory / name for name in ("requirements.lock", "job.log", "proof.json"))
            lock_path.write_bytes(lock.encode("utf-8"))
            log_path.write_bytes(log.encode("utf-8"))
            completed = subprocess.run(
                [sys.executable, str(HELPER), "--lock", str(lock_path), "--log", str(log_path),
                 "--output", str(output_path), "--source-commit", "synthetic-commit", "--ci-run-id", "synthetic-run"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertEqual(result, json.loads(output_path.read_text(encoding="utf-8")))
            self.assertEqual(result["inputs"]["lock"]["sha256"], hashlib.sha256(lock.encode()).hexdigest())
            self.assertEqual(result["inputs"]["log"]["sha256"], hashlib.sha256(log.encode()).hexdigest())
            self.assertFalse(result["runtime_execution"])
            self.assertFalse(result["application_execution"])
            self.assertFalse(result["network_access"])
            self.assertEqual(result["successfully_installed_line_count"], 3)
            self.assertEqual(result["ignored_non_application_receipt_lines"], [1])
            self.assertTrue(result["two_environments"])
            self.assertEqual(result["environment_receipt_counts"], {"main": 1, "docker": 1})
            self.assertEqual(len(result["application_install_batches"]), 2)
            return completed.returncode, result

    def test_success_two_exact_normalized_receipts(self) -> None:
        code, result = self.exercise()
        self.assertEqual(code, 0)
        self.assertTrue(result["verified"])
        self.assertEqual(result["versions_matched"], 104)
        self.assertEqual([item["versions_matched"] for item in result["application_install_batches"]], [52, 52])
        self.assertEqual([item["line_number"] for item in result["application_install_batches"]], [4, 5])
        self.assertEqual(result["application_install_batches"][1]["docker_build_step"], 17)

    def test_mismatch_in_one_environment_fails(self) -> None:
        code, result = self.exercise("mismatch")
        self.assertEqual(code, 1)
        self.assertFalse(result["verified"])
        self.assertEqual(result["versions_matched"], 103)
        self.assertTrue(result["application_install_batches"][0]["exact_constraints_match"])
        self.assertEqual(result["application_install_batches"][1]["version_mismatches"], {
            "synthetic-package-21": {"expected": "1.21.0", "installed": "9.9.9"}
        })

    def test_missing_pin_cannot_be_proved_by_download_or_metadata(self) -> None:
        code, result = self.exercise("missing")
        self.assertEqual(code, 1)
        self.assertFalse(result["verified"])
        self.assertEqual(result["versions_matched"], 103)
        self.assertEqual(result["application_install_batches"][1]["missing_pins"], ["synthetic-package-21"])
        self.assertEqual(result["application_install_batches"][1]["version_mismatches"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
