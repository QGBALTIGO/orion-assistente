"""Address composition and editable slug normalization, without external I/O."""
import json
import re

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.management_schemas import SiteSave
from app.public_site_api import checked_slug
from app.public_site_rendering import public_html
from app.site_address import ADDRESS_DEFAULTS, address_display, merge_address_fields
from app.site_publication import validate_slug
from app.site_slugs import normalize_site_slug, numbered_slug_candidates


@pytest.mark.parametrize("raw,expected", [
    ("  Barbearia São José  ", "barbearia-sao-jose"),
    ("Espaço & Beleza — Água", "espaco-beleza-agua"),
    ("cafe\u0301  da  prac\u0327a", "cafe-da-praca"),
    ("STUDIO__13...NORTE", "studio-13-norte"),
    ("--salão---da--maria--", "salao-da-maria"),
    ("ＡＢＣ ①", "abc-1"),
    ("a" * 79 + " espaço", "a" * 79),
])
def test_editable_slug_matches_frontend_unicode_and_length_contract(raw, expected):
    assert normalize_site_slug(raw) == expected
    assert SiteSave(expected_version=0, slug=raw).slug == expected


def test_normalizing_editor_input_never_relaxes_public_url_validation():
    for raw in ("São José", "UPPERCASE", "../site", "with_space"):
        with pytest.raises(HTTPException):
            validate_slug(raw)
        with pytest.raises(HTTPException):
            checked_slug(raw)
    assert SiteSave(expected_version=0, slug=" ").slug is None
    for raw in ("😀", "ab", "ADMIN", "x" * 161):
        with pytest.raises(ValidationError):
            SiteSave(expected_version=0, slug=raw)


def test_numeric_suffix_continues_cleanly_and_leaves_room_at_eighty_characters():
    assert list(numbered_slug_candidates("salao", count=3)) == ["salao-2", "salao-3", "salao-4"]
    assert list(numbered_slug_candidates("salao-09", count=3)) == ["salao-10", "salao-11", "salao-12"]
    candidates = list(numbered_slug_candidates("a" * 80, count=3))
    assert len(set(candidates)) == 3
    for candidate in candidates:
        assert len(candidate) == 80 and validate_slug(candidate) == candidate


def test_address_fields_preserve_legacy_text_and_accept_real_establishment_numbers():
    legacy = "Rua Antiga, 42 - fundos"
    assert address_display({"address": legacy}) == legacy
    saved = SiteSave(expected_version=0, address="Praça da Sé", address_number=" 12-A ",
                     address_complement=" Sala 3 ", neighborhood=" Sé ", postal_code="01001000", state="sp")
    assert saved.address_number == "12-A" and saved.postal_code == "01001-000" and saved.state == "SP"
    assert address_display(saved.model_dump()) == "Praça da Sé, 12-A — Sala 3 — Sé"
    without_number = SiteSave(expected_version=0, address="Estrada Rural", address_number="12", address_no_number=True)
    assert without_number.address_number is None
    assert address_display(without_number.model_dump()) == "Estrada Rural, s/n"
    assert address_display({}) is None
    for field, value in (("address_number", "x" * 31), ("address_complement", "x" * 161),
                         ("neighborhood", "x" * 121), ("address_no_number", "true")):
        with pytest.raises(ValidationError):
            SiteSave(expected_version=0, **{field: value})


def test_omitted_address_fields_survive_and_explicit_number_unsets_no_number():
    current = {"address_number": "42", "address_complement": "Fundos", "neighborhood": "Centro", "address_no_number": False}
    empty_payload = SiteSave(expected_version=1, title="Outro título").model_dump()
    assert merge_address_fields(current, empty_payload, {"title"}) == current
    assert merge_address_fields(current, {**empty_payload, "address_number": None}, {"address_number"})["address_number"] is None
    paused = merge_address_fields(current, {**empty_payload, "address_no_number": True}, {"address_no_number"})
    assert paused["address_number"] is None and paused["address_no_number"] is True
    numbered = merge_address_fields(paused, {**empty_payload, "address_number": "7B"}, {"address_number"})
    assert numbered["address_number"] == "7B" and numbered["address_no_number"] is False
    assert {key: numbered[key] for key in ("address_complement", "neighborhood")} == {"address_complement": "Fundos", "neighborhood": "Centro"}


def test_structured_metadata_uses_the_complete_published_address_and_escapes_markup():
    site = {"title": "Empresa", "address": "Rua <Central>", "address_number": "8-A",
            "address_complement": "Sala </script>", "neighborhood": "Centro", "city": "São Paulo",
            "state": "SP", "postal_code": "01001-000", "address_no_number": False}
    rendered, _ = public_html("<html><head></head><body></body></html>", site, "empresa")
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', rendered).group(1)
    address = json.loads(raw)["address"]
    assert address["streetAddress"] == "Rua <Central>, 8-A — Sala </script> — Centro"
    assert address["addressLocality"] == "São Paulo" and address["postalCode"] == "01001-000"
    assert "<" not in raw
    assert ADDRESS_DEFAULTS == {"address_number": None, "address_complement": None, "neighborhood": None, "address_no_number": False}
