"""A verified archive and read-only readiness gates precede group additions."""
import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from scripts import backup_and_migrate_subscription_group_add as release
from scripts import backup_and_migrate_subscription_direct as previous_release
from scripts import wait_for_subscription_group_add_schema as barrier

ROOT = Path(__file__).resolve().parents[2]


def prepared(monkeypatch, revisions):
    events, versions = [], iter(revisions)
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(release, "migrated_version", lambda _: next(versions))
    monkeypatch.setattr(release, "storage_values", lambda: {})
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def backup(*args, **kwargs):
        assert kwargs == {"schema_revision": release.PREVIOUS,
            "backup_context": "subscription-group-add-0072", "preserve_ownership": True}
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


def test_group_add_is_a_single_additive_head_after_published_direct_messages():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "alembic"))
    migrations = ScriptDirectory.from_config(config)
    assert migrations.get_heads() == [release.TARGET]
    assert release.TARGET == barrier.SCHEMA_MARKER == "0072_subscription_group_add"
    assert release.PREVIOUS == previous_release.TARGET == "0071_subscription_direct"
    assert migrations.get_revision(release.TARGET).down_revision == release.PREVIOUS
    assert migrations.get_revision(release.PREVIOUS).down_revision == "0070_subscription_bulk"
    assert len(release.TARGET) <= 32


def test_verified_backup_is_required_before_the_only_0072_ddl(monkeypatch):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, release.TARGET])
    assert release.main() == 0
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("revision", ["0070_subscription_bulk", "0073_unreviewed", ""])
def test_unreviewed_origin_never_runs_backup_or_ddl(monkeypatch, revision):
    events = prepared(monkeypatch, [revision])
    assert release.main() == 3 and events == []


@pytest.mark.parametrize("valid", [True, False])
def test_restart_of_the_published_revision_only_reads_the_catalog(monkeypatch, valid):
    events = prepared(monkeypatch, [release.TARGET])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == (0 if valid else 3)
    assert events == []


@pytest.mark.parametrize("after_backup", [False, True])
def test_0071_contract_must_still_hold_immediately_before_ddl(monkeypatch, after_backup):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])
    states = iter([True, False] if after_backup else [False])
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: next(states))
    assert release.main() == 3
    assert events == (["verified-backup"] if after_backup else [])


@pytest.mark.parametrize("revision,valid,expected", [
    (release.TARGET, True, 0), (release.TARGET, False, 3), ("0073_unreviewed", True, 3),
])
def test_concurrent_publication_is_verified_without_a_second_ddl(monkeypatch, revision, valid, expected):
    events = prepared(monkeypatch, [release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == expected
    assert events == ["verified-backup"]


@pytest.mark.parametrize("revision,valid", [(release.PREVIOUS, True), (release.TARGET, False)])
def test_alembic_success_is_insufficient_without_the_expected_revision_and_contract(monkeypatch, revision, valid):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS, revision])
    monkeypatch.setattr(release, "schema_ready", lambda _: valid)
    assert release.main() == 3
    assert events == ["verified-backup", "migration"]


@pytest.mark.parametrize("failure", ["backup", "migration"])
def test_failed_backup_or_ddl_stops_release_without_logging_private_details(monkeypatch, capsys, failure):
    events = prepared(monkeypatch, [release.PREVIOUS, release.PREVIOUS])

    def failed(*_args, **_kwargs):
        raise RuntimeError("synthetic-private-release-detail")

    monkeypatch.setattr(release if failure == "backup" else release.subprocess,
                        "perform_backup" if failure == "backup" else "run", failed)
    assert release.main() == 3
    assert events == ([] if failure == "backup" else ["verified-backup"])
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
    assert "schema_release_failed_closed" in output.err
    assert "orion_subscription_group_add_schema_ready" not in output.out


@pytest.mark.parametrize("failure", [None, "dump", "archive", "upload", "head_size", "head_hash",
                                    "readback_size", "readback_hash"])
