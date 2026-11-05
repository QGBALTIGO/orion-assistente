"""A reviewed catalog and a verified private archive precede campaign DDL."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from scripts import backup_and_migrate_subscription_campaigns as release
from scripts import backup_and_migrate_subscription_diagnostics as previous_release
from scripts import wait_for_subscription_campaigns_schema as barrier


def prepared(monkeypatch, versions):
    events = []
    revisions = iter(versions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(revisions))
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs == {"schema_revision": release.PREVIOUS,
                          "backup_context": "subscription-campaigns-0068",
                          "preserve_ownership": True}
        events.append("verified-backup")

    def migrate(command, **kwargs):
        assert events == ["verified-backup"]
        assert command == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
        assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
        assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        events.append("migration")

    monkeypatch.setattr(release, "perform_backup", backup)
    monkeypatch.setattr(release.subprocess, "run", migrate)
    return events


def test_campaigns_precedes_scheduled_messages_after_published_diagnostics_and_agenda():
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root / "alembic"))
    migrations = ScriptDirectory.from_config(config)
    assert migrations.get_heads() == ["0071_subscription_direct"]
    assert migrations.get_revision("0070_subscription_bulk").down_revision == "0069_subscription_schedules"
    assert migrations.get_revision("0069_subscription_schedules").down_revision == release.TARGET
    assert release.TARGET == barrier.SCHEMA_MARKER == "0068_subscription_campaigns"
    assert release.PREVIOUS == previous_release.TARGET == "0067_subscription_diagnostics"
    assert migrations.get_revision(release.TARGET).down_revision == release.PREVIOUS
    assert migrations.get_revision(release.PREVIOUS).down_revision == "0066_appointment_operations"
    assert migrations.get_revision("0066_appointment_operations").down_revision == "0065_subscription_sent_messages"


def test_only_verified_backup_allows_reviewed_revision(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0065_subscription_sent_messages", "0066_appointment_operations",
                                      "0066_subscription_diagnostics", "0069_unreviewed", ""])
def test_unreviewed_origin_never_runs_backup_or_ddl(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [True, False])
def test_restart_of_published_revision_only_verifies_catalog(monkeypatch, valid):
    events = prepared(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert events == []


def test_published_diagnostics_and_agenda_contracts_must_be_intact(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: False)
    assert release.main() == 3 and events == []


def test_predecessor_catalog_is_rechecked_after_backup_before_any_ddl(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])
    states = iter([True, False])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: next(states))
    assert release.main() == 3 and events == ["verified-backup"]


@pytest.mark.parametrize("revision,valid,expected", [
    (release.TARGET, True, 0), (release.TARGET, False, 3), ("unreviewed", True, 3),
])
def test_concurrent_revision_change_is_verified_or_rejected_without_overwrite(monkeypatch, revision, valid, expected):
    events = prepared(monkeypatch, [release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == expected and events == ["verified-backup"]


@pytest.mark.parametrize("revision,valid", [(release.PREVIOUS, True), (release.TARGET, False)])
def test_successful_alembic_process_cannot_hide_wrong_revision_or_invalid_catalog(monkeypatch, revision, valid):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == 3 and events == ["verified-backup", "migration"]


def test_failed_migration_is_not_ready_and_does_not_print_internal_exception(monkeypatch, capsys):
    prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-private-database-detail")

    monkeypatch.setattr(release.subprocess, "run", fail)
    assert release.main() == 3
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
    assert "schema_release_failed_closed" in output.err
    assert "orion_subscription_campaigns_schema_ready" not in output.out


@pytest.mark.parametrize("failure", [None, "dump", "archive", "upload", "head_size",
                                    "head_hash", "readback_size", "readback_hash"])
def test_real_backup_path_requires_archive_remote_metadata_and_complete_readback_before_ddl(monkeypatch, capsys, failure):
    """Exercise the actual shared backup function; no subprocess or network escapes."""
    import boto3

    data = b"synthetic-owner-and-grant-preserving-database-archive" * 8
    digest = hashlib.sha256(data).hexdigest()
    version = [release.PREVIOUS]
    events, uploaded = [], {}
    url = SimpleNamespace(host="127.0.0.1", port=5432, username="app_migrator",
                          database="synthetic", password="synthetic-private-password")
    settings = {"endpoint": "https://storage.invalid", "bucket": "synthetic-private",
                "region": "iad", "access": "synthetic", "secret": "synthetic", "style": "path"}
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", url))
    monkeypatch.setattr(release, "migrated_version", lambda _: version[0])
    monkeypatch.setattr(release, "storage_values", lambda: settings)
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def command(args, **kwargs):
        if args[0] == "pg_dump":
            events.append("dump")
            assert "--no-owner" not in args and "--no-acl" not in args
            assert url.password not in args
            assert kwargs["env"]["PGPASSWORD"] == url.password
            assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
            if failure == "dump":
                return SimpleNamespace(returncode=1)
            Path(args[args.index("--file") + 1]).write_bytes(data)
        elif args[0] == "pg_restore":
            events.append("archive")
            assert args[1] == "--list"
            return SimpleNamespace(returncode=int(failure == "archive"))
        else:
            assert args[-3:] == ["alembic", "upgrade", release.TARGET]
            assert events == ["dump", "archive", "upload", "head", "readback", "close"]
            events.append("migration")
            version[0] = release.TARGET
        return SimpleNamespace(returncode=0)

    def upload(filename, bucket, key, ExtraArgs):
        events.append("upload")
        assert Path(filename).read_bytes() == data
        assert bucket == settings["bucket"]
        assert key.startswith("production/subscription-campaigns-0068/pre-schema-0067_subscription_diagnostics-")
        assert ExtraArgs == {"Metadata": {"sha256": digest, "schema": release.PREVIOUS}}
        uploaded.update(bucket=bucket, key=key)
        if failure == "upload":
            raise RuntimeError("synthetic-private-storage-detail")

    def head(Bucket, Key):
        events.append("head")
        assert (Bucket, Key) == (uploaded["bucket"], uploaded["key"])
        return {"ContentLength": len(data) + int(failure == "head_size"),
                "Metadata": {"sha256": "0" * 64 if failure == "head_hash" else digest}}

    class Body:
        def iter_chunks(self, chunk_size):
            events.append("readback")
            payload = data[:-1] if failure == "readback_size" else data
            if failure == "readback_hash":
                payload = b"x" * len(data)
            yield payload[:len(payload) // 2]
            yield payload[len(payload) // 2:]

        def close(self):
            events.append("close")

    def get_object(Bucket, Key):
        assert (Bucket, Key) == (uploaded["bucket"], uploaded["key"])
        return {"Body": Body()}

    client = SimpleNamespace(upload_file=upload, head_object=head, get_object=get_object)
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: client)
    monkeypatch.setattr(release.subprocess, "run", command)
    assert release.main() == (0 if failure is None else 3)
    assert ("migration" in events) is (failure is None)
    assert version[0] == (release.TARGET if failure is None else release.PREVIOUS)
    output = capsys.readouterr()
    assert url.password not in output.out + output.err
    assert "synthetic-private-storage-detail" not in output.out + output.err
    if failure in {"readback_size", "readback_hash"}:
        assert events[-1] == "close"
        assert "private_backup_archive_verified" not in output.out


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_probe_only_uses_bounded_readonly_catalogs_as_runtime(monkeypatch, ready):
    closed = []

    class Connection:
        async def fetchrow(self, query):
            assert query == barrier.SCHEMA_QUERY
            return {"runtime_role": "app_runtime", "authenticated_role": "app_runtime",
                    "restricted_role": True, "schema_ready": ready}

        def terminate(self):
            closed.append(True)

    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["timeout"] == kwargs["command_timeout"] == 3
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        assert kwargs["server_settings"]["search_path"] == "pg_catalog"
        assert kwargs["server_settings"]["statement_timeout"] == "3000"
        assert kwargs["server_settings"]["lock_timeout"] == "3000"
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,authenticated,restricted", [
    ("app_migrator", "app_migrator", True), ("app_support", "app_support", True),
    ("app_runtime", "postgres", True), ("app_runtime", "app_runtime", False),
])
async def test_probe_rejects_privileged_or_assumed_identity_and_closes(monkeypatch, role, authenticated, restricted):
    closed = []

    class Connection:
        async def fetchrow(self, _query):
            return {"runtime_role": role, "authenticated_role": authenticated,
                    "restricted_role": restricted, "schema_ready": True}

        def terminate(self):
            closed.append(True)

    async def connect(*_args, **_kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(barrier.Stop, match="runtime_role_required"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert closed == [True]


@pytest.mark.asyncio
async def test_probe_terminates_the_connection_on_catalog_failure(monkeypatch):
    closed = []

    class Connection:
        async def fetchrow(self, _query):
            raise RuntimeError("synthetic-catalog-failure")

        def terminate(self):
            closed.append(True)

    async def connect(*_args, **_kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="synthetic-catalog-failure"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert closed == [True]
