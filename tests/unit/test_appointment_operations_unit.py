"""Private appointment contracts and timing invariants, without external I/O."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.appointment_lifecycle import LifecycleActor
from app.appointment_operations import (
    CompanyManualBooking, ExtraServicesRequest, ManualBooking,
    extra_interval, require_staff_context, validate_manual_period,
)

UTC = timezone.utc


def request(**changes):
    return {"mode": "exceptional", "service_ids": [uuid4()],
            "customer_name": "Cliente de teste", "customer_phone": "+5511999999999",
            "start_at": datetime(2030, 1, 7, 23, tzinfo=UTC),
            "idempotency_key": "synthetic-operation-001", **changes}


def service(**changes):
    return {"duration_min": 40, "prep_min": 5, "cleanup_min": 5,
            "price_cents": 3500, "price_mode": "fixed", **changes}


@pytest.mark.parametrize("field", ["professional_id", "tenant_id", "customer_id", "status", "created_by_account_id"])
def test_professional_manual_contract_never_accepts_scope_or_result_from_client(field):
    with pytest.raises(ValidationError):
        ManualBooking(**request(**{field: str(uuid4())}))


def test_company_may_reference_a_customer_but_never_a_tenant_or_actor():
    data = request(professional_id=uuid4(), customer_id=uuid4())
    assert CompanyManualBooking(**data).customer_id == data["customer_id"]
    for field in ("tenant_id", "created_by_account_id", "commission_cents"):
        with pytest.raises(ValidationError):
            CompanyManualBooking(**data, **{field: str(uuid4())})


@pytest.mark.parametrize("changes", [
    {"customer_phone": None},
    {"walk_in": True},
    {"walk_in": True, "customer_phone": None, "appointment_messages_opt_in": True},
    {"mode": "retroactive", "appointment_messages_opt_in": True},
    {"mode": "exceptional", "final_total_cents": 4000},
    {"mode": "exceptional", "actual_duration_min": 60},
    {"mode": "retroactive", "final_total_cents": True},
    {"mode": "retroactive", "actual_duration_min": "60"},
    {"customer_phone": "+123"},
    {"customer_name": "A\x00B"},
    {"walk_in": "false"},
])
def test_manual_contract_rejects_missing_contacts_financial_coercion_and_invalid_consent(changes):
    with pytest.raises(ValidationError):
        ManualBooking(**request(**changes))


def test_walk_in_has_no_fake_phone_and_explicit_mode_retains_final_value():
    payload = ManualBooking(**request(mode="retroactive", walk_in=True, customer_phone=None,
                                      final_total_cents=0, actual_duration_min=60))
    assert payload.customer_phone is None
    assert payload.final_total_cents == 0 and payload.appointment_messages_opt_in is False
    assert ManualBooking(**request(customer_phone="(11) 99999-9999")).customer_phone == "+5511999999999"


@pytest.mark.parametrize("field,value", [
    ("expected_updated_at", datetime(2030, 1, 7, 12)),
    ("preserve_reserved_time", 1),
    ("actual_duration_min", True),
    ("service_ids", []),
])
def test_extra_contract_needs_aware_version_and_explicit_choices(field, value):
    data = {"service_ids": [uuid4()], "expected_updated_at": datetime(2030, 1, 7, 12, tzinfo=UTC),
            "idempotency_key": "synthetic-extra-001", field: value}
    with pytest.raises(ValidationError):
        ExtraServicesRequest(**data)


def test_duplicate_service_choices_are_rejected_before_any_write():
    item = uuid4()
    for model, data in ((ManualBooking, request()), (ExtraServicesRequest,
            {"expected_updated_at": datetime(2030, 1, 7, 12, tzinfo=UTC),
             "idempotency_key": "synthetic-extra-002"})):
        with pytest.raises(ValidationError):
            model(**{**data, "service_ids": [item, item]})


def test_private_exception_is_not_bound_to_public_grid_notice_or_opening_hours():
    now = datetime(2030, 1, 7, 22, tzinfo=UTC)
    shop = {"timezone": "America/Campo_Grande", "min_notice_minutes": 120,
            "slot_step_minutes": 30, "booking_horizon_days": 1}
    start = now + timedelta(minutes=7)
    payload = ManualBooking(**request(start_at=start))
    interval = validate_manual_period(payload, shop, [service()], now)
    assert interval["start_at"] == start and interval["end_at"] == start + timedelta(minutes=40)
    assert interval["occupied_from"] == start - timedelta(minutes=5)


def test_retroactive_must_be_fully_finished_and_uses_actual_duration_when_supplied():
    now = datetime(2030, 1, 7, 22, tzinfo=UTC)
    shop = {"timezone": "America/Campo_Grande"}
    start = now - timedelta(minutes=30)
    with pytest.raises(HTTPException) as error:
        validate_manual_period(ManualBooking(**request(mode="retroactive", start_at=start)), shop, [service()], now)
    assert error.value.status_code == 422
    result = validate_manual_period(ManualBooking(**request(mode="retroactive", start_at=start,
                                    actual_duration_min=20)), shop, [service()], now)
    assert result["end_at"] == start + timedelta(minutes=20)
    assert result["occupied_until"] == start + timedelta(minutes=25)


@pytest.mark.parametrize("mode,delta", [("exceptional", -1), ("retroactive", 1), ("exceptional", 367), ("retroactive", -367)])
def test_manual_modes_cannot_lie_about_time_or_escape_bounded_history(mode, delta):
    now = datetime(2030, 1, 7, 22, tzinfo=UTC)
    with pytest.raises(HTTPException):
        validate_manual_period(ManualBooking(**request(mode=mode, start_at=now + timedelta(days=delta))),
                               {"timezone": "America/Campo_Grande"}, [service()], now)


def test_extra_duration_uses_saved_items_and_zero_time_addons_without_repricing():
    start = datetime(2030, 1, 7, 12, tzinfo=UTC)
    row = {"start_at": start, "end_at": start + timedelta(minutes=40),
           "occupied_from": start - timedelta(minutes=5), "occupied_until": start + timedelta(minutes=45)}
    saved = [{"duration_snapshot_min": 40, "price_snapshot_cents": 3500, "added_duration_min": None},
             {"duration_snapshot_min": 20, "price_snapshot_cents": 2000, "added_duration_min": 0}]
    extra = {"added_duration_min": 20, "prep_min": 0, "cleanup_min": 0}
    result = extra_interval(row, saved, [extra])
    assert result["services_duration_min"] == 60 and result["end_at"] == start + timedelta(minutes=60)
    assert result["occupied_from"] == row["occupied_from"] and result["occupied_until"] == start + timedelta(minutes=65)
    unchanged = extra_interval(row, saved, [{**extra, "added_duration_min": 0}])
    assert all(unchanged[key] == row[key] for key in row)


def test_later_extra_does_not_undo_a_previous_choice_to_preserve_reserved_time():
    start = datetime(2030, 1, 7, 12, tzinfo=UTC)
    row = {"start_at": start, "end_at": start + timedelta(minutes=40),
           "occupied_from": start, "occupied_until": start + timedelta(minutes=45)}
    saved = [{"duration_snapshot_min": 40, "added_duration_min": None},
             {"duration_snapshot_min": 20, "added_duration_min": 20}]
    no_time = {"added_duration_min": 0, "prep_min": 0, "cleanup_min": 0}
    preserved = extra_interval(row, saved, [no_time])
    assert preserved["services_duration_min"] == 60
    assert all(preserved[key] == row[key] for key in row)
    extended = extra_interval(row, saved, [{**no_time, "added_duration_min": 10}])
    assert extended["services_duration_min"] == 70
    assert extended["end_at"] == start + timedelta(minutes=50)


@pytest.mark.parametrize("context", [None, LifecycleActor(), LifecycleActor(role="customer", source="public_site"),
    LifecycleActor(role="system", source="system"), LifecycleActor(role="professional", source="panel"),
    LifecycleActor(role="owner", source="public_site", account_id=uuid4())])
def test_domain_exception_requires_trusted_staff_adapter(context):
    with pytest.raises(HTTPException) as error:
        require_staff_context(context)
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", ["owner", "editor", "professional", "admin", "support"])
def test_trusted_staff_context_keeps_actual_actor_identity(role):
    require_staff_context(LifecycleActor(role=role, source="panel", account_id=uuid4()))


@pytest.mark.asyncio
async def test_retroactive_audit_does_not_enter_any_outbox(monkeypatch):
    from app import appointment_notifications, calendar_sync
    from app.booking_service import history
    calls = []

    class Database:
        async def execute(self, sql, params):
            calls.append((str(sql), params))

    async def forbidden(*args, **kwargs):
        pytest.fail("A historical record must not enqueue a message or calendar event")

    monkeypatch.setattr(appointment_notifications, "enqueue_event", forbidden)
    monkeypatch.setattr(calendar_sync, "enqueue_calendar_sync", forbidden)
    await history(Database(), uuid4(), uuid4(), "retroactive_created",
                  context=LifecycleActor(role="owner", source="panel", account_id=uuid4()),
                  notifications=False, calendar=False)
    assert len(calls) == 1 and "INSERT INTO appointment_history" in calls[0][0]
    assert '"role": "owner"' in calls[0][1]["details"]


@pytest.mark.asyncio
async def test_reschedule_preserves_original_quote_and_no_time_extra_when_catalogue_changes():
    from app.booking_service import rescheduling_items, amounts
    ids = [uuid4(), uuid4()]
    saved = [{"service_id": ids[0], "name_snapshot": "Corte original", "duration_snapshot_min": 40,
              "price_snapshot_cents": 3500, "price_mode_snapshot": "fixed", "prep_snapshot_min": 5,
              "cleanup_snapshot_min": 5, "item_origin": "booked", "added_duration_min": None},
             {"service_id": ids[1], "name_snapshot": "Adicional sem tempo", "duration_snapshot_min": 20,
              "price_snapshot_cents": 2000, "price_mode_snapshot": "fixed", "prep_snapshot_min": 0,
              "cleanup_snapshot_min": 0, "item_origin": "extra", "added_duration_min": 0}]

    class Result:
        def mappings(self):
            return self

        def all(self):
            return saved

    class Database:
        async def execute(self, *args):
            return Result()

    catalogue = [{"id": ids[0], **service(name="Preço novo", duration_min=90, price_cents=9900)},
                 {"id": ids[1], **service(name="Adicional novo", duration_min=30, price_cents=4000)}]
    effective, preserved = await rescheduling_items(Database(), uuid4(), uuid4(), catalogue)
    assert preserved is True
    assert amounts(effective) == (40, 5, 5, 5500)
    assert effective[0]["name"] == "Corte original"
    saved[1]["item_origin"] = "booked"
    effective, preserved = await rescheduling_items(Database(), uuid4(), uuid4(), catalogue)
    assert effective is catalogue and preserved is False


def test_professional_message_uses_reservation_duration_after_explicit_preservation(monkeypatch):
    from app.appointment_messages import compose
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    row = {"id": uuid4(), "customer_name": "Cliente", "professional_name": "Profissional",
           "status": "confirmed", "start_at": datetime(2030, 1, 7, 12, tzinfo=UTC),
           "timezone": "America/Campo_Grande", "protocol": "AG-ABCDEF",
           "reserved_duration_min": 40, "services_duration_min": 60,
           "items": [{"name_snapshot": "Corte", "duration_snapshot_min": 40},
                     {"name_snapshot": "Barba", "duration_snapshot_min": 20}]}
    message = compose("professional_new", row, company="Empresa sintética", signature="Orion Agenda")
    assert "*40 minutos*" in message and "*60 minutos*" not in message
