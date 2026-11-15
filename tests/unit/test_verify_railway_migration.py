"""Post-migration inspection must detect data loss and keep secrets out of logs."""
from contextlib import redirect_stdout
import hashlib
import io
import os
import unittest
from unittest.mock import patch

from scripts import verify_railway_migration as check


class Body:
    def __init__(self, data):
        self.data = data
        self.closed = False

    def iter_chunks(self, chunk_size):
        yield self.data[:1]
        yield self.data[1:]

    def close(self):
        self.closed = True


class Store:
    def __init__(self, entries):
        self.entries = entries
        self.streams = []

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        from datetime import datetime, timezone
        yield {"Contents": [{"Key": key, "Size": len(value),
            "LastModified": datetime(2026, 11, 14, tzinfo=timezone.utc)}
            for key, value in self.entries.items()]}

    def get_object(self, **kwargs):
        body = Body(self.entries[kwargs["Key"]])
        self.streams.append(body)
        return {"Body": body}


class MigrationInspectionTests(unittest.TestCase):
    def test_native_manifest_hides_object_names(self):
        client = Store({"private-client-file": b"data"})
        output = io.StringIO()
        with patch.object(check, "s3", return_value=(client, "bucket")), patch.dict(
                os.environ, {"ORION_VERIFY_MEDIA_ENDPOINT": "https://storage.test",
                    "ORION_VERIFY_BACKUP_ENDPOINT": "https://storage.test"}), redirect_stdout(output):
            check.storage_manifest()
        self.assertNotIn("private-client-file", output.getvalue())
        self.assertIn(hashlib.sha256(b"private-client-file").hexdigest(), output.getvalue())
        self.assertIn('"ok": true', output.getvalue())

    def test_manifest_comparison_ignores_copy_time_and_detects_corruption(self):
        source = [{"key_sha256": "key", "size": 4, "sha256": "content", "modified": "before"}]
        target = [{**source[0], "modified": "after"}]
        self.assertEqual(check.compare_storage_manifests(source, target)["matched"], 1)
        self.assertEqual(check.compare_storage_manifests(source, [
            {**target[0], "sha256": "corrupt"}])["mismatched"], ["key"])
        self.assertEqual(check.compare_storage_manifests(source, [])["missing"], ["key"])
        with self.assertRaises(ValueError):
            check.compare_storage_manifests(source, target + target)

    def test_railway_virtual_host_style_is_accepted_by_boto(self):
        self.assertEqual(check.s3_addressing_style("virtual-host"), "virtual")
        for value in ("path", "virtual", "auto"):
            self.assertEqual(check.s3_addressing_style(value), value)
        for value in ("", "unsupported"):
            with self.assertRaises(ValueError):
                check.s3_addressing_style(value)

    def run_storage(self, source, target, exclusion=""):
        before, after = Store(source), Store(target)

        def client(prefix):
            return (before, "before") if prefix.startswith("MIGRATION_SOURCE_") else (after, "after")

        output = io.StringIO()
        with patch.object(check, "s3", side_effect=client), patch.dict(
                os.environ, {"MIGRATION_EXCLUDE_PREFIX": exclusion}), redirect_stdout(output):
            check.storage()
        self.assertTrue(all(body.closed for body in before.streams + after.streams))
        return output.getvalue()

    def test_identical_archives_are_compared_by_content(self):
        output = self.run_storage({"one": b"data", "two": b""}, {"one": b"data", "two": b""})
        self.assertEqual(output.count('"matched_by_sha256": 2'), 2)

    def test_missing_object_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "storage_integrity_mismatch"):
            self.run_storage({"one": b"data"}, {})

    def test_same_size_corruption_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "storage_integrity_mismatch"):
            self.run_storage({"one": b"abcd"}, {"one": b"abce"})

    def test_only_explicit_migration_prefix_is_excluded(self):
        output = self.run_storage({"migration/archive": b"temp", "keep": b"live"},
            {"keep": b"live"}, exclusion="migration/")
        self.assertEqual(output.count('"excluded_objects": 1'), 2)
        with self.assertRaises(RuntimeError):
            self.run_storage({"migration-other": b"data"}, {}, exclusion="migration/")

    def test_stream_digest_and_close(self):
        client = Store({"key": b"hello"})
        self.assertEqual(check.digest_object(client, "bucket", "key"),
            (5, hashlib.sha256(b"hello").hexdigest()))
        self.assertTrue(client.streams[0].closed)

    def test_bridge_redaction_retains_logout_cause(self):
        result = check.safe_bridge_result({"status": "logged_out", "phone": "private-phone",
            "lastError": "private-token", "messages": ["private-message"],
            "transportConfig": {"token": "private-token"},
            "connection_health": {"state": "needs_attention", "disconnect_code": 401,
                "reason": "logged_out", "action": "reauthenticate", "secret": "private-secret"},
            "deliveryDiagnostics": {"delivered": 5, "token": "private-token", "pending": "private-value"}})
        self.assertEqual(result["connection_health"]["action"], "reauthenticate")
        self.assertEqual(result["deliveryDiagnostics"], {"delivered": 5})
        self.assertNotIn("private", str(result))

    def test_unknown_bridge_strings_are_not_logged(self):
        self.assertEqual(check.safe_bridge_result({"status": "private-token",
            "disconnectCode": "private-code", "connected": "private-value",
            "connection_health": {"state": "private-state", "reason": "private-reason",
                "action": "private-action"}}), {"connection_health": {}})

    def test_failure_does_not_log_exception_secrets(self):
        output = io.StringIO()
        with patch.object(check, "database", side_effect=RuntimeError("private-password")), \
                patch.object(check, "bridge"), patch.object(check, "storage"), redirect_stdout(output):
            self.assertEqual(check.main(), 1)
        self.assertNotIn("private-password", output.getvalue())
        self.assertIn('"failed_sections": ["database"]', output.getvalue())


if __name__ == "__main__":
    unittest.main()
