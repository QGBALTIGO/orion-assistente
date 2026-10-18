"""Observed WhatsApp labels, bounded reads and authenticated name ingress.

Every phone/name is synthetic. No WhatsApp lookup, outbound message or real
database is used in this file; SQL/RLS/concurrent editing have a separate PG gate.
"""
import asyncio
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import qr_ai, qr_api
from app.customer_names import contact_phone, display_customer, useful_name
from app.messaging import extract_messages

PHONE = "5511999990001"
OWN = "5511999990099"
TOKEN = "synthetic-bridge-name-test-secret-1234567890"


@pytest.mark.parametrize(("value", "expected"), [
    ("  Ana   Maria  ", "Ana Maria"), ("José D'Ávila ✂️", "José D'Ávila ✂️"),
    ("Ａｎａ", "Ana"), ("🌻", "🌻"), ("👨‍👩‍👧‍👦", "👨‍👩‍👧‍👦"), ("👩🏽‍🔧", "👩🏽‍🔧"),
    ("Cliente 42", "Cliente 42"), (None, None), ("", None),
    (" CLIENTE   WhatsApp ", None), ("Nome não informado", None),
    ("Contato WhatsApp", None), (PHONE, None), ("+55 (11) 99999-0001", None),
    ("Ana\x00", None), ("Ana\u202e", None), ("Ana\u200b", None),
    ("Ana\u200d", None), ("contact@lid", None), ("user@s.whatsapp.net", None),
    ("A" * 121, None), ([], None),
])
def test_profile_labels_keep_real_unicode_without_inventing_missing_names(value, expected):
    assert useful_name(value) == expected


@pytest.mark.parametrize(("value", "expected"), [
    (PHONE, PHONE), ("+" + PHONE, PHONE), ("14155550100", "14155550100"),
    ("5511876543210", "5511876543210"),
    (PHONE + "@lid", None), (PHONE + "@s.whatsapp.net", None),
    ("123:2@lid", None), ("+55 (11) 99999-0001", None),
    ("05511999990001", None), (12345678901, None), (None, None),
])
def test_only_explicit_pns_are_used_for_contact_matching(value, expected):
    assert contact_phone(value) == expected


def test_presentation_keeps_the_stored_manual_field_and_uses_phone_without_metadata():
    original = {"name": "Nome manual", "phone": "+" + PHONE, "version": 8}
    assert display_customer(original, "Nome do perfil") == {
        **original, "display_name": "Nome manual", "whatsapp_name": "Nome do perfil"}
    assert "display_name" not in original
    placeholder = {**original, "name": "Cliente WhatsApp"}
    assert display_customer(placeholder, "🌻")["display_name"] == "🌻"
    result = display_customer(placeholder)
    assert result["name"] == "Cliente WhatsApp"
    assert result["display_name"] == "+" + PHONE and result["whatsapp_name"] is None
    assert display_customer({"name": "Sem nome", "phone": None})["display_name"] is None


@pytest.mark.asyncio
async def test_name_lookup_is_bounded_to_requested_pns_and_one_server_bound_tenant(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    calls = []

    async def bridge(path, method="GET", payload=None, query=None):
        calls.append((path, method, payload))
        return {"account_phone": OWN, "items": [
            {"phone": PHONE, "display_name": "Nome observado", "name_source": "push_name"},
            {"phone": OWN, "display_name": "Nome próprio", "name_source": "contact_name"},
            {"phone": "5511999990022", "display_name": "Fora do pedido", "name_source": "push_name"},
            {"phone": PHONE + "@lid", "display_name": "LID não é PN", "name_source": "push_name"},
        ]}

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    result = await qr_api.whatsapp_names(tenant, [PHONE, "+" + PHONE, OWN, "123@lid"])
    assert result == {PHONE: "Nome observado"}
    assert calls == [("/internal/contact-names", "POST", {"phones": sorted([PHONE, OWN])})]
    assert await qr_api.whatsapp_names(uuid4(), [PHONE]) == {}
    assert len(calls) == 1
    assert await qr_api.whatsapp_names(tenant, ["55119" + f"{i:08d}" for i in range(251)]) == {}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "invalid", "changed"])
async def test_chunked_lookup_discards_names_if_account_identity_is_not_consistent(monkeypatch, mode):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    phones = ["55119" + f"{i:08d}" for i in range(101)]
    calls = []

    async def bridge(path, method="GET", payload=None, query=None):
        calls.append(payload["phones"])
        account = OWN
        if len(calls) == 2:
            account = {"missing": None, "invalid": OWN + "@lid", "changed": "5511999990088"}[mode]
        return {"account_phone": account, "items": [
            {"phone": payload["phones"][0], "display_name": "Nome observado", "name_source": "contact_name"}]}

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    assert await qr_api.whatsapp_names(tenant, phones) == {}
    assert [len(batch) for batch in calls] == [100, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["old_bridge", "timeout"])
