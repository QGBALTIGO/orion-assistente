"""Audience parsing, confidentiality, scoped previews and bounded API inputs."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_audience as api
from app import subscription_audience_schemas as schemas
from app import subscription_audience_import as parser
from app import subscription_audience_store as store
from app.catalog_api import Actor, authenticate


@pytest.mark.parametrize(("value", "country", "expected"), [
    ("(67) 99999-1234", "BR", "+5567999991234"),
    ("55 67 99999-1234", "BR", "+5567999991234"),
    ("+595 (991) 234-567", "BR", "+595991234567"),
    ("00595 991234567", "BR", "+595991234567"),
    ("0991234567", "PY", "+595991234567"),
    ("(202) 555-0123", "US", "+12025550123"),
    ("12025550123", "US", "+12025550123"),
    ("+44 20 7946 0123", "BR", "+442079460123"),
    ("+351 912 345 678", "BR", "+351912345678"),
])
def test_multiple_phone_presentations_keep_the_country(value, country, expected):
    assert schemas.normalize_phone(value, country) == expected


@pytest.mark.parametrize("value", ["=55123456789", "123456789@lid", "5511999999999@s.whatsapp.net",
    "https://wa.me/5567999991234", "+5567999991234 ramal 2", "+0123456789", "5.567999991234E+12", "１２３４５６７８９", "+123"])
def test_opaque_or_spreadsheet_identifiers_never_become_phone_numbers(value):
    with pytest.raises(ValueError):
        schemas.normalize_phone(value)


def test_bom_semicolon_quotes_multiline_and_precise_invalid_line_counts():
    result = parser.parse('\ufeffTelefone;Nome\r\n+55 (67) 99999-1234;"Ana; Souza"\r\n'
        '5567999991234;Duplicada\r\n+595991234567;"Nome\r\ncomposto"\r\nerrado;Inválido\r\n\r\n')
    assert result["counts"] == {"total": 4, "valid": 3, "invalid": 1, "duplicates": 1, "blank": 1, "unique": 2}
    assert result["contacts"][0]["name"] == "Ana; Souza"
    assert result["contacts"][1]["name"] == "Nome composto"
    assert result["errors"][0]["line"] == 6
    assert result["fatal"] is False


@pytest.mark.parametrize("content", [
    'Nome,Telefone\n"Ana, Souza",+5567999991234\n',
    '"Ana, Souza",+5567999991234\n',
    '+5567999991234,"Ana, Souza"\n',
    'telefone\tnome\n+5567999991234\tAna, Souza\n',
])
def test_two_column_csv_txt_orders_and_tab_are_supported(content):
    result = parser.parse(content)
    assert result["counts"]["valid"] == 1
    assert result["contacts"][0]["name"] == "Ana, Souza"


def test_quoted_csv_failure_cannot_commit_partial_prefix():
    value = parser.parse('Telefone,Nome\n+5567999991234,Ana\n+5567999991235,"incompleto')
    assert value["fatal"] is True
    assert value["counts"]["valid"] == 1 and value["counts"]["invalid"] == 1
    assert value["errors"][0]["code"] == "malformed_csv"


@pytest.mark.parametrize("count", [4211, 5001, 20000])
def test_large_files_are_all_parsed_without_page_or_group_truncation(count):
    content = "Telefone,Nome\n" + "".join(f"+5567{900000000 + index},Pessoa {index}\n" for index in range(count))
    value = parser.parse(content)
    assert len(value["contacts"]) == count
    assert value["counts"]["valid"] == count
    assert value["counts"]["invalid"] == 0
    assert value["contacts"][-1]["name"] == f"Pessoa {count - 1}"
    assert len(value["sample"]) == 50


def test_oversize_file_fails_whole_import_explicitly():
    content = "".join(f"+5567{900000000 + index},Pessoa\n" for index in range(20001))
    with pytest.raises(ValueError, match="20.000"):
        parser.parse(content)


def test_unknown_extra_columns_are_rejected_instead_of_discarded():
    result = parser.parse("Telefone,Nome,Senha\n+5567999991234,Ana,segredo")
    assert result["fatal"] and result["contacts"] == []
    assert "segredo" not in json.dumps(result)


def test_contact_requires_identity_and_password_clear_is_explicit():
    with pytest.raises(ValidationError):
        schemas.Contact(name="Contato")
    assert schemas.Contact(name="Telegram", telegram="@PessoaTeste").telegram == "@pessoateste"
    with pytest.raises(ValidationError):
        schemas.Contact(name="Ana", phone="67999991234", service_password="segredo", clear_password=True)
    with pytest.raises(ValidationError):
        schemas.Contact(name="Ana", phone="67999991234", price_cents=True)
    assert schemas.UpdateGroup(idempotency_key=uuid4(), expected_version=1, name="Nome").contact_keys is None


def test_detail_list_and_export_never_return_service_password():
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    row = {"key": "phone:5567999991234", "name": "=HYPERLINK(1)", "phone": "5567999991234", "telegram": None,
        "email": None, "notes": "observação", "chatbot_enabled": True, "audio_transcription_enabled": False,
        "plan_name": "Mensal", "price_cents": 2590, "due_date": None, "service_username": "login-sintetico",
        "service_password_encrypted": "ciphertext-never-return", "service_password": "plaintext-never-return",
        "password_configured": True, "source_version": 1, "source_updated": now}
    dto = store.contact_dto(row, [])
    assert dto["password_configured"] is True
    assert "password" not in {key for key in dto if key != "password_configured"}
    encoded = json.dumps(dto)
    assert "ciphertext-never-return" not in encoded and "plaintext-never-return" not in encoded
    export = parser.csv_document([dto])
    assert "'=HYPERLINK(1)" in export and "'+5567999991234" in export
    assert "login-sintetico" not in export and "observação" not in export


def test_preview_binding_tamper_expiry_and_wrong_purpose(monkeypatch):
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(store.channels, "_cipher", lambda: cipher)
    tenant, account = uuid4(), uuid4()
    actor = Actor("member", account_id=account)
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    data = {"purpose": store.PURPOSE, "tenant_id": str(tenant), "actor_account_id": str(account),
            "issued_at": int(now.timestamp()), "expires_at": int(now.timestamp()) + schemas.PREVIEW_SECONDS,
            "contacts": [{"name": "Ana", "phone_e164": "+5567999991234"}]}
    token = cipher.encrypt(json.dumps(data).encode()).decode()
    assert store.decode_preview(token, tenant, actor, now)["contacts"]
    for other_tenant, other_actor in [(uuid4(), actor), (tenant, Actor("member", account_id=uuid4()))]:
        with pytest.raises(HTTPException) as error:
            store.decode_preview(token, other_tenant, other_actor, now)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        store.decode_preview(token, tenant, actor, now + timedelta(seconds=schemas.PREVIEW_SECONDS))
    assert error.value.status_code == 410
    with pytest.raises(HTTPException):
        store.decode_preview(token[:30] + "bad" + token[33:], tenant, actor, now)


@pytest.mark.asyncio
async def test_owner_is_checked_before_secret_body_validation(monkeypatch):
    @asynccontextmanager
    async def denied(*args, **kwargs):
        raise HTTPException(403, "forbidden")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: Actor("member", account_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        reply = await client.post(api.BASE.format(tenant_id=uuid4()) + "/contacts", content=b'{"service_password":"private"')
    assert reply.status_code == 403 and "private" not in reply.text


def test_ui_fixture_matches_the_real_contact_projection():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/subscription_audience_contract.json").read_text())
    assert len(fixture["contacts"]["items"]) == 2
    for contact in fixture["contacts"]["items"]:
        assert schemas.key(contact["key"]) == contact["key"]
        assert len(contact["version"]) == 64
        assert "service_password" not in contact
