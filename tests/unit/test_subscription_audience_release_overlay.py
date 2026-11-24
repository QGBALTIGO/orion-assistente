"""Artifact-boundary checks; no Docker daemon or application dependencies needed."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import subscription_audience_release_overlay as release


class OverlayReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "manifest.json"
        self.revision = "a" * 40
        self.manifest = {"schema_version": 1, "services": {}}
        for name, definition in release.SERVICES.items():
            source = "qr-bridge/src/check.mjs" if name == "bridge" else f"app/{name}.py"
            unit = "qr-bridge/test/check.test.mjs" if name == "bridge" else f"tests/unit/test_{name}.py"
            integration = ["tests/integration/test_api.py"] if name == "api" else []
            for relative in (source, unit, *integration):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# synthetic\n")
            target = "/srv/qr/src/check.mjs" if name == "bridge" else "/srv/app/" + source
            self.manifest["services"][name] = {
                "base_image": definition["base_image"],
                "files": [{"source": source, "target": target, "base_sha256": None}],
                "tests": {"unit": [unit], "integration": integration},
            }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.manifest))

    def validate(self):
        self.save()
        return release.load_manifest(self.root, self.path)

    def test_three_pinned_images_and_declared_tests_are_accepted(self):
        self.assertEqual(self.validate(), self.manifest)

    def test_different_base_or_missing_service_is_rejected(self):
        for change in ("base", "service"):
            with self.subTest(change=change):
                original = copy.deepcopy(self.manifest)
                if change == "base":
                    self.manifest["services"]["api"]["base_image"] = "ghcr.io/example/app:latest"
                else:
                    del self.manifest["services"]["worker"]
                with self.assertRaises(release.ReleaseError):
                    self.validate()
                self.manifest = original

    def test_wrong_target_duplicate_and_invalid_hash_are_rejected(self):
        service = self.manifest["services"]["api"]
        for changed in ({"target": "/srv/app/requirements.lock"}, {"base_sha256": "absent"}):
            original = copy.deepcopy(service["files"])
            service["files"][0].update(changed)
            with self.assertRaises(release.ReleaseError):
                self.validate()
            service["files"] = original
        service["files"].append(dict(service["files"][0]))
        with self.assertRaises(release.ReleaseError):
            self.validate()

    def test_dependency_files_and_traversal_are_not_overlay_sources(self):
        (self.root / "requirements.lock").write_text("unchanged dependencies")
        for source in ("requirements.lock", "app/../requirements.lock", "/etc/passwd"):
            self.manifest["services"]["api"]["files"][0]["source"] = source
            with self.assertRaises(release.ReleaseError):
                self.validate()

    def test_source_file_and_parent_symlinks_are_rejected(self):
        path = self.root / "app/api.py"
        path.unlink()
        path.symlink_to(self.root / "app/worker.py")
        with self.assertRaises(release.ReleaseError):
            self.validate()
        path.unlink()
        directory = self.root / "app"
        directory.rename(self.root / "actual")
        directory.symlink_to(self.root / "actual", target_is_directory=True)
        with self.assertRaises(release.ReleaseError):
            self.validate()

    def test_missing_integration_or_wildcard_test_is_rejected(self):
        self.manifest["services"]["api"]["tests"]["integration"] = []
        with self.assertRaises(release.ReleaseError):
            self.validate()
        self.manifest["services"]["api"]["tests"]["integration"] = ["tests/integration/test_api.py"]
        self.manifest["services"]["api"]["tests"]["unit"] = ["tests/unit/test_*.py"]
        with self.assertRaises(release.ReleaseError):
            self.validate()

    def test_runtime_configuration_and_every_base_layer_are_preserved(self):
        base = {"Config": {"Cmd": ["start"], "Env": ["A=B"], "User": "appuser"},
                "RootFS": {"Layers": ["first", "second"]}}
        candidate = copy.deepcopy(base)
        candidate["RootFS"]["Layers"].append("overlay")
        candidate["Config"]["Labels"] = {"revision": self.revision}
        release.assert_inherited(base, candidate)
        for bad in ("config", "layers"):
            altered = copy.deepcopy(candidate)
            if bad == "config":
                altered["Config"]["Env"] = ["A=C"]
            else:
                altered["RootFS"]["Layers"][0] = "reinstalled"
            with self.assertRaises(release.ReleaseError):
                release.assert_inherited(base, altered)

    def audit(self, path, expected):
        return subprocess.run([sys.executable, "-c", release.AUDIT_PY], text=True,
            input=json.dumps([{"target": str(path), "sha256": expected}]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_real_auditor_distinguishes_absence_hash_drift_and_symlink(self):
        path = self.root / "new.py"
        self.assertEqual(self.audit(path, None).returncode, 0)
        path.write_text("known original\n")
        original = release.sha256(path)
        self.assertEqual(self.audit(path, original).returncode, 0)
        self.assertNotEqual(self.audit(path, None).returncode, 0)
        path.write_text("concurrent change\n")
        self.assertNotEqual(self.audit(path, original).returncode, 0)
        path.unlink()
        path.symlink_to(self.root / "app/api.py")
        self.assertNotEqual(self.audit(path, release.sha256(path)).returncode, 0)

    def test_build_context_contains_only_declared_overlay_files(self):
        output = self.root / "release"
        base = {"Id": "sha256:base", "Config": {"WorkingDir": "/srv/app"},
                "RootFS": {"Layers": ["base-dependencies", "base-app"]}}
        candidate = copy.deepcopy(base)
        candidate["Id"] = "sha256:candidate"
        candidate["RootFS"]["Layers"].append("only-overlay")
        calls = []
        with patch.object(release, "assert_checkout"), patch.object(release, "audit_files") as audit, \
             patch.object(release, "run", side_effect=lambda command, **kw: calls.append(command)), \
             patch.object(release, "inspect", side_effect=lambda image: base if "@" in image else candidate):
            release.build(self.root, self.path, self.manifest, self.revision, "123-1", output)
        self.assertEqual(audit.call_count, 6)
        self.assertFalse(any("push" in command for command in calls))
        for name in release.SERVICES:
            context = output / name
            self.assertEqual(sorted(item.name for item in context.iterdir()), ["Dockerfile", "payload"])
            expected = {str(Path(entry["target"]).relative_to(release.SERVICES[name]["root"]))
                        for entry in self.manifest["services"][name]["files"]}
            actual = {str(path.relative_to(context / "payload"))
                      for path in (context / "payload").rglob("*") if path.is_file()}
            self.assertEqual(actual, expected)
            for path in (context / "payload").rglob("*"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644)
            dockerfile = (context / "Dockerfile").read_text()
            self.assertEqual(sum(line.startswith("COPY ") for line in dockerfile.splitlines()), 1)
            self.assertNotIn("RUN ", dockerfile)
            self.assertNotIn("CMD ", dockerfile)
            self.assertIn(self.manifest["services"][name]["base_image"], dockerfile)
        record = json.loads((output / "build.json").read_text())
        self.assertEqual(record["revision"], self.revision)
        self.assertEqual(record["manifest_sha256"], release.sha256(self.path))

    def test_publish_is_blocked_before_all_candidate_gates_pass(self):
        record = {"services": {name: {"qualified": {"unit": True, "integration": False}}
                               for name in release.SERVICES}}
        with patch.object(release, "checked_record", return_value=record), \
             patch.object(release, "run") as command:
            with self.assertRaises(release.ReleaseError):
                release.publish(self.root, self.path, self.manifest, self.revision, self.root)
            command.assert_not_called()

    def test_candidate_tests_mount_tests_without_replacing_application_code(self):
        command = release.container_command(self.root, "api", "candidate")
        mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
        self.assertEqual(len(mounts), 1)
        self.assertIn("target=/srv/app/tests,readonly", mounts[0])
        self.assertNotIn("target=/srv/app/app", mounts[0])
        self.assertIn("none", command)
        self.assertNotIn("GITHUB_TOKEN", " ".join(command))

    def test_migration_and_integration_cannot_use_a_remote_or_mismatched_database(self):
        environment = {
            "APP_DATABASE_URL": "postgresql+asyncpg://app_runtime:synthetic@127.0.0.1:5432/orion_subscription_audience_ci",
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://app_migrator:synthetic@127.0.0.1:5432/orion_subscription_audience_ci",
            "SUPPORT_DATABASE_URL": "postgresql+asyncpg://app_support:synthetic@127.0.0.1:5432/orion_subscription_audience_ci",
            "REDIS_URL": "redis://127.0.0.1:6379/15",
            "REQUIRE_POSTGRES_TESTS": "1", "REQUIRE_REDIS_TESTS": "1",
        }
        with patch.dict(os.environ, environment, clear=True):
            release.validate_disposable_environment()
            for bad in (environment["MIGRATION_DATABASE_URL"].replace("127.0.0.1", "production.invalid"),
                        environment["MIGRATION_DATABASE_URL"].replace("audience_ci", "real_data"),
                        environment["MIGRATION_DATABASE_URL"].replace(":5432/", ":5440/")):
                os.environ["MIGRATION_DATABASE_URL"] = bad
                with self.assertRaises(release.ReleaseError) as failure:
                    release.validate_disposable_environment()
                self.assertNotIn("synthetic@", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
