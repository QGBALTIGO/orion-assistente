"""The Cakto probe is read-only after the unrelated Orion 0053 migration."""
import pytest

from scripts import backup_and_migrate_cakto as cakto


def test_0053_cakto_predeploy_uses_only_existing_schema_check(monkeypatch):
    calls = []
    monkeypatch.setattr(cakto, "connection_values", lambda: ("synthetic-db", "synthetic-url"))
    monkeypatch.setattr(cakto, "migrated_version", lambda _: "0053_central_notifications")
    monkeypatch.setattr(cakto, "schema_ready", lambda raw: calls.append(raw) or True)
    monkeypatch.setattr(cakto, "perform_backup", lambda *_args, **_kwargs: pytest.fail("0043 backup must not repeat"))
    monkeypatch.setattr(cakto.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("0043 DDL must not repeat"))
    assert cakto.main() == 0
    assert calls == ["synthetic-db"]


def test_0053_cakto_predeploy_fails_closed_when_original_schema_is_broken(monkeypatch):
    monkeypatch.setattr(cakto, "connection_values", lambda: ("synthetic-db", "synthetic-url"))
    monkeypatch.setattr(cakto, "migrated_version", lambda _: "0053_central_notifications")
    monkeypatch.setattr(cakto, "schema_ready", lambda _: False)
    assert cakto.main() == 3


def test_unknown_future_revision_remains_rejected(monkeypatch):
    monkeypatch.setattr(cakto, "connection_values", lambda: ("synthetic-db", "synthetic-url"))
    monkeypatch.setattr(cakto, "migrated_version", lambda _: "0054_unknown")
    assert cakto.main() == 2
