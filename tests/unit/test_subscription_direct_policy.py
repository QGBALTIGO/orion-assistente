"""Direct inputs and exact literal/media semantics with synthetic recipients."""
from copy import deepcopy
from uuid import uuid4

from pydantic import ValidationError
import pytest

from app import subscription_bulk_policy as bulk_policy
from app import subscription_direct_policy as policy
from app import subscription_direct_schemas as schemas

DEVICE, KEY, ASSET, TEMPLATE = (uuid4() for _ in range(4))
PHONE = "5567999999901"


def message(**changes):
    return {"kind": "text", "device_id": str(DEVICE), "recipients": {"mode": "phone", "phone": PHONE},
            "text": "Olá {name}. *Negrito* _itálico_ ```código``` ~riscado~ 😀"} | changes


def parse(value):
    return schemas.ReviewDirectMessage(idempotency_key=KEY, message=value).model_dump(mode="json")["message"]


def media(kind="audio", **changes):
    mime = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg", "document": "application/pdf"}[kind]
    suffix = {"image": "jpg", "video": "mp4", "audio": "mp3", "document": "pdf"}[kind]
    value = {"kind": "media", "device_id": str(DEVICE), "recipients": {"mode": "phone", "phone": PHONE},
             "asset_id": str(ASSET), "file_name": "arquivo." + suffix, "caption": "Linha 1\nLinha {name}"} | changes
    descriptor = {"id": str(ASSET), "kind": kind, "mime": mime, "size": 100, "sha256": "a" * 64,
                  "width": None, "height": None, "duration_ms": None, "file_name": "arquivo." + suffix,
                  "thumbnail": None}
    return parse(value), descriptor


@pytest.mark.parametrize("value", ["+55 (67) 99999-9901", PHONE, "+5567999999901"])
def test_manual_number_normalizes_and_literal_text_keeps_tags_and_markup(value):
    item = parse(message(recipients={"mode": "phone", "phone": value}))
    definition = policy.expanded_definition(item, KEY)
    assert len(definition["items"]) == 1 and definition["interval_seconds"] == 10
    row = definition["items"][0]
    assert row["phone"] == PHONE and row["source"] == "manual"
    assert bulk_policy.frozen_plan(row, None, {"name": "Substituição proibida"}) == [
        {"type": "text", "text": message()["text"]}]
    assert policy.expanded_definition(item, KEY) == definition
    assert policy.expanded_definition(item, uuid4())["items"][0]["row_key"] != row["row_key"]


@pytest.mark.parametrize("recipients", [
    {"mode": "phone", "phone": "01234567"}, {"mode": "phone", "phone": "1234567"},
    {"mode": "phone", "phone": "1" * 16}, {"mode": "phone", "phone": "1e10"},
    {"mode": "phone", "phone": True}, {"mode": "phone"},
    {"mode": "phone", "phone": PHONE, "contact_keys": ["phone:" + PHONE]},
    {"mode": "contacts", "contact_keys": []},
    {"mode": "contacts", "phone": PHONE, "contact_keys": ["phone:" + PHONE]},
    {"mode": "contacts", "contact_keys": [PHONE]},
    {"mode": "contacts", "contact_keys": ["phone:0" + PHONE]},
    {"mode": "contacts", "contact_keys": ["phone:556799999" + str(9900 + n) for n in range(11)]},
])
def test_invalid_or_ambiguous_recipients_rejected(recipients):
    with pytest.raises(ValidationError):
        parse(message(recipients=recipients))


def test_ten_unique_selected_contacts_keep_order_and_deduplicate_before_expansion():
    keys = ["phone:556799999" + str(9900 + n) for n in range(10)]
    value = parse(message(recipients={"mode": "contacts", "contact_keys": keys + [keys[0]]}))
    rows = policy.expanded_definition(value, KEY)["items"]
    assert len(rows) == len({row["row_key"] for row in rows}) == 10
    assert [row["contact_key"] for row in rows] == keys
    assert all(row["source"] == "catalog" and row["phone"] is None for row in rows)


