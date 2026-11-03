"""Real booking, verified consent and withdrawal without global opt-in fixtures.

All numbers, channels and tenants are synthetic. Sender spies never contact a
transport; persisted outbox and consent guards still run against PostgreSQL.
"""
import hashlib
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import appointment_confirmation as confirmation
from app import appointment_notifications as notifications
from app.appointment_consent import revoke_scoped_consents
from app.appointment_lifecycle import LifecycleActor
from app.appointment_messages import compose_prefill
from app.booking_service import book_appointment, confirm_hold
from app.contact_preferences import record_optout
from app.database import tenant_transaction
from tests.integration.test_booking import booking_db, phone, start_at
from tests.integration.test_appointment_notifications_pg import comm_db, PRO_PHONE

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def scoped_db(comm_db):
    d = comm_db
    # The legacy fixture seeds standing permissions. Remove them to exercise
    # the ordinary first booking that the missed reminders exposed.
    async with d['migrator'].begin() as db:
        await db.execute(text('DELETE FROM contact_consents WHERE tenant_id=:t'), {'t': d['a']})
    try:
        yield d
    finally:
        async with d['migrator'].begin() as db:
            for table in ('appointment_message_consents', 'management_requests'):
                await db.execute(text(f'DELETE FROM {table} WHERE tenant_id=:t'), {'t': d['a']})


async def new_booking(d, *, opted_in=False, role=None, hour=10):
    context = LifecycleActor(role=role, source='panel', account_id=d['account']) if role else None
    key = 'scoped-confirm-' + str(uuid4())
    async with d['factory']() as db:
        held = await book_appointment(db, d['a'], d['pro'], [d['cut']], phone(920),
            'Cliente sintético', start_at(d['day'], hour), 'scoped-hold-' + str(uuid4()),
            context=context, source='public_site' if role is None else 'unknown')
        confirmed = await confirm_hold(db, d['a'], held['id'], context=context,
            source='public_site' if role is None else 'unknown',
            customer_phone=phone(920) if role is None else None,
            idempotency_key=key, appointment_messages_opt_in=opted_in)
    return confirmed, context, key


async def consent_rows(d):
    async with d['migrator'].connect() as db:
        scoped = (await db.execute(text('SELECT * FROM appointment_message_consents WHERE tenant_id=:t'),
                                  {'t': d['a']})).mappings().all()
        standing = (await db.execute(text('SELECT * FROM contact_consents WHERE tenant_id=:t'),
                                    {'t': d['a']})).mappings().all()
    return scoped, standing


async def due_reminder(d, appointment):
    async with d['migrator'].begin() as db:
        return (await db.execute(text("""
            UPDATE appointment_notifications SET due_at=clock_timestamp()-interval '1 second',
              expires_at=clock_timestamp()+interval '1 minute'
            WHERE tenant_id=:t AND appointment_id=:a AND event='reminder' RETURNING *
        """), {'t': d['a'], 'a': appointment['id']})).mappings().one()


@pytest.mark.asyncio
async def test_no_consent_reproduces_client_failure_while_professional_notice_succeeds(scoped_db):
    d = scoped_db
    appointment, _, _ = await new_booking(d)
    assert await consent_rows(d) == ([], [])
    reminder = await due_reminder(d, appointment)
    async with d['migrator'].connect() as db:
        staff = (await db.execute(text("""SELECT id FROM appointment_notifications
            WHERE tenant_id=:t AND appointment_id=:a AND event='professional_new'"""),
            {'t': d['a'], 'a': appointment['id']})).scalar_one()
    sent = []
    async def sender(_tenant, _channel, number, _body, operation):
        sent.append(number)
        return {'accepted': True, 'state': 'accepted', 'operation_id': operation}
    assert await notifications.process_notification({'appointment_sender': sender},
        str(d['a']), str(reminder['id'])) == 'communication_not_authorized'
    assert await notifications.process_notification({'appointment_sender': sender},
        str(d['a']), str(staff)) == 'accepted'
    assert sent == [PRO_PHONE]


