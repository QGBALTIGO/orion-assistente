"""Manual offer preparation and delivery with synthetic providers only."""
import asyncio
import concurrent.futures
import io
import json
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from affiliate_pilot.engine import Engine
from affiliate_pilot.main import create_app
from affiliate_pilot.offers import card_font, card_png, caption, price_label, wrap_card_text
from affiliate_pilot.phrase import clean_phrase, initial_phrase, phrase_suggestions
from affiliate_pilot.providers import ProviderError
from affiliate_pilot.store import Store


BASE = "http://testserver"
ORIGIN = {"Origin": BASE}
PASSWORD = "synthetic-password-for-manual-offer-tests"
OFFER = "1" * 32
TEXT_TEMPLATE = "2" * 32
DESTINATIONS = [str(number) * 32 for number in (3, 4, 5)]
OWN_LINK = "https://www.amazon.com.br/dp/B0C1234567?tag=synthetic-owner-20"
TEMPLATE = {"name": "Texto de teste", "brand": "Ótimas opções", "layout": "simple", "media": "text",
            "text": "{produto}\n{frase_ia}\n{preco}\n{link}", "style": "plain"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("AFFILIATE_GROQ_API_KEY", raising=False)
    monkeypatch.delenv("AFFILIATE_PROMOTECH_BINDING", raising=False)

    async def blocked(*args, **kwargs):
        raise AssertionError("Manual offer tests must never access an external service")

    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "manual.sqlite3", Fernet.generate_key().decode())
    for username in ("first", "second"):
        db.bootstrap(username, PASSWORD, username.title())
    return db


def company(store, username="first"):
    return store.user(store.authenticate(username, PASSWORD))["id"]


def login(client, username="first"):
    assert client.post("/api/login", headers=ORIGIN,
                       json={"username": username, "password": PASSWORD}).status_code == 200


def prepared_offer(**changes):
    phrase, source = initial_phrase({"identity": "amazon:synthetic-product"})
    return {"identity": "amazon:synthetic-product", "platform": "amazon", "retailer_name": "Amazon",
            "title": "Lâmpada para leitura — edição café", "price": "39.90", "price_max": "39.90",
            "currency": "BRL", "image": None, "catalog_url": "https://www.amazon.com.br/dp/B0C1234567",
            "affiliate_url": OWN_LINK, "affiliate_status": "provided", "link_source": "Informado pelo usuário",
            "phrase": phrase, "phrase_source": source, **changes}


def records(store, tenant):
    offer = store.put(tenant, "offer", OFFER, prepared_offer())
    template = store.put(tenant, "template", TEXT_TEMPLATE, TEMPLATE)
    version = store.save_connection(tenant, "telegram",
                                    {"token": "synthetic-only", "bot_id": 99, "operator": 100}, {"status": "connected"})
    destinations = [store.put(tenant, "destination", ident,
                              {"name": f"Destino {index+1}", "mode": "telegram", "chat_id": -100001-index,
                               "verified": True, "test": False, "connection_version": version})
                    for index, ident in enumerate(DESTINATIONS)]
    return offer, template, destinations, version


def send_payload(client, destinations=None, request_id=None):
    preview = client.get(f"/api/offers/{OFFER}/preview?template={TEXT_TEMPLATE}")
    assert preview.status_code == 200, preview.text
    return {"template": TEXT_TEMPLATE, "destinations": destinations or DESTINATIONS[:2],
            "request_id": request_id or uuid4().hex, "preview_token": preview.json()["send"]["preview_token"]}


def submit(client, payload):
    return client.post(f"/api/offers/{OFFER}/send", headers=ORIGIN, json=payload)


