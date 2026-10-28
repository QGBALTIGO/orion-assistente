"""Bounded provider output, local civil time and invitation authority contracts."""
from datetime import date, time
import json
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest
from pydantic import ValidationError

from app import management_ai as ai
from app.management_blocks import BlockSeries, expand_occurrences
from app.management_preferences import AutomationPreferences, BusinessPreferences
from app.management_team import InvitationCreate, InvitationToken, MemberChange


def block(**changes):
    values = dict(professional_id=uuid4(), start_date=date(2026, 10, 19), until_date=date(2026, 11, 1),
                  frequency="weekdays", weekdays=[0, 2, 4], kind="hours", start_time=time(12), end_time=time(13), idempotency_key=str(uuid4()))
    return BlockSeries.model_validate({**values, **changes})


def test_recurrence_uses_local_timezone_and_selected_weekdays():
    result = expand_occurrences(block(), "America/Sao_Paulo")
    assert [r["occurrence_date"].weekday() for r in result] == [0, 2, 4, 0, 2, 4]
    assert len(result) == 6 and result[0]["start_at"].hour == 15
    assert all((r["end_at"]-r["start_at"]).total_seconds() == 3600 for r in result)


def test_full_day_follows_dst_real_duration_without_guessing_an_hour():
    payload = block(start_date=date(2026, 3, 8), until_date=date(2026, 3, 8), kind="day_off", start_time=None, end_time=None, frequency="daily")
    result = expand_occurrences(payload, "America/New_York")
    assert (result[0]["end_at"]-result[0]["start_at"]).total_seconds() == 23*3600


@pytest.mark.parametrize("day,start,stop", [(date(2026, 3, 8), time(2, 30), time(3, 30)), (date(2026, 11, 1), time(1, 30), time(2, 30))])
def test_recurring_dst_gap_or_ambiguous_time_is_rejected(day, start, stop):
    with pytest.raises(HTTPException) as error:
        expand_occurrences(block(start_date=day, until_date=day, start_time=start, end_time=stop, frequency="daily"), "America/New_York")
    assert error.value.status_code == 422


@pytest.mark.parametrize("changes", [{"until_date": date(2028, 1, 1)}, {"until_date": date(2020, 1, 1)}, {"weekdays": [0, 0]}, {"weekdays": []}, {"weekdays": [8]}, {"start_time": time(18), "end_time": time(12)}, {"kind": "day_off"}])
def test_invalid_series_cannot_expand(changes):
    with pytest.raises(ValidationError):
        block(**changes)


def test_no_matching_day_is_not_a_successful_empty_series():
    with pytest.raises(HTTPException):
        expand_occurrences(block(until_date=date(2026, 10, 19), weekdays=[1]), "UTC")


def test_invites_cannot_grant_owner_or_accept_arbitrary_token():
    with pytest.raises(ValidationError):
        InvitationCreate(role="owner", idempotency_key=str(uuid4()))
    with pytest.raises(ValidationError):
        InvitationToken(token="not-a-token")
    with pytest.raises(ValidationError):
        MemberChange(role="editor", active=True, expected_updated_at="2026-01-01T00:00:00")
    assert InvitationCreate(email="TEAM@EXAMPLE.INVALID", role="viewer", idempotency_key=str(uuid4())).email == "team@example.invalid"


def test_operational_settings_do_not_accept_executable_urls_or_repeated_events():
    with pytest.raises(ValidationError):
        BusinessPreferences(google_review_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        BusinessPreferences(tax_document_kind="cpf", tax_document="123")
    with pytest.raises(ValidationError):
        AutomationPreferences(rules=[{"event": "reminder_1h", "template": "Olá"}]*2)


def test_ai_status_has_no_secret_or_fabricated_credit_balance(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-test-key")
    monkeypatch.setenv("MANAGEMENT_AI_ENABLED", "0")
    config, key = ai.configuration()
    assert config["enabled"] is False and key == "secret-test-key"
    assert "secret-test-key" not in json.dumps(config) and "credits" not in config


def test_strict_output_schema_requires_all_fields_and_rejects_actions_outside_routes():
    schema = ai.strict_schema(ai.ConsultantOutput)
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        ai.ConsultantOutput(answer="Texto", actions=[{"title": "Pagar", "description": "Execute", "page": "transfer"}])


@pytest.mark.asyncio
async def test_provider_uses_strict_output_without_tools_and_returns_real_usage():
    def handler(request):
        assert request.url == ai.GROQ_URL
        payload = json.loads(request.content)
        assert "tools" not in payload and payload["response_format"]["json_schema"]["strict"] is True
        assert payload["max_completion_tokens"] > 1000
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"answer": "Ainda não há atendimentos concluídos no período.", "actions": []})}}], "usage": {"prompt_tokens": 18, "completion_tokens": 21, "total_tokens": 39}})
    output, usage = await ai.generate(task="consultant", request={"question": "Como foi o mês?"}, sources={"revenue_cents": 0}, key="test", model="openai/gpt-oss-120b", transport=httpx.MockTransport(handler))
    assert output["actions"] == [] and usage["total_tokens"] == 39


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"function": {"name": "send"}}]}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"answer":"ok","actions":[],"send":true}'}}]},
    {"choices": [{"finish_reason": "length", "message": {"content": '{"answer":"ok","actions":[]}'}}]},
    {"choices": []},
])
async def test_invalid_provider_response_never_becomes_an_action(response):
    with pytest.raises(HTTPException) as error:
        await ai.generate(task="consultant", request={}, sources={}, key="test", model="llama-3.3-70b-versatile", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_provider_failures_do_not_echo_credentials_or_raw_response():
    with pytest.raises(HTTPException) as error:
        await ai.generate(task="loyalty", request={}, sources={}, key="sensitive-test", model="llama-3.3-70b-versatile", transport=httpx.MockTransport(lambda _: httpx.Response(401, text="sensitive-test provider failure")))
    assert error.value.status_code == 503 and "sensitive-test" not in error.value.detail


@pytest.mark.asyncio
async def test_management_entry_points_serve_their_assets_and_reject_other_files():
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        panel = await client.get("/panel")
        assert panel.status_code == 200 and "no-store" in panel.headers["cache-control"]
        for name, mime in (("orion-suite-live.js", "text/javascript"), ("orion-suite.css", "text/css"), ("orion-fields.js", "text/javascript"), ("orion-fields.css", "text/css")):
            path = "/panel-assets/" + name
            assert path in panel.text
            asset = await client.get(path)
            assert asset.status_code == 200 and asset.headers["content-type"].startswith(mime)
            assert asset.headers["x-content-type-options"] == "nosniff" and len(asset.content) > 1000
        invitation = await client.get("/convite")
        assert invitation.status_code == 200 and invitation.headers["referrer-policy"] == "no-referrer"
        assert "script-src 'self'" in invitation.headers["content-security-policy"]
        for name in ("orion-invite.js", "orion-invite.css"):
            path = "/panel-assets/" + name
            assert path in invitation.text
            assert (await client.get(path)).status_code == 200
        for name in ("orion-public.js", "orion-public.css"):
            assert (await client.get("/public-assets/" + name)).status_code == 200
        for name in ("figtree-latin.woff2", "outfit-latin.woff2"):
            font = await client.get("/brand/" + name)
            assert font.status_code == 200 and font.headers["content-type"].startswith("font/woff2")
            assert font.content.startswith(b"wOF2")
        for path in ("/panel-assets/panel.html", "/public-assets/orion-invite.html", "/brand/figtree-OFL.txt"):
            assert (await client.get(path)).status_code == 404