@pytest.mark.parametrize("text", ["a" * 1001, "😀" * 501, "\x00bad", "\ud800", " \n "])
def test_text_limit_counts_utf16_and_rejects_nontext(text):
    with pytest.raises(ValidationError):
        parse(message(text=text))


def test_exact_utf16_boundary_and_raw_braces_remain_literal():
    assert parse(message(text="😀" * 500))["text"] == "😀" * 500
    value = parse(message(text='{"arbitrary":"{not_a_tag}"}'))
    assert policy.optional_template(value, [{"type": "text", "text": value["text"]}]) is None
    with pytest.raises(ValidationError):
        policy.optional_template(value | {"save_template": {"name": "JSON"}},
                                 [{"type": "text", "text": value["text"]}])


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "😀" * 51, "\x00", "\udfff"])
def test_save_template_requires_explicit_valid_name(name):
    with pytest.raises(ValidationError):
        parse(message(save_template={"name": name}))


def test_optional_text_template_keeps_library_tags_without_rendering_direct_text():
    value = parse(message(save_template={"name": "😀" * 50}))
    candidate = policy.optional_template(value, [{"type": "text", "text": value["text"]}])
    assert candidate["name"] == "😀" * 50 and candidate["text"] == value["text"]
    assert candidate["kind"] == "text" and candidate["asset_id"] is None and candidate["followup_text"] == ""


@pytest.mark.parametrize("kind", ["image", "video", "audio", "document"])
def test_avulso_media_is_one_action_with_observed_caption_semantics(kind):
    value, descriptor = media(kind, save_template={"name": "Modelo"})
    before = deepcopy(descriptor)
    plan, warnings = policy.media_plan(value, descriptor)
    assert len(plan) == 1 and descriptor == before and plan[0]["asset"] == descriptor
    assert plan[0]["caption"] == ("" if kind == "audio" else "Linha 1\nLinha {name}")
    assert warnings == (["AUDIO_CAPTION_OMITTED"] if kind == "audio" else [])
    candidate = policy.optional_template(value, plan)
    assert candidate["text"] == plan[0]["caption"] and candidate["followup_text"] == ""
    assert candidate["asset_id"] == str(ASSET)


def test_optional_empty_media_caption_does_not_invent_text_or_warning():
    value, descriptor = media(caption="")
    plan, warnings = policy.media_plan(value, descriptor)
    assert len(plan) == 1 and plan[0]["caption"] == "" and warnings == []


@pytest.mark.parametrize("change", [
    {"recipients": {"mode": "contacts", "contact_keys": ["phone:" + PHONE]}},
    {"file_name": "../secret.pdf"}, {"file_name": "bad\nname.pdf"},
    {"caption": "😀" * 501}, {"caption": "\x00"}, {"followup_text": "não permitido"},
])
def test_media_schema_rejects_multiple_recipients_or_unbounded_content(change):
    with pytest.raises(ValidationError):
        media(**change)


def test_media_must_use_verified_normalized_name_and_supported_type():
    value, descriptor = media()
    with pytest.raises(ValueError):
        policy.media_plan(value | {"file_name": "spoof.exe"}, descriptor)
    with pytest.raises(ValueError):
        policy.media_plan(value, descriptor | {"kind": "sticker"})


@pytest.mark.parametrize("extra", [{"text": "overridden"}, {"asset_id": str(ASSET)}, {"save_template": {"name": "Copy"}}])
def test_existing_template_selection_cannot_inject_another_payload(extra):
    value = {"kind": "template", "device_id": str(DEVICE), "recipients": {"mode": "phone", "phone": PHONE},
             "template_id": str(TEMPLATE)}
    assert policy.expanded_definition(parse(value), KEY)["items"][0]["template_id"] == str(TEMPLATE)
    with pytest.raises(ValidationError):
        parse(value | extra)
