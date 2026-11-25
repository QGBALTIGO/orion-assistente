import asyncio
import concurrent.futures
import io
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image

from affiliate_pilot.engine import DEMO, Engine
from affiliate_pilot.main import create_app
from affiliate_pilot.offers import caption, card_png, formatted_html, next_slot, preference, sold_label
from affiliate_pilot.providers import ProviderError, Shopee, normalize_offer, product_ids, resolve_product
from affiliate_pilot.promotech import PromoTech
from affiliate_pilot.store import DEAL_TEMPLATE, DEAL_TEMPLATE_ID, DEFAULT_TEMPLATE, Store


BASE = "http://testserver"
ORIGIN = {"Origin": BASE}
PASSWORD = "a-pilot-access-password-for-tests"
DEST = "a" * 32
offer = normalize_offer({
    "itemId": 8, "shopId": 11, "productName": "Fone", "priceMin": "59.90", "priceMax": "79.90",
    "shopName": "Loja fictícia",
})


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "pilot.sqlite3", Fernet.generate_key().decode())
    instance.bootstrap("first", PASSWORD, "Primeira empresa")
    instance.bootstrap("second", PASSWORD, "Segunda empresa")
    return instance


def login(client, user):
    return client.post("/api/login", headers=ORIGIN, json={"username": user, "password": PASSWORD})


def company(store, name="first"):
    return store.user(store.authenticate(name, PASSWORD))["id"]


def test_company_scope_and_backend_origin_checks(store):
    app = create_app(store, origin=BASE, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/login", json={"username": "first", "password": PASSWORD}).status_code == 403
        assert login(client, "first").status_code == 200
        assert client.get("/api/state").json()["company"] == "Primeira empresa"
        response = client.post("/api/offers/demo", headers=ORIGIN)
        assert response.json()["demo"] is True
        assert client.get("/api/offers/demo/card.png").headers["content-type"] == "image/png"
        assert client.post("/api/logout", headers=ORIGIN).status_code == 200
        assert login(client, "second").status_code == 200
        assert client.get("/api/state").json()["offer"] == []
        assert client.get("/api/offers/demo/card.png").status_code == 404


def test_orion_access_is_scoped_to_one_workspace_method_and_route(store, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    signer = Fernet(key)

    def signed(tenant, method, path, *, timestamp=None):
        claim = json.dumps({"tenant": tenant, "name": "Ofertas de " + tenant[0],
                            "method": method, "path": path}).encode()
        token = signer.encrypt(claim) if timestamp is None else signer.encrypt_at_time(claim, timestamp)
        return {**ORIGIN, "X-Orion-Affiliate-Access": token.decode()}

    first, second = "a" * 32, "b" * 32
    with TestClient(create_app(store, origin=BASE, start_worker=False)) as client:
        for tenant in (first, second):
            assert client.get("/api/state", headers=signed(tenant, "GET", "/api/state")).json()["company"] == "Ofertas de " + tenant[0]
        assert client.post("/api/offers/demo", headers=signed(first, "POST", "/api/offers/demo")).is_success
        assert client.get("/api/state", headers=signed(first, "GET", "/api/state")).json()["offer"][0]["demo"]
        assert client.get("/api/state", headers=signed(second, "GET", "/api/state")).json()["offer"] == []
        assert client.get("/api/state", headers=signed(first, "POST", "/api/state")).status_code == 401
        assert client.get("/api/state", headers=signed(first, "GET", "/api/offers/demo")).status_code == 401
        assert client.get("/api/state", headers=signed(first, "GET", "/api/state",
                          timestamp=int(time.time())-31)).status_code == 401
        assert login(client, "orion-" + first).status_code == 401
        assert login(client, "first").status_code == 200
        assert client.get("/api/state").json()["offer"] == []


def test_whatsapp_channel_role_is_scoped_to_connection_and_tenant(store, monkeypatch):
    monkeypatch.setenv("AFFILIATE_WA_TOKEN", "test-token")
    monkeypatch.setenv("AFFILIATE_WA_PORT", "18068")
    monkeypatch.setenv("AFFILIATE_BOOTSTRAP_USER", "first")
    role = "OWNER"
    version = "wearable-1"

    def handle(request):
        assert request.headers.get("Authorization") == "Bearer test-token"
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "linked", "version": version})
        assert request.url.path == "/channel" and request.url.params["invite"] == "0" * 22
        if role == "GUEST":
            return httpx.Response(403, json={"code": "channel_admin"})
        return httpx.Response(200, json={"version": version, "address": "120363470000000000@newsletter",
                                         "title": "Teste", "role": role})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    app = create_app(store, origin=BASE, start_worker=False)
    path = f"/api/destinations/{DEST}"
    verify = path + "/verify-channel"
    with TestClient(app) as client:
        assert client.post(verify, headers=ORIGIN).status_code == 401
        assert login(client, "first").is_success
        request = {"name": "Canal de teste", "mode": "whatsapp_channel", "test": True}
        assert client.put(path, headers=ORIGIN, json={**request, "address": "https://example.org/channel/" + "0" * 22}).is_success
        assert client.post(verify, headers=ORIGIN).status_code == 422
        assert client.put(path, headers=ORIGIN, json={**request, "address": "https://whatsapp.com/channel/" + "0" * 22}).is_success
        saved = client.post(verify, headers=ORIGIN).json()
        assert saved["verified"] and not saved["test"]
        assert client.get("/api/state").json()["destination"][0]["verified"]
        version = "wearable-2"
        assert not client.get("/api/state").json()["destination"][0]["verified"]
        role = "GUEST"
        assert client.post(verify, headers=ORIGIN).status_code == 422
        assert not store.get(company(store), "destination", DEST)["verified"]
        assert client.post("/api/logout", headers=ORIGIN).is_success
        assert login(client, "second").is_success
        assert client.post(verify, headers=ORIGIN).status_code == 404


