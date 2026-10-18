"""Customer confirmation copy hides the identifier; internal evidence survives."""
import json
from datetime import datetime

import pytest

from app import qr_ai, qr_operation_receipt, qr_presentation as presentation
from test_preview_audio import MemoryRedis
from test_qr_multi_flow import flow
from test_qr_followup_context import identified


@pytest.mark.parametrize("operation", ["booking", "reschedule"])
@pytest.mark.parametrize("style", presentation.STYLE_LABELS)
def test_customer_success_omits_protocol_without_losing_verified_facts_or_internal_evidence(operation, style):
    summary = presentation.BookingSummary(("Corte",), datetime.fromisoformat("2026-10-20T15:30:00-04:00"),
        "America/Campo_Grande", 3500, professional="Bruno", duration_min=40, protocol="AG-PRIVATE123")
    result = presentation.compose_summary(summary, {"style": style, "emoji": "light"}, confirmed=True, operation=operation)
    answer = result.render()
    assert "Protocolo" not in answer and "AG-PRIVATE123" not in answer
    assert all(fact in answer for fact in ("Corte", "Bruno", "20/10/2026", "15h30", "R$ 35,00", "40 min"))
    assert "confirmad" in answer and result.prompt == "" and "Posso confirmar" not in answer
    assert summary.protocol == "AG-PRIVATE123"
    assert presentation.fits_message(answer)


@pytest.mark.asyncio
async def test_actual_qr_confirmation_keeps_protocol_in_result_and_scoped_receipt_but_not_customer_text(flow, monkeypatch):
    import redis.asyncio as redis_asyncio
    h = flow
    await identified(h, "Quero agendar corte amanhã às15h30 com Bruno", identity="new-summary")
    memory = MemoryRedis(); memory.values = h.redis.data
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **kw: memory)
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "0")
    result = await qr_ai.run_locked(h.tenant, "5567999999999", "SIM", h.namespace, message_id="new-consent")
    assert result["step"] == "confirmed" and result["protocol"] == "SYNTHETIC"
    assert "SYNTHETIC" not in result["answer"] and "Protocolo" not in result["answer"]
    assert [entry[0] for entry in h.calls["writes"]] == ["hold", "confirm"]
    receipt = json.loads(memory.values[qr_operation_receipt.key(h.tenant, "5567999999999")])
    assert receipt["protocol"] == result["protocol"] and receipt["message_id"] == "new-consent"
    assert receipt["answer"] == result["answer"]


def test_owned_history_identifiers_remain_available_for_selection():
    records = [{"start_at": datetime.fromisoformat("2026-10-20T15:30:00-04:00"), "status": "confirmed", "protocol": "AG-HISTORY123"}]
    answer = presentation.render_booking_history(records, timezone="America/Campo_Grande")
    assert "AG-HISTORY123" in answer and records[0]["protocol"] == "AG-HISTORY123"
