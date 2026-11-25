"""Exact public sharing hosts; all HTTP responses below are local fixtures.

Published URLs establish host/format coverage, not live price, ownership or
extraction availability. See shared_link_hosts.md for the public sources.
"""
import asyncio
import json
from urllib.parse import urlsplit

import httpx
import pytest

from affiliate_pilot import marketplaces as mp
from affiliate_pilot.providers import ProviderError


CLIENT_TYPE = httpx.AsyncClient
SHARED_LINKS = [
    ("shopee", "https://shopee.promo/s/2SQsd4"),
    ("magalu", "https://divulgador.magalu.com/sEaeLFJT"),
    ("magalu", "https://magalu.promo/"),
    ("magalu", "https://magazineluiza.onelink.me/589508454/c9h19gx1"),
    ("natura", "https://natura.promo/"),
    ("shein", "https://onelink.shein.com/4/4216oi9k96jn"),
    ("shein", "https://shein.top/gaow0qv"),
    ("shein", "https://m.shein.com/br/tax-policy-a-3476.html"),
    ("temu", "https://share.temu.com/fcwTccaFnAA"),
    ("temu", "https://temu.to/m/uav22pf66f1"),
]
NETWORK_LINK = "https://ofertou.ai/CnkMj"
MAGALU_PRODUCT = (
    "https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/"
    "divulgador/oferta/240604800/ga/cnle/?promoter_id=1799284&partner_id=3440"
)
KABUM_PRODUCT = (
    "https://www.kabum.com.br/produto/715915/"
    "mouse-gamer-razer-deathadder-v3-30-000dpi-preto-rz0104640100"
)
AWIN_LINK = (
    "https://www.awin1.com/cread.php?awinmid=17729&awinaffid=1725617&ued="
    + KABUM_PRODUCT
)


@pytest.fixture(autouse=True)
def isolate_cached_previews():
    mp._cache.clear()
    mp._inflight.clear()
    yield
    mp._cache.clear()
    mp._inflight.clear()


def mock_http(monkeypatch, handler):
    async def addresses(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(mp, "_public_addresses", addresses)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: CLIENT_TYPE(transport=httpx.MockTransport(handler), **kwargs),
    )


def fixture_product(url):
    product = {
        "@type": "Product", "name": "Produto de teste", "url": url,
        "image": "https://images.example.com/product.jpg",
        "offers": {"@type": "Offer", "price": "99.90", "priceCurrency": "BRL"},
    }
    return '<script type="application/ld+json">' + json.dumps(product) + '</script>'


@pytest.mark.parametrize("platform,url", SHARED_LINKS)
def test_published_aliases_remain_in_the_existing_37_retailers(platform, url):
    retailer = mp.retailer_for_url(url)
    assert retailer.id == platform
    assert mp.checked_product_url(url, platform) == url
    assert mp.checked_affiliate_url(url, platform) == url
    assert len(mp.RETAILERS) == len({row.id for row in mp.RETAILERS}) == 37
    hosts = [host for row in mp.RETAILERS for host in row.hosts]
    assert len(hosts) == len(set(hosts))


@pytest.mark.parametrize("platform,url", SHARED_LINKS)
@pytest.mark.parametrize("host_shape", ["www.{}", "{}.evil.example", "unregistered.{}"])
def test_new_aliases_do_not_allow_unproven_subdomains_or_suffix_matches(platform, url, host_shape):
    bad = "https://" + host_shape.format(urlsplit(url).hostname) + "/product"
    with pytest.raises(ProviderError):
        mp.checked_product_url(bad, platform)
    with pytest.raises(ProviderError):
        mp.checked_affiliate_url(bad, platform)


def test_custom_publisher_is_an_intermediary_not_a_38th_retailer():
    assert "ofertou.ai" in mp.AFFILIATE_NETWORK_HOSTS
    assert "ofertou.ai" not in mp.BY_HOST
    assert mp.checked_affiliate_url(NETWORK_LINK, "kabum") == NETWORK_LINK
    with pytest.raises(ProviderError):
        mp.retailer_for_url(NETWORK_LINK)
    for host in ("www.ofertou.ai", "ofertou.ai.evil.example", "other.ofertou.ai"):
        with pytest.raises(ProviderError):
            mp.checked_affiliate_url("https://" + host + "/CnkMj", "kabum")


def test_magalu_divulgador_redirect_keeps_partner_parameters_and_source(monkeypatch):
    source = "https://divulgador.magalu.com/sEaeLFJT"
    calls = []

    def handle(request):
        original = str(request.url.copy_with(host=request.headers["host"]))
        calls.append(original)
        assert request.extensions["sni_hostname"] == request.headers["host"]
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if original == source:
            return httpx.Response(302, headers={"location": MAGALU_PRODUCT})
        assert original == MAGALU_PRODUCT
        return httpx.Response(200, text=fixture_product(MAGALU_PRODUCT), headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(source))
    assert calls == [source, MAGALU_PRODUCT]
    assert offer["source_url"] == source
    assert offer["resolved_url"] == MAGALU_PRODUCT
    assert offer["platform"] == "magalu" and offer["title"] == "Produto de teste"
    assert offer["affiliate_url"] is None and offer["affiliate_status"] == "not_converted"


def test_publisher_network_store_chain_preserves_full_query_without_claiming_conversion(monkeypatch):
    calls = []
    redirects = {NETWORK_LINK: AWIN_LINK, AWIN_LINK: KABUM_PRODUCT}

    def handle(request):
        original = str(request.url.copy_with(host=request.headers["host"]))
        calls.append(original)
        assert request.extensions["sni_hostname"] == request.headers["host"]
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if original in redirects:
            return httpx.Response(302, headers={"location": redirects[original]})
        assert original == KABUM_PRODUCT
        return httpx.Response(200, text=fixture_product(KABUM_PRODUCT), headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(NETWORK_LINK))
    assert calls == [NETWORK_LINK, AWIN_LINK, KABUM_PRODUCT]
    assert offer["source_url"] == NETWORK_LINK
    assert offer["resolved_url"] == KABUM_PRODUCT
    assert offer["platform"] == "kabum" and offer["title"] == "Produto de teste"
    assert offer["affiliate_url"] is None and offer["affiliate_status"] == "not_converted"


@pytest.mark.parametrize("target", [
    "https://ofertou.ai.evil.example/CnkMj",
    "https://unregistered.onelink.me/anything",
    "https://127.0.0.1/private",
    "https://www.kabum.com.br:8443/product",
    "https://user:password@www.kabum.com.br/product",
])
def test_publisher_does_not_follow_unknown_or_unsafe_redirects(monkeypatch, target):
    calls = []

    def handle(request):
        calls.append(request.headers["host"])
        return httpx.Response(302, headers={"location": target})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError):
        asyncio.run(mp.extract_offer(NETWORK_LINK))
    assert calls == ["ofertou.ai"]


def test_registered_shortener_is_not_enough_to_claim_product_data(monkeypatch):
    source = "https://magazineluiza.onelink.me/589508454/c9h19gx1"
    calls = []

    def handle(request):
        calls.append(request.headers["host"])
        return httpx.Response(200, text="OK", headers={"content-type": "text/plain"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(source))
    assert calls == ["magazineluiza.onelink.me"]
    assert offer["source_url"] == source
    assert not offer.get("title") and not offer.get("price") and not offer.get("image")
    assert offer["extraction"]["status"] == "not_product"
    assert offer["affiliate_url"] is None