async def test_optional_lookup_failure_preserves_the_customer_page(monkeypatch, failure):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    monkeypatch.setattr(qr_api, "CONTACT_NAME_TIMEOUT", 0.01)

    async def unavailable(*args, **kwargs):
        if failure == "old_bridge":
            raise HTTPException(422, "Old bridge has no name endpoint")
        await asyncio.Event().wait()

    monkeypatch.setattr(qr_api, "_bridge", unavailable)
    rows = [{"name": "Cliente WhatsApp", "phone": "+" + PHONE},
            {"name": "Nome manual", "phone": "+5511999990002"}]
    result = await qr_api.customer_display_names(tenant, rows)
    assert [row["display_name"] for row in result] == ["+" + PHONE, "Nome manual"]
    assert all(row["whatsapp_name"] is None for row in result)
    assert all("display_name" not in row for row in rows)


@pytest.mark.asyncio
async def test_one_failed_chunk_does_not_leave_another_request_running_after_the_timeout(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    monkeypatch.setattr(qr_api, "CONTACT_NAME_TIMEOUT", 0.01)
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def bridge(path, method="GET", payload=None, query=None):
        if len(payload["phones"]) == 100:
            await started.wait()
            raise HTTPException(422, "Old bridge")
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    assert await qr_api.whatsapp_names(tenant, ["55119" + f"{i:08d}" for i in range(101)]) == {}
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_reverse_name_lookup_is_bound_complete_and_cannot_return_self_or_lids(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    calls = []

    async def bridge(path, method="GET", payload=None, query=None):
        calls.append((path, method, payload))
        return {"account_phone": OWN, "phones": [PHONE, PHONE, OWN, PHONE + "@lid", "14155550100"]}

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    assert await qr_api.whatsapp_name_matches(tenant, "Nome observado") == (["+14155550100", "+" + PHONE], OWN)
    assert calls == [("/internal/contact-name-matches", "POST", {"q": "Nome observado"})]
    assert await qr_api.whatsapp_name_matches(uuid4(), "Nome observado") == ([], None)
    assert await qr_api.whatsapp_name_matches(tenant, "(11) 99999-0001") == ([], None)
    assert len(calls) == 1

    async def truncated(*args, **kwargs):
        return {"account_phone": OWN, "phones": [PHONE] * 2001}

    monkeypatch.setattr(qr_api, "_bridge", truncated)
    assert await qr_api.whatsapp_name_matches(tenant, "Nome") == ([], None)


@pytest.mark.asyncio
async def test_chat_names_preserve_international_pns_and_survive_optional_database_failure(monkeypatch):
    from app.catalog_api import Actor
    monkeypatch.setenv("QR_AI_TENANT_ID", str(uuid4()))

    async def bridge(path, *args, **kwargs):
        assert path == "/internal/chats"
        return {"items": [{"phone": "14155550100", "display_name": "Nome internacional", "name_source": "push_name"},
                          {"phone": PHONE, "preview": "Mensagem sintética"}]}

    def unavailable():
        raise RuntimeError("Synthetic optional database failure")

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    monkeypatch.setattr(qr_api, "get_session_factory", unavailable)
    result = await qr_api.qr_chats(Actor("support"))
    assert result["items"][0]["display_name"] == "Nome internacional"
    assert result["items"][0]["phone"] == "14155550100"
    assert result["items"][1]["display_name"] == PHONE
    assert result["items"][1]["preview"] == "Mensagem sintética"


@pytest.mark.asyncio
async def test_private_profile_ingress_respects_auth_allowlist_and_server_tenant(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", PHONE)
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    app = FastAPI(); app.include_router(qr_ai.router)
    writes = []
    turns = []

    async def remember(t, phone, name):
        writes.append((t, phone, name))

    async def run(t, phone, body, namespace, message_id, **kwargs):
        turns.append((t, phone, body))
        if kwargs.get("prepare"):
            await kwargs["prepare"]()
        return {"answer": None, "step": "synthetic_no_send"}

    monkeypatch.setattr(qr_ai, "remember_whatsapp_name", remember)
    monkeypatch.setattr(qr_ai, "run_locked", run)
    message = {"phone": PHONE, "message_id": "synthetic-profile-1", "text": "Oi",
               "profile": {"name": "Nome observado", "source": "push_name"}}
    headers = {"x-orion-bridge-key": TOKEN}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        assert (await client.post("/internal/qr/ai/reply", json=message)).status_code == 401
        wrong = await client.post("/internal/qr/ai/reply", json={**message, "tenant_id": str(uuid4())}, headers=headers)
        assert wrong.status_code == 422
        assert not writes and not turns
        okay = await client.post("/internal/qr/ai/reply", json=message, headers=headers)
        assert okay.status_code == 200 and writes == [(tenant, PHONE, "Nome observado")]
        assert turns == [(tenant, PHONE, "Oi")]
        no_name = {key: value for key, value in message.items() if key != "profile"}
        assert (await client.post("/internal/qr/ai/reply", json=no_name, headers=headers)).status_code == 200
        assert len(writes) == 1
        blocked = await client.post("/internal/qr/ai/reply", json={**message, "phone": "5511999990002"}, headers=headers)
        assert blocked.json()["step"] == "not_allowlisted" and len(writes) == 1
        monkeypatch.setenv("QR_AI_ENABLED", "0")
        disabled = await client.post("/internal/qr/ai/reply", json=message, headers=headers)
        assert disabled.json()["step"] == "disabled" and len(writes) == 1


@pytest.mark.asyncio
async def test_profile_storage_failure_does_not_prevent_the_existing_reply(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", PHONE)
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    app = FastAPI(); app.include_router(qr_ai.router)

    async def failed(*args):
        raise SQLAlchemyError("Synthetic database failure")

    async def run(*args, **kwargs):
        await kwargs["prepare"]()
        return {"answer": "Resposta sintética normal", "step": "information"}

    monkeypatch.setattr(qr_ai, "remember_whatsapp_name", failed)
    monkeypatch.setattr(qr_ai, "run_locked", run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post("/internal/qr/ai/reply", headers={"x-orion-bridge-key": TOKEN}, json={
            "phone": PHONE, "message_id": "synthetic-profile-failure", "text": "Oi",
            "profile": {"name": "Nome observado", "source": "contact_name"}})
    assert response.status_code == 200 and response.json()["answer"] == "Resposta sintética normal"
    assert "database" not in response.text


@pytest.mark.asyncio
async def test_private_name_batch_cannot_accept_tenant_lid_unknown_source_or_missing_authorization(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("QR_AI_TENANT_ID", str(uuid4()))
    app = FastAPI(); app.include_router(qr_api.router)

    def forbidden():
        raise AssertionError("Rejected metadata cannot open a database session")

    monkeypatch.setattr(qr_api, "get_session_factory", forbidden)
    payload = {"account_phone": OWN, "items": [{"phone": PHONE, "name": "Nome observado", "source": "push_name"}]}
    headers = {"x-orion-bridge-key": TOKEN}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        assert (await client.post("/internal/qr/contact-names", json=payload)).status_code == 401
        assert (await client.post("/internal/qr/contact-names", json={**payload, "tenant_id": str(uuid4())}, headers=headers)).status_code == 422
        for field, value in (("phone", PHONE + "@lid"), ("source", "message_body")):
            bad = {**payload, "items": [{**payload["items"][0], field: value}]}
            assert (await client.post("/internal/qr/contact-names", json=bad, headers=headers)).status_code == 422
        assert (await client.post("/internal/qr/contact-names", json={**payload, "items": payload["items"] * 101}, headers=headers)).status_code == 422
        monkeypatch.delenv("QR_AI_TENANT_ID")
        assert (await client.post("/internal/qr/contact-names", json=payload, headers=headers)).status_code == 503


def test_meta_profile_is_matched_by_wa_id_and_does_not_change_legacy_message_acceptance():
    def payload(sender, contacts):
        return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "synthetic-channel"}, "contacts": contacts,
            "messages": [{"from": sender, "id": "synthetic-event", "type": "text", "text": {"body": "Oi"}}]
        }}]}]}

    contacts = [{"wa_id": "5511999990002", "profile": {"name": "Outro contato"}},
                {"wa_id": PHONE, "profile": {"name": " Nome   observado "}}]
    assert extract_messages(payload(PHONE, contacts))[0]["whatsapp_name"] == "Nome observado"
    assert "whatsapp_name" not in extract_messages(payload("5511999990003", contacts))[0]
    assert "whatsapp_name" not in extract_messages(payload(PHONE, [{"wa_id": PHONE, "profile": {"name": "Cliente WhatsApp"}}]))[0]
    # Metadata validation is stricter than the existing webhook's text ingress;
    # this change must not silently drop a previously accepted message.
    assert extract_messages(payload("55123", contacts))[0]["text"] == "Oi"
    assert extract_messages(payload(PHONE, {"unexpected": "object"}))[0]["text"] == "Oi"