def test_prepare_partial_product_and_complete_preview_without_creating_jobs(store, monkeypatch):
    from affiliate_pilot import marketplaces
    tenant = company(store)
    records(store, tenant)

    async def extract(url, *, shopee=None):
        assert url == "https://www.amazon.com.br/dp/B0C1234567"
        return prepared_offer(price=None, price_max=None, currency=None,
                              affiliate_url=None, affiliate_status="not_converted",
                              extraction={"status": "partial", "missing": ["price", "image"]})

    monkeypatch.setattr(marketplaces, "extract_offer", extract)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        result = client.post("/api/offers", headers=ORIGIN,
                             json={"url": "https://www.amazon.com.br/dp/B0C1234567"})
        assert result.status_code == 200
        draft = result.json()
        assert draft["title"] == "Lâmpada para leitura — edição café" and draft["price"] is None
        assert draft["affiliate_status"] == "required" and draft["phrase"]
        path = f"/api/offers/{draft['id']}"
        preview = client.get(path + f"/preview?template={TEXT_TEMPLATE}").json()
        assert preview["send"]["enabled"] is False
        assert len(preview["suggestions"]) == 5
        edited = client.put(path, headers=ORIGIN,
                            json={"title": "Lâmpada para leitura", "price": "39.90", "affiliate_url": OWN_LINK})
        assert edited.status_code == 200
        assert edited.json()["affiliate_status"] == "provided"
        assert edited.json()["currency"] == "BRL"
        assert edited.json()["tracking_status"] == "unavailable"
        preview = client.get(path + f"/preview?template={TEXT_TEMPLATE}").json()
        assert preview["send"]["enabled"] is True
        assert OWN_LINK in preview["text"]
        assert all(item["eligible"] for item in preview["destinations"])
        assert store.history(tenant) == [], "prepare/edit/preview must never publish or enqueue"


@pytest.mark.parametrize("status,code,message", [
    (503, "unavailable", "A loja não disponibilizou essa página."),
    (404, "unavailable", "A página do produto não foi encontrada."),
    (403, "blocked", "A loja restringiu a leitura automática."),
])
def test_empty_extraction_requires_manual_choice_and_never_saves_on_retry(store, monkeypatch, status, code, message):
    from affiliate_pilot import marketplaces
    tenant = company(store)
    records(store, tenant)
    original = store.list(tenant, "offer")
    url = "https://amzn.to/synthetic-product"

    async def extract(value, *, shopee=None):
        assert value == url
        return prepared_offer(title=None, price=None, price_max=None, currency=None, image=None,
                              affiliate_url=None, affiliate_status="not_converted", source_url=value,
                              extraction={"status": "blocked" if status == 403 else "partial", "code": code,
                                          "reason": message, "http_status": status,
                                          "missing": ["title", "price", "currency", "image", "rating"]})

    monkeypatch.setattr(marketplaces, "extract_offer", extract)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        for _ in range(2):
            result = client.post("/api/offers", headers=ORIGIN, json={"url": url, "note": "Minha observação"})
            assert result.status_code == 422
            detail = result.json()["detail"]
            assert detail["code"] == "PRODUCT_EXTRACTION_EMPTY"
            assert detail["message"] == message and detail["source_url"] == url
            assert detail["manual_available"] is True
            assert detail["extraction"]["http_status"] == status
            assert store.list(tenant, "offer") == original
            assert store.history(tenant) == []

        manual = client.post("/api/offers", headers=ORIGIN,
                             json={"url": url, "note": "Minha observação", "allow_manual": True})
        assert manual.status_code == 200
        draft = manual.json()
        assert draft["title"] is None and draft["affiliate_url"] is None
        assert draft["extraction"]["manual_requested"] is True
        assert draft["note"] == "Minha observação" and draft["source_url"] == url
        assert len(store.list(tenant, "offer")) == len(original) + 1
        assert store.get(tenant, "offer", OFFER) == original[0]
        assert store.history(tenant) == []


def test_manual_choice_does_not_bypass_url_validation(store, monkeypatch):
    from affiliate_pilot import marketplaces
    tenant = company(store)

    async def extract(*args, **kwargs):
        raise ProviderError("retailer", "Essa loja ainda não está na lista de lojas aceitas.")

    monkeypatch.setattr(marketplaces, "extract_offer", extract)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        result = client.post("/api/offers", headers=ORIGIN,
                             json={"url": "https://unsupported.example/product", "allow_manual": True})
        assert result.status_code == 422
        assert result.json()["code"] == "retailer"
        assert store.list(tenant, "offer") == []
        assert store.history(tenant) == []


