"""Links preserve international phones and message bytes; QR never truncates."""
import base64
from urllib.parse import urlsplit, parse_qs
import xml.etree.ElementTree as ET

from pydantic import ValidationError
import pytest

from app.subscription_whatsapp_link import generate
from app.subscription_whatsapp_link_schemas import LinkRequest


@pytest.mark.parametrize("phone", ["+55 (67) 99999-9901", "+595 981 234567", "+351 912345678", "+12025550123"])
def test_international_phone_and_message_are_preserved(phone):
    message = "Olá! Quero informações & valores.\nMeu nome é José 💙 + = ? # %"
    payload = LinkRequest(phone=phone, message=message)
    value = generate(payload)
    parsed = urlsplit(value["url"])
    assert parsed.scheme == "https" and parsed.netloc == "wa.me"
    assert parsed.path == "/" + payload.phone[1:]
    assert parse_qs(parsed.query)["text"] == [message]
    assert base64.b64decode(value["qr"]["png_base64"]).startswith(b"\x89PNG\r\n\x1a\n")
    svg = ET.fromstring(value["qr"]["svg"])
    assert svg.tag.endswith("svg") and any(node.tag.endswith("path") for node in svg.iter())
    assert "http://" not in value["url"]


def test_empty_message_has_no_unnecessary_query():
    value = generate(LinkRequest(phone="+5567999999901"))
    assert value["url"] == "https://wa.me/5567999999901"
    assert value["qr"] is not None and value["qr_notice"] is None


def test_thousand_characters_are_accepted_and_not_truncated():
    message = "a" * 1000
    value = generate(LinkRequest(phone="+5567999999901", message=message))
    assert parse_qs(urlsplit(value["url"]).query)["text"] == [message]
    assert value["qr"] is not None


def test_oversized_qr_keeps_the_complete_click_to_chat_link():
    message = "💙" * 1000
    value = generate(LinkRequest(phone="+5567999999901", message=message))
    assert parse_qs(urlsplit(value["url"]).query)["text"] == [message]
    assert value["qr"] is None and "Reduza" in value["qr_notice"]


@pytest.mark.parametrize("changes", [{"phone": "123"}, {"phone": "https://evil.example"}, {"phone": True}, {"message": "a"*1001}, {"message": "bad\x00"}, {"message": "bad\ud800"}, {"redirect_url": "https://evil.example"}])
def test_rejects_invalid_unbounded_or_extra_input(changes):
    with pytest.raises(ValidationError):
        LinkRequest.model_validate({"phone": "+5567999999901", **changes})
