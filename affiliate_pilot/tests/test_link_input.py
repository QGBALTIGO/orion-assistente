"""Human link input is pure parsing: no request, tag conversion or deep-link execution."""
from urllib.parse import quote

import pytest

from affiliate_pilot.link_input import ProductLinkChoice, intent_fallback_url, normalize_product_input
from affiliate_pilot.providers import ProviderError


AMAZON = "https://amzn.to/3QLdZgA"
ML = "https://meli.la/11F9L45"
TRACKED = "https://www.amazon.com.br/dp/B09ZXN77L2?tag=owner-20&tag=second&linkId=A%2bB+x%2F&ref_=share#variant=blue"


@pytest.mark.parametrize("raw,expected", [
    (AMAZON, AMAZON),
    (" amzn.to/3QLdZgA ", AMAZON),
    ("//amzn.to/3QLdZgA", AMAZON),
    ("HTTPS://AMZN.TO/3QLdZgA", AMAZON),
    ("http://amzn.to/3QLdZgA", AMAZON),
    ("Confira http://amzn.to/3QLdZgA?tag=A%2BB+X", AMAZON + "?tag=A%2BB+X"),
    ("http://tidd.ly/synthetic-link", "https://tidd.ly/synthetic-link"),
    ("Confira \u200bhttps://amzn.to/3QLdZgA\u200b", AMAZON),
    ("\ufeff<" + AMAZON + "\u200b>\u2060", AMAZON),
    (AMAZON + "?tag=inside\u200bvalue", AMAZON + "?tag=inside\u200bvalue"),
    ("www.amazon.com.br/dp/B09ZXN77L2?tag=owner-20&tag=second&linkId=A%2bB+x%2F&ref_=share#variant=blue", TRACKED),
    ("🔥 Oferta boa!\n" + AMAZON + "\nCupom: EXEMPLO10", AMAZON),
    ("Confira " + AMAZON + ". Depois me conte!", AMAZON),
    ("Confira <" + TRACKED + ">", TRACKED),
    ("Confira (" + TRACKED + ").", TRACKED),
    ("Confira '" + TRACKED + "'.", TRACKED),
    ("Confira \"" + TRACKED + "\".", TRACKED),
    ("Confira “" + TRACKED + "”.", TRACKED),
    ("Confira `" + TRACKED + "`.", TRACKED),
    ("[Oferta azul](" + TRACKED + ")", TRACKED),
    ("Veja [Oferta azul](" + TRACKED + ") agora", TRACKED),
    ("(" + AMAZON + ")", AMAZON),
    (AMAZON + ";", AMAZON),
    ("https://www.amazon.com.br/dp/B09ZXN77L2.", "https://www.amazon.com.br/dp/B09ZXN77L2"),
    ("https://www.dafiti.com.br/Tenis-14433482.html.", "https://www.dafiti.com.br/Tenis-14433482.html"),
    ("https://shopee.com.br/Garrafa-T%C3%A9rmica-i.958537610.22398954580.",
     "https://shopee.com.br/Garrafa-T%C3%A9rmica-i.958537610.22398954580"),
    (AMAZON + "?tag=literal.,;!", AMAZON + "?tag=literal.,;!"),
    (AMAZON + "?tag=owner%29", AMAZON + "?tag=owner%29"),
    (AMAZON + "?", AMAZON + "?"),
    ("https://www.mercadolivre.com.br/p/MLB26004393?pdp_filters=item_id%3AMLB123#origin=share&wid=MLB123",
     "https://www.mercadolivre.com.br/p/MLB26004393?pdp_filters=item_id%3AMLB123#origin=share&wid=MLB123"),
    ("https://chat.whatsapp.com/synthetic\n" + AMAZON + "\nhttps://t.me/synthetic", AMAZON),
    (AMAZON + "\n" + AMAZON + "\namzn.to/3QLdZgA", AMAZON),
    ("tidd.ly/synthetic-link", "https://tidd.ly/synthetic-link"),
    ("Confira ofertou.ai/synthetic-link.", "https://ofertou.ai/synthetic-link"),
    ("Confira https://www.awin1.com/cread.php?awinmid=1&awinaffid=2&ued=https%3A%2F%2Fwww.amazon.com.br%2Fdp%2FB09ZXN77L2",
     "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2&ued=https%3A%2F%2Fwww.amazon.com.br%2Fdp%2FB09ZXN77L2"),
])
def test_shared_messages_normalize_only_outer_structure(raw, expected):
    assert normalize_product_input(raw) == expected


