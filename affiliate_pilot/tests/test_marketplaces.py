"""Public metadata fixtures only: no credentials, external HTTP or real posts."""
import asyncio
import gzip
import json
import socket
from pathlib import Path

import httpx
import pytest

from affiliate_pilot import marketplaces as mp
from affiliate_pilot.providers import ProviderError, normalize_offer, product_ids, product_image, resolve_product


LINKS = json.loads(Path(__file__).with_name("retailer_links.json").read_text())
PRODUCT_URL = "https://www.kabum.com.br/produto/112948/mouse"
IMAGE_URL = "https://images.example.com/product.jpg"
CLIENT_TYPE = httpx.AsyncClient
PRODUCT = {"@type": "Product", "name": "Mouse RGB", "url": PRODUCT_URL,
           "image": [IMAGE_URL], "offers": {"@type": "Offer", "price": "99.90", "priceCurrency": "BRL",
                                           "availability": "https://schema.org/InStock"},
           "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", "reviewCount": "12"}}


def markup(product=PRODUCT, extra=""):
    return '<html><head><script type="application/ld+json">' + json.dumps(product) + '</script>' + extra + '</head></html>'


@pytest.fixture(autouse=True)
def clear_cache():
    mp._cache.clear()
    mp._inflight.clear()
    yield
    mp._cache.clear()
    mp._inflight.clear()


def mock_http(monkeypatch, handler):

    async def addresses(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(mp, "_public_addresses", addresses)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: CLIENT_TYPE(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("link", LINKS, ids=[link["retailer"] for link in LINKS])
def test_all_37_requested_retailers_have_exact_registered_domains(link):
    retailer = mp.retailer_for_url(link["url"])
    assert retailer.id == link["retailer"]
    assert mp.checked_product_url(link["url"], retailer.id) == link["url"]
    assert len(mp.RETAILERS) == len({row.id for row in mp.RETAILERS}) == 37


def test_retailer_capabilities_do_not_claim_automatic_conversion_for_other_stores():
    rows = mp.retailer_options()
    assert len(rows) == 37
    assert [row["id"] for row in rows if row["mode"] == "automatic"] == ["shopee"]
    assert all(row["domains"] and row["name"] for row in rows)


@pytest.mark.parametrize("url", [
    "http://www.kabum.com.br/produto/1", "https://kabum.com.br.evil.example/product/1",
    "https://kabum.com.br@127.0.0.1/product/1", "https://user:pass@www.kabum.com.br/product/1",
    "https://www.kabum.com.br:443/product/1", "https://www.kabum.com.br:8443/product/1",
    "https://www.kabum.com.br\\@evil.example/product/1", "https://www.kabum.com.br/%0afoo",
    "https://www.kabum.com.br/%250dfoo", "https://www.kabum.com.br\n/product/1",
    "https://www.kabum.com.br./product/1", "https://127.0.0.1/product/1", "https://[::1]/product/1",
    "file:///etc/passwd", "data:text/html,test", "https://localhost/private", "https://x.internal/private",
])
def test_url_rejects_ambiguous_or_unregistered_hosts_before_network(url):
    with pytest.raises(ProviderError):
        mp.checked_product_url(url)


def test_product_link_must_match_retailer_and_does_not_infer_affiliation():
    assert mp.checked_product_url("https://amzn.to/example", "amazon") == "https://amzn.to/example"
    with pytest.raises(ProviderError, match="mesma loja"):
        mp.checked_product_url("https://amzn.to/example", "shopee")
    with pytest.raises(ProviderError):
        mp.checked_image_url("https://127.0.0.1/image.jpg", "amazon")
    assert mp.checked_image_url(IMAGE_URL, "amazon") == IMAGE_URL


@pytest.mark.parametrize("url", ["https://awin1.com/cread.php?awinmid=123&awinaffid=456",
                               "https://www.awin1.com/cread.php?awinmid=123&awinaffid=456",
                               "https://tidd.ly/example"])
def test_provided_network_links_are_valid_without_forging_conversion(url):
    assert mp.checked_affiliate_url(url, "magalu") == url
    with pytest.raises(ProviderError):
        mp.checked_product_url(url, "magalu")


def test_magazine_voce_is_a_magalu_domain_and_network_allowlist_is_exact():
    url = "https://www.magazinevoce.com.br/magazineexample/produto/p/123"
    assert mp.checked_affiliate_url(url, "magalu") == url
    with pytest.raises(ProviderError):
        mp.checked_affiliate_url(url, "amazon")
    for bad in ["https://tidd.ly.evil.example/x", "https://127.0.0.1/x", "https://evil.example/?url=awin1.com"]:
        with pytest.raises(ProviderError):
            mp.checked_affiliate_url(bad, "magalu")


def test_network_product_import_follows_known_hops_and_keeps_link_unconverted(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.headers["host"])
        if request.headers["host"] == "tidd.ly":
            return httpx.Response(302, headers={"location": "https://www.awin1.com/cread.php?awinmid=1"})
        if request.headers["host"] == "www.awin1.com":
            return httpx.Response(302, headers={"location": PRODUCT_URL})
        return httpx.Response(200, text=markup(), headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://tidd.ly/fixture"))
    assert offer["platform"] == "kabum" and offer["source_url"] == "https://tidd.ly/fixture"
    assert offer["title"] == "Mouse RGB" and offer["affiliate_url"] is None
    assert offer["affiliate_status"] == "not_converted"
    assert calls == ["tidd.ly", "www.awin1.com", "www.kabum.com.br"]


def test_network_import_never_follows_an_unknown_hop(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://example.com/not-a-retailer"})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError, match="domínio não permitido"):
        asyncio.run(mp.extract_offer("https://tidd.ly/fixture"))
    assert len(calls) == 1


@pytest.mark.parametrize("url,expected", [
    ("https://shopee.com.br/product/11/8", (11, 8)),
    ("https://shopee.com.br/opaanlp/1547311235/22494544030?__mobile__=1&utm_content=----", (1547311235, 22494544030)),
    ("https://shopee.com.br/Fone-i.11.8?from=someone", (11, 8)),
    ("https://shopee.com.br/%70roduct%2F11%2F8", (11, 8)),
    ("https://shopee.com.br/%2570roduct%252F11%252F8", (11, 8)),
    ("https://shopee.com.br/product/0/8", None),
    ("https://shopee.com.br/product/11/0", None),
    ("https://evil.example/product/11/8", None),
    ("http://shopee.com.br/product/11/8", None),
    ("https://shopee.com.br:8443/product/11/8", None),
])
def test_shopee_product_route_rewrites_are_parsed_without_fetch(url, expected):
    assert product_ids(url) == expected


def test_exact_failing_shopee_shortlink_resolves_opaanlp_in_one_request(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["host"] == "s.shopee.com.br"
        assert request.url.path == "/9AQFRAtKYz"
        return httpx.Response(301, headers={"Location": "https://shopee.com.br/opaanlp/1547311235/22494544030?__mobile__=1&utm_content=----"})

    mock_http(monkeypatch, handle)
    assert asyncio.run(resolve_product("https://s.shopee.com.br/9AQFRAtKYz")) == (1547311235, 22494544030)
    assert len(requests) == 1


def test_shopee_encoded_redirect_and_same_store_meta_redirect(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='<meta http-equiv="refresh" content="0; url=https://shopee.com.br/%70roduct%2F11%2F8">')

    mock_http(monkeypatch, handle)
    assert asyncio.run(resolve_product("https://s.shopee.com.br/test")) == (11, 8)
    assert asyncio.run(resolve_product("https://shopee.com.br/share?url=https%253A%252F%252Fshopee.com.br%252Fproduct%252F11%252F8")) == (11, 8)
    assert len(calls) == 1


def test_redirect_to_other_host_is_not_followed(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/admin"})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError):
        asyncio.run(resolve_product("https://s.shopee.com.br/test"))
    assert len(calls) == 1


def test_redirect_loop_and_limit_are_bounded(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "/" + str(len(calls))})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError, match="redirecionamentos demais"):
        asyncio.run(mp._fetch_document("https://amzn.to/initial", mp.BY_ID["amazon"]))
    assert len(calls) == mp.MAX_REDIRECTS + 1


def test_fetch_pins_validated_ip_preserves_tls_and_does_not_forward_auth(monkeypatch):
    def handle(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "www.kabum.com.br"
        assert request.extensions["sni_hostname"] == "www.kabum.com.br"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, text=markup())

    mock_http(monkeypatch, handle)
    response = asyncio.run(mp._fetch_document(PRODUCT_URL, mp.BY_ID["kabum"]))
    assert response.url == PRODUCT_URL
    assert response.status == 200


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["169.254.169.254"], ["10.0.0.1"], ["::1"], ["fc00::1"],
                                         ["224.0.0.1"], ["ff02::1"], ["93.184.216.34", "192.168.1.1"], []])
def test_dns_rejects_all_private_and_mixed_answers(monkeypatch, addresses):
    async def run():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET6 if ":" in value else socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 443)) for value in addresses]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(ProviderError, match="seguran"):
            await mp._public_addresses("cdn.example.com")
    asyncio.run(run())