def test_batch_has_exact_selected_destinations_and_retries_keep_same_jobs(store):
    tenant = company(store)
    records(store, tenant)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        payload = send_payload(client)
        first = submit(client, payload)
        assert first.status_code == 200, first.text
        batch = first.json()
        assert batch["created"] is True and len(batch["jobs"]) == 2
        assert {job["destination"] for job in batch["jobs"]} == set(DESTINATIONS[:2])
        assert all(job["status"] == "queued" for job in batch["jobs"])
        assert client.put(f"/api/offers/{OFFER}/phrase", headers=ORIGIN,
                          json={"text": "Uma nova frase que ainda não foi enviada."}).status_code == 200
        replay = submit(client, payload).json()
        assert replay["created"] is False and replay["jobs"] == batch["jobs"]
        assert len(store.history(tenant)) == 2
        assert submit(client, {**payload, "destinations": [DESTINATIONS[2]]}).status_code == 409
        status = client.get(f"/api/send-batches/{payload['request_id']}").json()
        assert status["jobs"] == batch["jobs"]
        login(client, "second")
        assert client.get(f"/api/send-batches/{payload['request_id']}").status_code == 404


def test_concurrent_batch_retries_are_atomic(store):
    tenant = company(store)
    offer, template, destinations, _ = records(store, tenant)
    request_id = uuid4().hex
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.enqueue_manual(tenant, request_id, "same-intent",
                                                                offer, template, destinations[:2]), range(12)))
    assert sum(created for _, created in results) == 1
    assert len({tuple(job["id"] for job in batch["jobs"]) for batch, _ in results}) == 1
    assert len(store.history(tenant)) == 2


@pytest.mark.parametrize("selection,expected", [([], 422), ([DESTINATIONS[0]] * 2, 422),
                                               ([DESTINATIONS[0], "f" * 32], 404)])
def test_invalid_selection_never_partially_enqueues(store, selection, expected):
    tenant = company(store)
    records(store, tenant)
    other = company(store, "second")
    store.put(other, "destination", "f" * 32, {"name": "Other account", "mode": "telegram", "verified": True})
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        payload = {**send_payload(client), "destinations": selection}
        assert submit(client, payload).status_code == expected
        assert store.history(tenant) == []


def test_changed_preview_or_unverified_selected_destination_blocks_entire_batch(store):
    tenant = company(store)
    _, _, destinations, _ = records(store, tenant)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        payload = send_payload(client)
        client.put(f"/api/offers/{OFFER}/phrase", headers=ORIGIN, json={"text": "Outra frase para esta oferta."})
        assert submit(client, payload).status_code == 409
        payload = send_payload(client)
        store.put(tenant, "destination", destinations[1]["id"], {**destinations[1], "verified": False})
        assert submit(client, payload).status_code == 422
        assert store.history(tenant) == []


@pytest.mark.parametrize("changes", [
    {"affiliate_url": "https://www.amazon.com.br.evil.test/product"},
    {"affiliate_url": "https://www.mercadolivre.com.br/product"},
    {"image": "http://127.0.0.1/private"},
    {"image": "https://127.0.0.1/private"},
    {"affiliate_status": "generated"}, {"link_version": "forged"}, {"identity": "forged"},
    {"price": "NaN"}, {"price": "-1"},
])
def test_draft_edits_cannot_forge_attribution_or_external_destinations(store, changes):
    tenant = company(store)
    original, _, _, _ = records(store, tenant)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        assert client.put(f"/api/offers/{OFFER}", headers=ORIGIN, json=changes).status_code == 422
        assert store.get(tenant, "offer", OFFER) == original


def test_source_currency_is_not_relabelled_as_reais_until_explicit_price_edit(store):
    tenant = company(store)
    original, _, _, _ = records(store, tenant)
    row = {**original, "currency": "USD", "price": "12.50", "price_max": "12.50", "previous_price": "20.00"}
    store.put(tenant, "offer", OFFER, row)
    assert price_label(row) == "USD 12,50"
    assert "R$" not in caption(row, TEMPLATE)
    assert price_label({**row, "currency": None}) == "Preço sem moeda confirmada"
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        preview = client.get(f"/api/offers/{OFFER}/preview?template={TEXT_TEMPLATE}").json()
        assert preview["send"]["enabled"] is False
        assert "reais" in preview["send"]["reason"]
        edited = client.put(f"/api/offers/{OFFER}", headers=ORIGIN, json={"price": "69.90"}).json()
        assert edited["currency"] == "BRL" and edited["previous_price"] is None
        assert edited["discount_rate"] is None
        assert client.get(f"/api/offers/{OFFER}/preview?template={TEXT_TEMPLATE}").json()["send"]["enabled"]