@pytest.mark.parametrize("raw", [
    "", " ", None, "x" * 8193,
    "https://amazon.com.br.evil.example/dp/B09ZXN77L2",
    "amazon.com.br.evil.example/dp/B09ZXN77L2",
    "https://amazon.com.br@evil.example/dp/B09ZXN77L2",
    "https://evil.example@amazon.com.br/dp/B09ZXN77L2",
    "https://www.amazon.com.br:443/dp/B09ZXN77L2",
    "https://127.0.0.1/?url=" + AMAZON,
    "http://amzn.to:80/3QLdZgA",
    "http://amzn.to@evil.example/3QLdZgA",
    "http://amzn.to.evil.example/3QLdZgA",
    "ftp://amzn.to/3QLdZgA",
    "https://amzn.to\\evil.example/path",
    "https://аmzn.to/3QLdZgA",  # Cyrillic a, not a registered host.
    "https://amzn.to./3QLdZgA",
    "https://amzn.to/x%0d%0aHost:evil.example",
    "https://amzn.to/x%255cother",
    "https://evil.example/?next=" + AMAZON,
    'https://evil.example/?next="' + AMAZON + '"',
    "Confira https://evil.example/?next=<" + AMAZON + ">",
    "https://evil.example/path/amzn.to/3QLdZgA",
    "evil.example/?next=amzn.to/3QLdZgA",
    "email@amzn.to/3QLdZgA",
    "javascript:location='" + AMAZON + "'",
    "https://chat.whatsapp.com/synthetic\nCupom: AMAZON20",
    "Compre\x00 " + AMAZON,
])
def test_input_never_discovers_nested_urls_or_guesses_hosts(raw):
    with pytest.raises(ProviderError):
        normalize_product_input(raw)


def test_multiple_distinct_urls_require_choice_in_order_without_coalescing_tags():
    links = [AMAZON, ML, TRACKED, TRACKED.replace("owner-20", "other-20"), "https://tidd.ly/synthetic"]
    with pytest.raises(ProductLinkChoice) as caught:
        normalize_product_input("\n".join([*links, AMAZON, "https://t.me/synthetic"]))
    assert [row["url"] for row in caught.value.candidates] == links
    assert [row["retailer"] for row in caught.value.candidates] == ["amazon", "mercado_livre", "amazon", "amazon", None]
    assert caught.value.candidates[-1]["retailer_name"] == "Link de oferta"


def test_registry_is_read_at_use_time(monkeypatch):
    from affiliate_pilot import marketplaces
    retailer = marketplaces.Retailer("new_store", "Nova loja", ("newstore.example",))
    monkeypatch.setitem(marketplaces.BY_HOST, "newstore.example", retailer)
    assert normalize_product_input("Confira newstore.example/product?ref=A%2BB") == "https://newstore.example/product?ref=A%2BB"
    monkeypatch.setattr(marketplaces, "AFFILIATE_NETWORK_HOSTS", frozenset({*marketplaces.AFFILIATE_NETWORK_HOSTS, "newlink.example"}))
    assert normalize_product_input("Confira newlink.example/short") == "https://newlink.example/short"


def test_text_and_source_have_separate_length_limits():
    assert normalize_product_input("Oferta " + "x" * 6000 + "\n" + AMAZON) == AMAZON
    source = AMAZON + "?tracking="
    source += "a" * (4096 - len(source))
    assert normalize_product_input(source) == source
    with pytest.raises(ProviderError):
        normalize_product_input(source + "a")


def make_intent(fallback):
    return "intent://product/example#Intent;scheme=market;package=example.app;S.browser_fallback_url=" + quote(fallback, safe="") + ";end"


def test_intent_reads_one_explicit_https_fallback_without_changing_tracking():
    value = make_intent(TRACKED)
    assert intent_fallback_url(value) == TRACKED
    assert intent_fallback_url(value + ";") == TRACKED
    assert normalize_product_input("Veja este produto: " + value) == TRACKED
    assert normalize_product_input("Veja este produto: " + value + ";") == TRACKED
    assert normalize_product_input(make_intent("https://ofertou.ai/synthetic?ref=a+b")) == "https://ofertou.ai/synthetic?ref=a+b"


@pytest.mark.parametrize("value", [
    "intent://product#Intent;scheme=https;end",
    make_intent("http://amzn.to/3QLdZgA"),
    make_intent("https://evil.example/product"),
    make_intent("https://amazon.com.br@evil.example/product"),
    make_intent("https://www.amazon.com.br:443/product"),
    make_intent("javascript:alert(1)"),
    make_intent(quote(AMAZON, safe="")),
    make_intent(AMAZON).replace(";end", ";S.browser_fallback_url=" + quote(ML, safe="") + ";end"),
    make_intent(AMAZON).replace("%3A", "%ZZ"),
    make_intent(AMAZON).replace(";end", ""),
])
def test_deep_links_cannot_execute_or_supply_ambiguous_or_untrusted_fallbacks(value):
    with pytest.raises(ProviderError):
        intent_fallback_url(value)
    with pytest.raises(ProviderError):
        normalize_product_input(value)
