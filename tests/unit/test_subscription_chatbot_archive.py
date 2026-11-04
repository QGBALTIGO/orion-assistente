"""Pure planning/integrity regressions; full SQL workflows have PG coverage."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from app import subscription_chatbot_archive as archive
from app import subscription_chatbot_store as store
from app.catalog_api import Actor


def uid(value):
    return str(UUID(int=value))


def definition(value="Texto", *, device=None, actions=None):
    return {"device_id": device, "enabled": True, "match_mode": "equal", "keyword": value,
        "response": {"kind": "sequence", "actions": actions or [{"id": uid(99), "type": "text", "text": value, "delay_seconds": 1}]}}


def current(number, origin, value="Texto", *, order=1, actions=None):
    return {"id": UUID(uid(number)), "origin_id": UUID(uid(origin)), "revision": 1,
            "creation_order": order, **definition(value, actions=actions)}


def document(*records, templates=None, assets=None):
    return {"format": archive.codec.FORMAT, "version": 1, "exported_at": "2026-11-04T12:00:00+00:00",
        "rules": [{"id": uid(identity), "creation_order": index+1, "definition": value}
                  for index, (identity, value) in enumerate(records)],
        "templates": templates or [], "assets": assets or []}


class Rows:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self):
        return self
    def all(self):
        return self.rows


class QuotaDB:
    """The pure planner only needs registry quota; no auth is stubbed in PG."""
    def __init__(self, rows=()):
        self.rows = list(rows)
    async def execute(self, query, values):
        assert "subscription_chatbot_assets" in str(query)
        return Rows(self.rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("add_missing", {"create": 1, "update": 0, "skip": 1, "delete": 0}),
    ("merge", {"create": 1, "update": 1, "skip": 0, "delete": 0}),
    ("exact", {"create": 1, "update": 1, "skip": 0, "delete": 1}),
])
async def test_restore_plans_keep_origin_identity_and_apply_only_reviewed_mode(mode, expected):
    rows = {uid(11): current(11, 1, "Edição local"), uid(13): current(13, 3, "Extra", order=2)}
    graph = (rows, {}, {})
    original = deepcopy(graph)
    source = document((1, definition("Backup")), (2, definition("Ausente")))
    plan, counts, warnings = await archive._plan(QuotaDB(), UUID(uid(900)), Actor("member", account_id=uuid4()),
                                               source, mode, None, graph)
    assert {key: counts[key] for key in expected} == expected
    assert warnings == [] and plan["can_apply"]
    assert plan["rules"][0]["id"] == uid(11)
    writes, _, merged, _ = archive._materialize(source, plan, graph)
    assert len(writes) == expected["create"] + expected["update"]
    assert merged[uid(11)]["keyword"] == ("Edição local" if mode == "add_missing" else "Backup")
    assert (uid(13) in merged) is (mode != "exact")
    assert graph == original


@pytest.mark.asyncio
async def test_empty_exact_snapshot_removes_rules_and_preserves_no_fake_creation():
    graph = ({uid(11): current(11, 1)}, {}, {})
    source = document()
    plan, counts, _ = await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "exact", None, graph)
    assert plan["can_apply"] and counts == {"create": 0, "update": 0, "skip": 0, "delete": 1,
                                           "template_clones": 0, "media_copies": 0}
    assert archive._materialize(source, plan, graph)[2] == {}


@pytest.mark.asyncio
async def test_missing_original_device_blocks_preview_until_explicit_valid_override(monkeypatch):
    missing, selected = uid(700), uid(701)
    async def check(db, tenant, device_id, *, actor_id):
        if str(device_id) != selected:
            raise HTTPException(404, "Unavailable")
    monkeypatch.setattr(store, "check_device", check)
    source = document((1, definition(device=missing)))
    actor = Actor("member", account_id=uuid4())
    blocked, _, warning = await archive._plan(QuotaDB(), uuid4(), actor, source, "merge", None, ({}, {}, {}))
    assert not blocked["can_apply"] and warning
    allowed, _, warning = await archive._plan(QuotaDB(), uuid4(), actor, source, "merge", UUID(selected), ({}, {}, {}))
    assert allowed["can_apply"] and not warning
    writes = archive._materialize(source, allowed, ({}, {}, {}))[0]
    assert writes[0][1]["device_id"] == selected


@pytest.mark.asyncio
async def test_template_content_change_clones_and_preserves_unrelated_local_rules():
    local_template = {"id": UUID(uid(300)), "origin_id": UUID(uid(301)), "revision": 2,
        "name": "Atual", "kind": "text", "body": "Conteúdo local", "asset_id": None, "asset": None}
    local = current(10, 10, actions=[{"id": uid(98), "type": "template", "template_id": uid(300), "delay_seconds": 1}])
    graph = ({uid(10): local}, {uid(300): local_template}, {})
    source = document((1, definition(actions=[{"id": uid(97), "type": "template", "template_id": uid(301), "delay_seconds": 1}])),
        templates=[{"id": uid(301), "definition": {"name": "Importado", "kind": "text", "text": "Conteúdo diferente", "asset_id": None}}])
    plan, counts, _ = await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "merge", None, graph)
    assert counts["template_clones"] == 1
    clone = plan["templates"][0]
    assert clone["id"] != uid(300) and clone["origin_id"] != uid(301)
    writes, template_writes, merged, _ = archive._materialize(source, plan, graph)
    assert merged[uid(10)]["response"]["actions"][0]["template_id"] == uid(300)
    assert writes[0][1]["response"]["actions"][0]["template_id"] == clone["id"]
    assert template_writes[0]["body"] == "Conteúdo diferente"


@pytest.mark.asyncio
async def test_identical_templates_reuse_content_without_overwriting_name_or_body():
    local_template = {"id": UUID(uid(300)), "origin_id": UUID(uid(301)), "revision": 2,
        "name": "Nome local", "kind": "text", "body": "Mesmo conteúdo", "asset_id": None, "asset": None}
    graph = ({}, {uid(300): local_template}, {})
    source = document((1, definition(actions=[{"id": uid(97), "type": "template", "template_id": uid(302), "delay_seconds": 1}])),
        templates=[{"id": uid(302), "definition": {"name": "Nome local", "kind": "text", "text": "Mesmo conteúdo", "asset_id": None}}])
    plan, counts, _ = await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "merge", None, graph)
    assert counts["template_clones"] == 0 and plan["templates"][0]["id"] == uid(300)
    writes, created, _, merged_templates = archive._materialize(source, plan, graph)
    assert created == [] and merged_templates[uid(300)]["name"] == "Nome local"
    assert writes[0][1]["response"]["actions"][0]["template_id"] == uid(300)


@pytest.mark.asyncio
async def test_distinct_template_names_are_preserved_even_with_equal_content():
    local = {"id": UUID(uid(300)), "origin_id": UUID(uid(301)), "revision": 1,
             "name": "Nome local", "kind": "text", "body": "Mesmo", "asset_id": None, "asset": None}
    source = document((1, definition(actions=[{"id": uid(97), "type": "template", "template_id": uid(301), "delay_seconds": 1}])),
        templates=[{"id": uid(301), "definition": {"name": "Nome importado", "kind": "text", "text": "Mesmo", "asset_id": None}}])
    graph = ({}, {uid(300): local}, {})
    plan, counts, _ = await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "merge", None, graph)
    assert counts["template_clones"] == 1 and plan["templates"][0]["id"] != uid(300)
    created = archive._materialize(source, plan, graph)[1]
    assert created[0]["name"] == "Nome importado" and local["name"] == "Nome local"


@pytest.mark.asyncio
async def test_repeated_equivalent_source_templates_create_only_one_clone():
    source = document((1, definition(actions=[
        {"id": uid(91), "type": "template", "template_id": uid(301), "delay_seconds": 1},
        {"id": uid(92), "type": "template", "template_id": uid(302), "delay_seconds": 1}])),
        templates=[{"id": uid(key), "definition": {"name": "Mesmo nome", "kind": "text", "text": "Mesmo", "asset_id": None}}
                   for key in (301, 302)])
    plan, counts, _ = await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "merge", None, ({}, {}, {}))
    assert counts["template_clones"] == 1
    assert len({item["id"] for item in plan["templates"]}) == 1
    assert len(archive._materialize(source, plan, ({}, {}, {}))[1]) == 1


def test_export_selection_closes_references_and_rewrites_local_ids_to_origins():
    leaf = current(11, 1, "Folha")
    root = current(12, 2, "Raiz", order=2, actions=[{"id": uid(95), "type": "nested_reply", "rule_id": uid(11), "delay_seconds": 1}])
    extra = current(13, 3, "Extra", order=3)
    graph = ({uid(11): leaf, uid(12): root, uid(13): extra}, {}, {})
    value = archive._snapshot(graph, [UUID(uid(12))])
    assert [row["id"] for row in value["rules"]] == [uid(1), uid(2)]
    assert value["rules"][1]["definition"]["response"]["actions"][0]["rule_id"] == uid(1)
    assert graph[0][uid(12)]["response"]["actions"][0]["rule_id"] == uid(11)
    with pytest.raises(HTTPException) as error:
        archive._snapshot(graph, [uuid4()])
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_add_missing_cannot_silently_break_a_current_reference_graph():
    local = current(11, 1, "Lista antiga")
    # The incoming root requires a list, but add_missing preserves the current
    # sequence under the same origin. Import must fail instead of changing it.
    list_definition = {"device_id": None, "enabled": True, "match_mode": "equal", "keyword": "Lista",
        "response": {"kind": "list", "title": "", "description": "Escolha", "footer": "", "button_text": "Abrir",
            "sections": [{"id": uid(401), "title": "", "rows": [{"id": uid(402), "title": "Um", "description": "",
                "action": {"id": uid(403), "type": "text", "text": "Um", "delay_seconds": 1}}]}]}}
    source = document((1, list_definition), (2, definition(actions=[
        {"id": uid(96), "type": "nested_list", "rule_id": uid(1), "delay_seconds": 1}])))
    with pytest.raises(HTTPException) as error:
        await archive._plan(QuotaDB(), uuid4(), Actor("member", account_id=uuid4()), source, "add_missing", None,
                            ({uid(11): local}, {}, {}))
    assert error.value.detail["code"] == "CHATBOT_INVALID_GRAPH"


@pytest.mark.asyncio
async def test_inactive_dependency_flags_roundtrip_and_do_not_block_unrelated_import():
    leaf = current(11, 1, "Dependência")
    leaf["enabled"] = False
    parent = current(12, 2, "Pai", order=2, actions=[
        {"id": uid(95), "type": "nested_reply", "rule_id": uid(11), "delay_seconds": 1}])
    graph = ({uid(11): leaf, uid(12): parent}, {}, {})
    actor = Actor("member", account_id=uuid4())
    snapshot = archive._snapshot(graph)
    encoded = archive.codec.write_archive(snapshot, "json")
    source = archive.codec.read_archive(encoded, "snapshot.json")
    plan, _, _ = await archive._plan(QuotaDB(), uuid4(), actor, source, "exact", None, graph)
    restored = archive._materialize(source, plan, graph)[2]
    assert restored[uid(11)]["enabled"] is False and restored[uid(12)]["enabled"] is True
    unrelated = document((3, definition("Sem relação")))
    addition, _, _ = await archive._plan(QuotaDB(), uuid4(), actor, unrelated, "add_missing", None, graph)
    merged = archive._materialize(unrelated, addition, graph)[2]
    assert len(merged) == 3 and not merged[uid(11)]["enabled"] and merged[uid(12)]["enabled"]


@pytest.mark.asyncio
async def test_expired_preview_with_cleaned_private_payload_has_safe_conflict():
    preview = {"snapshot": {}, "expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)}
    with pytest.raises(HTTPException) as error:
        await archive._reviewed_document(None, uuid4(), preview)
    assert error.value.detail["code"] == "CHATBOT_PREVIEW_EXPIRED"


def test_snapshot_budget_is_checked_during_serialization():
    with pytest.raises(HTTPException) as error:
        archive._bounded_size({"rules": ["x" * 30] * 1000}, 100)
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_media_quota_counts_failed_objects_but_same_hash_retry_adds_no_reservation(monkeypatch):
    asset = {"id": uid(500), "kind": "document", "sha256": "a"*64, "size": 10, "thumbnail": None}
    registry = {"kind": "document", "sha256": "a"*64, "size": 10, "thumbnail_size": None, "status": "failed"}
    monkeypatch.setattr(store, "MAX_ASSETS", 1)
    monkeypatch.setattr(store, "MAX_TENANT_MEDIA_BYTES", 10)
    assert await archive._media_quota(QuotaDB([registry]), uuid4(), {"assets": [asset]}, {"asset_ids": [uid(500)]}) == 1
    with pytest.raises(HTTPException) as error:
        await archive._media_quota(QuotaDB([registry]), uuid4(), {"assets": [{**asset, "sha256": "b"*64}]}, {"asset_ids": [uid(500)]})
    assert error.value.detail["code"] == "CHATBOT_MEDIA_QUOTA"
