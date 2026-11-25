"""Shared-link navigation uses public destinations and never app execution."""
import asyncio
from urllib.parse import quote

import httpx
import pytest

from affiliate_pilot import marketplaces as mp
from affiliate_pilot.providers import ProviderError


CLIENT = httpx.AsyncClient
PRODUCT = "https://www.amazon.com.br/dp/B07H3B86KY"
HTML = '''<input id="ASIN" value="B07H3B86KY">
<span id="productTitle">Lavadora de alta pressão — 220V</span>
<img id="landingImage" src="https://m.media-amazon.com/images/I/product.jpg">
<span id="priceblock_ourprice">R$ 378,90</span>
<span id="acrPopover" title="4,6 de 5 estrelas"></span>'''


@pytest.fixture(autouse=True)
def clean_cache():
    mp._cache.clear()
    mp._inflight.clear()
    yield
    mp._cache.clear()
    mp._inflight.clear()


def install_http(monkeypatch, handler):
    async def addresses(host):
        return ["93.184.216.34"]
    monkeypatch.setattr(mp, "_public_addresses", addresses)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: CLIENT(transport=httpx.MockTransport(handler), **kwargs))


def source_of(request):
    return str(request.url.copy_with(host=request.headers["host"]))


def intent(target):
    return "intent://product/#Intent;scheme=shop;S.browser_fallback_url=" + quote(target, safe="") + ";end"


def test_observed_amazon_app_sharing_chain_extracts_selected_asin(monkeypatch):
    source = "https://a.co/d/09cF1wPn"
    landing = PRODUCT + "?social_share=AbC+%2B&ref_=mobile"
    clp = "https://www.amazon.com.br/clp/B07H3B86KY?social_share=AbC+%2B&ref_=mobile"
    final = PRODUCT + "?social_share=AbC+%2B&ref_=mobile&plpRedirect=mhFallback"
    steps = [(source, 301, landing), (landing, 302, clp), (clp, 503, None),
             (clp, 302, final), (final, 200, None)]
    seen = []
    def handler(request):
        url, status, target = steps[len(seen)]
        seen.append(source_of(request))
        assert seen[-1] == url
        return httpx.Response(status, headers={"content-type": "text/html", **({"location": target} if target else {})},
                              text=HTML if status == 200 else "Temporary service error")
    install_http(monkeypatch, handler)
    offer = asyncio.run(mp.extract_offer(source))
    assert len(seen) == len(steps)
    assert offer["source_url"] == source and offer["resolved_url"] == final
    assert offer["identity"] == "amazon:B07H3B86KY"
    assert "220V" in offer["title"] and offer["price"] == "378.90"
    assert offer["rating"] == "4.6" and offer["image"]
    assert offer["affiliate_url"] is None


def test_redirect_can_use_explicit_https_intent_fallback_without_query_rewriting(monkeypatch):
    source = "https://amzn.to/AppShare"
    target = PRODUCT + "?tag=original-20&q=AbC%2B+%2F&x=&x=two"
    seen = []
    def handler(request):
        seen.append(source_of(request))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": intent(target)})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
    install_http(monkeypatch, handler)
    offer = asyncio.run(mp.extract_offer(source))
    assert seen == [source, target]
    assert offer["source_url"] == source and offer["resolved_url"] == target
    assert offer["price"] == "378.90" and offer["affiliate_url"] is None


@pytest.mark.parametrize("target", [
    "https://127.0.0.1/private", "https://www.amazon.com.br.evil.example/product",
    "https://shopee.com.br/product/1/2", "http://www.amazon.com.br/dp/B07H3B86KY",
    "javascript:alert(1)",
])
def test_intent_redirect_cannot_leave_store_or_use_unsafe_destination(monkeypatch, target):
    seen = []
    def handler(request):
        seen.append(source_of(request))
        return httpx.Response(302, headers={"location": intent(target)})
    install_http(monkeypatch, handler)
    with pytest.raises(ProviderError):
        asyncio.run(mp.extract_offer("https://amzn.to/AppShare"))
    assert seen == ["https://amzn.to/AppShare"]


def test_observed_magalu_verification_route_is_not_requested(monkeypatch):
    source = "https://www.magazinevoce.com.br/example/p/fkdde6gdf0/"
    seen = []
    def handler(request):
        seen.append(source_of(request))
        return httpx.Response(302, headers={"location": "/az-request-verify?next=product"})
    install_http(monkeypatch, handler)
    offer = asyncio.run(mp.extract_offer(source))
    assert seen == [source]
    assert offer["extraction"]["code"] == "blocked"
    assert offer["title"] is None


def test_shopee_coupon_shortlink_does_not_fetch_wallet_or_create_product(monkeypatch):
    source = "https://s.shopee.com.br/806ctxlWrB"
    seen = []
    def handler(request):
        seen.append(source_of(request))
        return httpx.Response(301, headers={"location": "https://shopee.com.br/user/voucher-wallet"})
    install_http(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        asyncio.run(mp.extract_offer(source))
    assert error.value.code == "not_product"
    assert seen == [source]


@pytest.mark.parametrize("source", ["https://meli.promo/s/o2fOpD", "https://amzon.promo/s/LEWFzv"])
def test_unavailable_shortener_does_not_claim_destination_expired(monkeypatch, source):
    install_http(monkeypatch, lambda request: httpx.Response(404, text="Link não encontrado ou expirado."))
    offer = asyncio.run(mp.extract_offer(source))
    assert offer["source_url"] == source
    assert offer["extraction"]["code"] == "shortener_unavailable"
    assert "expirou" not in offer["extraction"]["reason"]
    assert offer["title"] is None and offer["price"] is None
