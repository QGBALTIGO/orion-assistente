"""Explicit deployment authority, sealed migrations and safe diagnostic output."""
from dataclasses import replace
from uuid import uuid4
import pytest
from scripts import one_time_communication_migration as ops
from scripts import wait_for_communication_schema as gate
from test_one_time_onboarding_migration import configuration


def test_disabled_operation_never_connects(monkeypatch, capsys):
    monkeypatch.delenv('ORION_RUN_COMMUNICATION_MIGRATION', raising=False)
    monkeypatch.setattr(ops.backup, 'engine_for', lambda *_: pytest.fail('database touched'))
    assert ops.main() == 0 and 'disabled' in capsys.readouterr().out


def test_inbox_has_independent_authorization_and_run_identity(monkeypatch):
    configuration(monkeypatch)
    with pytest.raises(ops.Stop, match='explicit_flag_required'):
        ops.Settings.from_environment()
    monkeypatch.setenv('ORION_RUN_COMMUNICATION_MIGRATION', '1')
    with pytest.raises(ops.Stop, match='invalid_configuration'):
        ops.Settings.from_environment()
    run_id = str(uuid4())
    monkeypatch.setenv('ORION_COMMUNICATION_MIGRATION_RUN_ID', run_id)
    settings = ops.Settings.from_environment()
    assert settings.run_id == run_id and 'synthetic' not in repr(settings)


def test_only_reviewed_communication_upgrade_loads_load():
    modules = ops.reviewed_migrations()
    assert [m.revision for m in modules] == [row[0] for row in ops.MIGRATIONS]
    assert modules[0].down_revision == ops.SOURCE and modules[-1].revision == ops.TARGET


def test_changed_migration_refuses_before_engine_io(monkeypatch):
    monkeypatch.setattr(ops, 'MIGRATIONS', ((ops.MIGRATIONS[0][0], ops.SOURCE, '0'*64),))
    monkeypatch.setattr(ops.backup, 'engine_for', lambda *_: pytest.fail('database touched'))
    with pytest.raises(ops.Stop, match='unreviewed_communication_migration'):
        ops.execute(None)


@pytest.mark.parametrize('change', ['run_id', 'database', 'snapshot'])
def test_isolated_restore_proof_cannot_authorize_another_target(monkeypatch, change):
    monkeypatch.setattr(ops, 'reviewed_migrations', lambda: [])
    from types import SimpleNamespace
    run_id = str(uuid4()); name = ops.backup.restore_name(run_id); expected = {'source': 'sealed'}
    proof = ops._RestoredSourceProof(run_id, name, expected)
    proof = replace(proof, **{change: {'run_id': str(uuid4()), 'database': 'railway', 'snapshot': {}}[change]})
    settings = SimpleNamespace(run_id=run_id, url=SimpleNamespace(database=name))
    with pytest.raises(ops.Stop, match='restored_source_proof_mismatch'):
        ops.apply_exact_migrations(None, settings, expected, restored_proof=proof)


def test_ordinary_api_discards_inbox_operation_authority():
    from scripts.start_api import runtime_environment
    assert runtime_environment({'ORION_RUN_COMMUNICATION_MIGRATION': '1',
        'ORION_COMMUNICATION_MIGRATION_RUN_ID': str(uuid4()), 'APP_DATABASE_URL': 'runtime-only'}) == {
            'APP_DATABASE_URL': 'runtime-only'}


async def test_gate_preserves_bounded_runtime_probe_and_reports_new_marker():
    async def ready(*_): return True
    result = await gate.wait_for_schema(None, probe=ready)
    assert result == {'ok': True, 'status': 'communication_schema_ready', 'schema_marker': ops.TARGET}
