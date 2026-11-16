"""A reconnection does not revive an expired/revoked appointment purpose."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.automation_policy import delivery_reason
from test_automation_policy import NOW, context


@pytest.mark.parametrize("change,expected", [
    ("current", None),
    ("past_appointment", "appointment_started"),
    ("cancelled_appointment", "appointment_changed"),
    ("rescheduled_appointment", "appointment_changed"),
    ("missing_appointment", "appointment_changed"),
    ("expired_reminder", "expired"),
    ("human_took_over", "conversation_changed"),
    ("new_customer_message", "conversation_changed"),
    ("revoked_consent", "consent_changed"),
])
def test_reminder_final_authority_is_loaded_again_after_an_interruption(change, expected):
    job, consent, state, settings = context("reminder_1h")
    job.update(appointment_id=uuid4(), appointment_version=NOW - timedelta(days=1))
    appointment = {"status": "confirmed", "updated_at": job["appointment_version"],
                   "start_at": NOW + timedelta(minutes=30)}
    if change == "past_appointment":
        appointment["start_at"] = NOW
    elif change == "cancelled_appointment":
        appointment["status"] = "cancelled"
    elif change == "rescheduled_appointment":
        appointment["updated_at"] = NOW
    elif change == "missing_appointment":
        appointment = None
    elif change == "expired_reminder":
        job["expires_at"] = NOW
    elif change == "human_took_over":
        state["human_paused"] = True
    elif change == "new_customer_message":
        state["revision"] += 1
    elif change == "revoked_consent":
        consent["operational_opt_in"] = False
    assert delivery_reason(job, consent, state, settings, now=NOW,
                           preferences_version=2, appointment=appointment) == expected