def test_jsonld_preserves_verified_fields_without_creating_affiliation():
    offer = mp.parse_html_offer(markup(), PRODUCT_URL)
    assert offer["title"] == "Mouse RGB"
    assert offer["price"] == offer["price_max"] == "99.90"
    assert offer["currency"] == "BRL" and offer["availability"] is True
    assert offer["rating"] == "4.8" and offer["rating_scale"] == "5" and offer["review_count"] == 12
    assert offer["image"] == IMAGE_URL and offer["images"] == [IMAGE_URL]
    assert offer["identity"] == "kabum:112948"
    assert offer["affiliate_url"] is None and offer["affiliate_status"] == "not_converted"
    assert offer["commission"] is None and offer["sales"] is None and offer["coupon"] is None
    assert offer["extraction"]["status"] == "complete"


def test_missing_fields_are_null_and_plain_text_claims_are_not_scraped_as_facts():
    offer = mp.parse_html_offer('<h1>Super oferta 99,90, 5 estrelas, 1000 vendas</h1>', PRODUCT_URL)
    assert all(offer[key] is None for key in ("title", "price", "rating", "review_count", "sales", "commission", "image"))
    assert offer["extraction"]["status"] == "partial"
    assert set(offer["extraction"]["missing"]) == {"title", "price", "currency", "image", "rating"}


@pytest.mark.parametrize("value,expected", [(True, None), (False, None), ("NaN", None), ("Infinity", None),
                                          ("-1", None), ("0", None), ("R$ 1.234,56", "1234.56"),
                                          ("1,234", None), ("10x R$ 9,90", None), ("99.90", "99.90"),
                                          ("1e99999", None), ("1e-100000000", None), ("9.99-19.99", None)])
def test_prices_accept_unambiguous_amounts_and_never_guess(value, expected):
    assert mp.number_text(value) == expected


def test_aggregate_offer_preserves_range_and_original_rating_scale():
    product = {**PRODUCT, "offers": {"@type": "AggregateOffer", "lowPrice": "20.50", "highPrice": "40.99", "priceCurrency": "BRL"},
               "aggregateRating": {"ratingValue": "87", "bestRating": "100", "ratingCount": "24"}}
    offer = mp.parse_html_offer(markup(product), PRODUCT_URL)
    assert offer["price"] == "20.50" and offer["price_max"] == "40.99"
    assert offer["rating"] == "87" and offer["rating_scale"] == "100" and offer["review_count"] == 24
    assert offer["previous_price"] is None and offer["discount_rate"] is None


def test_mixed_currencies_and_invalid_ratings_stay_unknown():
    product = {**PRODUCT, "offers": [{"price": 10, "priceCurrency": "BRL"}, {"price": 20, "priceCurrency": "USD"}],
               "aggregateRating": {"ratingValue": 6, "bestRating": 5, "reviewCount": True}}
    offer = mp.parse_html_offer(markup(product), PRODUCT_URL)
    assert offer["price"] is None and offer["currency"] is None
    assert offer["rating"] is None and offer["review_count"] is None


def test_graph_selects_matching_product_and_does_not_import_related_products():
    other = {**PRODUCT, "url": "https://www.kabum.com.br/produto/99/outro", "name": "Outro", "offers": {"price": 1, "priceCurrency": "BRL"}}
    graph = {"@graph": [other, PRODUCT, {"@type": "ItemList", "itemListElement": [{"item": other}]}]}
    assert mp.parse_html_offer(markup(graph), PRODUCT_URL)["title"] == "Mouse RGB"
    assert mp.parse_html_offer(markup({"@type": "ItemList", "itemListElement": [{"item": other}]}), PRODUCT_URL)["price"] is None
    assert mp.parse_html_offer(markup(other), PRODUCT_URL)["title"] is None


def test_ambiguous_multiple_products_are_not_arbitrarily_chosen():
    one = {"@type": "Product", "name": "Primeiro", "offers": {"price": 1, "priceCurrency": "BRL"}}
    two = {**one, "name": "Segundo"}
    assert mp.parse_html_offer(markup([one, two]), PRODUCT_URL)["price"] is None


def test_carrefour_matches_explicit_sku_when_legacy_jsonld_id_differs():
    url = "https://www.carrefour.com.br/produto/cafeteira-arno-7304311"
    product = {**PRODUCT, "url": None, "@id": "https://www.carrefour.com.br/cafeteira-5869595/p", "sku": "7304311"}
    offer = mp.parse_html_offer(markup(product), url)
    assert offer["title"] == "Mouse RGB" and offer["price"] == "99.90"
    unrelated = {**product, "sku": "7304312"}
    assert mp.parse_html_offer(markup(unrelated), url)["price"] is None