def test_secrets_and_reassignment_are_isolated(store):
    a, b = company(store), company(store, "second")
    a_version = store.save_connection(a, "shopee", {"key": "private-first"}, {"status": "connected"})
    b_version = store.save_connection(b, "shopee", {"key": "private-second"}, {"status": "connected"})
    assert a_version != b_version
    assert store.credentials(a, "shopee")[0]["key"] == "private-first"
    assert b"private-first" not in store.path.read_bytes()
    assert "private-first" not in str(store.list(a, "connection"))
    store.revoke(a, "shopee")
    assert store.credentials(a, "shopee") == (None, None)
    assert store.credentials(b, "shopee")[0]["key"] == "private-second"


def test_price_range_and_absent_fields_do_not_invent_discount():
    prepared = {**offer, "affiliate_url": "https://s.shopee.com.br/example"}
    template = {**DEFAULT_TEMPLATE, "text": "{produto}\nDe {preco_anterior}\n{preco}\nCupom {cupom}"}
    text = caption(prepared, template)
    assert "A partir de R$ 59,90" in text
    assert "Cupom" not in text and "De R$" not in text
    assert prepared["affiliate_url"] in text and "Publicidade" not in text
    assert Image.open(io.BytesIO(card_png(prepared, template))).size == (900, 970)


def test_promotech_caption_uses_native_markers_and_reliable_fields():
    row = {**offer, "title": "Fone <adaptador> * estereo", "price_max": offer["price"],
           "affiliate_url": "https://s.shopee.com.br/confirmed", "rating": "4.8", "sales": 11400,
           "discount_rate": 21, "niche": "eletronicos", "phrase": "Confira uma opção para ouvir suas músicas.",
           "previous_price": None}
    text = caption(row, DEAL_TEMPLATE)
    rendered = formatted_html(text)
    assert "🔥 *Fone" in text and "_Confira uma opção" in text and "✅ Por *R$ 59,90 (21% OFF)*" in text
    assert "⭐ _4,8/5 de avaliação +11,4 mil vendidos_" in text
    assert "❌ De" not in text and "#Eletrônicos" in text
    assert row["affiliate_url"] in text and "<strong>" in rendered and "<em>" in rendered
    assert "<adaptador>" not in rendered