def test_missing_photo_is_identified_before_card_job_is_accepted(store):
    tenant = company(store)
    records(store, tenant)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        preview = client.get(f"/api/offers/{OFFER}/preview?template=default").json()
        assert not preview["send"]["enabled"] and "imagem" in preview["send"]["reason"]
        payload = {"template": "default", "destinations": [DESTINATIONS[0]], "request_id": uuid4().hex,
                   "preview_token": preview["send"]["preview_token"]}
        assert submit(client, payload).status_code == 422
        assert not store.history(tenant)


def test_explicit_out_of_stock_is_not_overridden_by_a_manual_price(store):
    tenant = company(store)
    original, _, _, _ = records(store, tenant)
    store.put(tenant, "offer", OFFER, {**original, "availability": False, "price": None})
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        assert client.put(f"/api/offers/{OFFER}", headers=ORIGIN, json={"price": "29.99"}).status_code == 200
        preview = client.get(f"/api/offers/{OFFER}/preview?template={TEXT_TEMPLATE}").json()
        assert not preview["send"]["enabled"] and "indisponível" in preview["send"]["reason"]
        assert submit(client, send_payload(client)).status_code == 422
        assert not store.history(tenant)


def test_manual_telegram_dispatch_uses_only_selected_destinations_and_keeps_legacy_gate(store, monkeypatch):
    tenant = company(store)
    offer, template, destinations, version = records(store, tenant)
    batch, _ = store.enqueue_manual(tenant, uuid4().hex, "intent", offer, template, destinations[:2])
    legacy, _ = store.enqueue(tenant, destinations[2]["id"], offer, template, int(time.time()), approval=False)
    clock = [int(time.time())]
    monkeypatch.setattr("affiliate_pilot.engine.time.time", lambda: clock[0])
    published = []

    class Telegram:
        async def verify_destination(self, chat_id, bot_id, operator):
            assert bot_id == 99 and operator == 100
            return {"chat_id": chat_id, "title": "Verified destination"}

        async def publish(self, chat_id, text, image=None, **kwargs):
            assert OWN_LINK in text and image is None
            pending = [row for row in store.history(tenant) if row["status"] == "dispatching"]
            assert len(pending) == 1
            assert pending[0]["doc"]["offer"]["tracking_status"] == "unavailable"
            assert pending[0]["doc"]["offer"]["utm_content"] is None
            published.append(chat_id)
            return len(published)

    engine = Engine(store, sending=False)
    engine.telegram = lambda _: (Telegram(), {"bot_id": 99, "operator": 100}, version)
    first = store.claim(telegram=False)
    assert first["id"] in {row["id"] for row in batch["jobs"]}
    asyncio.run(engine.dispatch_job(first))
    clock[0] += 12
    asyncio.run(engine.dispatch_job(store.claim(telegram=False)))
    assert set(published) == {destinations[0]["chat_id"], destinations[1]["chat_id"]}
    assert store.claim(telegram=False) is None
    assert store.history(tenant, legacy)[0]["status"] == "queued"


@pytest.mark.parametrize("changed_field,changed_value", [("price", "49.90"), ("title", "Outro produto"),
                                                       ("identity", "shopee:11:999")])