def test_product_group_out_of_stock_does_not_use_list_price_as_sale_price():
    group = {"@type": "ProductGroup", "url": "https://www.decathlon.com.br/garrafa/p", "name": "Garrafa", "image": IMAGE_URL,
             "hasVariant": [{"@type": "Product", "sku": "123", "offers": {"@type": "Offer", "price": 0,
                "priceCurrency": "BRL", "availability": "https://schema.org/OutOfStock",
                "priceSpecification": [{"@type": "UnitPriceSpecification", "priceType": "https://schema.org/ListPrice", "price": 29.99}]}}]}
    offer = mp.parse_html_offer(markup(group), group["url"])
    assert offer["title"] == "Garrafa" and offer["image"] == IMAGE_URL
    assert offer["price"] is None and offer["availability"] is False and offer["currency"] == "BRL"


def test_product_group_prices_keep_range_or_specific_selected_sku():
    url = "https://www.decathlon.com.br/garrafa/p"
    group = {"@type": "ProductGroup", "url": url, "name": "Garrafa", "hasVariant": [
        {"@type": "Product", "sku": "1", "offers": {"price": 20, "priceCurrency": "BRL"}},
        {"@type": "Product", "sku": "2", "offers": {"price": 30, "priceCurrency": "BRL"}}]}
    offer = mp.parse_html_offer(markup(group), url)
    assert offer["price"] == "20" and offer["price_max"] == "30"
    # The page canonical associates the group with its explicit SKU selection.
    offer = mp.parse_html_offer(markup(group, '<link rel="canonical" href="' + url + '">'), url + "?skuId=2")
    assert offer["price"] == offer["price_max"] == "30"
    assert offer["catalog_url"] == url + "?skuId=2"


AMAZON_URL = "https://www.amazon.com.br/Echo/dp/B09ZXN77L2"
AMAZON_HTML = '''<input id="ASIN" value="B09ZXN77L2">
  <span id="productTitle">Produto principal</span>
  <img id="landingImage" data-a-dynamic-image='{"https://m.media-amazon.com/a-small.jpg":[300,300],"https://m.media-amazon.com/a-large.jpg":[700,700]}'>
  <span id="acrPopover" title="4,8 de 5 estrelas"><span>4,8</span></span>
  <span id="acrCustomerReviewText" aria-label="79.411 Análises">(79.411)</span>
  <div id="corePrice_feature_div"><span class="a-price apex-pricetopay-value"><span class="a-offscreen">R$399,00</span></span>
    <span class="a-price a-text-price"><span class="a-offscreen">R$499,00</span></span></div>
  <div id="recommendations"><span class="a-price"><span class="a-offscreen">R$1,00</span></span></div>'''


def test_amazon_public_dom_extracts_only_current_main_product_fields():
    offer = mp.parse_html_offer(AMAZON_HTML, AMAZON_URL)
    assert offer["title"] == "Produto principal" and offer["price"] == "399.00" and offer["currency"] == "BRL"
    assert offer["rating"] == "4.8" and offer["review_count"] == 79411
    assert offer["images"] == ["https://m.media-amazon.com/a-large.jpg"]
    assert offer["previous_price"] is None and offer["affiliate_url"] is None
    assert offer["extraction"]["method"] == "amazon_public_page"


def test_amazon_conditional_prices_and_mismatching_asin_are_not_guessed():
    extra = '<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">R$299,00</span></span></div>'
    offer = mp.parse_html_offer(AMAZON_HTML + extra, AMAZON_URL)
    assert offer["price"] is None
    other = mp.parse_html_offer(AMAZON_HTML.replace('value="B09ZXN77L2"', 'value="B000000000"'), AMAZON_URL)
    assert other["title"] is None and other["price"] is None


def test_large_successful_public_pages_still_parse_bounded_jsonld():
    page = " " * 2_100_000 + markup()
    offer = mp.parse_html_offer(page, PRODUCT_URL)
    assert offer["title"] == "Mouse RGB" and mp.HTML_LIMIT == 8_000_000


def test_open_graph_and_image_lists_are_sanitized_and_deduplicated():
    page = '''<meta property="og:title" content="Fone &amp; capa">
      <meta property="product:price:amount" content="R$ 1.234,56">
      <meta property="product:price:currency" content="BRL">
      <meta property="og:image" content="https://images.example.com/a.jpg">
      <meta property="og:image" content="https://images.example.com/a.jpg">
      <meta property="og:image" content="https://127.0.0.1/private.jpg">
      <meta property="og:image" content="javascript:alert(1)">
      <meta property="og:image" content="data:image/png;base64,a">'''
    offer = mp.parse_html_offer(page, PRODUCT_URL)
    assert offer["title"] == "Fone & capa" and offer["price"] == "1234.56"
    assert offer["images"] == ["https://images.example.com/a.jpg"]
    assert offer["rating"] is None and offer["extraction"]["status"] == "partial"


def test_invalid_json_and_foreign_canonical_cannot_replace_origin():
    page = '<script type="application/ld+json">{invalid</script><link rel="canonical" href="https://evil.example/item">'
    offer = mp.parse_html_offer(page, PRODUCT_URL)
    assert offer["catalog_url"] == PRODUCT_URL and offer["title"] is None


@pytest.mark.parametrize("path", ["/dp/B09ZXN77L2", "/gp/product/B09ZXN77L2",
                                  "/gp/aw/d/B09ZXN77L2", "/exec/obidos/ASIN/B09ZXN77L2"])
def test_amazon_public_paths_share_sku_and_do_not_replace_selected_asin_with_parent(path):
    url = "https://www.amazon.com.br" + path + "?tag=fixture-owner-20"
    page = AMAZON_HTML + '<link rel="canonical" href="https://www.amazon.com.br/dp/B000000000">'
    offer = mp.parse_html_offer(page, url)
    assert offer["identity"] == "amazon:B09ZXN77L2"
    assert offer["title"] == "Produto principal" and offer["price"] == "399.00"
    assert offer["image"] == "https://m.media-amazon.com/a-large.jpg"
    assert offer["catalog_url"] == offer["source_url"] == url
    assert offer["affiliate_url"] is None
    mismatch = mp.parse_html_offer(page.replace('value="B09ZXN77L2"', 'value="B000000000"'), url)
    assert mismatch["title"] is None and mismatch["price"] is None


def test_jsonld_document_references_resolve_product_offer_image_and_rating_without_network():
    nodes = [
        {"@type": "WebPage", "@id": PRODUCT_URL, "mainEntity": {"@id": "#product"}},
        {"@type": "Product", "@id": "#product", "url": PRODUCT_URL, "name": "Mouse do produto",
         "offers": {"@id": "#offer"}, "image": {"@id": "#photo"}, "aggregateRating": {"@id": "#rating"}},
        {"@type": "Offer", "@id": "#offer", "price": "139.90", "priceCurrency": "BRL"},
        {"@type": "ImageObject", "@id": "#photo", "contentUrl": IMAGE_URL},
        {"@type": "AggregateRating", "@id": "#rating", "ratingValue": "4.7", "reviewCount": "24"},
        {"@type": "Offer", "@id": "#unrelated-offer", "price": "1.00", "priceCurrency": "BRL"},
    ]
    # Definitions may be in another JSON-LD script on the same page.
    page = markup({"@graph": nodes[:2]}) + markup({"@graph": nodes[2:]})
    offer = mp.parse_html_offer(page, PRODUCT_URL)
    assert offer["title"] == "Mouse do produto" and offer["price"] == "139.90"
    assert offer["image"] == IMAGE_URL and offer["rating"] == "4.7" and offer["review_count"] == 24
    assert offer["affiliate_url"] is None and offer["extraction"]["method"] == "json_ld"


