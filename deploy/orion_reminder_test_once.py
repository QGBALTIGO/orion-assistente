"""One owner-authorized reminder on the existing Orion demonstration account."""
import asyncio
from datetime import timedelta
from time import monotonic

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointment_lifecycle import LifecycleActor, SYSTEM_ACTOR
from app.appointment_notifications import central_channel, central_enabled
from app.appointment_operations import CompanyManualBooking, create_manual_appointment
from app.booking_service import cancel_appointment
from app.catalog_api import support_session_factory
from app.database import get_engine, get_session_factory, tenant_transaction
from app.legacy_admin_identity import lookup_identity
from app.review_qa_preflight import PHONE, TENANT
from app.orion_reminder_live_probe import _log

RUN = 'orion-demo-reminder-20261124-v1'


async def _mute_other_messages(db, appointment):
    params = {'tenant': TENANT, 'id': appointment}
    await db.execute(text("""UPDATE appointment_notifications
        SET status='cancelled',last_error='demo_reminder_test',updated_at=clock_timestamp()
        WHERE tenant_id=:tenant AND appointment_id=:id
        AND status='pending' AND event<>'reminder' AND attempted_at IS NULL"""), params)
    await db.execute(text("""UPDATE calendar_outbox
        SET status='dead',last_error='demo_reminder_test',updated_at=clock_timestamp()
        WHERE tenant_id=:tenant AND appointment_id=:id AND status='pending'
        AND external_event_id IS NULL"""), params)


async def _prepare():
    admin, channel = central_channel()
    if not central_enabled() or admin != TENANT or channel is None:
        return None, 'demo_sender_unavailable'
    async with support_session_factory()() as db:
        identity = await lookup_identity(db)
    if identity is None:
        return None, 'demo_admin_unavailable'
    async with get_engine().begin() as connection:
        async with AsyncSession(bind=connection, join_transaction_mode='create_savepoint',
                                expire_on_commit=False) as db:
            async with tenant_transaction(db, TENANT):
                await db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:run))'), {'run': RUN})
                previous = (await db.execute(text("""SELECT a.id FROM appointments a
                    JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                    WHERE a.tenant_id=:tenant AND a.idempotency_key=:run AND c.whatsapp_e164=:phone"""),
                    {'tenant': TENANT, 'run': RUN, 'phone': PHONE})).scalar_one_or_none()
                if previous is not None:
                    return previous, 'existing_demo_test'
                customer = (await db.execute(text("""SELECT c.id,c.name FROM customers c
                    JOIN tenants t ON t.id=c.tenant_id
                    WHERE t.id=:tenant AND t.status='active' AND c.whatsapp_e164=:phone
                    AND c.active AND c.deleted_at IS NULL"""),
                    {'tenant': TENANT, 'phone': PHONE})).mappings().one_or_none()
                if customer is None:
                    return None, 'demo_contact_unavailable'
                catalog = (await db.execute(text("""SELECT p.id AS professional_id,s.id AS service_id
                    FROM professionals p
                    JOIN professional_services ps ON ps.tenant_id=p.tenant_id AND ps.professional_id=p.id
                    JOIN services s ON s.tenant_id=p.tenant_id AND s.id=ps.service_id
                    WHERE p.tenant_id=:tenant AND p.active AND p.accepts_appointments AND s.active
                    ORDER BY s.duration_min,p.id,s.id LIMIT 1"""),
                    {'tenant': TENANT})).mappings().one_or_none()
                if catalog is None:
                    return None, 'demo_service_unavailable'
                now = (await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
            booking = await create_manual_appointment(db, TENANT, catalog['professional_id'],
                CompanyManualBooking(
                    mode='exceptional', professional_id=catalog['professional_id'],
                    service_ids=[catalog['service_id']], customer_id=customer['id'],
                    customer_name=customer['name'], customer_phone=PHONE,
                    appointment_messages_opt_in=True, idempotency_key=RUN,
                    start_at=(now + timedelta(hours=1, minutes=2)).replace(second=0, microsecond=0),
                ), context=LifecycleActor(role='admin', source='panel', account_id=identity['id']))
            async with tenant_transaction(db, TENANT):
                await _mute_other_messages(db, booking['id'])
                reminder = (await db.execute(text("""SELECT id FROM appointment_notifications
                    WHERE tenant_id=:tenant AND appointment_id=:id AND event='reminder'
                    AND status='pending' AND recipient_kind='customer'"""),
                    {'tenant': TENANT, 'id': booking['id']})).scalar_one()
            return booking['id'], 'demo_reminder_scheduled'


async def _cancel_test(appointment):
    async with get_engine().begin() as connection:
        async with AsyncSession(bind=connection, join_transaction_mode='create_savepoint',
                                expire_on_commit=False) as db:
            await cancel_appointment(db, TENANT, appointment, context=SYSTEM_ACTOR,
                                     reason='Teste autorizado de lembrete concluído')
            async with tenant_transaction(db, TENANT):
                await _mute_other_messages(db, appointment)
    _log('demo_appointment_cancelled')


async def run():
    appointment = None
    try:
        appointment, state = await _prepare()
        _log(state)
        if appointment is None:
            return
        deadline = monotonic() + 330
        prior = None
        while monotonic() < deadline:
            async with get_session_factory()() as db:
                async with tenant_transaction(db, TENANT):
                    reminder = (await db.execute(text("""SELECT status,attempts,attempted_at,last_error
                        FROM appointment_notifications WHERE tenant_id=:tenant AND appointment_id=:id
                        AND event='reminder' AND recipient_kind='customer'"""),
                        {'tenant': TENANT, 'id': appointment})).mappings().one()
            facts = dict(reminder)
            if facts != prior:
                _log('demo_reminder_progress', **facts)
                prior = facts
            if reminder['status'] in {'delivered','read'} and reminder['attempted_at']:
                _log('demo_reminder_delivered')
                return
            if reminder['status'] in {'failed','cancelled'}:
                _log('demo_reminder_terminal_without_delivery')
                return
            await asyncio.sleep(4)
        _log('demo_reminder_receipt_window_elapsed')
    except Exception as error:
        origin = getattr(error, 'orig', None)
        _log('demo_test_error', kind=type(error).__name__,
             sqlstate=getattr(origin, 'sqlstate', None),
             http_status=getattr(error, 'status_code', None))
    finally:
        if appointment is not None:
            try:
                await _cancel_test(appointment)
            except Exception as error:
                _log('demo_cleanup_error', kind=type(error).__name__)
