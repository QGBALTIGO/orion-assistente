"""Real PostgreSQL name enrichment, optimistic editing and tenant isolation.

Synthetic customers only. The bridge and Redis enqueue are intercepted; these
tests do not send a message or read the user's WhatsApp address book.
"""
import asyncio
from contextlib import suppress
import json
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import customer_names, qr_api, qr_customer
from app.database import tenant_transaction
from app.whatsapp_api import router as whatsapp_router
from test_management import management_db, customer
from test_whatsapp import make_payload, signed

pytestmark = pytest.mark.integration
PHONE = "5511912345678"
OWN = "5511999990099"
TOKEN = "synthetic-bridge-name-test-secret-1234567890"


@pytest_asyncio.fixture
async def names_db(management_db, monkeypatch):
    d = management_db
    monkeypatch.setenv("QR_AI_TENANT_ID", str(d["a"]))
    monkeypatch.setenv("QR_BRIDGE_TOKEN", TOKEN)
    d["app"].include_router(qr_api.router)
    d["app"].include_router(whatsapp_router)

    async def no_external_names(path, method="GET", payload=None, query=None):
        if path == "/internal/contact-name-matches":
            return {"account_phone": OWN, "phones": []}
        assert path == "/internal/contact-names" and method == "POST"
        return {"account_phone": OWN, "items": []}

    monkeypatch.setattr(qr_api, "_bridge", no_external_names)
    d["name_headers"] = {"x-orion-bridge-key": TOKEN}
    d["factory"] = async_sessionmaker(d["runtime"], expire_on_commit=False)
    return d


async def row(d, identity):
    async with d["migrator"].connect() as db:
        return dict((await db.execute(text("SELECT * FROM customers WHERE id=:id"),
                                      {"id": UUID(identity)})).mappings().one())


async def name_batch(d, items, **extra):
    return await d["client"].post("/internal/qr/contact-names", headers=d["name_headers"],
        json={"account_phone": OWN, "items": items, **extra})


@pytest.mark.asyncio
async def test_profile_fills_placeholder_once_and_real_version_prevents_stale_manual_edit(names_db):
    d = names_db
    current = await customer(d, name="Cliente WhatsApp", phone=PHONE, notes="Observação manual preservada")
    other = await customer(d, other=True, name="Cliente WhatsApp", phone=PHONE)
    before = await row(d, current["id"])
    assert await customer_names.remember_whatsapp_name(d["a"], PHONE, "Nome observado") is True
    enriched = await row(d, current["id"])
    assert enriched["name"] == "Nome observado" and enriched["version"] == 2
    assert enriched["updated_at"] > before["updated_at"]
    assert enriched["notes"] == before["notes"] and enriched["whatsapp_e164"] == before["whatsapp_e164"]
    assert await customer_names.remember_whatsapp_name(d["a"], PHONE, "Outro perfil") is False
    assert await row(d, current["id"]) == enriched
    assert (await row(d, other["id"]))["name"] == "Cliente WhatsApp"
    stale = await d["client"].patch(d["base"] + "/customers/" + current["id"],
                                   json={"name": "Edição antiga", "expected_version": 1})
    assert stale.status_code == 409
    await qr_customer.remember_name(d["a"], PHONE, "Nome declarado pelo cliente")
    declared = await row(d, current["id"])
    assert declared["name"] == "Nome declarado pelo cliente" and declared["version"] == 3
    assert declared["updated_at"] > enriched["updated_at"]
    await qr_customer.remember_name(d["a"], PHONE, "Nome declarado pelo cliente")
    assert await row(d, current["id"]) == declared
    stale = await d["client"].patch(d["base"] + "/customers/" + current["id"],
                                   json={"name": "Edição anterior à declaração", "expected_version": 2})
    assert stale.status_code == 409
    manual = await d["client"].patch(d["base"] + "/customers/" + current["id"],
                                    json={"name": "Nome manual escolhido", "expected_version": 3})
    assert manual.status_code == 200
    assert await customer_names.remember_whatsapp_name(d["a"], PHONE, "Metadado posterior") is False
    assert (await row(d, current["id"]))["name"] == "Nome manual escolhido"


