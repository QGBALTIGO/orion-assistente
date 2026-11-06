"""Synthetic retry authority, immutable snapshots and safe history presentation."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_sent_messages as api
from app.subscription_sent_messages_schemas import ResendRequest
from app.catalog_api import Actor, authenticate

CONTRACT = json.loads((Path(__file__).parents[1]/"fixtures/subscription_sent_messages_contract.json").read_text())
NOW = datetime(2026, 11, 4, 18, tzinfo=timezone.utc)
TENANT, OWNER, DEVICE = uuid4(), uuid4(), uuid4()
SECRET = "SYNTHETIC_PRIVATE_TOKEN_NOT_FOR_HISTORY"


def effect(**overrides):
    row = {"id": uuid4(), "tenant_id": TENANT, "device_id": DEVICE, "generation": uuid4(),
        "settings_revision": 1, "event_id": uuid4(), "type": "chatbot_action", "purpose": "chatbot_reply",
        "payload": {"phone": "5567999999901", "delay_seconds": 1, "action": {"type": "text", "text": "Resposta sintética"}},
        "status": "not_sent", "error_code": "TRANSPORT_UNAVAILABLE_BEFORE_SEND", "provider_id": None,
        "attempts": 1, "authorized_at": NOW-timedelta(seconds=2), "expires_at": NOW+timedelta(minutes=5),
        "created_at": NOW-timedelta(seconds=3), "finished_at": NOW, "chatbot_revision": 1, "result": {},
        "device_name": "Dispositivo sintético", "device_removed_at": None, "device_runtime": {},
        "incoming_payload": {"text": "Recebida sintética", "name": "Contato", "origin": "contact"},
        "incoming_kind": "text", "run_origin": "contact", "message_type": "text", "resend_block": None}
    return row | overrides


def test_fixture_has_fixed_pagination_explicit_unknown_duration_and_opaque_version():
    value = ResendRequest.model_validate(CONTRACT["resend_request"])
    assert len(value.expected_version) == 64
    assert CONTRACT["list_response"]["per_page"] == api.PER_PAGE == 30
    assert CONTRACT["list_response"]["transcription"]["total"]["unknown_completions"] == 1
    assert CONTRACT["list_response"]["transcription"]["total"]["complete"] is False


@pytest.mark.parametrize("extra", [{"phone": "5567999999902"}, {"text": "substituir"}, {"rule_id": str(uuid4())}, {"asset": SECRET}])
def test_resend_cannot_override_recipient_content_or_source(extra):
    with pytest.raises(ValidationError):
        ResendRequest.model_validate(CONTRACT["resend_request"] | extra)


@pytest.mark.parametrize("version", [1, True, "", "a"*63, "G"*64, "a"*65])
def test_resend_version_must_be_a_bounded_opaque_digest(version):
    with pytest.raises(ValidationError):
        ResendRequest.model_validate(CONTRACT["resend_request"] | {"expected_version": version})


@pytest.mark.parametrize("state,expected", [("accepted", "sent"), ("not_sent", "failed"), ("failed", "failed"),
    ("uncertain", "uncertain"), ("running", "pending"), ("pending", "pending")])
def test_acceptance_uncertainty_and_pending_are_not_fabricated_delivery(state, expected):
    row = effect(status=state, resend_block="MESSAGE_OUTCOME_UNCERTAIN")
    view = api.item_dto(row, NOW)
    assert view["status"] == expected
    assert view["actions"] == []
    assert "delivered" not in json.dumps(view, default=str)


@pytest.mark.parametrize("code", [None, "", "CHATBOT_EFFECT_FAILED", "DEVICE_EFFECT_FAILED", "RECIPIENT_NOT_REGISTERED",
    "RECIPIENT_IDENTITY_UNVERIFIED", "MESSAGE_CONTENT_INVALID", "MESSAGE_OUTCOME_UNCERTAIN", "CHATBOT_REVOKED"])
def test_generic_null_or_unsafe_failures_are_not_evidence_for_retry(code):
    assert not api.proven_unsent(effect(error_code=code), NOW)


@pytest.mark.parametrize("code", sorted(api.SAFE_RETRY_CODES | api.TAIL_CODES))
def test_only_proved_unsent_failure_codes_can_advance_to_policy_checks(code):
    assert api.proven_unsent(effect(error_code=code), NOW)
    assert not api.proven_unsent(effect(error_code=code, provider_id="ACCEPTED_SYNTHETIC"), NOW)
    assert not api.proven_unsent(effect(error_code=code, status="uncertain"), NOW)


def test_expiry_proof_requires_no_authorization_and_no_attempt():
    row = effect(status="pending", attempts=0, authorized_at=None, expires_at=NOW-timedelta(seconds=1))
    assert api.proven_unsent(row, NOW)
    assert api.public_state(row, NOW)[:2] == ("failed", "not_sent")
    assert not api.proven_unsent(row | {"attempts": 1}, NOW)
    assert not api.proven_unsent(row | {"authorized_at": NOW-timedelta(seconds=5)}, NOW)
    assert api.public_state(row | {"status": "running"}, NOW)[0] == "uncertain"


def test_list_and_detail_are_bounded_without_leaking_tokens_http_results_or_urls():
    action = {"type": "list", "title": "Menu", "description": "Descrição visível", "button_text": "Escolher",
        "sections": [{"title": "Planos", "rows": [{"id": SECRET, "title": "Mensal", "description": "Detalhe visível"}]}]}
    row = effect(message_type="list", payload={"phone": "5567999999901", "action": action},
        result={"response_json": {"credential": SECRET}}, incoming_payload={"text": "x"*5000, "origin": "owner_command", "name": "Dono, não destinatário"})
    brief = api.item_dto(row, NOW)
    full = api.item_dto(row, NOW, detail=True)
    assert brief["received"]["truncated"] and len(brief["received"]["text"]) == 2000
    assert full["received"]["text"] == "x"*5000 and not full["received"]["truncated"]
    assert all(word in brief["sent"]["text"] for word in ("Menu", "Descrição visível", "Escolher", "Planos", "Mensal", "Detalhe visível"))
    assert brief["recipient"]["display_name"] is None
    assert SECRET not in json.dumps(full, default=str)


def test_snapshot_hash_is_immutable_content_while_version_tracks_completion():
    row = effect()
    finished = row | {"status": "accepted", "provider_id": "SYNTHETIC_ACK"}
    assert api.snapshot_hash(row) == api.snapshot_hash(finished)
    assert api.item_version(row) != api.item_version(finished)
    changed = deepcopy(row)
    changed["payload"]["action"]["text"] = "Outra resposta"
    assert api.snapshot_hash(row) != api.snapshot_hash(changed)


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows
    def one_or_none(self): return self.rows[0] if self.rows else None


class SnapshotDB:
    def __init__(self, selections=(), assets=()):
        self.selections = list(selections)
        self.assets = {str(row["id"]): row for row in assets}
        self.calls = []
    async def execute(self, statement, params):
        sql = str(statement)
        self.calls.append(sql)
        assert sql.startswith("SELECT "), "Snapshot checks cannot mutate or execute a new action"
        if "subscription_chatbot_selections" in sql:
            return Rows(self.selections)
        if "subscription_chatbot_assets" in sql:
            row = self.assets.get(str(params["id"]))
            return Rows([row] if row else [])
        raise AssertionError("Unexpected SQL")


async def test_list_retry_clones_original_action_bindings_without_recompiling():
    token = "ocb:" + "x"*32
    original_action = {"kind": "row", "definition": {"type": "text", "text": "Ação congelada", "delay_seconds": 1}}
    row = effect(payload={"phone": "5567999999901", "delay_seconds": 1,
        "action": {"type": "list", "title": "Menu", "description": "Original", "button_text": "Escolher",
                   "sections": [{"title": "Planos", "rows": [{"id": token, "title": "Mensal", "description": "Original"}]}]}})
    before = deepcopy(row)
    db = SnapshotDB(selections=[{"token_hash": hashlib.sha256(token.encode()).hexdigest(), "action": original_action}])
    payload, result, bindings = await api.frozen_payload(db, row, clone_tokens=True)
    fresh = payload["action"]["sections"][0]["rows"][0]["id"]
    assert fresh != token and fresh.startswith("ocb:") and bindings == [(hashlib.sha256(fresh.encode()).hexdigest(), original_action)]
    assert payload["action"]["description"] == "Original" and row == before and result is None


@pytest.mark.parametrize("mode", ["missing", "wrong_hash", "wrong_kind"])
async def test_missing_or_mismatched_interactive_snapshot_is_not_rebuilt(mode):
    token = "ocb:" + "x"*32
    row = effect(payload={"phone": "5567999999901", "action": {"type": "list",
        "sections": [{"rows": [{"id": token, "title": "Original"}]}]}})
    bindings = [] if mode == "missing" else [{"token_hash": hashlib.sha256((token if mode != "wrong_hash" else "other").encode()).hexdigest(),
        "action": {"kind": "carousel" if mode == "wrong_kind" else "row", "definition": {"type": "text", "text": "Original"}}}]
    with pytest.raises(HTTPException) as error:
        await api.frozen_payload(SnapshotDB(selections=bindings), row, clone_tokens=True)
    assert error.value.detail["code"] == "MESSAGE_SELECTION_SNAPSHOT_UNAVAILABLE"


def asset(identity):
    return {"id": identity, "kind": "image", "mime": "image/jpeg", "size": 100, "sha256": "a"*64,
        "width": 30, "height": 30, "duration_ms": None, "thumbnail_size": None, "thumbnail_mime": None, "thumbnail_sha256": None}


async def test_carousel_retry_uses_all_three_stored_assets_and_new_selection_tokens():
    assets = [asset(uuid4()) for _ in range(3)]
    tokens = ["ocb:"+str(index)*32 for index in range(3)]
    row = effect(payload={"phone": "5567999999901", "action": {"type": "carousel", "body": "Original",
        "cards": [{"selection_id": token, "title": "Card", "body": "Texto", "button_text": "Ver", "image_url": "https://untrusted.invalid/changed"} for token in tokens]}},
        result={"carousel_assets": {str(index): str(item["id"]) for index, item in enumerate(assets)}})
    db = SnapshotDB(assets=assets, selections=[{"token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "action": {"kind": "carousel", "card_index": index}} for index, token in enumerate(tokens)])
    before = deepcopy(row)
    payload, result, bindings = await api.frozen_payload(db, row, clone_tokens=True)
    assert row == before and result == row["result"] and len(bindings) == 3
    assert all("image_url" not in card and card["asset"]["id"] == str(assets[i]["id"])
               and card["selection_id"] != tokens[i] for i, card in enumerate(payload["action"]["cards"]))
    db.assets.pop(str(assets[1]["id"]))
    with pytest.raises(HTTPException) as error:
        await api.frozen_payload(db, row, clone_tokens=True)
    assert error.value.detail["code"] == "MESSAGE_MEDIA_SNAPSHOT_UNAVAILABLE"


async def test_http_is_never_a_retryable_message():
    with pytest.raises(HTTPException):
        await api.frozen_payload(SnapshotDB(), effect(payload={"phone": "5567999999901", "action": {"type": "http", "url": "https://invalid.test"}}), clone_tokens=True)


async def test_owner_is_checked_before_reading_resend_body_even_after_global_router_guards(monkeypatch):
    @asynccontextmanager
    async def forbidden(*args, **kwargs):
        raise HTTPException(403, "Owner required")
        yield
    monkeypatch.setattr(api, "owner_transaction", forbidden)
    app = FastAPI()
    for route in api.router.routes:
        if isinstance(route, APIRoute):
            app.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    app.dependency_overrides[authenticate] = lambda: Actor("member", TENANT, OWNER, "owner")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.test") as client:
        result = await client.post(api.BASE.format(tenant_id=TENANT)+f"/{uuid4()}/resend", content=b"{invalid", headers={"Content-Type": "application/json"})
    assert result.status_code == 403


def test_url_button_history_displays_one_blank_line_before_configured_footer():
    row=effect(message_type="url_button",payload={"phone":"5567999999901","action":{
        "type":"url_button","text":"Texto de exemplo","footer":"Rodapé configurado",
        "button_text":"Acessar","url":"https://example.com/acesso"}})
    record=api.item_dto(row,NOW,detail=True)
    assert "Texto de exemplo\n\nRodapé configurado" in record["sent"]["text"]
    assert "Acessar" in record["sent"]["text"]


def test_url_button_history_does_not_duplicate_footer_after_native_composition():
    row=effect(message_type="url_button",payload={"phone":"5567999999901","action":{
        "type":"url_button","text":"Texto de exemplo\n\nRodapé configurado","footer":"",
        "button_text":"Acessar","url":"https://example.com/acesso"}})
    record=api.item_dto(row,NOW,detail=True)
    assert record["sent"]["text"].count("Rodapé configurado") == 1
    assert "Texto de exemplo\n\nRodapé configurado" in record["sent"]["text"]
