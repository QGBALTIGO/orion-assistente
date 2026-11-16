"""The public boundary cannot ask for staff-only shared capacity."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.appointment_operations import ManualBooking
from app.public_site_api import PublicBooking


def manual(**changes):
    return dict(mode='exceptional', service_ids=[uuid4()], customer_name='Cliente sintético',
        walk_in=True, start_at=datetime(2030,4,5,12,tzinfo=timezone.utc),
        idempotency_key=str(uuid4()), **changes)


def test_shared_mode_is_opt_in_and_does_not_coerce_checkbox_values():
    assert ManualBooking(**manual()).allow_overlap is False
    assert ManualBooking(**manual(allow_overlap=True)).allow_overlap is True
    for value in ('true','false',1,0,None):
        with pytest.raises(ValidationError):
            ManualBooking(**manual(allow_overlap=value))


def test_historical_booking_cannot_claim_shared_capacity():
    values=manual(allow_overlap=True)
    values['mode']='retroactive'
    with pytest.raises(ValidationError):
        ManualBooking(**values)


def test_public_booking_never_accepts_a_shared_exception_switch():
    values=dict(quote='x'*60,start_at=datetime(2030,4,5,12,tzinfo=timezone.utc),
        customer_name='Cliente do site',customer_phone='+5567999999999',confirmed=True)
    assert PublicBooking(**values)
    for changes in ({'allow_overlap':True},{'mode':'exceptional'}):
        with pytest.raises(ValidationError):
            PublicBooking(**values,**changes)