def test_jsonld_conflicting_or_unresolved_offer_ids_never_borrow_another_price():
    product = {**PRODUCT, "offers": {"@id": "#price"}}
    conflict = {"@graph": [product,
        {"@type": "Offer", "@id": "#price", "price": "10", "priceCurrency": "BRL"},
        {"@type": "Offer", "@id": "#price", "price": "20", "priceCurrency": "BRL"}]}
    assert mp.parse_html_offer(markup(conflict), PRODUCT_URL)["price"] is None
    unresolved = {"@graph": [{**product, "offers": {"@id": "https://external.example/offer"}},
                            {"@type": "Offer", "price": "1", "priceCurrency": "BRL"}]}
    assert mp.parse_html_offer(markup(unresolved), PRODUCT_URL)["price"] is None
    wrong_type = {"@graph": [product, {"@type": "Product", "@id": "#price", "price": "9", "priceCurrency": "BRL"}]}
    assert mp.parse_html_offer(markup(wrong_type), PRODUCT_URL)["price"] is None


def test_jsonld_cycles_and_recommendation_lists_cannot_supply_product_prices():
    graph = {"@graph": [
        {**PRODUCT, "@id": "#main", "offers": {"@id": "#cycle"}},
        {"@type": "Offer", "@id": "#cycle", "priceSpecification": {"@id": "#cycle"}},
        {"@type": "ItemList", "itemListElement": [{"item": {**PRODUCT, "offers": {"price": "1", "priceCurrency": "BRL"}}}]},
    ]}
    offer = mp.parse_html_offer(markup(graph), PRODUCT_URL)
    assert offer["title"] == "Mouse RGB" and offer["price"] is None
    assert mp.parse_html_offer(markup(graph["@graph"][-1]), PRODUCT_URL)["title"] is None


def variant_graph():
    base = "https://www.decathlon.com.br/garrafa/p"
    group = {"@type": "ProductGroup", "@id": "#group", "url": base, "name": "Garrafa",
             "image": "https://images.example.com/group-red.jpg", "hasVariant": [{"@id": "#red"}, {"@id": "#blue"}]}
    return base, {"@graph": [group,
        {"@type": "Product", "@id": "#red", "url": base + "?color=red", "sku": "sku-red", "color": "Vermelho",
         "name": "Garrafa vermelha", "image": "https://images.example.com/red.jpg", "offers": {"@id": "#red-price"}},
        {"@type": "Product", "@id": "#blue", "url": base + "?color=blue", "sku": "sku-blue", "color": "Azul",
         "name": "Garrafa azul", "image": "https://images.example.com/blue.jpg", "offers": {"@id": "#blue-price"}},
        {"@type": "Offer", "@id": "#red-price", "price": "10", "priceCurrency": "BRL"},
        {"@type": "Offer", "@id": "#blue-price", "price": "20", "priceCurrency": "BRL"},
    ]}


@pytest.mark.parametrize("query", ["?color=blue", "?skuId=sku-blue", "?variantId=sku-blue"])
def test_product_group_resolves_selected_variant_price_image_and_keeps_selection(query):
    base, graph = variant_graph()
    url = base + query
    offer = mp.parse_html_offer(markup(graph, '<link rel="canonical" href="' + base + '">'), url)
    assert offer["title"] == "Garrafa azul" and offer["price"] == offer["price_max"] == "20"
    assert offer["image"] == "https://images.example.com/blue.jpg"
    assert offer["variant"]["sku"] == "sku-blue" and offer["variant"]["color"] == "Azul"
    assert offer["catalog_url"] == offer["source_url"] == url
    assert offer["affiliate_url"] is None


def test_unknown_variant_does_not_use_group_price_image_or_default_open_graph():
    base, graph = variant_graph()
    group = graph["@graph"][0]
    group["offers"] = {"price": "10", "priceCurrency": "BRL"}
    page = markup(graph, '<link rel="canonical" href="' + base + '"><meta property="product:price:amount" content="10">'
                  '<meta property="product:price:currency" content="BRL"><meta property="og:image" content="https://images.example.com/red.jpg">')
    offer = mp.parse_html_offer(page, base + "?color=green")
    assert offer["price"] is None and offer["image"] is None
    assert offer["catalog_url"] == base + "?color=green"
    assert "variante" in offer["extraction"]["reason"]


def test_variant_without_own_price_or_image_does_not_inherit_group_or_recommendations():
    base, graph = variant_graph()
    graph["@graph"][2].pop("offers")
    graph["@graph"][2].pop("image")
    offer = mp.parse_html_offer(markup(graph, '<link rel="canonical" href="' + base + '">'), base + "?color=blue")
    assert offer["title"] == "Garrafa azul" and offer["variant"]["sku"] == "sku-blue"
    assert offer["price"] is None and offer["image"] is None


def test_canonical_cannot_authorize_the_price_of_another_explicit_amazon_asin():
    url = "https://www.amazon.com.br/dp/B000000001"
    other = "https://www.amazon.com.br/dp/B000000002"
    product = {**PRODUCT, "url": other, "name": "Outro SKU", "offers": {"price": "500", "priceCurrency": "BRL"}}
    offer = mp.parse_html_offer(markup(product, '<link rel="canonical" href="' + other + '">'), url)
    assert offer["identity"] == "amazon:B000000001" and offer["catalog_url"] == url
    assert offer["title"] is None and offer["price"] is None and offer["image"] is None


def test_explicit_wrong_amazon_page_asin_rejects_all_metadata_including_open_graph():
    url = "https://www.amazon.com.br/dp/B000000001"
    page = (AMAZON_HTML.replace('value="B09ZXN77L2"', 'value="B000000002"')
            + markup({**PRODUCT, "url": url, "offers": {"price": "500", "priceCurrency": "BRL"}})
            + '<meta property="og:title" content="Outro SKU"><meta property="og:image" content="' + IMAGE_URL + '">'
            '<meta property="product:price:amount" content="500"><meta property="product:price:currency" content="BRL">')
    offer = mp.parse_html_offer(page, url)
    assert offer["extraction"]["code"] == "product_mismatch"
    assert all(offer[field] is None for field in ("title", "price", "image", "rating", "review_count"))
    assert offer["identity"] == "amazon:B000000001" and offer["catalog_url"] == url


def test_unknown_variant_never_falls_back_to_an_aggregate_vtex_product(monkeypatch):
    base, graph = variant_graph()
    page = markup(graph, '<link rel="canonical" href="' + base + '"><script src="https://assets.vtexassets.com/runtime.js"></script>')
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.url.path == "/garrafa/p", "An unresolved SKU must not consult the aggregate catalogue"
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(base + "?color=green"))
    assert offer["extraction"]["code"] == "variant_unresolved"
    assert offer["price"] is None and offer["image"] is None and calls == ["/garrafa/p"]