def test_actual_backup_pipeline_verifies_archive_and_full_remote_readback_before_ddl(monkeypatch, capsys, failure):
    """Run perform_backup itself with synthetic subprocess and storage boundaries."""
    import boto3

    data = b"synthetic-0071-archive-preserving-ownership-and-grants" * 8
    digest = hashlib.sha256(data).hexdigest()
    version, events, uploaded = [release.PREVIOUS], [], {}
    url = SimpleNamespace(host="127.0.0.1", port=5432, username="app_migrator",
        database="synthetic", password="synthetic-private-password")
    settings = {"endpoint": "https://storage.invalid", "bucket": "synthetic-private",
        "region": "iad", "access": "synthetic", "secret": "synthetic", "style": "path"}
    monkeypatch.setattr(release.os, "environ", {})
    monkeypatch.setattr(release, "connection_values", lambda: ("synthetic", url))
    monkeypatch.setattr(release, "migrated_version", lambda _: version[0])
    monkeypatch.setattr(release, "storage_values", lambda: settings)
    monkeypatch.setattr(previous_release, "schema_ready", lambda _: True)
    monkeypatch.setattr(release, "schema_ready", lambda _: True)

    def command(args, **kwargs):
        if args[0] == "pg_dump":
            events.append("dump")
            assert "--no-owner" not in args and "--no-acl" not in args
            assert url.password not in args and kwargs["env"]["PGPASSWORD"] == url.password
            assert kwargs["stdout"] == kwargs["stderr"] == release.subprocess.DEVNULL
            if failure == "dump":
                return SimpleNamespace(returncode=1)
            Path(args[args.index("--file") + 1]).write_bytes(data)
        elif args[0] == "pg_restore":
            events.append("archive")
            assert args[1] == "--list"
            return SimpleNamespace(returncode=int(failure == "archive"))
        else:
            assert args == [release.sys.executable, "-m", "alembic", "upgrade", release.TARGET]
            assert events == ["dump", "archive", "upload", "head", "readback", "close"]
            assert kwargs["env"]["ORION_MIGRATION_SWITCH_ROLE"] == "1"
            events.append("migration")
            version[0] = release.TARGET
        return SimpleNamespace(returncode=0)

    def upload(filename, bucket, key, ExtraArgs):
        events.append("upload")
        assert Path(filename).read_bytes() == data
        assert bucket == settings["bucket"]
        assert key.startswith("production/subscription-group-add-0072/pre-schema-0071_subscription_direct-")
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


@pytest.mark.parametrize("name", ["subscription_diagnostics", "subscription_campaigns",
    "subscription_scheduled_messages", "subscription_bulk", "subscription_direct"])
@pytest.mark.parametrize("valid", [True, False])
def test_published_predecessor_restart_on_0072_never_replays_migration(monkeypatch, name, valid):
    module = importlib.import_module("scripts.backup_and_migrate_" + name)
    monkeypatch.setattr(module, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(module, "migrated_version", lambda _: release.TARGET)
    monkeypatch.setattr(module, "schema_ready", lambda _: valid)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A published predecessor cannot repeat a backup or migration")

    monkeypatch.setattr(module, "perform_backup", forbidden)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    assert module.main() == (0 if valid else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,authenticated,restricted,ready", [
    ("app_runtime", "app_runtime", True, True), ("app_runtime", "app_runtime", True, False),
    ("app_support", "app_support", True, True), ("app_migrator", "app_migrator", True, True),
    ("app_runtime", "postgres", True, True), ("app_runtime", "app_runtime", False, True),
])
async def test_probe_requires_bounded_readonly_authenticated_runtime(monkeypatch, role, authenticated, restricted, ready):
    terminated = []

    class Connection:
        async def fetchrow(self, query):
            assert query == barrier.SCHEMA_QUERY
            return {"runtime_role": role, "authenticated_role": authenticated,
                "restricted_role": restricted, "schema_ready": ready}

        def terminate(self):
            terminated.append(True)

    async def connect(dsn, **kwargs):
        assert dsn == "synthetic"
        assert kwargs["timeout"] == kwargs["command_timeout"] == 3
        assert kwargs["server_settings"] == {"default_transaction_read_only": "on", "search_path": "pg_catalog",
            "statement_timeout": "3000", "lock_timeout": "3000", "application_name": "orion-subscription-group-add-schema-wait"}
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    if (role, authenticated, restricted) == ("app_runtime", "app_runtime", True):
        assert await barrier.probe_schema(barrier.Settings("synthetic"), 3) is ready
    else:
        with pytest.raises(barrier.Stop, match="runtime_role_required"):
            await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert terminated == [True]


@pytest.mark.asyncio
async def test_catalog_query_failure_closes_the_probe_connection(monkeypatch):
    terminated = []

    class Connection:
        async def fetchrow(self, query):
            raise RuntimeError("synthetic-catalog-error")

        def terminate(self):
            terminated.append(True)

    async def connect(*_args, **_kwargs):
        return Connection()

    monkeypatch.setattr(barrier.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="synthetic-catalog-error"):
        await barrier.probe_schema(barrier.Settings("synthetic"), 3)
    assert terminated == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("ready_after", [2, None])
async def test_readiness_wait_is_bounded_and_does_not_turn_missing_schema_into_success(ready_after):
    elapsed, calls = [0.0], []

    async def probe(settings, budget):
        assert settings.dsn == "synthetic" and 0 < budget <= 3
        calls.append(budget)
        return ready_after is not None and len(calls) >= ready_after

    async def sleep(seconds):
        elapsed[0] += seconds

    task = barrier.wait_for_schema(barrier.Settings("synthetic"), clock=lambda: elapsed[0], sleep=sleep, probe=probe)
    if ready_after is None:
        with pytest.raises(barrier.Stop, match="agenda_schema_timeout"):
            await task
        assert elapsed[0] == 90
    else:
        assert await task == {"ok": True, "status": "subscription_group_add_schema_ready", "schema_marker": release.TARGET}
        assert elapsed[0] == 1


