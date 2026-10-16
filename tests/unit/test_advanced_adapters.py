from uuid import uuid4

import pytest

from app.advanced_adapters import (
    FakeTranscriber, SafeFunctionGateway, ServerVerifiedScope, ChatwootAdapter
)


def test_ai_cannot_change_tenant_or_create_booking():
    tenant = uuid4()
    gateway = SafeFunctionGateway(ServerVerifiedScope(tenant))
    assert gateway.validate("availability", {"date": "2026-12-10"})["tenant_id"] == str(tenant)
    with pytest.raises(PermissionError):
        gateway.validate("availability", {"tenant_id": str(uuid4())})
    with pytest.raises(PermissionError):
        gateway.validate("book_appointment", {"start": "2026-12-10T14:00:00"})
    with pytest.raises(PermissionError):
        gateway.validate("eval", {"sql": "DELETE FROM tenants"})


@pytest.mark.asyncio
async def test_fake_audio_not_recognition():
    p = FakeTranscriber()
    assert "fictícia" in await p.transcribe(b"fake audio", "audio/ogg")
    with pytest.raises(ValueError):
        await p.transcribe(b"", "audio/ogg")
    with pytest.raises(ValueError):
        await p.transcribe(b"x", "application/pdf")


@pytest.mark.asyncio
async def test_chatwoot_not_connected():
    with pytest.raises(RuntimeError, match="não conectado"):
        await ChatwootAdapter().notify_handoff(uuid4(), "+5567999999999")