def test_selected_variant_without_own_price_never_uses_aggregate_vtex_prices(monkeypatch):
    base, graph = variant_graph()
    graph["@graph"][2].pop("offers")
    page = markup(graph, '<link rel="canonical" href="' + base + '"><script src="https://assets.vtexassets.com/runtime.js"></script>')
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.url.path == "/garrafa/p", "A selected variant must not consult aggregate catalogue prices"
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(base + "?color=blue"))
    assert offer["variant"]["sku"] == "sku-blue" and offer["price"] is None
    assert offer["image"] == "https://images.example.com/blue.jpg" and calls == ["/garrafa/p"]


@pytest.mark.parametrize("query", ["?promoter_id=1799284&partner_id=3440",
    "?onelink=4/fixture&requestId=synthetic&campaign_id=20&url_from=affiliate_fixture"])
def test_attribution_parameters_do_not_turn_a_product_group_into_an_unknown_variant(query):
    base = "https://www.magazineluiza.com.br/garrafa/p/1234/ud/garr"
    group = {"@type": "ProductGroup", "url": base, "name": "Garrafa", "image": IMAGE_URL,
             "offers": {"@type": "AggregateOffer", "lowPrice": "99.90", "highPrice": "129.90", "priceCurrency": "BRL"},
             "hasVariant": [{"@type": "Product", "sku": "1"}, {"@type": "Product", "sku": "2"}]}
    offer = mp.parse_html_offer(markup(group, '<link rel="canonical" href="' + base + '">'), base + query)
    assert offer["price"] == "99.90" and offer["price_max"] == "129.90" and offer["image"] == IMAGE_URL
    assert offer["source_url"] == base + query and offer["variant"] is None
    assert offer["extraction"]["code"] is None


def test_ambiguous_duplicate_variant_selectors_do_not_pick_one_value():
    base, graph = variant_graph()
    url = base + "?skuId=sku-red&skuId=sku-blue"
    offer = mp.parse_html_offer(markup(graph, '<link rel="canonical" href="' + base + '">'), url)
    assert offer["price"] is None and offer["image"] is None
    assert offer["extraction"]["code"] == "variant_unresolved" and offer["catalog_url"] == url


def test_a_product_list_price_specification_is_not_a_current_sale_price():
    product = {**PRODUCT, "offers": {"@type": "Offer", "priceCurrency": "BRL", "price": 0,
               "priceSpecification": {"@type": "UnitPriceSpecification", "priceType": "https://schema.org/ListPrice", "price": 200}}}
    assert mp.parse_html_offer(markup(product), PRODUCT_URL)["price"] is None


def test_shein_public_onelink_uses_unique_declared_web_product_without_executing_javascript():
    short = "https://onelink.shein.com/4/fixture"
    target = "http://br.shein.com/Women-Shorts-p-32211486-cat-1912.html?onelink=4/fixture&url_from=affiliate_fixture"
    page = ('<meta property="og:title" content="Kit de shorts"><meta property="og:image" content="' + IMAGE_URL + '">'
            '<input id="url" value="' + target.replace('&', '&amp;') + '" style="display:none">')
    offer = mp.parse_html_offer(page, short)
    assert offer["identity"] == "shein:32211486" and offer["catalog_url"] == target.replace("http:", "https:", 1)
    assert offer["title"] == "Kit de shorts" and offer["image"] == IMAGE_URL
    assert offer["price"] is None and offer["source_url"] == short and offer["affiliate_url"] is None
    ambiguous = mp.parse_html_offer(page + '<input id="url" value="https://br.shein.com/Other-p-42.html">', short)
    assert ambiguous["catalog_url"] == short
    assert mp.parse_html_offer(page, "https://br.shein.com/other-page")["catalog_url"] == "https://br.shein.com/other-page"
    malicious = mp.parse_html_offer(page.replace(target.replace('&', '&amp;'), 'http://br.shein.com@127.0.0.1/private'), short)
    assert malicious["catalog_url"] == short


def vtex_data(*, quantity=5, currency="BRL", price=89.9):
    return [{"productName": "Café", "productId": "123", "linkText": "cafe", "items": [
        {"itemId": "42", "images": [{"imageUrl": IMAGE_URL}], "sellers": [
            {"commertialOffer": {"Price": price, "ListPrice": 99.9, "AvailableQuantity": quantity, "PriceCurrency": currency}}
        ]}
    ]}]


def test_vtex_public_catalogue_selects_exact_product_and_actual_prices():
    offer = mp.parse_vtex_offer(vtex_data(), "https://www.cea.com.br/cafe/p")
    assert offer["title"] == "Café" and offer["price"] == "89.9" and offer["previous_price"] == "99.9"
    assert offer["currency"] == "BRL" and offer["images"] == [IMAGE_URL]
    assert offer["rating"] is None and offer["sales"] is None and offer["availability"] is True
    assert mp.parse_vtex_offer(vtex_data(), "https://www.cea.com.br/outro/p") is None


def test_vtex_does_not_infer_currency_or_inventory_counts_and_skips_unavailable_price():
    unknown = mp.parse_vtex_offer(vtex_data(currency=None, quantity=None), "https://www.cea.com.br/cafe/p")
    assert unknown["currency"] is None and unknown["availability"] is None and unknown["price"] == "89.9"
    unavailable = mp.parse_vtex_offer(vtex_data(quantity=0), "https://www.cea.com.br/cafe/p")
    assert unavailable["availability"] is False and unavailable["price"] is None


