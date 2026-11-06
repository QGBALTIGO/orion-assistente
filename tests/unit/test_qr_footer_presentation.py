"""Complete footer paragraphs before wire freezing, using only synthetic data."""
from copy import deepcopy
import json
import time
from uuid import UUID

import pytest

from app import qr_assistant_interactions as choices
from app import subscription_chatbot_rules as rules, subscription_direct_policy as direct
from app.subscription_device_rules import transcription_parts
from app.subscription_device_schemas import DeviceSettings
from app.whatsapp_footer import FooterTooLong, action_footer, footer_template, spaced_footer, units


class Redis:
    def __init__(self): self.data = {}; self.writes = []
    async def get(self, key): return self.data.get(key)
    async def set(self, key, value, **kwargs): self.data[key] = value; self.writes.append(key)
    async def delete(self, *keys):
        for key in keys: self.data.pop(key, None)


@pytest.mark.parametrize("kind,field,maximum", [("list", "description", 1000),
    ("reply_buttons", "text", 1024), ("url_button", "text", 1024)])
def test_shared_composition_preserves_all_decisions_and_is_idempotent(kind, field, maximum):
    action = {"type": kind, field: "Texto completo\n", "footer": "Rodapé",
        "url": "https://example.invalid/path", "buttons": [{"id": "opaque-id", "title": "Confirmar"}],
        "sections": [{"rows": [{"id": "opaque-row", "title": "Escolher"}]}]}
    original = deepcopy(action)
    result = action_footer(action)
    assert result[field] == "Texto completo\n\nRodapé" and result["footer"] == ""
    assert result | {field: original[field], "footer": original["footer"]} == original
    assert action == original and action_footer(result) == result
    assert units(result[field]) <= maximum


@pytest.mark.parametrize("maximum", [1000, 1024])
def test_full_body_never_falls_back_to_an_unspaced_native_footer(maximum):
    with pytest.raises(FooterTooLong): rules._spaced_footer("A"*maximum, "Rodapé", maximum=maximum)


@pytest.mark.parametrize("body,allowed", [("😀"*510, True), ("😀"*511, False)])
def test_utf16_boundary_preserves_unicode_and_all_footer_content(body, allowed):
    if allowed: assert spaced_footer(body, "A", maximum=1024) == (body+"\n\nA", "")
    else:
        with pytest.raises(FooterTooLong): spaced_footer(body, "A", maximum=1024)


def test_direct_url_keeps_literal_braces_url_and_label_with_one_footer():
    source = {"text": "Olá {name}", "footer": "Rodapé {literal}", "button_text": "Abrir",
        "url": "https://example.com/support"}
    before = deepcopy(source)
    action = direct.url_button_plan(source)[0]
    assert action == {"type": "url_button", **source,
        "text": "Olá {name}\n\nRodapé {literal}", "footer": ""}
    assert source == before


def test_transcription_footer_is_spaced_before_chunking_without_evaluating_transcript():
    settings = DeviceSettings(transcription={"template": "{name}\n{transcript}\n{footer}",
        "footer": "Rodapé"}).model_dump(mode="json")
    before = deepcopy(settings)
    source = "😀"*32001 + "{footer} literal"
    parts = transcription_parts(settings, {"name": "Nome", "phone": "5567999999901"}, source)
    assert "".join(parts) == "Nome\n" + source + "\n\nRodapé"
    assert all(units(part) <= 32000 for part in parts) and settings == before


def test_template_spacing_only_applies_to_real_footer_fields():
    assert footer_template("{{footer}} {transcript}\n{footer}", name="Nome",
        transcript="Rodapé {footer}", footer="Rodapé") == "{footer} Rodapé {footer}\n\nRodapé"


def test_authored_media_caption_keeps_paragraphs_and_normalizes_every_line_ending():
    cases = [("Mensagem\r\n\r\nRodapé {literal}", "Mensagem\n\nRodapé {literal}"),
             ("Mensagem\r\rRodapé {literal}", "Mensagem\n\nRodapé {literal}"),
             ("Mensagem\n\nRodapé {literal}", "Mensagem\n\nRodapé {literal}"),
             ("Linha 1\r\nLinha 2\rLinha 3\n\nRodapé 😀", "Linha 1\nLinha 2\nLinha 3\n\nRodapé 😀")]
    for kind, mime, name in (("image", "image/jpeg", "image.jpg"),
                             ("video", "video/mp4", "video.mp4"),
                             ("document", "application/pdf", "document.pdf")):
        for caption, expected in cases:
            message = {"file_name": name, "caption": caption}
            descriptor = {"kind": kind, "mime": mime, "file_name": name}
            original = deepcopy(message), deepcopy(descriptor)
            plan, warnings = direct.media_plan(message, descriptor)
            assert plan[0]["caption"] == expected and warnings == []
            assert plan[0]["file_name"] == name and plan[0]["asset"] == descriptor
            assert (message, descriptor) == original
    plan, warnings = direct.media_plan({"file_name": "audio.mp3", "caption": "Mensagem\r\rRodapé"},
        {"kind": "audio", "mime": "audio/mpeg", "file_name": "audio.mp3"})
    assert plan[0]["caption"] == "" and warnings == ["AUDIO_CAPTION_OMITTED"]