def test_saved_template_update_preserves_custom_text(store):
    tenant = company(store)
    custom = {**DEAL_TEMPLATE, "text": "🔥 *{produto}*\n_{publicidade}_\n🔗 {link}"}
    store.put(tenant, "template", DEAL_TEMPLATE_ID, custom)
    store.ensure_deal_templates()
    assert store.get(tenant, "template", DEAL_TEMPLATE_ID)["text"] == "🔥 *{produto}*\n🔗 {link}"


def test_same_product_price_history_and_discount_ranking(store):
    a, b = company(store), company(store, "second")
    now = int(time.time())
    first = {**offer, "price": "100", "price_max": "100", "fetched_at": now - 7200}
    lowest = {**first, "price": "79.90", "price_max": "79.90", "fetched_at": now - 100, "sales": 10999}
    assert store.record_prices(a, [first])[0]["previous_price"] is None
    reduced, = store.record_prices(a, [lowest])
    reduced["affiliate_url"] = "https://s.shopee.com.br/fixture"
    msg = caption(reduced, DEAL_TEMPLATE)
    assert "❌ De ~R$ 100,00~" in msg and "<s>R$ 100,00</s>" in formatted_html(msg)
    assert "✅ Por *R$ 79,90 (20% OFF)*" in msg and "+10,9 mil vendidos" in msg
    assert "Publicidade" not in msg
    assert preference(reduced) > preference({**first, "sales": 20_000})
    assert preference({**first, "discount_rate": 18}) > preference(first)
    assert store.record_prices(b, [lowest])[0]["previous_price"] is None
    ranged = {**reduced, "price_max": "99.90"}
    assert "❌ De" not in caption(ranged, DEAL_TEMPLATE)
    assert "✅ *A partir de R$ 79,90*" in caption(ranged, DEAL_TEMPLATE)
    stable, = store.record_prices(a, [{**lowest, "fetched_at": now}])
    assert stable["previous_price"] == "100"
    assert sold_label(10000) == "+10 mil vendidos"


def test_shopee_small_fallback_keeps_native_category_filter(monkeypatch):
    connector = Shopee("9", "fixture")
    requests = []

    async def graphql(query, payload):
        requests.append(query)
        if "limit:3," not in query:
            raise ProviderError("schema", "Erro interno")
        return {"productOfferV2": {"nodes": [
            {"itemId": 2, "shopId": 4, "productName": "Panela", "priceMin": "20", "productCatIds": [100717]},
            {"itemId": 3, "shopId": 4, "productName": "Fone", "priceMin": "21", "productCatIds": [100535]},
        ]}}

    monkeypatch.setattr(connector, "graphql", graphql)
    rows = asyncio.run(connector.search("panela", category=100717, sort=1))
    assert [row["title"] for row in rows] == ["Panela"]
    assert len(requests) == 3 and "productCatId:100717" in requests[0] and "productCatId:" not in requests[-1]


def test_groq_phrase_saves_only_tenant_key_and_accepts_manual_edits(store, monkeypatch):
    key = "gsk_" + "t" * 36
    sample = "Uma opção para acompanhar suas músicas no dia a dia."
    request_count = []

    def handle(request):
        assert request.url.host == "api.groq.com"
        assert request.headers["Authorization"] == "Bearer " + key
        request_count.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": sample}}]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    tenant = company(store)
    store.put(tenant, "offer", DEST, offer)
    app = create_app(store, origin=BASE, start_worker=False)
    with TestClient(app) as client:
        assert login(client, "first").is_success
        assert client.put("/api/platforms/groq", headers=ORIGIN, json={"key": key}).is_success
        assert key not in client.get("/api/state").text
        path = f"/api/offers/{DEST}/phrase"
        ai = client.post(path, headers=ORIGIN).json()
        assert ai["phrase"] == sample and ai["phrase_source"] == "groq"
        manual = client.put(path, headers=ORIGIN, json={"text": "Confira as opções de cores."}).json()
        assert manual["phrase_source"] == "manual" and manual["phrase"] == "Confira as opções de cores."
        assert client.delete("/api/platforms/groq", headers=ORIGIN).is_success
        assert store.credentials(tenant, "groq") == (None, None)
    assert len(request_count) == 2


