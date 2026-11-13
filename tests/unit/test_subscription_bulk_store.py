"""Frozen per-row context, permission/source boundaries and bounded SQL writes."""
from copy import deepcopy
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_bulk_store as store
from app import subscription_bulk_schemas as schemas
from test_subscription_bulk_policy import ACTOR, BULK, DEVICE, GENERATION, KEY, NOW, OWNER, PHONE, TENANT, Rows, definition, item


class CaptureDB:
    def __init__(self, permissions=()): self.permissions, self.calls = list(permissions), []
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        if "FROM subscription_campaign_permissions" in sql: return Rows(self.permissions)
        if "contact_phone" in sql: return Rows([{"name": "Empresa", "email": "owner@example.invalid", "contact_phone": None}])
        raise AssertionError(sql)


def setup_capture(monkeypatch, *, verified=False, own_phone=PHONE):
    calls = []
    channel = uuid4()
    async def zone(*args): return "America/Cuiaba"
    async def device(db, tenant, identity, **kwargs):
        calls.append(identity)
        assert tenant == TENANT and kwargs["actor_id"] == OWNER
        return {"id": identity, "name": "Aparelho", "generation": GENERATION, "revision": 3,
            "status": "connected", "owner_account_id": OWNER, "channel_id": channel,
            "runtime": {"phone": own_phone, "phone_verified": verified}}, {"enabled": True}
    monkeypatch.setattr(store, "timezone_for", zone)
    monkeypatch.setattr(store.shared, "check_device", device)
    return calls


def clean_definition(**changes):
    return schemas.BulkDefinition.model_validate(definition(**changes)).model_dump(mode="json")


@pytest.mark.asyncio
async def test_manual_duplicates_keep_order_content_device_and_no_implicit_catalog_mutation(monkeypatch):
    other = uuid4()
    calls = setup_capture(monkeypatch)
    db = CaptureDB()
    value = clean_definition(items=[item(text='{"name":"literal"}'),
        item(row_key=str(uuid4()), device_id=str(other), text="Outro conteúdo"),
        item(row_key=str(uuid4()), text="Terceira linha")])
    snapshot = await store.capture_review(db, TENANT, ACTOR, value)
    assert [row["ordinal"] for row in snapshot["rows"]] == [1, 2, 3]
    assert [row["context"]["device"]["id"] for row in snapshot["rows"]] == [str(DEVICE), str(other), str(DEVICE)]
    assert [row["plan"][0]["text"] for row in snapshot["rows"]] == [row["text"] for row in value["items"]]
    assert snapshot["counts"] == {"total": 3, "eligible": 3, "blocked": 0,
        "duplicates": 2, "duplicate_destinations": 1, "unique_destinations": 1}
    assert all(row["name"] == "Contato" and row["origins"] == [] and row["source"] == "manual" for row in snapshot["rows"])
    assert calls == [DEVICE, other]  # Resolve each distinct device once, not per row.
    assert all(sql.startswith("SELECT") for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("verified,revoked,expected", [(False, False, None), (True, False, "BULK_SELF_CONTACT"),
    (False, True, "BULK_OPTED_OUT"), (True, True, "BULK_SELF_CONTACT")])
async def test_manual_confirmation_never_bypasses_optout_or_verified_own_number(monkeypatch, verified, revoked, expected):
    setup_capture(monkeypatch, verified=verified)
    db = CaptureDB([{"phone": PHONE, "version": 7, "revoked": revoked}])
    snapshot = await store.capture_review(db, TENANT, ACTOR, clean_definition())
    row = snapshot["rows"][0]
    assert row["permission_version"] == 7 and row["blocked_reason"] == expected
    assert snapshot["counts"]["eligible"] == (0 if expected else 1)
    assert row["context"]["device"]["phone_e164"] == ("+" + PHONE if verified else None)


@pytest.mark.asyncio
async def test_catalog_rows_keep_frozen_versions_while_duplicate_manual_rows_remain_independent(monkeypatch):
    setup_capture(monkeypatch)
    origins = [{"kind": "customer", "id": str(uuid4()), "phone": PHONE, "version": 8}]
    async def contacts(db, tenant, selection):
        assert selection["contact_keys"] == ["phone:" + PHONE]
        return [{"phone": PHONE, "name": "Ana", "origins": origins, "permission_version": 3, "blocked": False}], 0
    monkeypatch.setattr(store, "selected_contacts", contacts)
    rows = [item(source="catalog", phone=None, contact_key="phone:" + PHONE),
            item(row_key=str(uuid4()), text="Também manual")]
    snapshot = await store.capture_review(CaptureDB([{"phone": PHONE, "version": 3, "revoked": False}]),
                                          TENANT, ACTOR, clean_definition(items=rows))
    assert snapshot["rows"][0]["origins"] == origins and snapshot["rows"][0]["name"] == "Ana"
    assert snapshot["rows"][1]["origins"] == [] and snapshot["rows"][1]["name"] == "Contato"
    assert snapshot["counts"]["total"] == 2 and snapshot["counts"]["duplicates"] == 1


@pytest.mark.asyncio
async def test_catalog_over_limit_is_rejected_before_materializing_rows():
    class DB:
        calls = 0
        async def execute(self, query, params):
            self.calls += 1
            return Rows([{"total": 501, "direct": 501, "duplicates": 0}])
    db = DB()
    with pytest.raises(HTTPException) as error:
        await store.selected_contacts(db, TENANT, {"group_ids": [], "contact_keys": [], "all_contacts": True})
    assert error.value.detail["code"] == "BULK_LIMIT" and db.calls == 1


@pytest.mark.asyncio
async def test_removed_selected_catalog_contact_does_not_silently_reduce_audience():
    class DB:
        async def execute(self, *args): return Rows([{"total": 0, "direct": 0, "duplicates": 0}])
    with pytest.raises(HTTPException) as error:
        await store.selected_contacts(DB(), TENANT, {"group_ids": [], "contact_keys": ["phone:" + PHONE], "all_contacts": False})
    assert error.value.detail["code"] == "BULK_CONTACT_CHANGED"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,origins,consent,phone,expected", [
    ("manual", [], True, PHONE, True), ("manual", [], False, PHONE, False),
    ("manual", [], "true", PHONE, False), ("manual", [{"kind": "customer"}], True, PHONE, False),
    ("manual", [], True, "0", False), ("unknown", [], True, PHONE, False),
    ("catalog", [], True, PHONE, False), ("catalog", [{}] * 102, False, PHONE, False),
])
async def test_manual_source_requires_explicit_consent_and_never_uses_added_catalog_sources(source, origins, consent, phone, expected):
    class DB:
        async def execute(self, *args): raise AssertionError("Invalid or manual source must not query customer catalog")
    assert await store.sources_valid(DB(), TENANT, origins, phone, source=source, manual_consent=consent) is expected


