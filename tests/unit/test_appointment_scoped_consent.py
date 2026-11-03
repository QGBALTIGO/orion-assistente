"""A booking or a quoted permission is never a message authorization."""
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit
from contextlib import asynccontextmanager

import pytest

from app.appointment_consent import explicit_customer_statement, staff_consent_actor
from app.appointment_lifecycle import LifecycleActor
from app.appointment_messages import (
    APPOINTMENT_MESSAGE_CONSENT_STATEMENT, TEMPLATES, compose_prefill,
)


def test_old_prefill_is_unchanged_and_does_not_grant_messages():
    body = compose_prefill('AG-ABCDEF')
    assert body == TEMPLATES['public_prefill'].replace('{{protocolo}}', 'AG-ABCDEF')
    assert not explicit_customer_statement(body, 'AG-ABCDEF')
    assert not explicit_customer_statement(APPOINTMENT_MESSAGE_CONSENT_STATEMENT, 'AG-ABCDEF')


def test_complete_explicit_prefill_allows_only_transport_whitespace_and_case_changes():
    body = compose_prefill('AG-ABCDEF', appointment_messages_opt_in=True)
    assert body.endswith(APPOINTMENT_MESSAGE_CONSENT_STATEMENT)
    assert explicit_customer_statement(body, 'AG-ABCDEF')
    assert explicit_customer_statement(' \r\n' + body.lower().replace('\n', '\r\n') + ' ', 'AG-ABCDEF')
    assert not explicit_customer_statement(body, 'AG-ABCDEX')


@pytest.mark.parametrize('alter', [
    lambda body: body.replace('Autorizo receber', 'Não autorizo receber'),
    lambda body: 'Não aceito a mensagem abaixo:\n' + body,
    lambda body: body + '\nNão quero receber mensagens.',
    lambda body: body.replace('um convite de avaliação', 'ofertas e campanhas'),
    lambda body: body.replace('deste agendamento', 'de qualquer agendamento'),
    lambda body: body.replace(APPOINTMENT_MESSAGE_CONSENT_STATEMENT, 'Pode confirmar.'),
])
def test_altered_or_negated_statement_never_grants(alter):
    body = compose_prefill('AG-ABCDEF', appointment_messages_opt_in=True)
    assert not explicit_customer_statement(alter(body), 'AG-ABCDEF')


@pytest.mark.parametrize('context', [
    None,
    LifecycleActor(role='customer', source='public_site', customer_id=uuid4()),
    LifecycleActor(role='customer', source='whatsapp_qr', customer_id=uuid4()),
    LifecycleActor(role='support', source='panel'),
    LifecycleActor(role='viewer', source='panel', account_id=uuid4()),
    LifecycleActor(role='owner', source='unknown', account_id=uuid4()),
])
def test_staff_attestation_requires_trusted_authenticated_writer(context):
    assert staff_consent_actor(context) is None


@pytest.mark.parametrize('role,source', [('owner', 'panel'), ('professional', 'professional_portal')])
def test_staff_attestation_retains_actual_account(role, source):
    account = uuid4()
    assert staff_consent_actor(LifecycleActor(role=role, source='panel', account_id=account)) == (
        source, {'role': role, 'account_id': str(account)})


@pytest.mark.asyncio
async def test_public_choice_only_prepares_verified_customer_statement_without_writing_permission(monkeypatch):
    from app import appointment_confirmation, plan_permissions, public_site_api
    from tests.unit.test_central_booking_whatsapp import BookingSession, tenant_scope
    tenant, admin = uuid4(), uuid4()
    db = BookingSession(uuid4())
    db.tenant = tenant
    async def resolve(_slug):
        return tenant
    async def entitled(_db, _tenant, _feature):
        return True
    async def connected(target):
        assert target == admin
        return '556781234567'
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED', '1')
    monkeypatch.setenv('ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED', '1')
    monkeypatch.setenv('QR_AI_TENANT_ID', str(admin))
    monkeypatch.setattr(public_site_api, 'resolve_public', resolve)
    monkeypatch.setattr(plan_permissions, 'has_feature', entitled)
    monkeypatch.setattr(appointment_confirmation, 'get_session_factory', lambda: lambda: db)
    monkeypatch.setattr(appointment_confirmation, 'tenant_transaction', tenant_scope)
    monkeypatch.setattr(appointment_confirmation, 'connected_channel_phone', connected)
    result = await appointment_confirmation.public_followup('synthetic',
        {'protocol': 'AG-G32XU5'}, '+5567999999902', appointment_messages_opt_in=True)
    message = parse_qs(urlsplit(result['whatsapp_confirmation_url']).query)['text'][0]
    assert explicit_customer_statement(message, 'AG-G32XU5')
    assert len(db.queries) == 1 and all(query.lstrip().startswith('SELECT') for query in db.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize('message,scope', [
    ('sair', 'all'), ('parar mensagens', 'all'), ('cancelar publicidade', 'marketing'),
])
async def test_legacy_whatsapp_uses_shared_explicit_withdrawal_scope(monkeypatch, message, scope):
    from app import contact_preferences, whatsapp_commands
    tenant, channel = uuid4(), uuid4()
    phone = '+5567999999920'
    calls, deleted = [], []
    class Db:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
    db = Db()
    @asynccontextmanager
    async def transaction(actual_db, actual_tenant):
        assert actual_db is db and actual_tenant == tenant
        yield db
    async def record(actual_db, actual_tenant, actual_phone, actual_scope):
        assert actual_db is db
        calls.append((actual_tenant, actual_phone, actual_scope))
    class Redis:
        async def delete(self, key):
            deleted.append(key)
    monkeypatch.setattr(whatsapp_commands, 'get_session_factory', lambda: lambda: db)
    monkeypatch.setattr(whatsapp_commands, 'tenant_transaction', transaction)
    monkeypatch.setattr(contact_preferences, 'record_optout', record)
    handled, reply = await whatsapp_commands.try_command(tenant, channel, phone, message,
        {}, Redis(), 'synthetic-customer-state')
    assert handled and calls == [(tenant, phone, scope)]
    assert deleted == ['synthetic-customer-state']
    assert ('automáticas' if scope == 'all' else 'promocionais') in reply
    assert 'Seus agendamentos permanecem.' in reply