def test_url_does_not_resolve_unknown_hosts_even_if_path_looks_valid():
    for url in ("https://shopee.com.br.evil.test/product/11/8", "https://shopee.com.br@127.0.0.1/product/11/8",
                "http://shopee.com.br/product/11/8", "https://shopee.com.br:8443/product/11/8"):
        with pytest.raises(ProviderError):
            asyncio.run(resolve_product(url))
    assert asyncio.run(resolve_product("https://shopee.com.br/product/11/8?affiliate=someone")) == (11, 8)
    assert product_ids("https://shopee.com.br/Fone-i.11.8") == (11, 8)


def test_concurrent_enqueues_and_worker_claims(store):
    tenant = company(store)
    now = int(time.time())
    def enqueue(_):
        return store.enqueue(tenant, DEST, offer, DEFAULT_TEMPLATE, now, approval=False, repeat_hours=12)
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        results = list(pool.map(enqueue, range(12)))
    assert len({row[0] for row in results}) == 1
    assert sum(row[1] for row in results) == 1
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(4)))
    assert sum(job is not None for job in claims) == 1


def test_worker_survives_uncertain_post_without_second_delivery(store):
    tenant = company(store)
    shopee = store.save_connection(tenant, "shopee", {"app_id": "1", "key": "test"}, {})
    telegram = store.save_connection(tenant, "telegram", {"token": "test"}, {})
    ident, _ = store.enqueue(tenant, DEST, offer, DEFAULT_TEMPLATE, int(time.time()), approval=False)
    job = store.claim()
    assert store.dispatch(job, job["doc"], shopee, telegram)
    with store.connect(True) as db:
        db.execute("UPDATE job SET lease=0 WHERE id=?", (ident,))
    assert store.claim() is None
    assert store.history(tenant)[0]["status"] == "unknown"


def test_revoked_credential_cannot_cross_dispatch_boundary(store):
    tenant = company(store)
    shopee = store.save_connection(tenant, "shopee", {"app_id": "1", "key": "test"}, {})
    telegram = store.save_connection(tenant, "telegram", {"token": "test"}, {})
    store.enqueue(tenant, DEST, offer, DEFAULT_TEMPLATE, int(time.time()), approval=False)
    job = store.claim()
    store.revoke(tenant, "shopee")
    assert not store.dispatch(job, job["doc"], shopee, telegram)


def test_simulated_preflight_refresh_and_provider_receipt(store, monkeypatch):
    tenant = company(store)
    sh, tg = (store.save_connection(tenant, kind, {"fixture": True}, {}) for kind in ("shopee", "telegram"))
    store.put(tenant, "destination", DEST, {
        "name": "Teste simulado", "mode": "telegram", "test": True, "verified": True, "chat_id": -100999,
        "connection_version": tg,
    })
    store.enqueue(tenant, DEST, {**offer, "link_version": sh, "affiliate_url": "https://s.shopee.com.br/old"},
                  {**DEFAULT_TEMPLATE, "media": "text"}, int(time.time()), approval=False)
    engine = Engine(store, True)
    calls = []

    class Provider:
        async def detail(self, shop, item):
            return {**offer, "price": "64.90"}
        async def affiliate_link(self, data, subid):
            return "https://s.shopee.com.br/new"
        async def tracked_affiliate_link(self, data, token):
            return {"affiliate_url": await self.affiliate_link(data, token), "tracking_token": token,
                    "utm_content": None, "tracking_status": "unavailable"}

    class Delivery:
        async def verify_destination(self, ident, bot, operator):
            return {"title": "Fake channel"}
        async def publish(self, dest, text, media):
            calls.append((dest, text, media))
            return 9123

    monkeypatch.setattr(engine, "shopee", lambda _: (Provider(), sh))
    monkeypatch.setattr(engine, "telegram", lambda _: (Delivery(), {"bot_id": 10, "operator": 14}, tg))
    asyncio.run(engine.dispatch_job(store.claim()))
    assert len(calls) == 1 and "64,90" in calls[0][1] and "/new" in calls[0][1]
    receipt = store.history(tenant)[0]
    assert receipt["status"] == "published" and receipt["receipt"] == "9123"