def test_vtex_is_only_requested_after_successful_page_that_identifies_it(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if "/api/" in request.url.path:
            return httpx.Response(200, json=vtex_data())
        return httpx.Response(200, text='<script src="https://example.vtexassets.com/app.js"></script>', headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://www.cea.com.br/cafe/p"))
    assert offer["title"] == "Café" and offer["price"] == "89.9"
    assert calls == ["/cafe/p", "/api/catalog_system/pub/products/search/cafe/p"]


@pytest.mark.parametrize("status,page", [(403, "blocked"), (429, "limited"),
                                       (200, '<title>Just a moment...</title><script src="https://example.vtexassets.com/app.js"></script>')])
def test_botwall_becomes_editable_draft_and_never_attempts_another_endpoint(monkeypatch, status, page):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text=page, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://www.cea.com.br/cafe/p"))
    assert offer["extraction"]["status"] == "blocked" and offer["title"] is None
    assert offer["price"] is None and offer["affiliate_url"] is None
    assert len(calls) == 1


def test_public_extractions_are_deduplicated_cached_and_caller_isolated(monkeypatch):
    calls = []

    async def handle(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, text=markup(), headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)

    async def run():
        first, second = await asyncio.gather(mp.extract_offer(PRODUCT_URL), mp.extract_offer(PRODUCT_URL))
        first["title"] = "edited"
        first["images"].append("local only")
        first["private"] = "tenant-one"
        third = await mp.extract_offer(PRODUCT_URL)
        assert second == third
        assert third["title"] == "Mouse RGB" and "private" not in third
        assert third["images"] == [IMAGE_URL]
    asyncio.run(run())
    assert len(calls) == 1


def test_public_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(mp, "CACHE_LIMIT", 2)
    mock_http(monkeypatch, lambda request: httpx.Response(200, text='<meta property="og:title" content="Preview">'))

    async def run():
        for ident in range(4):
            await mp.extract_offer(f"https://www.kabum.com.br/produto/{ident}/mouse")
    asyncio.run(run())
    assert len(mp._cache) == 2


def test_shared_request_survives_one_cancelled_caller(monkeypatch):
    started = None
    release = None
    calls = []

    async def public(url, retailer):
        calls.append(url)
        started.set()
        await release.wait()
        return mp.parse_html_offer(markup(), url)

    monkeypatch.setattr(mp, "_public_offer", public)

    async def run():
        nonlocal started, release
        started, release = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(mp.extract_offer(PRODUCT_URL))
        second = asyncio.create_task(mp.extract_offer(PRODUCT_URL))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["title"] == "Mouse RGB"
    asyncio.run(run())
    assert len(calls) == 1 and mp._inflight == {}


def test_shopee_authorized_results_are_not_shared_between_credentials(monkeypatch):
    calls = []

    class Client:
        def __init__(self, label):
            self.label = label

        async def detail(self, shop, item):
            calls.append(self.label)
            return normalize_offer({"shopId": shop, "itemId": item, "productName": self.label, "priceMin": "10"})

    async def run():
        first = await mp.extract_offer("https://shopee.com.br/opaanlp/11/8", shopee=Client("One"))
        second = await mp.extract_offer("https://shopee.com.br/opaanlp/11/8", shopee=Client("Two"))
        assert first["title"] == "One" and second["title"] == "Two"
        assert first["affiliate_url"] is None and second["affiliate_url"] is None
    asyncio.run(run())
    assert calls == ["One", "Two"]


def test_missing_shopee_catalogue_product_preserves_editable_draft_and_api_reason(monkeypatch):
    class Client:
        async def detail(self, shop, item):
            raise ProviderError("product", "Produto ausente do catálogo de afiliados.")

    mock_http(monkeypatch, lambda request: httpx.Response(403, text="blocked"))
    offer = asyncio.run(mp.extract_offer("https://shopee.com.br/opaanlp/11/8", shopee=Client()))
    assert offer["shop"] == 11 and offer["item"] == 8
    assert offer["catalog_url"] == "https://shopee.com.br/product/11/8"
    assert offer["title"] is None and offer["price"] is None and offer["affiliate_url"] is None
    assert offer["extraction"]["api_unavailable"] is True
    assert offer["extraction"]["status"] == "partial" and "catálogo" in offer["extraction"]["reason"]


@pytest.mark.parametrize("code", ["access", "rate"])
def test_shopee_credentials_and_rate_failures_do_not_become_successful_extractions(monkeypatch, code):
    class Client:
        async def detail(self, shop, item):
            raise ProviderError(code, "Falha da conta.")

    def handle(request):
        pytest.fail("Must not try another endpoint after credential/rate failure")

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError) as failure:
        asyncio.run(mp.extract_offer("https://shopee.com.br/product/11/8", shopee=Client()))
    assert failure.value.code == code


def test_query_field_limit_and_encoded_sanitization_are_validation_errors():
    with pytest.raises(ProviderError):
        mp.checked_product_url(PRODUCT_URL + "?" + "&".join(f"k{index}=1" for index in range(101)))
    assert product_ids("https://shopee.com.br/Fone%20Azul-i.11.8") == (11, 8)


@pytest.mark.parametrize("declared", ["101", "1"])
def test_size_limits_apply_before_parsing_and_to_streams(monkeypatch, declared):
    def handle(request):
        return httpx.Response(200, content=b"x" * 101, headers={"content-type": "text/html", "content-length": declared})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError, match="tamanho"):
        asyncio.run(mp._request_once(PRODUCT_URL, accept="text/html", limit=100))


@pytest.mark.parametrize("size,limit", [(99, 100), (100_000, 100)])
def test_compressed_body_is_bounded_before_allocating_full_output(monkeypatch, size, limit):
    compressed = gzip.compress(b"x" * size)

    def handle(request):
        return httpx.Response(200, stream=httpx.ByteStream(compressed), headers={"content-encoding": "gzip"})

    mock_http(monkeypatch, handle)
    if size > limit:
        with pytest.raises(ProviderError, match="tamanho"):
            asyncio.run(mp._request_once(PRODUCT_URL, accept="text/html", limit=limit))
    else:
        response = asyncio.run(mp._request_once(PRODUCT_URL, accept="text/html", limit=limit))
        assert response.body == b"x" * size


def test_login_and_browser_challenge_pages_do_not_become_product_metadata():
    login = '<h1>Para continuar, <span>acesse sua conta</span></h1>'
    offer = mp.parse_html_offer(login, "https://www.mercadolivre.com.br/p/MLB26004393")
    assert offer["extraction"]["status"] == "blocked"
    challenge = '<script src="https://static.kwcdn.com/upload-static/assets/chl/js/example.js"></script>'
    offer = mp.parse_html_offer(challenge, "https://www.temu.com/br/product-g-123.html")
    assert offer["extraction"]["status"] == "blocked"


def test_public_timeout_is_a_partial_unknown_draft(monkeypatch):
    async def fetch(*args):
        raise TimeoutError()

    monkeypatch.setattr(mp, "_fetch_document", fetch)
    offer = asyncio.run(mp.extract_offer(PRODUCT_URL))
    assert offer["extraction"]["code"] == "timeout" and offer["price"] is None and offer["title"] is None