async def present(monkeypatch, *, step="confirm", answer="Resumo completo", verified=False):
    tenant, phone = UUID(int=42), "5567999999901"
    redis = Redis(); key = f"orion:qr:ai:{tenant}:{phone}"
    state = {"step": step, "confirmation_summary": answer, "confirmation_expires_at": time.time()+240}
    await redis.set(key, json.dumps(state)); redis.writes.clear()
    async def link(*args): return "https://orionagenda.pro/AG-ABC234#synthetic-proof"
    monkeypatch.setattr(choices, "_appointment_url", link)
    result = await choices.present(redis, tenant, phone, phone,
        {"answer": answer, "step": step, "protocol": "synthetic"},
        choices.Capabilities(assistant_interactions=1), verified_operation=verified)
    assert json.loads(await redis.get(key)) == state
    return result, redis, key


async def test_actual_qr_reply_buttons_have_real_blank_line_and_no_native_footer(monkeypatch):
    result, redis, key = await present(monkeypatch)
    action = result["presentation"]["action"]
    assert action["text"] == "Resumo completo\n\nConfira os dados antes de escolher."
    assert action["footer"] == "" and len(action["buttons"]) == 3
    assert len(json.loads(await redis.get(key+":presentation"))["choices"]) == 3


async def test_successful_operation_overflow_keeps_complete_answer_footer_and_url_as_text(monkeypatch):
    answer = "A"*1000
    result, redis, key = await present(monkeypatch, step="confirmed", answer=answer, verified=True)
    assert "presentation" not in result
    assert result["answer"].startswith(answer+"\n\nRemarque ou cancele pela página segura.")
    assert "https://orionagenda.pro/AG-ABC234#synthetic-proof" in result["answer"]
    assert units(result["answer"]) <= 1200 and await redis.get(key+":presentation") is None


async def test_post_operation_1200_unit_original_answer_never_errors_or_loses_existing_url(monkeypatch, caplog):
    caplog.set_level("INFO", logger=choices.__name__)
    url = "https://orionagenda.pro/AG-ABC234#original-proof"
    answer = "A"*(1200-units(url)-2) + "\n\n" + url
    result, redis, key = await present(monkeypatch, step="rescheduled", answer=answer, verified=True)
    assert result["answer"] == answer and "presentation" not in result
    assert units(result["answer"]) == 1200 and await redis.get(key+":presentation") is None
    assert any(record.getMessage() == "qr_footer_presentation_fallback reason=native_body_limit optional_text=omitted"
               for record in caplog.records)
    assert url not in caplog.text, "Internal fallback diagnostics cannot expose the private link"


async def test_pre_operation_optional_widget_overflow_keeps_full_summary_without_new_choice_authority(monkeypatch):
    answer = "Resumo " + "A"*993
    result, redis, key = await present(monkeypatch, answer=answer)
    assert "presentation" not in result
    assert result["answer"] == answer + "\n\nConfira os dados antes de escolher."
    assert await redis.get(key+":presentation") is None


def test_footerless_text_and_non_footer_actions_are_not_guessed_or_reformatted():
    for action in ({"type": "text", "text": "Mensagem\nRodapé escrito pelo autor"},
                   {"type": "image", "caption": "Mensagem\n\nRodapé", "asset": {"id": "original"}}):
        assert action_footer(action) == action
    assert spaced_footer("Literal \\n", "", maximum=1024) == ("Literal \\n", "")


@pytest.mark.parametrize("template", ["{name}\n{transcript}{footer}", "{name}\n{transcript}\n\n\n{footer}"])
def test_transcription_boundary_has_one_blank_line_for_inline_and_spaced_templates(template):
    assert footer_template(template, name="Nome", transcript="Mensagem", footer="Rodapé") == "Nome\nMensagem\n\nRodapé"