@pytest.mark.parametrize("ready", [False, True])
def test_api_cannot_serve_before_the_readonly_0072_barrier(monkeypatch, ready):
    from scripts import start_api

    events = []
    monkeypatch.setattr(start_api.os, "environ", {"PORT": "8080", "APP_DATABASE_URL": "synthetic-runtime"})

    def wait(command, **kwargs):
        assert command == [start_api.sys.executable, "-m", "scripts.wait_for_subscription_group_add_schema"]
        assert kwargs["check"] is True and kwargs["timeout"] == 95
        assert "MIGRATION_DATABASE_URL" not in kwargs["env"]
        events.append("read-only-0072")
        if not ready:
            raise start_api.subprocess.CalledProcessError(1, command)

    def execute(binary, command, environment):
        assert events == ["read-only-0072"]
        assert command[1:4] == ["-m", "uvicorn", "app.main:app"]
        events.append("serve")

    monkeypatch.setattr(start_api.subprocess, "run", wait)
    monkeypatch.setattr(start_api.os, "execve", execute)
    if ready:
        start_api.main()
        assert events == ["read-only-0072", "serve"]
    else:
        with pytest.raises(start_api.subprocess.CalledProcessError):
            start_api.main()
        assert events == ["read-only-0072"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_worker_startup_does_not_consume_jobs_before_0072_is_ready(monkeypatch, ready):
    from app import worker
    from scripts.wait_for_agenda_schema import Settings

    order = ["plan_permissions", "appointment_consent", "subscription_devices", "subscription_chatbot",
        "subscription_templates", "subscription_sent_messages", "appointment_operations", "subscription_diagnostics",
        "subscription_campaigns", "subscription_scheduled_messages", "subscription_bulk", "subscription_direct",
        "subscription_group_add"]
    events = []
    monkeypatch.setattr(worker.os, "environ", {})
    monkeypatch.setattr(Settings, "from_environment", lambda: barrier.Settings("synthetic-runtime"))
    for name in order:
        module = importlib.import_module("scripts.wait_for_" + name + "_schema")

        async def wait(settings, item=name):
            assert settings.dsn == "synthetic-runtime"
            events.append(item)
            if item == "subscription_group_add" and not ready:
                raise barrier.Stop("agenda_schema_timeout")
            return {"ok": True}

        monkeypatch.setattr(module, "wait_for_schema", wait)
    context = {}
    assert worker.WorkerSettings.on_startup is worker.startup_plan_permissions
    if ready:
        await worker.startup_plan_permissions(context)
        assert context["plan_permissions_ready"] is True
    else:
        with pytest.raises(barrier.Stop, match="agenda_schema_timeout"):
            await worker.startup_plan_permissions(context)
        assert context.get("plan_permissions_ready") is not True
    assert events == order


@pytest.mark.parametrize("ready", [False, True])
def test_predeploy_runs_0072_after_0071_and_propagates_its_failure(monkeypatch, ready):
    from scripts import predeploy_one_click_links as chain
    from scripts import backup_and_migrate_commissions as backup
    from scripts import apply_variable_combo_migration as revisions
    from scripts import backup_and_migrate_registration as registration

    order = ["prepaid_pix", "manual_start_times", "appointment_consent", "subscription_devices", "subscription_chatbot",
        "subscription_templates", "subscription_sent_messages", "appointment_operations", "subscription_diagnostics",
        "subscription_campaigns", "subscription_scheduled_messages", "subscription_bulk", "subscription_direct",
        "subscription_group_add"]
    events = []
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(chain.os, "environ", {})
    monkeypatch.setattr(backup, "connection_values", lambda: ("synthetic", object()))
    monkeypatch.setattr(revisions, "migrated_version", lambda _: release.TARGET)
    monkeypatch.setattr(registration, "portal_schema_ready", lambda _: True)
    monkeypatch.setattr(registration, "professional_hardening_schema_ready", lambda _: True)

    def original(command, **kwargs):
        assert command == [chain.sys.executable, "-c", chain.ORIGINAL_RAILWAY_PREDEPLOY_CHECK]
        assert kwargs == {"check": True}
        events.append("original")

    monkeypatch.setattr(chain.subprocess, "run", original)
    for name in order:
        module = importlib.import_module("scripts.backup_and_migrate_" + name)
        monkeypatch.setattr(module, "main", lambda item=name: events.append(item) or int(item == "subscription_group_add" and not ready))
    if ready:
        chain.main()
    else:
        with pytest.raises(RuntimeError, match="subscription_group_add_schema_not_ready"):
            chain.main()
    assert events == ["original", *order]
