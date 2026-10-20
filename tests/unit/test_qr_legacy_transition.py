"""No messages or database access: old context identity and adoption guards."""
from dataclasses import replace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import qr_legacy, qr_channel_scope as scope, qr_intake_revision as revision, inbox_control
from scripts import one_time_legacy_adoption as ops
from test_qr_channels_scope import identity, ingress, request, PHONE  # noqa: F401


def test_stable_alias_is_distinct_per_company_and_never_a_generated_v4_channel():
    tenant=uuid4(); value=qr_legacy.channel_id(tenant)
    assert value.version==5 and value==qr_legacy.channel_id(tenant)
    assert value!=qr_legacy.channel_id(uuid4()) and qr_legacy.is_alias(tenant,value)


async def test_legacy_alias_preserves_namespace_and_message_receipts_and_scopes_tenant(monkeypatch):
    current=replace(identity(),legacy_transport=True,central_adopted=True)
    async def guard(): return current
    monkeypatch.setenv('INBOX_CONTROL_ENABLED','0')
    token=scope.bind(current,guard)
    try:
        assert scope.tenant_scope(current.tenant_id)==str(current.tenant_id)
        assert current.conversation_namespace(PHONE)==PHONE
        assert scope.message_identity('old-inbound')=='old-inbound'
        assert revision.keys(current.tenant_id,PHONE)==revision.channel_keys(current,PHONE)
        assert inbox_control.enabled() is True
        with pytest.raises(ValueError): scope.tenant_scope(uuid4())
    finally: scope.reset(token)
    assert inbox_control.enabled() is False


async def test_registered_alias_uses_old_context_and_done_markers_before_authority_activation(ingress,monkeypatch):
    a,_,identities,redis,calls,_,_,api=ingress
    a=replace(a,legacy_transport=True)
    identities[str(a.channel_id)]=a
    state_key=f'orion:qr:ai:{a.tenant_id}:{PHONE}'
    redis.values[state_key]='{"step":"select_time","preserved":"synthetic"}'
    response=await request(api,a)
    assert response.status_code==200 and calls[-1][2]==PHONE
    assert redis.values[state_key]=='{"step":"select_time","preserved":"synthetic"}'
    redis.values[f'orion:qr:ai:done:{a.tenant_id}:another-old-id']='1'
    response=await request(api,a,message_id='another-old-id')
    assert response.json()['step']=='duplicate'


def test_registration_operation_is_disabled_without_explicit_authority(monkeypatch,capsys):
    monkeypatch.delenv('ORION_RUN_LEGACY_ADOPTION',raising=False)
    monkeypatch.setattr(ops.backup,'engine_for',lambda *_:pytest.fail('database touched'))
    assert ops.main()==0 and 'disabled' in capsys.readouterr().out


def test_source_write_requires_proof_bound_to_the_operation_snapshot_and_phase():
    from types import SimpleNamespace
    settings=SimpleNamespace(run_id=str(uuid4()),url=SimpleNamespace(database='synthetic'),phase='activate')
    with pytest.raises(ops.Stop,match='rehearsal_proof_required'):
        ops.apply(None,settings,{}, {'fingerprint':'synthetic'})


def test_ordinary_startup_strips_legacy_operation_credentials():
    from scripts.start_api import runtime_environment
    assert runtime_environment({'ORION_RUN_LEGACY_ADOPTION':'1','ORION_LEGACY_ADOPTION_RUN_ID':'synthetic',
        'ORION_LEGACY_ADOPTION_PHASE':'activate','QR_LEGACY_CENTRAL_ENABLED':'1'})=={'QR_LEGACY_CENTRAL_ENABLED':'1'}
