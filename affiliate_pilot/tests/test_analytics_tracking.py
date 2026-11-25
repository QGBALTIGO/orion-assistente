"""Native Shopee attribution with synthetic providers and local SQLite only."""
import asyncio
import time
from urllib.parse import quote, urlencode

import httpx
import pytest
from cryptography.fernet import Fernet

from affiliate_pilot.engine import Engine
from affiliate_pilot.promotech import PromoTech
from affiliate_pilot.providers import ProviderError, Shopee, normalize_offer
from affiliate_pilot.store import DEAL_TEMPLATE, DEFAULT_TEMPLATE, Store


TOKEN = "orionj" + "a" * 32
SHORT_LINK = "https://s.shopee.com.br/synthetic"
OFFER = normalize_offer({"shopId": 11, "itemId": 8, "productName": "Produto sintético",
                         "priceMin": "59.90", "priceMax": "59.90", "shopName": "Loja de teste"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tracking tests cannot use network providers")
    monkeypatch.setattr(httpx, "AsyncClient", reject)


def provider_response(monkeypatch, payload):
    provider = Shopee("123", "synthetic-not-a-live-secret")
    calls = []

    async def graphql(query, variables):
        calls.append((query, variables))
        return {"generateShortLink": payload}

    monkeypatch.setattr(provider, "graphql", graphql)
    return provider, calls


@pytest.mark.parametrize("returned", [TOKEN, TOKEN + "----", TOKEN + "-native+value"])
def test_preserves_exact_native_utm_without_reconstructing_separator(monkeypatch, returned):
    native = OFFER["catalog_url"] + "?" + urlencode({"utm_source": "synthetic", "utm_content": returned}, quote_via=quote)
    provider, calls = provider_response(monkeypatch, {"shortLink": SHORT_LINK, "longLink": native})
    result = asyncio.run(provider.tracked_affiliate_link(OFFER, TOKEN))
    assert result == {"affiliate_url": SHORT_LINK, "tracking_token": TOKEN,
                      "utm_content": returned, "tracking_status": "verified"}
    assert calls[0][1] == {"origin": OFFER["catalog_url"], "sub": [TOKEN]}
    assert "shortLink longLink" in calls[0][0]


@pytest.mark.parametrize("native", [
    None,
    "",
    42,
    "http://shopee.com.br/product/11/8?utm_content=" + TOKEN,
    "https://shopee.com.br.evil.invalid/product/11/8?utm_content=" + TOKEN,
    "https://shopee.com.br@evil.invalid/product/11/8?utm_content=" + TOKEN,
    OFFER["catalog_url"],
    OFFER["catalog_url"] + "?utm_content=",
    OFFER["catalog_url"] + "?utm_content=other-job",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "other",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "&utm_content=" + TOKEN,
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "&%75tm_content=",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "%00",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "%20",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "%FF",
    OFFER["catalog_url"] + "?utm_content=" + TOKEN + "-" * 513,
])
def test_missing_or_invalid_native_mapping_keeps_valid_short_link_untracked(monkeypatch, native):
    provider, calls = provider_response(monkeypatch, {"shortLink": SHORT_LINK, "longLink": native})
    result = asyncio.run(provider.tracked_affiliate_link(OFFER, TOKEN))
    assert result == {"affiliate_url": SHORT_LINK, "tracking_token": TOKEN,
                      "utm_content": None, "tracking_status": "unavailable"}
    assert len(calls) == 1


@pytest.mark.parametrize("link", [None, 42, "http://s.shopee.com.br/test", "https://example.invalid/test"])
def test_tracked_link_preserves_short_link_domain_guard(monkeypatch, link):
    provider, _ = provider_response(monkeypatch, {"shortLink": link,
        "longLink": OFFER["catalog_url"] + "?utm_content=" + TOKEN})
    with pytest.raises(ProviderError) as failure:
        asyncio.run(provider.tracked_affiliate_link(OFFER, TOKEN))
    assert failure.value.code == "attribution"


@pytest.mark.parametrize("token", ["client-chosen", "orionj" + "a" * 31, "orionj" + "a" * 33, None])
def test_only_canonical_job_tokens_reach_shopee(monkeypatch, token):
    provider, calls = provider_response(monkeypatch, {"shortLink": SHORT_LINK})
    with pytest.raises(ProviderError) as failure:
        asyncio.run(provider.tracked_affiliate_link(OFFER, token))
    assert failure.value.code == "attribution"
    assert calls == []


def test_tracked_link_rejects_foreign_origin_and_preserves_provider_errors(monkeypatch):
    provider, calls = provider_response(monkeypatch, {"shortLink": SHORT_LINK})
    with pytest.raises(ProviderError) as failure:
        asyncio.run(provider.tracked_affiliate_link({**OFFER, "catalog_url": "https://example.invalid/product"}, TOKEN))
    assert failure.value.code == "url" and calls == []
    error = ProviderError("rate", "Synthetic provider limit", retry_after=90)

    async def limited(*args):
        raise error

    monkeypatch.setattr(provider, "graphql", limited)
    with pytest.raises(ProviderError) as failure:
        asyncio.run(provider.tracked_affiliate_link(OFFER, TOKEN))
    assert failure.value is error


@pytest.fixture
def context(tmp_path, monkeypatch):
    clock = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    store = Store(tmp_path / "tracking.sqlite3", Fernet.generate_key().decode())
    company = store.orion_workspace("a" * 32, "Empresa sintética")["id"]
    shopee_version = store.save_connection(company, "shopee", {"app_id": "123", "key": "fixture"}, {})
    telegram_version = store.save_connection(company, "telegram", {"token": "fixture"}, {})
    provider = Shopee("123", "fixture")
    state = {"store": store, "company": company, "clock": clock, "provider": provider,
             "shopee_version": shopee_version, "telegram_version": telegram_version,
             "calls": [], "native": True, "after_link": None}

    async def graphql(query, variables):
        token = variables["sub"][0]
        state["calls"].append((query, variables))
        if state["after_link"]:
            state["after_link"]()
        result = {"shortLink": "https://s.shopee.com.br/" + token}
        if state["native"]:
            result["longLink"] = OFFER["catalog_url"] + "?utm_content=" + token + "%2D%2D%2D%2D"
        return {"generateShortLink": result}

    async def detail(shop, item):
        assert (shop, item) == (OFFER["shop"], OFFER["item"])
        return {**OFFER, "fetched_at": clock[0]}

    monkeypatch.setattr(provider, "graphql", graphql)
    monkeypatch.setattr(provider, "detail", detail)
    return state


def telegram_job(context, destination, *, rule=None):
    store, company = context["store"], context["company"]
    store.put(company, "destination", destination, {"name": "Destino sintético", "mode": "telegram",
        "test": True, "verified": True, "chat_id": -100000 - int(destination[0], 16),
        "connection_version": context["telegram_version"]})
    # A queued snapshot is not evidence of attribution; dispatch must replace it.
    previous = {**OFFER, "affiliate_url": SHORT_LINK, "link_version": context["shopee_version"],
                "tracking_token": "client-value", "utm_content": "client-value", "tracking_status": "verified"}
    ident, created = store.enqueue(company, destination, previous, {**DEFAULT_TEMPLATE, "media": "text"},
                                  context["clock"][0], approval=False, rule=rule)
    assert created
    return ident


def engine_with_telegram(context, monkeypatch, publish):
    class Delivery:
        async def verify_destination(self, chat_id, bot_id, operator):
            return {"title": "Canal sintético"}

        async def publish(self, chat_id, text, media):
            return await publish(chat_id, text, media)

    engine = Engine(context["store"], True)
    monkeypatch.setattr(engine, "shopee", lambda company: (context["provider"], context["shopee_version"]))
    monkeypatch.setattr(engine, "telegram", lambda company: (Delivery(), {"bot_id": 1, "operator": 2}, context["telegram_version"]))
    return engine


def test_ready_offer_preserves_existing_string_link_workflow(context, monkeypatch):
    engine = Engine(context["store"], False)
    monkeypatch.setattr(engine, "shopee", lambda company: (context["provider"], context["shopee_version"]))
    result = asyncio.run(engine.ready_offer(context["company"], OFFER))
    assert isinstance(result["affiliate_url"], str)
    assert result["link_version"] == context["shopee_version"]
    assert "utm_content" not in result and "tracking_token" not in result
    query, variables = context["calls"][0]
    assert "longLink" not in query
    assert variables["sub"] == ["orion" + context["company"][:10]]


@pytest.mark.parametrize("native", [True, False])
def test_each_telegram_job_has_unique_native_link_persisted_before_send(context, monkeypatch, native):
    context["native"] = native
    store, company = context["store"], context["company"]
    jobs = {telegram_job(context, destination): destination for destination in ("a" * 32, "b" * 32)}
    sent = []

    async def publish(chat_id, text, media):
        current = next(row for row in store.history(company) if row["status"] == "dispatching")
        token = "orionj" + current["id"]
        prepared = current["doc"]["offer"]
        assert prepared["tracking_token"] == token
        assert prepared["utm_content"] == (token + "----" if native else None)
        assert prepared["tracking_status"] == ("verified" if native else "unavailable")
        assert prepared["link_version"] == context["shopee_version"]
        assert prepared["affiliate_url"] in text
        assert current["doc"]["published_text"] == text
        assert current["destination"] == jobs[current["id"]]
        sent.append(current["id"])
        return len(sent)

    engine = engine_with_telegram(context, monkeypatch, publish)
    for _ in jobs:
        asyncio.run(engine.dispatch_job(store.claim()))
        context["clock"][0] += 12
    assert set(sent) == set(jobs)
    assert len({row["doc"]["offer"]["affiliate_url"] for row in store.history(company)}) == 2
    assert all(row["status"] == "published" for row in store.history(company))
    assert {variables["sub"][0] for _, variables in context["calls"]} == {"orionj" + job for job in jobs}


def test_credential_rotation_before_dispatch_prevents_send_and_mapping_write(context, monkeypatch):
    ident = telegram_job(context, "a" * 32)
    store, company = context["store"], context["company"]
    sent = []

    async def publish(*args):
        sent.append(args)
        return 1

    def rotate():
        store.save_connection(company, "shopee", {"app_id": "456", "key": "new-fixture"}, {})

    context["after_link"] = rotate
    engine = engine_with_telegram(context, monkeypatch, publish)
    asyncio.run(engine.dispatch_job(store.claim()))
    current = store.history(company, ident)[0]
    assert current["status"] == "paused" and sent == []
    assert current["doc"]["offer"]["tracking_token"] != "orionj" + ident
    assert store.credentials(company, "shopee")[1] != current["doc"]["offer"]["link_version"]


def test_uncertain_send_retains_mapping_recorded_before_dispatch(context, monkeypatch):
    ident = telegram_job(context, "a" * 32)

    async def publish(*args):
        raise ProviderError("unknown", "Synthetic delivery without receipt")

    engine = engine_with_telegram(context, monkeypatch, publish)
    asyncio.run(engine.dispatch_job(context["store"].claim()))
    current = context["store"].history(context["company"], ident)[0]
    assert current["status"] == "unknown"
    assert current["doc"]["offer"]["utm_content"] == "orionj" + ident + "----"
    assert current["doc"]["offer"]["tracking_status"] == "verified"


def test_promotech_persists_its_job_link_before_the_native_dispatch_boundary(context, monkeypatch):
    store, company = context["store"], context["company"]
    destination, rule_id = "c" * 32, "d" * 32
    binding = {"company": company, "destination": destination, "rule": rule_id,
               "jid": "123456789@newsletter", "version": "e" * 64, "invite": "F" * 22}
    transport = PromoTech(binding, "synthetic", 18068)
    engine = Engine(store, False, transport)
    store.put(company, "destination", destination, {"name": "PromoTech sintético", "mode": "whatsapp_channel",
        "verified": True, "channel_jid": binding["jid"], "connection_version": binding["version"]})
    rule = {"name": "Regra sintética", "active": True, "mode": "automatic", "interval": 30,
            "daily": 48, "days": list(range(7)), "opens": "00:00", "closes": "00:00",
            "timezone": "UTC", "destinations": [destination], "blocked": []}
    store.put(company, "rule", rule_id, rule)
    old_offer = {**OFFER, "affiliate_url": SHORT_LINK, "link_version": context["shopee_version"]}
    ident, created = store.enqueue(company, destination, old_offer, DEAL_TEMPLATE, context["clock"][0],
                                  approval=False, rule={"id": rule_id, **rule})
    assert created
    posted = []

    async def publish(job_id, text, link, card, thumb):
        assert job_id == ident and card == b"synthetic-card" and thumb == b"synthetic-thumb"
        current = store.history(company, ident)[0]
        prepared = current["doc"]["offer"]
        assert current["status"] == "dispatching"
        assert prepared["tracking_token"] == "orionj" + ident
        assert prepared["utm_content"] == "orionj" + ident + "----"
        assert prepared["tracking_status"] == "verified"
        assert prepared["link_version"] == context["shopee_version"]
        assert prepared["affiliate_url"] == link and link in text
        assert current["doc"]["published_text"] == text
        posted.append(ident)
        return "123"

    async def card(*args):
        return b"synthetic-card", b"synthetic-thumb"

    monkeypatch.setattr(engine, "shopee", lambda company: (context["provider"], context["shopee_version"]))
    monkeypatch.setattr(transport, "publish", publish)
    monkeypatch.setattr("affiliate_pilot.engine.render_channel_card", card)
    asyncio.run(engine.dispatch_job(store.claim(telegram=False, promotech=binding)))
    assert posted == [ident]
    assert store.history(company, ident)[0]["status"] == "published"