def test_image_download_revalidates_each_redirect_and_checks_mime(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://another.example.com/product.png"})
        return httpx.Response(200, content=b"image fixture", headers={"content-type": "image/png"})

    mock_http(monkeypatch, handle)
    assert asyncio.run(product_image(IMAGE_URL)) == b"image fixture"
    assert [request.headers["host"] for request in calls] == ["images.example.com", "another.example.com"]
    mock_http(monkeypatch, lambda request: httpx.Response(200, text="not an image"))
    with pytest.raises(ProviderError, match="imagem"):
        asyncio.run(product_image(IMAGE_URL))


def test_shopee_api_normalization_retains_existing_ids_and_unknowns():
    offer = normalize_offer({"shopId": "11", "itemId": "8", "productName": "Fone", "ratingStar": "99"})
    assert offer["shop"] == 11 and offer["item"] == 8 and offer["platform"] == "shopee"
    assert offer["rating"] is None and offer["price"] is None and offer["images"] == []


@pytest.mark.parametrize("host,platform", [
    ("amzon.promo", "amazon"), ("link.amazon", "amazon"),
    ("amzlinks.in", "amazon"), ("meli.promo", "mercado_livre"),
])
def test_observed_shortener_hosts_are_exact_and_bound_to_one_platform(host, platform):
    url = f"https://{host}/s/example?tag=owner-fixture-20"
    assert mp.retailer_for_url(url).id == platform
    assert mp.checked_affiliate_url(url, platform) == url
    for wrong in (f"https://www.{host}/x", f"https://{host}.evil.example/x"):
        with pytest.raises(ProviderError):
            mp.checked_product_url(wrong)
    with pytest.raises(ProviderError):
        mp.checked_product_url(url, "shopee")


def test_observed_amazon_shortener_chain_keeps_each_url_and_attribution_separate(monkeypatch):
    chain = ["https://amzon.promo/s/hxR0xZ", "https://link.amazon/B0hMf4A8k",
             "https://amzlinks.in/B0hMf4A8k",
             "https://www.amazon.com.br/Sony-PlayStation/dp/B0GWNKJDCZ?tag=owner-fixture-20&linkId=keep%3Dthis&ref_=as_li_ss_tl"]
    calls = []

    def handle(request):
        actual = str(request.url.copy_with(host=request.headers["host"]))
        calls.append(actual)
        assert actual == chain[len(calls) - 1]
        if len(calls) < len(chain):
            return httpx.Response(302, headers={"location": chain[len(calls)]})
        return httpx.Response(200, text=AMAZON_HTML.replace("B09ZXN77L2", "B0GWNKJDCZ"),
                              headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(chain[0]))
    assert calls == chain and offer["redirect_chain"] == chain
    assert offer["source_url"] == chain[0] and offer["resolved_url"] == chain[-1]
    assert offer["catalog_url"] == chain[-1] and offer["http_status"] == 200
    assert offer["identity"] == "amazon:B0GWNKJDCZ"
    assert offer["title"] == "Produto principal" and offer["price"] == "399.00"
    assert offer["affiliate_url"] is None and offer["affiliate_status"] == "not_converted"


def test_observed_meli_promo_chain_preserves_blocked_destination_without_retry(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.headers["host"])
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://meli.la/1v5xsww"})
        return httpx.Response(403, text="Access denied", headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://meli.promo/s/aKaakx"))
    assert calls == ["meli.promo", "meli.la"]
    assert offer["source_url"] == "https://meli.promo/s/aKaakx"
    assert offer["resolved_url"] == "https://meli.la/1v5xsww" and offer["http_status"] == 403
    assert offer["extraction"]["status"] == "blocked" and offer["title"] is None


@pytest.mark.parametrize("status", [200, 404])
def test_mercado_livre_social_profile_and_lists_are_not_products(monkeypatch, status):
    chain = ["https://meli.la/11F9L45", "https://www.mercadolivre.com.br/social/sk20250723214017?matt_tool=fixture",
             "https://www.mercadolivre.com.br/social/sk20250723214017/lists"]
    calls = []

    def handle(request):
        calls.append(str(request.url.copy_with(host=request.headers["host"])))
        if len(calls) < len(chain):
            return httpx.Response(301 if len(calls) == 1 else 302, headers={"location": chain[len(calls)]})
        return httpx.Response(status, text='<title>Perfil social</title><meta property="og:title" content="Minhas ofertas">',
                              headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(chain[0]))
    assert calls == chain and offer["redirect_chain"] == chain
    assert offer["resolved_url"] == chain[-1] and offer["http_status"] == status
    assert offer["extraction"]["status"] == offer["extraction"]["code"] == "not_product"
    assert "perfil ou uma lista" in offer["extraction"]["reason"]
    assert offer["title"] is None and offer["price"] is None and offer["image"] is None


def test_exact_amazon_503_shape_is_retried_once_without_following_error_page(monkeypatch):
    source = "https://amzn.to/3QLdZgA"
    target = "https://www.amazon.com.br/dp/B0FY6Y9WZT?th=1&linkCode=sl2&tag=owner-fixture-20&ref_=as_li_ss_tl"
    calls, waits = [], []

    async def sleep(delay):
        waits.append(delay)

    def handle(request):
        actual = str(request.url.copy_with(host=request.headers["host"]))
        calls.append(actual)
        if actual == source:
            return httpx.Response(301, headers={"location": target})
        assert actual == target
        return httpx.Response(503, text='''<html><head><noscript><meta http-equiv="refresh"
          content="0; url=https://www.amazon.com.br/error/500"></noscript>
          <script>window.location.replace("https://www.amazon.com.br/error/500");</script></head></html>''',
                              headers={"content-type": "text/html"})

    monkeypatch.setattr(mp.asyncio, "sleep", sleep)
    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(source))
    assert calls == [source, target, target] and waits == [0.25]
    assert offer["redirect_chain"] == [source, target]
    assert offer["source_url"] == source and offer["resolved_url"] == target and offer["http_status"] == 503
    assert offer["extraction"]["status"] == offer["extraction"]["code"] == "temporary"
    assert offer["title"] is None and offer["price"] is None and offer["image"] is None


@pytest.mark.parametrize("status", [502, 503, 504])
def test_one_transient_retry_can_recover_the_same_product(monkeypatch, status):
    calls, waits = [], []

    async def sleep(delay):
        waits.append(delay)

    def handle(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(status, text="Temporary error", headers={"retry-after": "1"})
        return httpx.Response(200, text=markup(), headers={"content-type": "text/html"})

    monkeypatch.setattr(mp.asyncio, "sleep", sleep)
    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(PRODUCT_URL))
    assert len(calls) == 2 and calls[0] == calls[1] and waits == [1]
    assert offer["title"] == "Mouse RGB" and offer["http_status"] == 200


@pytest.mark.parametrize("status,page,headers,expected", [
    (401, "Unauthorized", {}, "blocked"), (403, "Forbidden", {}, "blocked"),
    (429, "Slow down", {"retry-after": "1"}, "blocked"),
    (503, "Temporary error", {"retry-after": "120"}, "temporary"),
    (503, "Temporary error", {"retry-after": "invalid"}, "temporary"),
    (503, "<title>Robot Check</title>", {}, "blocked"),
    (503, '<form action="/errors/validateCaptcha"></form>', {}, "blocked"),
    (404, "Link não encontrado ou expirado.", {}, "unavailable"),
    (410, "Gone", {}, "unavailable"),
])
def test_access_failures_challenges_and_expired_links_are_never_retried(monkeypatch, status, page, headers, expected):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text=page, headers={"content-type": "text/html", **headers})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://amzon.promo/s/hxR0xZ"))
    assert len(calls) == 1 and offer["http_status"] == status
    assert offer["extraction"]["status"] == expected
    assert offer["title"] is None and offer["price"] is None


def test_retry_after_http_date_is_respected_without_extending_the_budget(monkeypatch):
    from email.utils import formatdate
    monkeypatch.setattr(mp.time, "time", lambda: 1_800_000_000)
    assert mp._retry_delay(formatdate(1_800_000_001, usegmt=True)) == 1
    assert mp._retry_delay(formatdate(1_800_000_003, usegmt=True)) is None
    assert mp._retry_delay("2") == 2 and mp._retry_delay("3") is None


