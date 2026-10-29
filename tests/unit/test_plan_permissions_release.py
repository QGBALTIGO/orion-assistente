from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from scripts import backup_and_migrate_plan_permissions as release
from scripts import wait_for_plan_permissions_schema as barrier


@contextmanager
def unlocked(*args): yield


def test_unreviewed_source_and_missing_explicit_migration_flag_fail_closed(monkeypatch):
    monkeypatch.setattr(release,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(release,"release_lock",unlocked)
    monkeypatch.setattr(release,"migrated_version",lambda raw:release.PREVIOUS)
    monkeypatch.delenv("ORION_RUN_PLAN_PERMISSIONS_MIGRATION",raising=False)
    monkeypatch.setattr(release,"perform_backup",lambda *a,**k:pytest.fail("No backup or DDL without explicit rollout"))
    assert release.main()==3
    monkeypatch.setenv("ORION_RUN_PLAN_PERMISSIONS_MIGRATION","1")
    monkeypatch.setattr(release,"migrated_version",lambda raw:"unexpected-newer-schema")
    assert release.main()==2


def test_completed_restart_only_verifies_no_new_backup_or_grants(monkeypatch):
    monkeypatch.setattr(release,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(release,"release_lock",unlocked)
    monkeypatch.setattr(release,"migrated_version",lambda raw:release.TARGET)
    monkeypatch.setattr(release,"schema_ready",lambda raw:True)
    monkeypatch.setattr(release,"perform_backup",lambda *a,**k:pytest.fail("Completed restart must be read-only"))
    assert release.main()==0


def test_failed_private_backup_prevents_any_ddl(monkeypatch):
    monkeypatch.setattr(release,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(release,"release_lock",unlocked)
    monkeypatch.setattr(release,"migrated_version",lambda raw:release.PREVIOUS)
    monkeypatch.setattr(release,"previous_ready",lambda raw:True)
    monkeypatch.setenv("ORION_RUN_PLAN_PERMISSIONS_MIGRATION","1")
    monkeypatch.setattr(release,"configuration_binding",lambda:("synthetic","f"*64))
    monkeypatch.setattr(release,"storage_values",lambda:{})
    def failure(*args,**kwargs): raise RuntimeError("private_backup_failed")
    monkeypatch.setattr(release,"perform_backup",failure)
    monkeypatch.setattr(release.subprocess,"run",lambda *a,**k:pytest.fail("DDL after failed backup"))
    assert release.main()==3


@pytest.mark.asyncio
async def test_worker_waits_for_completed_initial_grants_before_jobs(monkeypatch):
    observed=[]
    async def probe(settings,timeout):
        observed.append("probe")
        return len(observed)>1
    async def sleep(seconds): observed.append("wait")
    result=await barrier.wait_for_schema(SimpleNamespace(),probe=probe,sleep=sleep)
    assert result["schema_marker"]==release.TARGET
    assert observed==["probe","wait","probe"]


@pytest.mark.asyncio
async def test_arq_startup_awaits_readonly_barrier(monkeypatch):
    from app.worker import WorkerSettings
    from scripts.wait_for_agenda_schema import Settings
    called=[]
    monkeypatch.setattr(Settings,"from_environment",lambda:"synthetic-runtime")
    async def wait(settings): called.append(settings)
    monkeypatch.setattr(barrier,"wait_for_schema",wait)
    context={}
    await WorkerSettings.on_startup(context)
    assert called==["synthetic-runtime"]
    assert context['plan_permissions_ready'] is True


def test_current_release_never_reruns_applied_earlier_migrations(monkeypatch):
    from scripts import backup_and_migrate_registration as entry
    from scripts import backup_and_migrate_appointment_communications as previous
    events=[]
    monkeypatch.setattr(entry,"connection_values",lambda:("synthetic",object()))
    monkeypatch.setattr(entry,"migrated_version",lambda raw:release.TARGET)
    monkeypatch.setattr(release,"main",lambda:events.append("plan-permissions") or 0)
    monkeypatch.setattr(previous,"main",lambda:pytest.fail("Earlier migration must not be replayed"))
    assert entry.predeploy_main()==0 and events==["plan-permissions"]
