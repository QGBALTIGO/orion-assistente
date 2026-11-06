"""Synthetic BotBot ZIP compatibility checks. No personal exports or real URLs."""
from __future__ import annotations

from io import BytesIO
import json
from uuid import UUID
import zipfile

import pytest

from app import subscription_chatbot_archive_format as codec
from app.subscription_chatbot_botbot import read_botbot


def archive(records, *, filename="replies.json", extra=None):
    data = {"exported_at": "2026-11-06T12:00:00+00:00", "replies": records}
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(filename, json.dumps(data, ensure_ascii=False))
        for name, content in extra or []:
            z.writestr(name, content)
    return out.getvalue()


def test_botbot_zip_text_and_url_are_imported_as_inactive_rules_with_stable_ids():
    records = [
        {"id": 12, "keyword": "/start", "match_mode": "equal",
         "enabled": True, "response": [{"type": "text", "text": "Olá de teste!"}]},
        {"id": 13, "keyword": "teste", "match_mode": "like", "enabled": True,
         "response": {"kind": "sequence", "actions": [
             {"type": "url", "url": "https://api.example.com/lookup",
              "method": "GET", "custom_template": "{{reply}}"}]}},
    ]
    raw = archive(records)
    a = codec.read_archive(raw, "botbot_replies_synthetic.zip")
    b = codec.read_archive(raw, "botbot_replies_synthetic.zip")
    assert a == b
    assert a["format"] == codec.FORMAT and a["version"] == codec.VERSION
    assert len(a["rules"]) == 2 and a["templates"] == a["assets"] == []
    assert [record["definition"]["enabled"] for record in a["rules"]] == [False, False]
    assert [record["definition"]["keyword"] for record in a["rules"]] == ["/start", "teste"]
    assert a["rules"][0]["definition"]["response"]["actions"][0]["text"] == "Olá de teste!"
    url = a["rules"][1]["definition"]["response"]["actions"][0]
    assert url["type"] == "url" and url["custom_template"] == "{{reply}}"
    assert URL := url["url"] == "https://api.example.com/lookup"
    assert all(UUID(record["id"]) for record in a["rules"])


def test_botbot_zip_imports_a_complete_list_with_nested_reference():
    records = [
        {"id": 1, "keyword": "menu", "response": {"kind": "list",
          "title": "Menu de teste", "description": "Escolha uma opção",
          "button_text": "Ver opções", "sections": [
              {"title": "Atendimento", "rows": [
                  {"title": "Saudação", "description": "Abrir resposta",
                   "action": {"type": "nested_reply", "reply_id": 2}}
              ]}
          ]}},
        {"id": 2, "keyword": "saudacao", "response": "Olá!"}
    ]
    result = codec.read_archive(archive(records), "botbot_replies_synthetic.zip")
    assert result["rules"][0]["definition"]["response"]["kind"] == "list"
    ref = result["rules"][0]["definition"]["response"]["sections"][0]["rows"][0]["action"]["rule_id"]
    assert ref == result["rules"][1]["id"]
    assert all(not record["definition"]["enabled"] for record in result["rules"])


def test_normal_orion_archive_remains_strict_and_does_not_accept_botbot_silently():
    raw = archive([{"id": 1, "keyword": "x", "response": "Olá!"}])
    with pytest.raises(ValueError):
        codec.read_archive(raw, "any-export.zip")
    with pytest.raises(ValueError):
        codec.read_archive(raw, "botbot_replies_test.json")
    assert read_botbot(raw, "botbot_replies_test.zip")["format"] == codec.FORMAT


@pytest.mark.parametrize("rows", [
    [{"id": 1, "keyword": "x", "response": {"type": "unknown_media", "asset": "1"}}],
    [{"id": 1, "keyword": "x", "response": [{"type": "text", "text": ""}]}],
    [{"id": 1, "keyword": "x", "response": {"type": "nested_reply", "reply_id": 999}}],
    [{"id": 1, "keyword": "x", "response": {"kind": "sequence", "actions": [
        {"type": "text", "text": "x", "delay": 15},
        {"type": "text", "text": "y", "delay": 15},
        {"type": "text", "text": "z", "delay": 15}]}}],
])
def test_botbot_zip_rejects_unknown_actions_missing_refs_and_oversized_delay(rows):
    with pytest.raises(ValueError):
        codec.read_archive(archive(rows), "botbot_replies_synthetic.zip")


def test_botbot_zip_rejects_zip_slip_duplicate_path_and_no_replies():
    with pytest.raises(ValueError):
        codec.read_archive(archive([{"keyword": "x", "response": "hi"}],
                                   filename="../reply.json"), "botbot_replies_test.zip")
    with pytest.raises(ValueError):
        codec.read_archive(archive([], filename="export.json"), "botbot_replies_test.zip")
    with pytest.raises(ValueError):
        codec.read_archive(b"not a zip", "botbot_replies_test.zip")


def test_botbot_import_keeps_sensitive_url_as_data_and_never_fetches_it(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "create_connection",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network accessed")))
    records = [{"id": 31, "keyword": "consulta",
                "response": {"type": "url", "url": "https://api.example.com/private-token",
                             "method": "POST", "custom_template": "{{reply}}"}}]
    parsed = codec.read_archive(archive(records), "botbot_replies_test.zip")
    assert parsed["rules"][0]["definition"]["enabled"] is False
    assert parsed["rules"][0]["definition"]["response"]["actions"][0]["url"].endswith("/private-token")