@pytest.mark.asyncio
async def test_name_batch_repairs_existing_only_without_reviving_or_crossing_tenants(names_db):
    d = names_db
    placeholder = await customer(d, name="CONTATO do WhatsApp", phone=PHONE)
    manual = await customer(d, name="Nome manual útil", phone="5511912345679")
    inactive = await customer(d, name="Cliente WhatsApp", phone="5511912345680", active=False)
    deleted = await customer(d, name="Cliente WhatsApp", phone="5511912345681")
    own = await customer(d, name="Cliente WhatsApp", phone=OWN)
    other = await customer(d, other=True, name="Cliente WhatsApp", phone=PHONE)
    response = await d["client"].request("DELETE", d["base"] + "/customers/" + deleted["id"], json={"expected_version": 1})
    assert response.status_code == 200
    deleted_before = await row(d, deleted["id"])
    items = [{"phone": phone, "name": name, "source": "push_name"} for phone, name in (
        (PHONE, "Nome importado"), ("5511912345679", "Perfil diferente"),
        ("5511912345680", "Nome inativo"), ("5511912345681", "Nome excluído"),
        (OWN, "Conta própria"), ("5511912345682", "Contato sem cadastro"),
        ("14155550100", "Contato internacional sem cadastro"),
    )]
    first = await name_batch(d, items)
    assert first.status_code == 200 and first.json() == {"ok": True, "updated": 2}
    assert (await name_batch(d, items)).json() == {"ok": True, "updated": 0}
    assert (await row(d, placeholder["id"]))["name"] == "Nome importado"
    assert (await row(d, manual["id"]))["name"] == "Nome manual útil"
    inactive_now = await row(d, inactive["id"])
    assert inactive_now["name"] == "Nome inativo" and inactive_now["active"] is False
    assert inactive_now["version"] == 2
    assert await row(d, deleted["id"]) == deleted_before
    await qr_customer.remember_name(d["a"], "5511912345681", "Declaração em cadastro excluído")
    assert await row(d, deleted["id"]) == deleted_before
    assert (await row(d, own["id"]))["name"] == "Cliente WhatsApp"
    assert (await row(d, other["id"]))["name"] == "Cliente WhatsApp"
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 5
    rejected = await name_batch(d, items, tenant_id=str(d["b"]))
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_absent_invalid_or_lid_name_never_creates_a_customer_and_rls_rejects_foreign_insert(names_db):
    d = names_db
    for phone, name in ((PHONE, "Cliente WhatsApp"), (PHONE, ""),
                        (PHONE + "@lid", "Nome não associável"), ("14155550100", "Nome internacional")):
        assert await customer_names.remember_whatsapp_name(d["a"], phone, name) is False
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 0
    async with d["factory"]() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, d["a"]):
                await customer_names.merge_whatsapp_name(db, d["b"], PHONE, "Tentativa fora do tenant")
    assert await customer_names.remember_whatsapp_name(d["a"], PHONE, "🌻") is True
    async with d["migrator"].connect() as db:
        actual = (await db.execute(text("SELECT name,version FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).one()
        assert tuple(actual) == ("🌻", 1)


@pytest.mark.asyncio
async def test_manual_edit_holding_the_row_wins_over_concurrent_profile_enrichment(names_db):
    d = names_db
    item = await customer(d, name="Cliente WhatsApp", phone=PHONE)
    attempted = asyncio.Event()

    async def delayed_metadata():
        async with d["factory"]() as db:
            async with tenant_transaction(db, d["a"]):
                attempted.set()
                return await customer_names.merge_whatsapp_name(db, d["a"], PHONE, "Perfil concorrente", create=False)

    task = None
    try:
        async with d["factory"]() as db:
            async with tenant_transaction(db, d["a"]):
                await db.execute(text("UPDATE customers SET name='Nome manual simultâneo',version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id"),
                                 {"tenant": d["a"], "id": UUID(item["id"])})
                task = asyncio.create_task(delayed_metadata())
                await asyncio.wait_for(attempted.wait(), 2)
                # The real row lock is still held by the uncommitted manual
                # edit. Shielding keeps the competing operation alive.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.05)
        assert await asyncio.wait_for(task, 3) is False
        actual = await row(d, item["id"])
        assert actual["name"] == "Nome manual simultâneo" and actual["version"] == 2
    finally:
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_customer_overlay_is_readonly_and_private_ingress_makes_search_detail_export_consistent(names_db, monkeypatch):
    d = names_db
    item = await customer(d, name="Cliente WhatsApp", phone=PHONE)
    manual = await customer(d, name="Nome manual", phone="5511912345679")
    foreign = await customer(d, other=True, name="Cliente WhatsApp", phone=PHONE)
    before = await row(d, item["id"])
    calls = []

    async def bridge(path, method="GET", payload=None, query=None):
        calls.append((path, method, payload))
        if path == "/internal/contact-name-matches":
            return {"account_phone": OWN, "phones": [PHONE]}
        if path == "/internal/chats":
            return {"items": [{"phone": PHONE, "display_name": "Nome importado", "name_source": "push_name"},
                              {"phone": "5511912345679", "display_name": "Nome do perfil", "name_source": "push_name"}]}
        assert path == "/internal/contact-names" and method == "POST"
        return {"account_phone": OWN, "items": [
            {"phone": PHONE, "display_name": "Nome importado", "name_source": "push_name"},
            {"phone": "5511912345679", "display_name": "Nome do perfil", "name_source": "contact_name"},
        ]}

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    listed = (await d["client"].get(d["base"] + "/customers")).json()
    by_id = {entry["id"]: entry for entry in listed["items"]}
    assert by_id[item["id"]]["display_name"] == "Nome importado"
    assert by_id[item["id"]]["name"] == "Cliente WhatsApp"
    assert by_id[manual["id"]]["display_name"] == "Nome manual"
    detail = (await d["client"].get(d["base"] + "/customers/" + item["id"])).json()
    assert detail["display_name"] == "Nome importado"
    assert await row(d, item["id"]) == before
    cache_search = (await d["client"].get(d["base"] + "/customers", params={"q": "Nome importado"})).json()
    assert cache_search["total"] == 1 and cache_search["items"][0]["id"] == item["id"]
    cache_export = await d["client"].get(d["base"] + "/customers/export.csv", params={"q": "Nome importado"})
    assert cache_export.status_code == 200 and "Nome importado" in cache_export.text
    assert await row(d, item["id"]) == before
    call_count = len(calls)
    other = (await d["client"].get(d["other_base"] + "/customers")).json()["items"][0]
    assert other["id"] == foreign["id"] and other["display_name"] == "+" + PHONE
    assert other["whatsapp_name"] is None and len(calls) == call_count
    received = await name_batch(d, [{"phone": PHONE, "name": "Nome importado", "source": "push_name"}])
    assert received.json() == {"ok": True, "updated": 1}
    # The database name now supports the existing full filtered pagination and
    # exports. No page-local search or directory-wide bridge scan is substituted.
    for query in ("Nome importado", "(11) 91234-5678", "+55 (11) 91234-5678"):
        found = (await d["client"].get(d["base"] + "/customers", params={"q": query})).json()
        assert found["total"] == 1 and found["items"][0]["id"] == item["id"]
    exported = await d["client"].get(d["base"] + "/customers/export.csv", params={"q": "Nome importado"})
    assert exported.status_code == 200 and "Nome importado" in exported.text and PHONE in exported.text
    chat = await d["client"].get("/api/v1/qr/chats")
    assert chat.status_code == 200
    assert [entry["display_name"] for entry in chat.json()["items"]] == ["Nome importado", "Nome manual"]
    assert all(entry["name_source"] == "customer" for entry in chat.json()["items"])


@pytest.mark.asyncio
async def test_cache_name_search_keeps_sql_status_scope_totals_and_pagination_without_writes(names_db, monkeypatch):
    d = names_db
    active = await customer(d, name="Cliente WhatsApp", phone=PHONE)
    inactive = await customer(d, name="Contato WhatsApp", phone="5511912345679", active=False)
    deleted = await customer(d, name="Sem nome", phone="5511912345680")
    await customer(d, other=True, name="Cliente WhatsApp", phone=PHONE)
    await customer(d, other=True, name="Cliente WhatsApp", phone="5511912345681")
    assert (await d["client"].request("DELETE", d["base"] + "/customers/" + deleted["id"], json={"expected_version": 1})).status_code == 200
    before = {item["id"]: await row(d, item["id"]) for item in (active, inactive, deleted)}
    calls = []
    matches = [PHONE, "5511912345679", "5511912345680", "5511912345681", "5511912345682"]

    async def bridge(path, method="GET", payload=None, query=None):
        calls.append(path)
        if path == "/internal/contact-name-matches":
            assert payload == {"q": "Ana do perfil"}
            return {"account_phone": OWN, "phones": matches}
        assert path == "/internal/contact-names"
        return {"account_phone": OWN, "items": [{"phone": phone, "display_name": "Ana do perfil", "name_source": "push_name"}
                                                for phone in payload["phones"]]}

    monkeypatch.setattr(qr_api, "_bridge", bridge)
    query = {"q": "Ana do perfil", "status": "active", "limit": 1}
    filtered = (await d["client"].get(d["base"] + "/customers", params=query)).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == active["id"]
    assert filtered["items"][0]["display_name"] == "Ana do perfil"
    empty_page = (await d["client"].get(d["base"] + "/customers", params={**query, "offset": 1})).json()
    assert empty_page["total"] == 1 and empty_page["items"] == []
    pages = [(await d["client"].get(d["base"] + "/customers", params={"q": "Ana do perfil", "limit": 1, "offset": offset})).json()
             for offset in (0, 1)]
    assert all(page["total"] == 2 for page in pages)
    assert {page["items"][0]["id"] for page in pages} == {active["id"], inactive["id"]}
    count = len(calls)
    other = (await d["client"].get(d["other_base"] + "/customers", params={"q": "Ana do perfil"})).json()
    assert other["total"] == 0 and other["items"] == [] and len(calls) == count
    assert {identity: await row(d, identity) for identity in before} == before


@pytest.mark.asyncio
async def test_signed_meta_profile_is_persisted_atomically_for_the_channel_tenant_only(names_db, monkeypatch):
    import arq
    d = names_db
    secret = "synthetic-meta-name-signing-secret"
    monkeypatch.setenv("META_APP_SECRET", secret)
    first = await customer(d, name="Cliente WhatsApp", phone=PHONE)
    second = await customer(d, other=True, name="Nome manual de outra empresa", phone=PHONE)
    channels = [(d["a"], uuid4(), "name-meta-" + uuid4().hex), (d["b"], uuid4(), "name-meta-" + uuid4().hex)]
    queued = []

    class Queue:
        async def enqueue_job(self, *args, **kwargs):
            queued.append((args, kwargs))
        async def aclose(self):
            pass

    async def pool(*args, **kwargs):
        return Queue()

    monkeypatch.setattr(arq, "create_pool", pool)
    try:
        async with d["migrator"].begin() as db:
            for tenant, channel, number in channels:
                await db.execute(text("INSERT INTO whatsapp_channels (id,tenant_id,phone_number_id,business_number,status) VALUES (:id,:tenant,:number,:phone,'testing')"),
                                 {"id": channel, "tenant": tenant, "number": number, "phone": "+" + OWN})
        for index in (0, 1, 0):
            tenant, channel, number = channels[index]
            payload = make_payload(number, "synthetic-name-event-" + str(index), sender=PHONE)
            payload["entry"][0]["changes"][0]["value"]["contacts"] = [
                {"wa_id": "5511999990088", "profile": {"name": "Não corresponde ao remetente"}},
                {"wa_id": PHONE, "profile": {"name": "Nome Meta observado"}},
            ]
            body = json.dumps(payload).encode()
            response = await d["client"].post("/webhooks/whatsapp", content=body, headers=signed(body, secret))
            assert response.status_code == 200
        assert (await row(d, first["id"]))["name"] == "Nome Meta observado"
        assert (await row(d, first["id"]))["version"] == 2
        assert (await row(d, second["id"]))["name"] == "Nome manual de outra empresa"
        assert len(queued) == 2
        async with d["migrator"].connect() as db:
            events = (await db.execute(text("SELECT tenant_id,status FROM inbound_events WHERE tenant_id IN (:a,:b)"),
                                      {"a": d["a"], "b": d["b"]})).all()
            assert set(events) == {(d["a"], "pending"), (d["b"], "pending")}
            assert (await db.execute(text("SELECT count(*) FROM message_outbox WHERE tenant_id IN (:a,:b)"),
                                     {"a": d["a"], "b": d["b"]})).scalar_one() == 0
    finally:
        async with d["migrator"].begin() as db:
            for table in ("message_outbox", "inbound_events", "whatsapp_channels"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})