def test_preview_deployment_has_no_send_override(store):
    tenant = company(store)
    ident, _ = store.enqueue(tenant, DEST, offer, DEFAULT_TEMPLATE, int(time.time()), approval=True)
    with TestClient(create_app(store, origin=BASE, sending=False, start_worker=False)) as client:
        login(client, "first")
        attempt = client.post(f"/api/jobs/{ident}", headers=ORIGIN, json={"action": "approve", "confirm_test": True})
        assert attempt.status_code == 409
        assert store.history(tenant)[0]["status"] == "approval"


def test_cuiaba_window_skips_off_days():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/Cuiaba")
    after = datetime(2026, 11, 24, 22, tzinfo=zone)
    rule = {"timezone": "America/Cuiaba", "days": [2], "opens": "09:00", "closes": "21:00"}
    assert datetime.fromtimestamp(next_slot(rule, int(after.timestamp())), zone) == datetime(2026, 11, 25, 9, tzinfo=zone)


def test_automatic_rule_prepares_only_eligible_review_items(store, monkeypatch):
    tenant = company(store)
    now = int(time.time())
    engine = Engine(store, False)
    rule = {"name": "Periféricos", "keyword": "fone", "blocked": ["recondicionado"],
            "template_id": "default", "destinations": [DEST], "days": list(range(7)),
            "opens": "00:00", "closes": "23:59", "timezone": "America/Cuiaba",
            "interval": 45, "daily": 5, "repeat_hours": 48, "min_price": 25,
            "mode": "automatic", "active": True, "next_at": now}
    when = next_slot(rule, now)
    rule["next_at"] = when
    store.put(tenant, "destination", DEST, {"mode":"telegram", "test":True, "name":"Mock", "verified":True})
    store.put(tenant, "rule", "rule", rule)

    class Catalog:
        async def search(self, term, limit):
            assert term == "fone"
            return [{**offer, "price": "5"}, {**offer, "title": "Fone recondicionado"}, offer]
        async def affiliate_link(self, item, subid):
            return "https://s.shopee.com.br/fixture"

    monkeypatch.setattr(engine, "shopee", lambda _: (Catalog(), "fixture"))
    asyncio.run(engine.plan_rule(tenant, "rule", rule, when))
    asyncio.run(engine.plan_rule(tenant, "rule", rule, when))
    records = store.history(tenant)
    assert len(records) == 1 and records[0]["status"] == "approval"
    assert records[0]["doc"]["offer"]["title"] == offer["title"]
    assert store.get(tenant, "rule", "rule")["next_at"] > when


