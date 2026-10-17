"""Known completed results stay factual; receipts never execute or restore state."""
import json
from uuid import uuid4

import pytest

from app import qr_intake_revision as revision, qr_operation_receipt as receipt


class Redis:
    def __init__(self):
        self.values = {};self.ttls = {};self.calls = []
    async def set(self, key, value, ex=None):
        self.calls.append(("set", key));self.values[key] = value;self.ttls[key] = ex
    async def get(self, key):
        self.calls.append(("get", key));return self.values.get(key)
    async def delete(self, key):
        self.calls.append(("delete", key));self.values.pop(key, None)


PHONE = "5521999999999"


@pytest.mark.asyncio
@pytest.mark.parametrize("step", sorted(receipt.TERMINAL))
async def test_only_explicit_verified_terminal_result_is_preserved_and_shown_as_completed(step):
    redis, tenant = Redis(), uuid4()
    result = {"answer": "Operação verificada. Protocolo AG-EXEMPLO.", "step": step,
        "protocol": "AG-EXEMPLO", "token": "NEVER_STORE", "state": {"private": "NEVER_STORE"}}
    assert await receipt.save(redis, tenant, PHONE, "message-1", result, verified_operation=True)
    target = receipt.key(tenant, PHONE)
    assert redis.ttls[target] == 300
    assert "NEVER_STORE" not in redis.values[target]
    assert json.loads(redis.values[target])["protocol"] == "AG-EXEMPLO"
    response = await receipt.pending(redis, tenant, PHONE, True)
    assert response["step"] == "completed_operation_review"
    assert result["answer"] in response["answer"] and "ainda não foi aplicada" in response["answer"]
    assert receipt.fits_message(response["answer"])
    # No pending hold, booking state, confirmation token or new operation appears.
    assert set(redis.values) == {target}


@pytest.mark.asyncio
async def test_latch_unknown_generic_cancellation_and_preview_are_not_evidence_of_completion():
    redis, tenant = Redis(), uuid4()
    for result, verified, preview in [
        ({"answer": "Encerrei esta solicitação", "step": "cancelled"}, False, False),
        ({"answer": None, "step": "superseded_after_write", "not_committed": False}, True, False),
        ({"answer": "Resumo", "step": "confirm"}, True, False),
        ({"answer": "Simulação concluída", "step": "preview_complete"}, True, True),
        ({"answer": "Confirmado", "step": "confirmed"}, True, True),
    ]:
        assert not await receipt.save(redis, tenant, PHONE, "message-1", result,
            verified_operation=verified, preview=preview)
    assert redis.calls == []
    assert await receipt.pending(redis, tenant, PHONE, True, preview=True) is None
    assert redis.calls == []


@pytest.mark.asyncio
async def test_accepted_current_reply_ack_removes_receipt_and_next_turn_does_not_repeat_status():
    redis, tenant = Redis(), uuid4()
    await receipt.save(redis, tenant, PHONE, "message-1", {"answer": "Confirmado para segunda.", "step": "confirmed"}, verified_operation=True)
    assert await receipt.pending(redis, tenant, PHONE, True)
    assert await receipt.pending(redis, tenant, PHONE, False) is None
    assert await receipt.pending(redis, tenant, PHONE, True) is None
    assert redis.values == {}


@pytest.mark.asyncio
async def test_receipt_never_crosses_tenant_contact_or_a_corrupted_scope_value():
    redis, tenant, other = Redis(), uuid4(), uuid4()
    await receipt.save(redis, tenant, PHONE, "message-1", {"answer": "Confirmado para segunda.", "step": "confirmed"}, verified_operation=True)
    assert await receipt.pending(redis, other, PHONE, True) is None
    assert await receipt.pending(redis, tenant, "5521999999998", True) is None
    redis.values[receipt.key(other, PHONE)] = redis.values[receipt.key(tenant, PHONE)]
    assert await receipt.pending(redis, other, PHONE, True) is None


@pytest.mark.asyncio
async def test_long_receipt_keeps_every_factual_character_with_utf16_budget():
    redis, tenant = Redis(), uuid4()
    body = "😀" * 589 + "FIM-VERIFICADO-1234567"
    assert receipt.fits_message(body)
    await receipt.save(redis, tenant, PHONE, "message-1", {"answer": body, "step": "confirmed"}, verified_operation=True)
    response = await receipt.pending(redis, tenant, PHONE, True)
    assert response["answer"] == body and receipt.fits_message(response["answer"])
    assert not await receipt.save(redis, tenant, PHONE, "message-2", {"answer": "😀" * 601, "step": "confirmed"}, verified_operation=True)


@pytest.mark.asyncio
async def test_expired_or_invalid_receipt_never_becomes_current_fact(monkeypatch):
    redis, tenant = Redis(), uuid4()
    monkeypatch.setattr(receipt, "time", lambda: 1000)
    await receipt.save(redis, tenant, PHONE, "message-1", {"answer": "Confirmado", "step": "confirmed"}, verified_operation=True)
    monkeypatch.setattr(receipt, "time", lambda: 1300)
    assert await receipt.pending(redis, tenant, PHONE, True) is None
    for malformed in ("[]", "null", "{}", "not-json"):
        redis.values[receipt.key(tenant, PHONE)] = malformed
        assert await receipt.pending(redis, tenant, PHONE, True) is None


@pytest.mark.asyncio
async def test_save_does_not_discard_a_known_result_just_because_its_turn_was_superseded(monkeypatch):
    redis, tenant = Redis(), uuid4()
    async def stale():
        raise revision.Superseded(not_committed=False)
    monkeypatch.setattr(revision, "assert_current", stale)
    assert await receipt.save(redis, tenant, PHONE, "message-1", {"answer": "Confirmado", "step": "confirmed"}, verified_operation=True)
    with pytest.raises(revision.Superseded):
        await receipt.pending(redis, tenant, PHONE, True)
