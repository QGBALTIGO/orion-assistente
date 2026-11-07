"""Booking message choices stay optional, explicit and appointment-scoped."""
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
import pytest
from starlette.requests import Request

from app.booking_api import ConfirmationRequest, MutationRequest
from app.professional_portal import ProfessionalBooking
from app import public_site_api as public


def public_payload():
    return {
        'quote': 'synthetic-quote-' + 'a' * 40,
        'start_at': datetime(2030, 1, 7, 12, tzinfo=timezone.utc),
        'customer_name': 'Cliente sintético',
        'customer_phone': '+5511999999999',
        'confirmed': True,
    }


@pytest.mark.parametrize('model,payload', [
    (public.PublicBooking, public_payload()),
    (ConfirmationRequest, {}),
    (ProfessionalBooking, {
        'service_ids': [uuid4()],
        'customer_name': 'Cliente sintético',
        'customer_phone': '+5511999999999',
        'start_at': datetime(2030, 1, 7, 12, tzinfo=timezone.utc),
        'idempotency_key': 'synthetic-booking-message-01',
    }),
])
def test_message_choice_is_optional_and_requires_a_real_boolean(model, payload):
    assert model(**payload).appointment_messages_opt_in is False
    for choice in (False, True):
        assert model(**payload, appointment_messages_opt_in=choice).appointment_messages_opt_in is choice
    for choice in ('true', 'false', 0, 1, None):
        with pytest.raises(ValidationError):
            model(**payload, appointment_messages_opt_in=choice)
    for field in ('marketing_opt_in', 'operational_opt_in', 'birthday_opt_in'):
        with pytest.raises(ValidationError):
            model(**payload, **{field: True})


def test_unrelated_appointment_mutations_do_not_accept_message_grants():
    with pytest.raises(ValidationError):
        MutationRequest(appointment_messages_opt_in=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', [False, True])
async def test_public_choice_only_changes_the_verified_whatsapp_followup(monkeypatch, choice):
    from app import appointment_access, appointment_confirmation
    saved = {'status': 'confirmed', 'protocol': 'SYNTHETIC-01'}
    seen = []

    async def saved_booking(*args):
        return saved

    async def followup(slug, result, phone, **kwargs):
        seen.append((slug, result, phone, kwargs))
        return {'whatsapp_confirmation_url': 'https://wa.me/5511999999999?text=synthetic'}

    monkeypatch.setattr(public, '_public_book_saved', saved_booking)
    monkeypatch.setattr(appointment_access, 'short_links_enabled', lambda: False)
    monkeypatch.setattr(appointment_confirmation, 'public_followup', followup)
    result = await public.public_book(
        'empresa-sintetica', public.PublicBooking(**public_payload(), appointment_messages_opt_in=choice),
        Request({'type': 'http', 'headers': []}), None,
    )
    assert seen == [('empresa-sintetica', saved, '+5511999999999', {
        'display_protocol': 'SYNTHETIC-01', 'appointment_messages_opt_in': choice,
    })]
    assert set(result) == {'status', 'protocol', 'whatsapp_confirmation_url'}
    assert result['status'] == 'confirmed'