def test_native_shopee_changes_pause_before_dispatch(store, changed_field, changed_value):
    tenant = company(store)
    _, template, destinations, telegram_version = records(store, tenant)
    shopee_version = store.save_connection(tenant, "shopee", {"app_id": "1", "key": "fake"}, {})
    offer = store.put(tenant, "offer", OFFER, prepared_offer(platform="shopee", retailer_name="Shopee",
        identity="shopee:11:8", shop=11, item=8, catalog_url="https://shopee.com.br/product/11/8",
        affiliate_url="https://s.shopee.com.br/synthetic", affiliate_status="generated",
        link_source="Shopee generateShortLink", link_version=shopee_version))
    store.enqueue_manual(tenant, uuid4().hex, "intent", offer, template, destinations[:1])
    calls = []

    class Shopee:
        async def detail(self, shop, item):
            return {**offer, changed_field: changed_value}

        async def tracked_affiliate_link(self, *args):
            raise AssertionError("changed products must not reach link generation or dispatch")

    class Telegram:
        async def verify_destination(self, chat_id, *args):
            return {"chat_id": chat_id}

        async def publish(self, *args, **kwargs):
            calls.append(args)

    engine = Engine(store, sending=False)
    engine.shopee = lambda _: (Shopee(), shopee_version)
    engine.telegram = lambda _: (Telegram(), {"bot_id": 99, "operator": 100}, telegram_version)
    job = store.claim(telegram=False)
    asyncio.run(engine.dispatch_job(job))
    current = store.history(tenant, job["id"])[0]
    assert current["status"] == "paused" and "prévia" in current["error"]
    assert calls == []


def test_manual_whatsapp_unknown_receipt_is_never_replayed(store):
    tenant = company(store)
    offer, template, _, _ = records(store, tenant)
    destination = store.put(tenant, "destination", DESTINATIONS[0],
        {"name": "Canal autorizado", "mode": "whatsapp_channel", "verified": True, "test": False,
         "address": "https://whatsapp.com/channel/ABCDEFGHIJKLMNOPQRSTUV", "channel_jid": "123456789@newsletter",
         "connection_version": "orion:synthetic-device:generation:1"})
    batch, _ = store.enqueue_manual(tenant, uuid4().hex, "intent", offer, template, [destination])
    calls = []

    class WhatsApp:
        def available(self, company):
            return company == tenant

        async def verify(self, company, selected):
            assert selected["id"] == destination["id"]
            return {"version": selected["connection_version"], "address": selected["channel_jid"], "title": selected["name"]}

        async def publish(self, ident, company, selected, text, link, media, thumbnail=None):
            assert ident == batch["jobs"][0]["id"] and company == tenant
            assert link == OWN_LINK and media is None
            assert store.history(tenant, ident)[0]["status"] == "dispatching"
            calls.append(ident)
            raise ProviderError("unknown", "Sem confirmação; confira o destino.")

    engine = Engine(store, sending=False, manual_transport=WhatsApp())
    asyncio.run(engine.dispatch_job(store.claim(telegram=False)))
    assert store.history(tenant, batch["jobs"][0]["id"])[0]["status"] == "unknown"
    assert store.claim(telegram=False) is None
    assert len(calls) == 1


@pytest.mark.parametrize("api_unavailable", [False, True])
def test_native_manual_send_preserves_reviewed_content_and_renews_only_attribution(store, api_unavailable):
    tenant = company(store)
    _, template, destinations, telegram_version = records(store, tenant)
    shopee_version = store.save_connection(tenant, "shopee", {"app_id": "1", "key": "synthetic"}, {})
    offer = store.put(tenant, "offer", OFFER, prepared_offer(platform="shopee", retailer_name="Shopee",
        identity="shopee:11:8", shop=11, item=8, catalog_url="https://shopee.com.br/product/11/8", rating="4.7",
        affiliate_url="https://s.shopee.com.br/preview", affiliate_status="generated",
        link_source="Shopee generateShortLink", link_version=shopee_version,
        extraction={"status": "complete", "api_unavailable": api_unavailable},
        manual_fields=["title", "price"] if api_unavailable else []))
    batch, _ = store.enqueue_manual(tenant, uuid4().hex, "intent", offer, template, destinations[:1])
    job_id = batch["jobs"][0]["id"]
    sent = []

    class Shopee:
        async def detail(self, shop, item):
            assert not api_unavailable, "an explicitly completed draft must not repeat the failed catalog request"
            return {**offer, "rating": "5.0", "sales": 9999}

        async def tracked_affiliate_link(self, row, token):
            assert token == "orionj" + job_id
            assert row["catalog_url"] == "https://shopee.com.br/product/11/8"
            return {"affiliate_url": "https://s.shopee.com.br/job-specific", "tracking_token": token,
                    "utm_content": token+"----", "tracking_status": "verified"}

    class Telegram:
        async def verify_destination(self, chat_id, *args):
            return {"chat_id": chat_id}

        async def publish(self, chat_id, text, media=None):
            current = store.history(tenant, job_id)[0]
            assert current["status"] == "dispatching"
            renewed = current["doc"]["offer"]
            assert renewed["rating"] == "4.7" and "sales" not in renewed
            assert renewed["title"] == offer["title"] and renewed["price"] == offer["price"]
            assert renewed["tracking_token"] == "orionj" + job_id
            assert "https://s.shopee.com.br/job-specific" in text
            sent.append(chat_id)
            return 123

    engine = Engine(store, sending=False)
    engine.shopee = lambda _: (Shopee(), shopee_version)
    engine.telegram = lambda _: (Telegram(), {"bot_id": 99, "operator": 100}, telegram_version)
    asyncio.run(engine.dispatch_job(store.claim(telegram=False)))
    assert sent == [destinations[0]["chat_id"]]
    assert store.history(tenant, job_id)[0]["status"] == "published"