def test_verified_whatsapp_rule_rotates_two_categories_at_ten_minute_intervals(store, monkeypatch):
    tenant = company(store)
    engine = Engine(store, False)
    now = 1_795_511_600
    rule = {"name": "Rodízio", "keyword": "", "niches": ["casa", "eletronicos"], "sort": 1, "blocked": [],
            "template_id": "default", "destinations": [DEST], "days": list(range(7)),
            "opens": "00:00", "closes": "23:59", "timezone": "UTC", "interval": 10, "daily": 3,
            "repeat_hours": 48, "mode": "review", "active": True, "next_at": now}
    store.put(tenant, "destination", DEST, {"mode": "whatsapp_channel", "name": "Teste", "test": False, "verified": True})
    store.put(tenant, "rule", "rule", rule)
    categories = []

    class Catalog:
        async def search(self, keyword, limit, category, sort):
            categories.append(category)
            return [{**offer, "identity": f"shopee:11:{category}", "item": category, "categories": [category]}]

        async def affiliate_link(self, item, subid):
            return "https://s.shopee.com.br/fixture"

    monkeypatch.setattr(engine, "shopee", lambda _: (Catalog(), "fixture"))
    asyncio.run(engine.plan_rule(tenant, "rule", rule, now))
    following = store.get(tenant, "rule", "rule")
    assert following["next_at"] == now + 600
    asyncio.run(engine.plan_rule(tenant, "rule", following, now + 600))
    ledger = store.history(tenant)
    assert categories == [100636, 100535]
    assert {row["doc"]["offer"]["niche"] for row in ledger} == {"casa", "eletronicos"}
    assert all(row["status"] == "approval" for row in ledger)


def test_promotech_claim_scopes_and_rechecks_the_active_rule(store, monkeypatch):
    tenant = company(store)
    other = company(store, "second")
    now = int(time.time())
    config = {"company": tenant, "destination": DEST, "rule": "b"*32,
              "jid": "123456789@newsletter", "version": "c"*64,
              "invite": "D"*22}
    transport = PromoTech(config, "fixture", 18068)
    engine = Engine(store, False, transport)
    job_offer = {**offer, "categories": [100017], "niche": "moda"}
    version = store.save_connection(tenant, "shopee", {"app_id": "1", "key": "fixture"}, {})
    job_offer["link_version"] = version
    destination = {"mode": "whatsapp_channel", "name": "PromoTech", "verified": True,
                   "channel_jid": config["jid"], "connection_version": config["version"]}
    store.put(tenant, "destination", DEST, destination)
    rule = {"active": True, "mode": "automatic", "interval": 30, "daily": 48, "days": list(range(7)),
            "opens": "00:00", "closes": "00:00", "timezone": "America/Cuiaba", "destinations": [DEST],
            "blocked": []}
    store.put(tenant, "rule", config["rule"], rule)
    assert next_slot(rule, now) == now
    planned = {"id": config["rule"], **rule}
    store.enqueue(other, DEST, job_offer, DEAL_TEMPLATE, now - 3, approval=False, rule=planned)
    queued, _ = store.enqueue(tenant, DEST, job_offer, DEAL_TEMPLATE, now, approval=False, rule=planned)

    class Catalog:
        async def detail(self, shop, item):
            return job_offer
        async def affiliate_link(self, row, tag):
            return "https://s.shopee.com.br/example"
        async def tracked_affiliate_link(self, row, token):
            return {"affiliate_url": await self.affiliate_link(row, token), "tracking_token": token,
                    "utm_content": None, "tracking_status": "unavailable"}

    posted = []
    async def send(ident, text, link, card, thumb):
        assert ident == queued and link in text and card and thumb
        posted.append(ident)
        return "364"

    async def image(offer, template):
        return b"card", b"thumb"

    monkeypatch.setattr(engine, "shopee", lambda _: (Catalog(), version))
    monkeypatch.setattr(transport, "publish", send)
    monkeypatch.setattr("affiliate_pilot.engine.render_channel_card", image)
    job = store.claim(telegram=False, promotech=config)
    assert job["company"] == tenant and job["id"] == queued
    asyncio.run(engine.dispatch_job(job))
    assert posted == [queued]
    assert store.history(tenant, queued)[0]["receipt"] == "364"
    assert store.history(other)[0]["status"] == "queued"
    assert store.claim(telegram=False, promotech=config) is None
