"""Direct origin uses the existing transport with its own final authority."""
from copy import deepcopy
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
import pytest

from app import subscription_bulk_runtime as runtime
from app import subscription_bulk_store as store
from app import subscription_sent_messages as history
from app.subscription_device_schemas import EffectRequest
from test_subscription_bulk_runtime import (
    authority, DB, batch, device, effect, recipient, NOW, TENANT, PHONE, EFFECT, CLAIM, Rows,
)  # noqa: F401 - reuse the synthetic authority boundary, not production transport.
from test_subscription_direct_store import ASSET, asset_row
from test_subscription_sent_messages import effect as history_effect


async def test_direct_claim_checks_persisted_origin_permission_and_preserves_protocol_one(authority):
    db = DB(batch_row=batch(origin="direct"))
    permit = await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8,
                                                EffectRequest(operation_id=EFFECT))
    assert permit["authorized"] is True and permit["type"] == permit["purpose"] == "bulk_action"
    assert permit["payload"] == db.effects[0]["payload"]
    assert authority == [(TENANT, {"feature": "direct_messages.manage"})]
    await runtime.create_effects(db, device(), db.batch, db.recipient, NOW)
    event = json.loads(db.events[0]["payload"])
    assert event["origin"] == "direct" and event["kind"] == "bulk"
    assert json.loads(db.created[0]["payload"]) == permit["payload"]


@pytest.mark.parametrize("recheck", [False, True])
async def test_revoking_only_direct_permission_denies_claim_or_recheck_without_blocking_bulk(authority, monkeypatch, recheck):
    async def features(db, tenant, **kwargs):
        if kwargs["feature"] == "direct_messages.manage":
            raise HTTPException(403, "direct revoked")
    monkeypatch.setattr(store.shared, "workspace", features)
    direct = DB(batch_row=batch(origin="direct"))
    if recheck:
        direct.effects[0].update(status="running", attempts=1, claimed_owner=CLAIM, claimed_fence=8, authorized_at=NOW)
    request = EffectRequest(operation_id=EFFECT, recheck=recheck)
    result = await runtime.authorize_bulk_effect(direct, device(), direct.effects[0], CLAIM, 8, request)
    assert result["authorized"] is False and result["error_code"] == "BULK_REVOKED"
    assert direct.effects[0]["status"] == ("running" if recheck else "not_sent")
    bulk = DB(batch_row=batch(origin="bulk"))
    assert (await runtime.authorize_bulk_effect(bulk, device(), bulk.effects[0], CLAIM, 8,
                                               EffectRequest(operation_id=EFFECT)))["authorized"] is True


async def test_unknown_origin_fails_closed_without_guessing_a_feature(authority):
    db = DB(batch_row=batch(origin="unknown"))
    result = await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW)
    assert result == ("BULK_REVOKED", None) and authority == []


class MediaDB(DB):
    def __init__(self):
        super().__init__(batch_row=batch(origin="direct"))
        self.asset = asset_row(tenant_id=TENANT)
        self.descriptor = jsonable_encoder(store.shared.asset_descriptor(self.asset))
        plan = [{"type": "audio", "asset": deepcopy(self.descriptor), "caption": "", "file_name": "arquivo.mp3"}]
        self.recipient.update(plan=plan, plan_hash=store.fingerprint(plan))
        self.recipient["context"]["media"] = deepcopy(self.descriptor)
        self.effects[0]["payload"] = {"phone": PHONE, "action": deepcopy(plan[0])}

    async def execute(self, query, params=None):
        if "FROM subscription_chatbot_assets" in str(query):
            self.calls.append((str(query), params))
            assert params == {"t": TENANT, "id": ASSET} and "status='ready'" in str(query)
            return Rows([self.asset] if self.asset else [])
        return await super().execute(query, params)


async def test_raw_audio_asset_is_rechecked_against_tenant_registry_before_claim_and_recheck(authority):
    db = MediaDB()
    request = EffectRequest(operation_id=EFFECT)
    assert (await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8, request))["authorized"]
    assert (await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8,
                                                request.model_copy(update={"recheck": True})))["authorized"]
    db.asset["sha256"] = "b" * 64
    recheck = await runtime.authorize_bulk_effect(db, device(), db.effects[0], CLAIM, 8,
                                                 request.model_copy(update={"recheck": True}))
    assert recheck["authorized"] is False and recheck["error_code"] == "BULK_TEMPLATE_CHANGED"
    assert db.effects[0]["status"] == "running" and db.effects[0]["attempts"] == 1


@pytest.mark.parametrize("changed", ["asset_missing", "origin", "extra_text", "audio_caption", "wrong_kind"])
async def test_raw_media_context_cannot_acquire_a_different_semantic_at_transport(authority, changed):
    db = MediaDB()
    if changed == "asset_missing":
        db.asset = None
    elif changed == "origin":
        db.batch["origin"] = "bulk"
    elif changed == "extra_text":
        db.recipient["plan"].append({"type": "text", "text": "Legenda proibida"})
    elif changed == "audio_caption":
        db.recipient["plan"][0]["caption"] = "Não deve enviar"
        db.effects[0]["payload"]["action"]["caption"] = "Não deve enviar"
    else:
        db.recipient["plan"][0]["type"] = "document"
        db.effects[0]["payload"]["action"]["type"] = "document"
    db.recipient["plan_hash"] = store.fingerprint(db.recipient["plan"])
    code, current = await runtime.bulk_effect_authority(db, device(), db.effects[0], NOW)
    assert code == ("BULK_TEMPLATE_CHANGED" if changed == "asset_missing" else "BULK_CONTEXT_CHANGED")
    assert current is None


@pytest.mark.parametrize("status", ["accepted", "not_sent", "failed", "pending", "running", "uncertain"])
def test_sent_history_classifies_direct_from_trusted_batch_and_never_offers_generic_resend(status):
    row = history_effect(type="bulk_action", purpose="bulk_action", status=status, outbound_origin="direct",
        incoming_kind="bulk", incoming_payload={"origin": "spoofed", "name": "Contato", "text": "Synthetic incoming"})
    value = history.item_dto(row, NOW)
    assert value["origin"] == {"code": "direct", "label": "Envio direto", "is_resend": False}
    assert value["received"] == {"type": "direct", "text": None, "truncated": False}
    assert value["actions"] == [] and value["resend"]["eligible"] is False
    assert "Enviar Mensagem" in value["resend"]["blocked_reason"]


def test_payload_origin_does_not_reclassify_a_bulk_record():
    row = history_effect(type="bulk_action", purpose="bulk_action", outbound_origin="bulk",
                         incoming_payload={"origin": "direct"})
    assert history.item_dto(row, NOW)["origin"]["code"] == "bulk"