def test_only_one_retry_is_shared_by_the_whole_redirect_chain(monkeypatch):
    calls, waits = [], []

    async def sleep(delay):
        waits.append(delay)

    def handle(request):
        calls.append(request.headers["host"])
        if len(calls) == 1:
            return httpx.Response(502, text="Temporary error")
        if len(calls) == 2:
            return httpx.Response(302, headers={"location": AMAZON_URL})
        return httpx.Response(503, text="Temporary error")

    monkeypatch.setattr(mp.asyncio, "sleep", sleep)
    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://amzn.to/example"))
    assert calls == ["amzn.to", "amzn.to", "www.amazon.com.br"] and waits == [0.25]
    assert offer["http_status"] == 503 and offer["extraction"]["status"] == "temporary"


def test_shortener_html_refresh_follows_same_retailer_and_keeps_query(monkeypatch):
    target = AMAZON_URL + "?tag=owner-fixture-20&linkId=keep%3Dthis"
    calls = []

    def handle(request):
        calls.append(request.headers["host"])
        if len(calls) == 1:
            return httpx.Response(200, text='<meta http-equiv="refresh" content="0; url=' + target.replace("&", "&amp;") + '">',
                                  headers={"content-type": "text/html; charset=utf-8"})
        assert str(request.url.copy_with(host=request.headers["host"])) == target
        return httpx.Response(200, text=AMAZON_HTML, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://link.amazon/example"))
    assert calls == ["link.amazon", "www.amazon.com.br"]
    assert offer["resolved_url"] == target and offer["price"] == "399.00"


@pytest.mark.parametrize("target", [
    "https://www.amazon.com.br/ap/signin?return_to=product",
    "https://www.amazon.com.br/captcha/verify", "https://www.amazon.com.br/webdevice/config",
    "https://www.amazon.com.br/config/registration", "https://www.amazon.com.br/error/500",
])
@pytest.mark.parametrize("kind", ["http", "meta"])
def test_shortener_never_requests_auth_configuration_or_error_destinations(monkeypatch, target, kind):
    calls = []

    def handle(request):
        calls.append(request)
        if kind == "http":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text='<meta http-equiv="refresh" content="0; url=' + target + '">',
                              headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://amzn.to/example"))
    assert len(calls) == 1 and offer["resolved_url"] == target
    assert offer["extraction"]["status"] == ("unavailable" if "/error/" in target else "blocked")
    assert offer["title"] is None and offer["price"] is None


@pytest.mark.parametrize("target", ["https://www.mercadolivre.com.br/p/MLB123", "https://127.0.0.1/admin",
                                    "https://amazon.com.br.evil.example/product"])
def test_shortener_meta_refresh_cannot_leave_retailer_or_reach_private_hosts(monkeypatch, target):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='<meta http-equiv="refresh" content="0; url=' + target + '">',
                              headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    with pytest.raises(ProviderError):
        asyncio.run(mp.extract_offer("https://amzon.promo/s/example"))
    assert len(calls) == 1


@pytest.mark.parametrize("url,page", [
    (AMAZON_URL, '<meta http-equiv="refresh" content="0; url=https://www.amazon.com.br/dp/B000000000">'),
    ("https://amzn.to/example", '<script>location.href="https://www.amazon.com.br/dp/B000000000";</script>'),
])
def test_storefront_refresh_and_shortener_javascript_are_not_executed(monkeypatch, url, page):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(url))
    assert len(calls) == 1 and offer["resolved_url"] == url
    assert offer["extraction"]["status"] == "not_product" and offer["title"] is None


def test_shortener_challenge_does_not_follow_its_meta_refresh(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='<title>Just a moment...</title><meta http-equiv="refresh" content="0; url=' + AMAZON_URL + '">',
                              headers={"content-type": "text/html"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://amzn.to/example"))
    assert len(calls) == 1 and offer["extraction"]["status"] == "blocked"


@pytest.mark.parametrize("code,ttl", [("temporary", 2), ("network", 2), ("timeout", 2),
                                     ("blocked", 20), ("unavailable", 20), ("not_product", 20)])
def test_transient_failure_cache_allows_a_prompt_retry_without_retry_storms(monkeypatch, code, ttl):
    now, calls = [100.0], []
    monkeypatch.setattr(mp.time, "monotonic", lambda: now[0])

    async def public(url, retailer):
        calls.append(url)
        return mp._finish(mp._base_offer(retailer, url), "public_page", status=code, code=code)

    monkeypatch.setattr(mp, "_public_offer", public)

    async def run():
        first = await mp._cached_public_offer(AMAZON_URL, mp.BY_ID["amazon"])
        first["source_url"] = "caller-edited"
        now[0] += ttl - 0.1
        second = await mp._cached_public_offer(AMAZON_URL, mp.BY_ID["amazon"])
        assert len(calls) == 1 and second["source_url"] == AMAZON_URL
        now[0] += 0.2
        await mp._cached_public_offer(AMAZON_URL, mp.BY_ID["amazon"])
        assert len(calls) == 2

    asyncio.run(run())


def test_transient_retry_does_not_extend_public_fetch_deadline(monkeypatch):
    calls = []
    monkeypatch.setattr(mp, "FETCH_BUDGET", 0.01)

    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="Temporary error", headers={"retry-after": "2"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer(AMAZON_URL))
    assert len(calls) == 1 and offer["extraction"]["code"] == "timeout"


@pytest.mark.parametrize("action", ["/signin", "/login", "/account/login", "/errors/validateCaptcha"])
def test_auxiliary_header_forms_do_not_hide_real_product_metadata(monkeypatch, action):
    page = '<header><form action="' + action + '"><input name="email"></form></header>' + markup()
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    offer = asyncio.run(mp.extract_offer(PRODUCT_URL))
    assert offer["title"] == "Mouse RGB" and offer["price"] == "99.90"
    assert offer["extraction"]["status"] == "complete" and offer["http_status"] == 200


@pytest.mark.parametrize("format", ["json_ld", "open_graph"])
def test_social_landing_with_real_product_metadata_is_still_read(monkeypatch, format):
    social = "https://www.mercadolivre.com.br/social/fixture/lists"
    canonical = "https://www.mercadolivre.com.br/p/MLB26004393"
    product = {**PRODUCT, "url": canonical}
    if format == "json_ld":
        page = markup(product, '<link rel="canonical" href="' + canonical + '">')
    else:
        page = ('<meta property="og:type" content="product"><meta property="og:title" content="Mouse RGB">'
                '<meta property="og:image" content="' + IMAGE_URL + '">'
                '<meta property="og:url" content="' + canonical + '">')
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    offer = asyncio.run(mp.extract_offer(social))
    assert offer["title"] == "Mouse RGB" and offer["image"] == IMAGE_URL
    assert offer["identity"] == "mercado_livre:MLB26004393"
    assert offer["resolved_url"] == social and offer["catalog_url"] == canonical
    assert offer["extraction"]["status"] in {"partial", "complete"}


def test_social_page_temporary_failure_is_not_misclassified_as_non_product(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="Temporary error", headers={"retry-after": "120"})

    mock_http(monkeypatch, handle)
    offer = asyncio.run(mp.extract_offer("https://www.mercadolivre.com.br/social/fixture/lists"))
    assert len(calls) == 1 and offer["http_status"] == 503
    assert offer["extraction"]["status"] == "temporary"