def test_orion_groups_without_test_flag_and_channel_permission_loss_use_tenant_transport(store, monkeypatch):
    key = Fernet.generate_key()
    signer = Fernet(key)
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    tenant_id = "a" * 32
    user = store.orion_workspace(tenant_id, "Synthetic Orion")
    company_id = user["id"]
    group_address = "123456789012@g.us"
    version = "orion:device:generation:1"

    def headers(method, path):
        claim = {"tenant": tenant_id, "name": "Synthetic Orion", "method": method, "path": path}
        return {**ORIGIN, "X-Orion-Affiliate-Access": signer.encrypt(json.dumps(claim).encode()).decode()}

    class WhatsApp:
        def available(self, company):
            return company == company_id

        async def control(self, company, path, **params):
            assert company == company_id
            if path == "channel":
                raise ProviderError("permissions", "A conta deixou de administrar este canal.")
            assert path == "groups"
            if params:
                assert params == {"address": group_address}
            return {"groups": [{"address": group_address, "title": "Grupo de teste", "role": "ADMIN"}],
                    "version": version}

    app = create_app(store, origin=BASE, sending=False, start_worker=False)
    with TestClient(app) as client:
        app.state.engine.manual_transport = WhatsApp()
        path = "/api/whatsapp/groups"
        response = client.get(path, headers=headers("GET", path))
        assert response.status_code == 200 and response.json()["truncated"] is False
        path = f"/api/destinations/{DESTINATIONS[0]}"
        saved = client.put(path, headers=headers("PUT", path),
                           json={"name": "Meu grupo", "mode": "whatsapp_group", "address": group_address})
        assert saved.status_code == 200 and saved.json()["verified"] is True
        assert saved.json()["test"] is False and saved.json()["connection_version"] == version
        store.put(company_id, "destination", DESTINATIONS[1],
                  {"name": "Canal", "mode": "whatsapp_channel", "verified": True, "test": False,
                   "address": "https://whatsapp.com/channel/ABCDEFGHIJKLMNOPQRSTUV", "connection_version": version})
        path = f"/api/destinations/{DESTINATIONS[1]}/verify-channel"
        assert client.post(path, headers=headers("POST", path)).status_code == 422
        assert store.get(company_id, "destination", DESTINATIONS[1])["verified"] is False
        assert store.history(company_id) == []