@pytest.mark.asyncio
@pytest.mark.parametrize('role,source', [('owner', 'panel'), ('professional', 'professional_portal')])
async def test_explicit_staff_attestation_grants_only_this_booking_and_replay_cannot_restore(scoped_db, role, source):
    d = scoped_db
    appointment, context, key = await new_booking(d, opted_in=True, role=role)
    scoped, standing = await consent_rows(d)
    assert not standing and len(scoped) == 1
    grant = scoped[0]
    assert grant['appointment_id'] == appointment['id']
    assert grant['customer_id'] == appointment['customer_id']
    assert grant['contact_e164'] == phone(920)
    assert grant['operational_opt_in'] and grant['review_opt_in']
    assert grant['source'] == source and grant['actor']['account_id'] == str(d['account'])
    assert len(grant['evidence_hash']) == 64 and grant['purpose_version'] == 1
    other, _, _ = await new_booking(d, hour=11)
    assert (await due_reminder(d, other))['appointment_id'] != grant['appointment_id']
    async with d['factory']() as db:
        async with tenant_transaction(db, d['a']):
            await revoke_scoped_consents(db, d['a'], phone(920), 'all')
        # Both an identical operation receipt and a no-key confirmed replay
        # return the booking without creating a new permission.
        for replay_key in (key, None):
            replay = await confirm_hold(db, d['a'], appointment['id'], context=context,
                idempotency_key=replay_key, appointment_messages_opt_in=True)
            assert replay['id'] == appointment['id']
    scoped, standing = await consent_rows(d)
    assert not standing and len(scoped) == 1
    assert not scoped[0]['operational_opt_in'] and not scoped[0]['review_opt_in']


@pytest.mark.asyncio
async def test_anonymous_confirm_flag_cannot_attest_for_a_customer(scoped_db):
    d = scoped_db
    with pytest.raises(HTTPException) as denied:
        await new_booking(d, opted_in=True)
    assert denied.value.status_code == 403
    assert await consent_rows(d) == ([], [])


@pytest.mark.asyncio
async def test_verified_complete_whatsapp_choice_enables_one_reminder_and_survives_no_regrant(scoped_db):
    d = scoped_db
    appointment, _, _ = await new_booking(d)
    protocol = appointment['protocol']
    selected = compose_prefill(protocol, appointment_messages_opt_in=True)
    # Ordinary confirmation and a negated statement still work as appointment
    # lookups, but neither silently authorizes proactive notifications.
    await confirmation.confirmation_reply(d['a'], phone(920), compose_prefill(protocol), 'qr-test', 'ordinary')
    await confirmation.confirmation_reply(d['a'], phone(920),
        selected.replace('Autorizo receber', 'Não autorizo receber'), 'qr-test', 'negated')
    assert await confirmation.confirmation_reply(d['a'], phone(921), selected, 'qr-test', 'wrong-phone') == confirmation.GENERIC
    assert await consent_rows(d) == ([], [])
    answer = await confirmation.confirmation_reply(d['a'], phone(920), selected, 'qr-test', 'explicit')
    assert 'confirmado' in answer
    scoped, standing = await consent_rows(d)
    assert len(scoped) == 1 and not standing
    assert scoped[0]['source'] == 'customer_whatsapp'
    assert scoped[0]['actor']['customer_id'] == str(appointment['customer_id'])
    assert scoped[0]['evidence_hash'] == hashlib.sha256(selected.encode()).hexdigest()
    reminder = await due_reminder(d, appointment)
    sent = []
    async def sender(_tenant, _channel, number, _body, operation):
        sent.append(number)
        return {'accepted': True, 'state': 'accepted', 'operation_id': operation}
    for _ in range(2):
        assert await notifications.process_notification({'appointment_sender': sender},
            str(d['a']), str(reminder['id'])) == 'accepted'
    assert sent == [phone(920)]
    async with d['factory']() as db:
        async with tenant_transaction(db, d['a']):
            await record_optout(db, d['a'], phone(920), 'all')
    for message_id in ('explicit', 'explicit-forwarded-again'):
        await confirmation.confirmation_reply(d['a'], phone(920), selected, 'qr-test', message_id)
    scoped, standing = await consent_rows(d)
    assert len(scoped) == 1 and not scoped[0]['operational_opt_in'] and not scoped[0]['review_opt_in']
    assert len(standing) == 1 and not standing[0]['operational_opt_in'] and not standing[0]['marketing_opt_in']


