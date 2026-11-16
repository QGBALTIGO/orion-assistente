"""Observed workbench semantics, literal text and bounded frozen sequences."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import subscription_bulk_policy as policy
from app import subscription_bulk_schemas as schemas
from app.catalog_api import Actor

NOW = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE, GROUP, BULK, KEY, ROW, GENERATION = (uuid4() for _ in range(8))
ACTOR = Actor("member", account_id=OWNER)
PHONE = "5567999999901"


def item(**changes):
    return {"row_key": str(ROW), "device_id": str(DEVICE), "source": "manual", "phone": PHONE,
            "message_type": "text", "text": "Olá, {name}!"} | changes


def definition(**changes):
    return {"interval_seconds": 10, "items": [item()]} | changes


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def scalars(self): return self
    def one(self): assert len(self.rows) == 1; return self.rows[0]
    def one_or_none(self): assert len(self.rows) <= 1; return self.rows[0] if self.rows else None
    def all(self): return self.rows
    def scalar_one(self): return self.one()
    def scalar_one_or_none(self): return self.one_or_none()


@pytest.mark.parametrize("interval", [10, 11, 15, 59, 60])
def test_every_integer_interval_is_allowed_without_schedule_step_restriction(interval):
    assert schemas.BulkDefinition.model_validate(definition(interval_seconds=interval)).interval_seconds == interval


@pytest.mark.parametrize("interval", [9, 61, 10.0, True, "10", None])
def test_interval_is_bounded_strict_integer(interval):
    with pytest.raises(ValidationError):
        schemas.BulkDefinition.model_validate(definition(interval_seconds=interval))


@pytest.mark.parametrize("phone", ["0", "", "0123456789", "1234567", "1" * 16, "abc", "abc+" + PHONE,
                                  PHONE + "@s.whatsapp.net", PHONE + " ext 2", "1e10", "١٢٣٤٥٦٧٨"])
def test_manual_rejects_invalid_identity_instead_of_copying_reference_zero_bug(phone):
    with pytest.raises(ValidationError):
        schemas.BulkItem.model_validate(item(phone=phone))


@pytest.mark.parametrize("phone", [PHONE, "+55 (67) 99999-9901", " 55 67 99999-9901 "])
def test_common_punctuation_is_normalized_without_changing_country_code(phone):
    assert schemas.BulkItem.model_validate(item(phone=phone)).phone == PHONE


@pytest.mark.parametrize("changes", [
    {"source": "manual", "contact_key": "phone:" + PHONE},
    {"source": "catalog"}, {"source": "catalog", "phone": None, "contact_key": "phone:0"},
    {"device_id": None}, {"row_key": None}, {"tenant_id": str(TENANT)}, {"permission_version": 99},
    {"text": ""}, {"text": "x" * 1001}, {"text": "😀" * 501}, {"text": "a\x00b"}, {"text": "\ud800"},
    {"template_id": str(uuid4())}, {"message_type": "template", "text": None},
    {"message_type": "template", "template_id": str(uuid4())},
])
def test_row_rejects_mixed_sources_content_and_client_supplied_authority(changes):
    with pytest.raises(ValidationError):
        schemas.BulkItem.model_validate(item(**changes))


def test_500_duplicate_manual_destinations_are_explicit_distinct_rows_in_original_order():
    values = [item(row_key=str(uuid4()), text=f"Linha {i}") for i in range(500)]
    value = schemas.BulkDefinition.model_validate(definition(items=values))
    assert len(value.items) == 500 and [row.text for row in value.items] == [f"Linha {i}" for i in range(500)]
    assert policy.duplicate_counts(row.phone for row in value.items) == {
        "duplicates": 499, "duplicate_destinations": 1, "unique_destinations": 1}
    with pytest.raises(ValidationError):
        schemas.BulkDefinition.model_validate(definition(items=values + [item(row_key=str(uuid4()))]))
    with pytest.raises(ValidationError):
        schemas.BulkDefinition.model_validate(definition(items=[item(), item()]))
    with pytest.raises(ValidationError):
        schemas.BulkDefinition.model_validate(definition(items=[]))


def test_catalog_deduplication_scope_does_not_destroy_repeated_rows_from_separate_additions():
    row = item(source="catalog", phone=None, contact_key="phone:" + PHONE)
    assert len(schemas.BulkDefinition.model_validate(definition(items=[row, row | {"row_key": str(uuid4())}])).items) == 2
    with pytest.raises(ValidationError):
        schemas.AudienceRequest(contact_keys=["phone:" + PHONE, "phone:" + PHONE])
    with pytest.raises(ValidationError):
        schemas.AudienceRequest(all_contacts=True, contact_keys=["phone:" + PHONE])
    with pytest.raises(ValidationError):
        schemas.AudienceRequest(all_contacts="true")


@pytest.mark.parametrize("value", ['{"name":"literal","x":1}', "{name} {unknown} {", "*Olá* _texto_ <b>inerte</b>", "😀" * 500])
def test_plain_text_is_literal_and_preserves_braces_markup_and_utf16_boundary(value):
    parsed = schemas.BulkItem.model_validate(item(text=value))
    plan = policy.frozen_plan(parsed.model_dump(mode="json"), None, {"name": "Nome que não deve substituir"})
    assert plan == [{"type": "text", "text": value}]


@pytest.mark.parametrize("kind", schemas.KINDS)
def test_all_eight_template_kinds_keep_the_existing_personalized_plan(kind):
    template = {"kind": kind, "body": "Olá {name}" if kind in {"text", "audio", "image", "video", "document"} else "",
        "followup_body": "Fim" if kind in {"audio", "image", "video", "document"} else "",
        "asset": {"id": str(uuid4()), "kind": kind, "file_name": "arquivo"} if kind not in {"text", "location"} else None,
        "location": {"latitude": -20.4, "longitude": -54.6} if kind == "location" else None}
    before = deepcopy(template)
    plan = policy.frozen_plan(item(message_type="template", text=None), template, {"name": "Contato"})
    assert plan[0]["type"] == kind and 1 <= len(plan) <= 3 and template == before
    if kind == "audio":
        assert [part["type"] for part in plan] == ["audio", "text", "text"]
        assert plan[1]["text"] == "Olá Contato"


@pytest.mark.parametrize("change", [{"consent_confirmed": False}, {"consent_confirmed": "true"},
    {"confirmation": "SIM"}, {"text": "payload override"}, {"device_id": str(DEVICE)}, {"review_version": "z" * 64}])
def test_confirmation_cannot_replace_frozen_content_or_omit_explicit_consent(change):
    with pytest.raises(ValidationError):
        schemas.ConfirmBulk.model_validate({"idempotency_key": str(KEY), "review_version": "a" * 64,
            "confirmation": "ENVIAR", "consent_confirmed": True} | change)


@pytest.mark.parametrize("code", sorted(policy.CANCELLATION_CODES))
def test_revocation_preserves_partial_acceptance_and_uncertain_outcomes(code):
    refused = {"status": "not_sent", "error_code": code, "expires_at": NOW + timedelta(minutes=5)}
    recipient = {"status": "running", "plan": [{}]}
    assert policy.recipient_state(recipient, [refused], NOW) == ("cancelled", code)
    recipient["plan"].append({})
    assert policy.recipient_state(recipient, [{"status": "accepted"}, refused], NOW) == ("failed", code)
    assert policy.recipient_state(recipient, [{"status": "uncertain"}, refused], NOW) == ("uncertain", "MESSAGE_OUTCOME_UNCERTAIN")


def test_read_projection_does_not_write_stale_transport_states():
    parts = [{"status": "pending", "expires_at": NOW}]
    before = deepcopy(parts)
    assert policy.recipient_state({"status": "running", "plan": [{}]}, parts, NOW) == ("failed", "BULK_EFFECT_EXPIRED")
    assert parts == before