def test_orion_state_keeps_offers_available_when_whatsapp_status_is_unavailable(store, monkeypatch):
    key = Fernet.generate_key()
    signer = Fernet(key)
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    tenant_id = "a" * 32
    user = store.orion_workspace(tenant_id, "Synthetic Orion")
    company_id = user["id"]
    store.put(company_id, "offer", OFFER, prepared_offer())
    store.put(company_id, "destination", DESTINATIONS[0],
              {"name": "Meu grupo", "mode": "whatsapp_group", "verified": True,
               "address": "123456789012@g.us", "connection_version": "orion:device:generation:1"})

    class WhatsApp:
        async def control(self, company, path, **params):
            assert company == company_id and path == "status" and not params
            raise ProviderError("permissions", "Conecte e atualize o WhatsApp desta conta.")

    app = create_app(store, origin=BASE, sending=False, start_worker=False)
    with TestClient(app) as client:
        app.state.engine.manual_transport = WhatsApp()
        claim = {"tenant": tenant_id, "name": "Synthetic Orion", "method": "GET", "path": "/api/state"}
        response = client.get("/api/state", headers={
            "X-Orion-Affiliate-Access": signer.encrypt(json.dumps(claim).encode()).decode()})
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["offer"][0]["id"] == OFFER
        assert state["destination"][0]["verified"] is False
        assert "WhatsApp" in state["destination"][0]["issue"]
        assert state["jobs"] == []
        assert store.get(company_id, "destination", DESTINATIONS[0])["verified"] is True


def test_destination_rotation_between_enqueue_and_dispatch_prevents_delivery(store):
    tenant = company(store)
    offer, template, destinations, _ = records(store, tenant)
    batch, _ = store.enqueue_manual(tenant, uuid4().hex, "intent", offer, template, destinations[:1])
    store.put(tenant, "destination", destinations[0]["id"], {**destinations[0], "chat_id": -100999})
    engine = Engine(store, sending=False)
    asyncio.run(engine.dispatch_job(store.claim(telegram=False)))
    assert store.history(tenant, batch["jobs"][0]["id"])[0]["status"] == "paused"
    assert store.get(tenant, "destination", destinations[0]["id"])["verified"] is True


def test_suggestions_offer_variety_without_selecting_or_inventing_claims(store):
    tenant = company(store)
    offer, _, _, _ = records(store, tenant)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client)
        path = f"/api/offers/{OFFER}/phrases"
        first = client.post(path, headers=ORIGIN).json()
        second = client.post(path, headers=ORIGIN).json()
        assert len(first["suggestions"]) == len(second["suggestions"]) == 5
        assert first["suggestions"] != second["suggestions"]
        assert store.get(tenant, "offer", OFFER)["phrase"] == offer["phrase"]
        assert all(clean_phrase(item["text"]) == item["text"] for item in first["suggestions"])
        chosen = second["suggestions"][-1]["text"]
        client.put(f"/api/offers/{OFFER}/phrase", headers=ORIGIN, json={"text": chosen})
        preview = client.get(f"/api/offers/{OFFER}/preview?template={TEXT_TEMPLATE}").json()
        assert chosen in preview["text"]
        assert preview["suggestions"][0]["text"] == chosen
        assert store.history(tenant) == []


def test_card_uses_bundled_portuguese_glyphs_and_measured_wrapping():
    font = card_font(32, True)
    for letter in "áàâãçéêíóôõúüÁÇÉÍÓÚ•…":
        assert bytes(font.getmask(letter)) != bytes(font.getmask("\u0378"))
    canvas = Image.new("RGB", (900, 970), "white")
    draw = ImageDraw.Draw(canvas)
    lines = wrap_card_text(draw, "W" * 180 + " ação edição café coração", font, 786)
    assert len(lines) == 3 and lines[-1].endswith("…")
    assert all(draw.textlength(line, font=font) <= 786 for line in lines)
    template = {**TEMPLATE, "media": "card", "layout": "coupon"}
    row = prepared_offer(title="Ação, coração e café: edição especial", coupon={"code": "AÇÃO", "conditions": ""})
    composed = card_png(row, template)
    decomposed = card_png({**row, "title": "Ac\u0327a\u0303o, corac\u0327a\u0303o e cafe\u0301: edic\u0327a\u0303o especial"}, template)
    assert composed == decomposed
    assert Image.open(io.BytesIO(composed)).size == (900, 970)
    assert card_png({**row, "title": "Acao, coracao e cafe: edicao especial"}, template) != composed


def test_rating_scale_is_preserved_in_caption():
    row = prepared_offer(rating="95", rating_scale="100")
    text = caption(row, {**TEMPLATE, "text": "{produto}\n{avaliacao_vendas}\n{link}"})
    assert "95,0/100 de avaliação" in text
    assert "95,0/5" not in text
