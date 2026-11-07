"""Recover mixed duplicate/new batches without replaying or guessing text splits."""
import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import qr_ai, qr_conversation_lock
from test_qr_channels_scope import ingress, request, PHONE


def part(identity, text):
    return {"message_id": identity, "text": text}


@pytest.mark.parametrize("parts", [
    [part("different-id", "Olá")],
    [part("message-1", "other text")],
    [part("message-1", "   ")],
    [part("message-1", "Olá"), part("message-1", "Olá")],
    [part("message-1", "Olá"), {"message_id": "message-2", "text": "Olá", "tenant_id": str(uuid4())}],
])
def test_parts_are_exact_bounded_message_content_not_authority(parts):
    with pytest.raises(ValidationError):
        qr_ai.Incoming(phone=PHONE, message_id="message-1", text="Olá", parts=parts)


@pytest.mark.asyncio
async def test_mixed_batch_keeps_only_unprocessed_multiline_content_and_identity(ingress):
    a, _, _, redis, calls, _, _, api = ingress
    first = await request(api, a, message_id="old-id", text="old\nmultiline",
                          parts=[part("old-id", "old\nmultiline")])
    assert first.status_code == 200
    result = await request(api, a, message_id="old-id", message_ids=["old-id", "fresh-id"],
                           text="old\nmultiline\nfresh\nrequest",
                           parts=[part("old-id", "old\nmultiline"), part("fresh-id", "fresh\nrequest")])
    assert result.status_code == 200 and result.json()["step"] == "confirmed"
    assert [call[-1] for call in calls] == ["old\nmultiline", "fresh\nrequest"]
    assert any(key.endswith(":fresh-id") for key in redis.values)
    assert (await request(api, a, message_id="fresh-id", text="fresh\nrequest")).json()["step"] == "duplicate"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_mixed_legacy_client_gets_clarification_without_consuming_fresh_id(ingress):
    a, _, _, redis, calls, _, _, api = ingress
    assert (await request(api, a, message_id="old-id", text="old")).status_code == 200
    result = await request(api, a, message_id="old-id", message_ids=["old-id", "fresh-id"], text="old\nnew")
    assert result.status_code == 200 and result.json()["step"] == "partial_duplicate"
    assert result.json()["answer"] and len(calls) == 1
    assert not any(key.endswith(":fresh-id") and ":done:" in key for key in redis.values)


@pytest.mark.asyncio
async def test_another_channel_dedupe_never_removes_new_company_content(ingress):
    a, b, _, _, calls, _, _, api = ingress
    assert (await request(api, a, message_id="same-id", text="company A")).status_code == 200
    result = await request(api, b, message_id="same-id", text="company B", parts=[part("same-id", "company B")])
    assert result.status_code == 200 and result.json()["step"] == "confirmed"
    assert calls[-1][2] == b.conversation_namespace(PHONE) and calls[-1][-1] == "company B"


@pytest.mark.asyncio
async def test_slow_authority_check_cannot_start_a_fresh_window_for_business_work(ingress, monkeypatch):
    a, _, _, _, calls, _, _, api = ingress
    monkeypatch.setattr(qr_conversation_lock, "PROCESSING_SECONDS", .01)
    async def slow_ready(identity):
        await asyncio.sleep(.05)
        return True
    monkeypatch.setattr(qr_ai, "_managed_assistant_ready", slow_ready)
    result = await request(api, a)
    assert result.status_code == 503 and calls == []