@pytest.mark.asyncio
@pytest.mark.parametrize('scope,operational,review', [
    ('birthday', True, True), ('marketing', True, False), ('all', False, False),
])
async def test_current_optout_revokes_only_corresponding_scoped_purposes(scoped_db, scope, operational, review):
    d = scoped_db
    appointment, _, _ = await new_booking(d, opted_in=True, role='owner')
    async with d['factory']() as db:
        async with tenant_transaction(db, d['a']):
            await record_optout(db, d['a'], phone(920), scope)
    scoped, standing = await consent_rows(d)
    assert scoped[0]['operational_opt_in'] is operational
    assert scoped[0]['review_opt_in'] is review
    assert standing[0]['marketing_opt_in'] is False
    reminder = await due_reminder(d, appointment)
    assert reminder['status'] == ('cancelled' if scope == 'all' else 'pending')


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['staff', 'verified_customer'])
async def test_fresh_scoped_choice_preserves_historical_global_false_values(scoped_db, origin):
    d = scoped_db
    async with d['migrator'].begin() as db:
        await db.execute(text("""INSERT INTO contact_consents
            (tenant_id,contact_e164,operational_opt_in,marketing_opt_in,source)
            VALUES(:t,:phone,false,false,'customer_request')"""), {'t': d['a'], 'phone': phone(920)})
    appointment, _, _ = await new_booking(d, opted_in=origin == 'staff',
                                          role='owner' if origin == 'staff' else None)
    if origin == 'verified_customer':
        await confirmation.confirmation_reply(d['a'], phone(920),
            compose_prefill(appointment['protocol'], appointment_messages_opt_in=True),
            'qr-test', 'new-explicit-choice')
    scoped, standing = await consent_rows(d)
    assert len(scoped) == len(standing) == 1
    assert scoped[0]['operational_opt_in'] and scoped[0]['review_opt_in']
    assert not standing[0]['operational_opt_in'] and not standing[0]['marketing_opt_in']
    assert standing[0]['source'] == 'customer_request'
    reminder = await due_reminder(d, appointment)
    async with d['factory']() as db:
        async with tenant_transaction(db, d['a']):
            reason, _, recipient, _ = await notifications.eligibility(db, d['a'], reminder)
    assert reason is None and recipient == phone(920)


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint,operational', [
    ('owner', False), ('legacy_crm', True), ('whatsapp', True),
    ('whatsapp_exit', False), ('whatsapp_stop_messages', False),
])
async def test_existing_explicit_withdrawal_entrypoints_also_revoke_booking_permission(scoped_db, monkeypatch, entrypoint, operational):
    from app.catalog_api import Actor
    d = scoped_db
    await new_booking(d, opted_in=True, role='owner')
    if entrypoint == 'owner':
        from app import automation_api, management_api
        monkeypatch.setenv('MARKETING_EXECUTION_ENABLED', '1')
        monkeypatch.setattr(management_api, 'get_session_factory', lambda: d['factory'])
        await automation_api.consent(d['a'], automation_api.ContactConsent(
            contact_e164=phone(920), operational_opt_in=False, marketing_opt_in=False,
            evidence='Cliente sintético retirou a autorização'), Actor('support', account_id=d['account']))
    elif entrypoint == 'legacy_crm':
        from app import advanced_api
        monkeypatch.setattr(advanced_api, 'get_session_factory', lambda: d['factory'])
        await advanced_api.set_consent(d['a'], advanced_api.Consent(
            contact_e164=phone(920), marketing_opt_in=False), Actor('support'))
    else:
        from app import whatsapp_commands
        monkeypatch.setattr(whatsapp_commands, 'get_session_factory', lambda: d['factory'])
        class Redis:
            async def delete(self, _key):
                return 1
        message = {'whatsapp': 'cancelar publicidade', 'whatsapp_exit': 'sair',
                   'whatsapp_stop_messages': 'parar mensagens'}[entrypoint]
        handled, reply = await whatsapp_commands.try_command(d['a'], d['channel'], phone(920),
            message, {}, Redis(), 'synthetic-contact-state')
        assert handled
        assert ('promocionais' if operational else 'automáticas') in reply
    scoped, _ = await consent_rows(d)
    assert scoped[0]['operational_opt_in'] is operational
    assert scoped[0]['review_opt_in'] is False
