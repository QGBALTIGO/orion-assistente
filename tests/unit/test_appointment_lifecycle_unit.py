"""Lifecycle identity, timestamp precision and untrusted request boundaries."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.appointment_lifecycle import (
    LifecycleActor, allowed_actions, check_expected_updated_at, metadata, panel_actor,
    public_history, resolved_actor,
)
from app.booking_api import CancellationRequest, MutationRequest, RescheduleRequest
from app.catalog_api import Actor
from app.management_schemas import AppointmentTransition


def test_legacy_hardcoded_operator_never_becomes_fabricated_attribution():
    row = {"id": uuid4(), "event": "cancelled", "operator": "support", "occurred_at": datetime.now(timezone.utc), "details": {}}
    result = public_history(row)
    assert result["requested_by"] == result["source"] == result["actor"]["role"] == "unknown"
    assert result["reason"] is None and result["actor"]["account_id"] is None


def test_panel_operator_is_separate_from_customer_who_requested_cancellation():
    account = uuid4()
    context = panel_actor(Actor("member", tenant_id=uuid4(), account_id=account, membership_role="editor"))
    details = metadata(context, requested_by="customer", reason="  Cliente pediu outra data.  ")
    assert details["requested_by"] == "customer" and details["source"] == "panel"
    assert details["actor"] == {"role": "editor", "account_id": str(account), "customer_id": None}
    assert details["reason"] == "Cliente pediu outra data."


@pytest.mark.parametrize("source", ["unknown", "public_site", "panel"])
def test_a_matching_phone_alone_is_not_an_authenticated_whatsapp_identity(source):
    context = resolved_actor(None, source, uuid4(), verified_customer=True)
    assert context.role == "unknown" and context.customer_id is None


@pytest.mark.parametrize("source", ["whatsapp_meta", "whatsapp_qr"])
def test_verified_channel_identity_is_bound_to_the_customer_and_source(source):
    customer = uuid4()
    context = resolved_actor(None, source, customer, verified_customer=True)
    details = metadata(context)
    assert details["requested_by"] == "customer" and details["source"] == source
    assert details["actor"] == {"role": "customer", "account_id": None, "customer_id": str(customer)}


def test_cas_preserves_microseconds_and_accepts_equivalent_offsets():
    stamp = datetime(2026, 10, 18, 12, 0, 0, 654321, tzinfo=timezone.utc)
    payload = MutationRequest(expected_updated_at=stamp.isoformat(), idempotency_key="cas-precision-01")
    check_expected_updated_at({"updated_at": stamp}, payload.expected_updated_at.astimezone(timezone(timedelta(hours=-3))))
    with pytest.raises(HTTPException) as exc:
        check_expected_updated_at({"updated_at": stamp}, stamp.replace(microsecond=654000))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("model,fields", [
    (MutationRequest, {}), (RescheduleRequest, {"start_at": "2026-10-19T10:00:00-03:00"}),
    (AppointmentTransition, {"idempotency_key": "complete-key-01"}),
])
def test_mutation_versions_require_explicit_timezone(model, fields):
    with pytest.raises(ValidationError):
        model(**fields, expected_updated_at="2026-10-18T10:00:00")


@pytest.mark.parametrize("fields", [
    {"source": "whatsapp_qr"}, {"actor": {"role": "admin"}}, {"actor_account_id": str(uuid4())},
    {"requested_by": "system"}, {"requested_by": "unknown"}, {"reason": "x" * 1001}, {"reason": "a\x00b"},
])
def test_public_mutation_body_cannot_inject_trusted_audit_fields(fields):
    with pytest.raises(ValidationError):
        CancellationRequest(**fields)


def test_terminal_readonly_expired_and_future_actions_are_factual():
    now = datetime.now(timezone.utc)
    future = {"status": "confirmed", "start_at": now + timedelta(seconds=1)}
    assert allowed_actions(future, can_write=True, now=now) == ["cancel", "reschedule"]
    assert allowed_actions(future, can_write=False, now=now) == []
    assert allowed_actions({**future, "start_at": now}, can_write=True, now=now) == ["cancel", "reschedule", "complete", "no_show"]
    for status in ("completed", "no_show", "cancelled"):
        assert allowed_actions({**future, "status": status}, can_write=True, now=now) == []
    assert allowed_actions({**future, "status": "hold", "hold_expires_at": now}, can_write=True, now=now) == []


def test_reason_can_include_portuguese_emoji_and_line_breaks_without_invented_actor():
    reason = "Paciente pediu outra data.\nMotivo pessoal 👩🏽‍🔧"
    parsed = CancellationRequest(requested_by="customer", reason=reason)
    result = metadata(LifecycleActor(), requested_by=parsed.requested_by, reason=parsed.reason)
    assert result["reason"] == reason and result["actor"]["role"] == "unknown"
