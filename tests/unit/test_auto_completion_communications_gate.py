"""Auto-completion eligibility is deliberately distinct from a real service review.

No client, calendar, or WhatsApp side effects. A completed timestamp alone
cannot prove the client attended and must never force a marketing review.
"""
from uuid import uuid4

import pytest
from app import auto_completion_worker as worker


def test_auto_completion_is_opt_in_and_has_explicit_pilot_guards(monkeypatch):
    monkeypatch.delenv("ORION_AUTO_COMPLETE_APPOINTMENTS",raising=False)
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","0")
    assert worker.enabled() is False
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","1")
    assert worker.enabled() is True
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY","1")
    assert worker.enabled() is True
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY","0")
    assert worker.enabled() is False


@pytest.mark.asyncio
async def test_automation_never_finalizes_an_appointment_in_active_communications_pilot(monkeypatch):
    pilot=uuid4()
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY","1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_TENANT_ID",str(pilot))
    monkeypatch.setattr(worker,"get_session_factory",lambda:pytest.fail("pilot completion must not touch database"))
    result=await worker.complete_due_appointment({},str(pilot),str(uuid4()))
    assert result=="requires_actual_completion"


@pytest.mark.asyncio
async def test_disabled_completion_worker_cannot_queue_jobs(monkeypatch):
    monkeypatch.setenv("ORION_AUTO_COMPLETE_APPOINTMENTS","0")
    monkeypatch.setattr(worker,"support_session_factory",lambda:pytest.fail("disabled worker must not scan DB"))
    assert await worker.scan_due_auto_completions({"redis":None})==0
