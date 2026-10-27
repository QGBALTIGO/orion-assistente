"""Professional contacts use the same Brazilian phone contract as the panel."""
import pytest
from pydantic import ValidationError

from app.catalog_schemas import Professional
from app.professional_commissions import ProfessionalConfiguration


@pytest.mark.parametrize(("raw", "expected"), [
    ("(67) 99876-5432", "+5567998765432"),
    ("67998765432", "+5567998765432"),
    ("+55 (67) 99876-5432", "+5567998765432"),
    ("5567998765432", "+5567998765432"),
    ("(11) 3456-7890", "+551134567890"),
    ("551134567890", "+551134567890"),
    ("(55) 99123-4567", "+5555991234567"),
    ("  +55 (55) 99876-5432  ", "+5555998765432"),
])
def test_professional_contact_accepts_local_formatted_and_international_br_numbers(raw, expected):
    for model in (Professional, ProfessionalConfiguration):
        assert model(name="Profissional sintético", phone_e164=raw).phone_e164 == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_professional_contact_can_be_left_empty_or_explicitly_cleared(raw):
    for model in (Professional, ProfessionalConfiguration):
        assert model(name="Profissional sintético", phone_e164=raw).phone_e164 is None


@pytest.mark.parametrize("raw", [
    "12345", "(00) 99876-5432", "+1 202 555 0199", "+54 11 99876-5432",
    "(67) 99876-54321", "67998765432abc", "67998765432 ramal 5", 67998765432,
    "(55) 99123-45678", "  (55) 99123-456789  ",
    "55 99123-45678", "55 99123 45678", "55-99123-45678",
])
def test_professional_contact_rejects_incomplete_foreign_or_mixed_input(raw):
    for model in (Professional, ProfessionalConfiguration):
        with pytest.raises(ValidationError):
            model(name="Profissional sintético", phone_e164=raw)


def test_professionals_without_a_contact_keep_the_legacy_contract():
    assert Professional(name="Cadastro existente").phone_e164 is None
    assert ProfessionalConfiguration(name="Cadastro existente").phone_e164 is None