def test_json_budget_and_metadata_do_not_duplicate_personalized_plans():
    value = {"numbers": [1, 2, 3]}
    with pytest.raises(HTTPException) as error:
        store.bounded_json(value, len(store.encode(value).encode()))
    assert error.value.detail["code"] == "BULK_LIMIT"
    snapshot = {"definition": definition(), "timezone": "UTC", "facts": {"my_name": "Empresa"},
                "counts": {"total": 1}, "rows": [{"phone": PHONE, "plan": [{"text": "x" * 1000}]}]}
    before = deepcopy(snapshot)
    metadata = store.metadata_snapshot(snapshot, ACTOR, KEY, NOW)
    assert "rows" not in metadata and "definition" not in metadata and metadata["consent"]["confirmed"] is True
    metadata["facts"]["my_name"] = "Changed"
    metadata["counts"]["total"] = 0
    assert snapshot == before


@pytest.mark.asyncio
async def test_confirmation_inserts_distinct_rows_without_rewriting_grants_or_creating_effects(monkeypatch):
    setup_capture(monkeypatch)
    snapshot = await store.capture_review(CaptureDB(), TENANT, ACTOR,
        clean_definition(items=[item(), item(row_key=str(uuid4()))]))
    writes = []
    class DB:
        async def execute(self, query, params):
            writes.append((str(query), params))
            return Rows([])
    await store.insert_recipients(DB(), TENANT, BULK, "a" * 64, snapshot, NOW)
    assert len(writes) == 1 and writes[0][0].startswith("INSERT INTO subscription_bulk_recipients")
    rows = json.loads(writes[0][1]["rows"])
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"] and rows[0]["row_key"] != rows[1]["row_key"]
    assert [row["phone"] for row in rows] == [PHONE, PHONE]
    assert all(row["manual_consent"] is True and row["status"] == "pending" for row in rows)


def test_preview_shows_exact_frozen_audio_text_and_followup_without_inventing_delivery():
    template = {"id": str(uuid4()), "name": "Áudio", "kind": "audio"}
    plan = [{"type": "audio", "caption": "", "file_name": "sintetico.ogg"},
            {"type": "text", "text": "Olá Contato"}, {"type": "text", "text": "Complemento"}]
    value = store.message_preview(plan, template)
    assert value["parts"] == [{"type": "audio", "text": "", "file_name": "sintetico.ogg"},
        {"type": "text", "text": "Olá Contato"}, {"type": "text", "text": "Complemento"}]
    assert value["template_name"] == "Áudio" and "sent_at" not in value
