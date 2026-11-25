"""Public product previews, separate from authenticated affiliate conversion.

Only registered storefronts are fetched. JSON-LD/Open Graph and, when a page
identifies VTEX, its documented public catalogue supply facts; inaccessible
pages produce an editable draft. No browser challenges, login or JS are run.
See https://schema.org/Product, https://ogp.me/ and
https://developers.vtex.com/docs/api-reference/search-api.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import ipaddress
import json
import re
import socket
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .providers import ProviderError, product_ids


@dataclass(frozen=True)
class Retailer:
    id: str
    name: str
    domains: tuple[str, ...]
    short_domains: tuple[str, ...] = ()

    @property
    def hosts(self):
        return frozenset((*self.domains, *self.short_domains))


def _retailer(ident, name, domain, *aliases, shorts=()):
    hosts = (domain, "www." + domain, *aliases) if not domain.startswith(("br.", "site.", "shop.", "loja.")) else (domain, *aliases)
    return Retailer(ident, name, tuple(dict.fromkeys(hosts)), tuple(shorts))


RETAILERS = (
    _retailer("amazon", "Amazon", "amazon.com.br",
              shorts=("amzn.to", "a.co", "amzon.promo", "link.amazon", "amzlinks.in")),
    _retailer("mercado_livre", "Mercado Livre", "mercadolivre.com.br", "produto.mercadolivre.com.br",
              shorts=("mercadolivre.com", "www.mercadolivre.com", "meli.la", "meli.promo")),
    _retailer("shopee", "Shopee", "shopee.com.br", "m.shopee.com.br",
              shorts=("s.shopee.com.br", "shope.ee", "shopee.promo")),
    _retailer("aliexpress", "AliExpress", "aliexpress.com", "pt.aliexpress.com", "m.aliexpress.com",
              "m.pt.aliexpress.com", shorts=("s.click.aliexpress.com", "a.aliexpress.com")),
    _retailer("magalu", "Magalu", "magazineluiza.com.br", "m.magazineluiza.com.br",
              "magazinevoce.com.br", "www.magazinevoce.com.br",
              shorts=("divulgador.magalu.com", "magalu.promo", "magazineluiza.onelink.me")),
    _retailer("shein", "SHEIN", "br.shein.com", "shein.com", "www.shein.com", "m.shein.com",
              shorts=("onelink.shein.com", "shein.top")),
    _retailer("temu", "Temu", "temu.com", shorts=("share.temu.com", "temu.to")),
    _retailer("americanas", "Americanas", "americanas.com.br"),
    _retailer("casas_bahia", "Casas Bahia", "casasbahia.com.br"),
    _retailer("ponto", "Ponto", "pontofrio.com.br", "ponto.com.br", "www.ponto.com.br"),
    _retailer("extra", "Extra", "extra.com.br"),
    _retailer("carrefour", "Carrefour", "carrefour.com.br", "mercado.carrefour.com.br"),
    _retailer("netshoes", "Netshoes", "netshoes.com.br"),
    _retailer("zattini", "Zattini", "zattini.com.br"),
    _retailer("dafiti", "Dafiti", "dafiti.com.br"),
    _retailer("centauro", "Centauro", "centauro.com.br"),
    _retailer("decathlon", "Decathlon", "decathlon.com.br"),
    _retailer("renner", "Renner", "lojasrenner.com.br"),
    _retailer("cea", "C&A", "cea.com.br"),
    _retailer("riachuelo", "Riachuelo", "riachuelo.com.br"),
    _retailer("nike", "Nike", "nike.com.br"),
    _retailer("adidas", "Adidas", "adidas.com.br"),
    _retailer("kabum", "KaBuM!", "kabum.com.br"),
    _retailer("pichau", "Pichau", "pichau.com.br"),
    _retailer("terabyteshop", "TerabyteShop", "terabyteshop.com.br"),
    _retailer("fast_shop", "Fast Shop", "site.fastshop.com.br", "fastshop.com.br", "www.fastshop.com.br"),
    _retailer("samsung", "Samsung", "shop.samsung.com", "samsung.com", "www.samsung.com"),
    _retailer("electrolux", "Electrolux", "loja.electrolux.com.br", "electrolux.com.br", "www.electrolux.com.br"),
    _retailer("madeiramadeira", "MadeiraMadeira", "madeiramadeira.com.br"),
    _retailer("mobly", "Mobly", "mobly.com.br"),
    _retailer("leroy_merlin", "Leroy Merlin", "leroymerlin.com.br"),
    _retailer("webcontinental", "Webcontinental", "webcontinental.com.br"),
    _retailer("natura", "Natura", "natura.com.br", shorts=("natura.promo",)),
    _retailer("boticario", "O Boticário", "boticario.com.br"),
    _retailer("beleza_na_web", "Beleza na Web", "belezanaweb.com.br"),
    _retailer("petz", "Petz", "petz.com.br"),
    _retailer("cobasi", "Cobasi", "cobasi.com.br"),
)
BY_ID = {retailer.id: retailer for retailer in RETAILERS}
BY_HOST = {host: retailer for retailer in RETAILERS for host in retailer.hosts}
AFFILIATE_NETWORK_HOSTS = frozenset({"awin1.com", "www.awin1.com", "tidd.ly", "ofertou.ai"})
# C&A and Electrolux expose product JSON-LD in public pages around 6 MB.
# Keep a finite bound, with a small concurrency ceiling for those storefronts.
HTML_LIMIT = 8_000_000
IMAGE_LIMIT = 3_000_000
MAX_REDIRECTS = 5
FETCH_BUDGET = 18
CACHE_SECONDS = 300
CACHE_LIMIT = 128
INFLIGHT_LIMIT = 8
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_inflight: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Task] = {}


def retailer_options():
    return [{"id": row.id, "name": row.name, "domains": list(row.domains),
             "mode": "automatic" if row.id == "shopee" else "own_link"} for row in RETAILERS]


def _https_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ProviderError("url", "Cole um endereço HTTPS válido do produto.")
    value = value.strip()
    # Do not let URL parsers silently discard controls, decode host delimiters,
    # accept credentials or turn browser backslashes into a different host.
    if re.search(r"[\x00-\x20\x7f\\]", value):
        raise ProviderError("url", "O endereço contém caracteres inválidos.")
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.port is not None
                or parts.netloc.lower() != parts.hostname.lower()
                or not re.fullmatch(r"[a-zA-Z0-9.-]+", parts.hostname)
                or parts.hostname.endswith(".") or "." not in parts.hostname):
            raise ValueError
        host = parts.hostname.lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        if host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            raise ValueError
        parse_qsl(parts.query, max_num_fields=100)
        # Percent-encoded controls/backslashes must not become a second URL.
        decoded = value
        for _ in range(2):
            decoded = unquote(decoded)
            if re.search(r"[\x00-\x1f\x7f\\]", decoded):
                raise ValueError
        return urlunsplit(("https", host, parts.path or "/", parts.query, ""))
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError("url", "Use um endereço HTTPS sem credenciais, portas ou IPs.") from None


def retailer_for_url(url: str) -> Retailer:
    normalized = _https_url(url)
    retailer = BY_HOST.get(urlsplit(normalized).hostname)
    if retailer is None:
        raise ProviderError("retailer", "Essa loja ainda não está na lista de lojas aceitas.")
    return retailer


def checked_product_url(url: str, platform: str | None = None) -> str:
    normalized = _https_url(url)
    retailer = retailer_for_url(normalized)
    if platform is not None and retailer.id != platform:
        raise ProviderError("url", "O link precisa pertencer à mesma loja do produto.")
    return normalized


def checked_affiliate_url(url: str, platform: str) -> str:
    """Validate an explicitly provided link, without claiming who owns it."""
    if platform not in BY_ID:
        raise ProviderError("retailer", "Loja inválida para esse link de afiliado.")
    normalized = _https_url(url)
    if urlsplit(normalized).hostname in AFFILIATE_NETWORK_HOSTS:
        return normalized
    return checked_product_url(normalized, platform)


def checked_image_url(url: str, platform: str | None = None) -> str:
    """Syntax check; the download pins a public DNS address before each hop.

    Retailers use independent image CDNs, so their storefront host allowlist is
    not an image allowlist. No credentials/cookies are forwarded to image hosts.
    """
    if platform is not None and platform not in BY_ID:
        raise ProviderError("retailer", "Loja inválida para essa imagem.")
    try:
        return _https_url(url)
    except ProviderError:
        raise ProviderError("image", "Use uma imagem pública com endereço HTTPS válido.") from None


async def _public_addresses(host: str) -> list[str]:
    try:
        entries = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 3,
        )
        addresses = list(dict.fromkeys(entry[4][0] for entry in entries))
        if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast
                                for address in addresses):
            raise ValueError
        return sorted(addresses, key=lambda address: ":" in address)
    except (ValueError, TimeoutError, OSError):
        raise ProviderError("url", "O endereço externo não pôde ser validado com segurança.") from None


@dataclass(frozen=True)
class Document:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    redirect_chain: tuple[str, ...] = ()
    stop_reason: str | None = None


async def _request_once(url: str, *, accept: str, limit: int) -> Document:
    normalized = _https_url(url)
    host = urlsplit(normalized).hostname
    addresses = await _public_addresses(host)
    # Pin the validated address for the socket, keeping the original hostname
    # for BOTH TLS verification/SNI and Host. httpcore supports sni_hostname;
    # a new connection per hop cannot reuse another hostname's TLS session.
    target = httpx.URL(normalized).copy_with(host=addresses[0])
    headers = {"Host": host, "User-Agent": "OrionProductPreview/1.0", "Accept": accept,
               "Accept-Language": "pt-BR,pt;q=0.9", "Accept-Encoding": "identity"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=4), follow_redirects=False,
                                     trust_env=False) as client:
            async with client.stream("GET", target, headers=headers,
                                     extensions={"sni_hostname": host}) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if response.is_redirect:
                    return Document(normalized, response.status_code, response_headers, b"")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise ProviderError("size", "A resposta da loja excede o tamanho permitido.")
                body = bytearray()
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding not in {"identity", "", "gzip", "deflate"}:
                    raise ProviderError("encoding", "A loja retornou um formato comprimido não suportado para leitura.")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in {"gzip", "deflate"} else None
                chunks = response.aiter_raw(chunk_size=65536) if decoder else response.aiter_bytes(chunk_size=65536)
                async for chunk in chunks:
                    # Bound decompression itself, not only its already-allocated
                    # output. This also protects arbitrary public image CDNs.
                    data = decoder.decompress(chunk, limit - len(body) + 1) if decoder else chunk
                    body.extend(data)
                    if len(body) > limit or (decoder and decoder.unconsumed_tail):
                        raise ProviderError("size", "A resposta da loja excede o tamanho permitido.")
                if decoder and (not decoder.eof or decoder.unused_data):
                    raise ProviderError("network", "A loja retornou uma resposta comprimida incompleta.")
                return Document(normalized, response.status_code, response_headers, bytes(body))
    except (httpx.HTTPError, zlib.error):
        raise ProviderError("network", "A loja não respondeu a tempo. Você pode revisar os dados manualmente.") from None


async def _fetch_document(url: str, retailer: Retailer) -> Document:
    visited = set()
    chain = []
    retried = False

    def result(response, *, stop_reason=None):
        return Document(response.url, response.status, response.headers, response.body,
                        tuple(chain), stop_reason)

    for _ in range(MAX_REDIRECTS + 1):
        url = checked_product_url(url, retailer.id)
        if url in visited:
            raise ProviderError("redirect", "O link da loja está redirecionando em círculo.")
        visited.add(url)
        chain.append(url)
        response = await _request_once(url, accept="text/html,application/ld+json,application/json;q=0.8", limit=HTML_LIMIT)
        if response.status in {502, 503, 504}:
            markup = response.body.decode("utf-8", "replace")
            if _blocked_markup(markup, retailer, failed_response=True):
                return result(response, stop_reason="blocked")
            delay = None if retried else _retry_delay(response.headers.get("retry-after"))
            if delay is not None:
                # Retry this exact public URL once, within the caller's existing
                # deadline. No alternative endpoint, identity or session is used.
                retried = True
                await asyncio.sleep(delay)
                response = await _request_once(url, accept="text/html,application/ld+json,application/json;q=0.8", limit=HTML_LIMIT)
                if response.status in {502, 503, 504} and _blocked_markup(response.body.decode("utf-8", "replace"), retailer, failed_response=True):
                    return result(response, stop_reason="blocked")
        target = None
        if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
            target = _redirect_target(url, response.headers["location"], retailer.id)
        elif response.status == 200 and urlsplit(url).hostname in retailer.short_domains:
            content_type = response.headers.get("content-type", "").partition(";")[0].lower()
            if content_type in {"text/html", "application/xhtml+xml"}:
                markup = response.body.decode("utf-8", "replace")
                if _blocked_markup(markup, retailer):
                    return result(response, stop_reason="blocked")
                metadata = _Metadata()
                metadata.feed(markup)
                if metadata.refresh:
                    target = _redirect_target(url, metadata.refresh, retailer.id)
        if target:
            target = checked_product_url(target, retailer.id)
            stop_reason = _restricted_route(target)
            if stop_reason:
                # Record the declared destination, but never request a login,
                # challenge, device-configuration or error page.
                chain.append(target)
                return result(response, stop_reason=stop_reason)
            url = target
            continue
        return result(response)
    raise ProviderError("redirect", "O link tem redirecionamentos demais. Cole o endereço final do produto.")


async def fetch_product_image(url: str) -> bytes:
    try:
        async with asyncio.timeout(12):
            visited = set()
            for _ in range(4):
                url = checked_image_url(url)
                if url in visited:
                    break
                visited.add(url)
                response = await _request_once(url, accept="image/jpeg,image/png,image/webp", limit=IMAGE_LIMIT)
                if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status != 200 or response.headers.get("content-type", "").partition(";")[0].lower() not in {
                    "image/jpeg", "image/png", "image/webp",
                }:
                    break
                if response.body:
                    return response.body
                break
    except TimeoutError:
        pass
    raise ProviderError("image", "A imagem do produto não pôde ser carregada. Escolha outra imagem pública.")


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.scripts: list[str] = []
        self.canonical: str | None = None
        self.refresh: str | None = None
        self.product_targets: list[str] = []
        self.title = ""
        self._title = False
        self._script: list[str] | None = None
        self._script_length = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and len(self.meta) < 256:
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content")
            if key and isinstance(content, str):
                self.meta.setdefault(key, []).append(content[:4096])
            if (attrs.get("http-equiv") or "").lower() == "refresh" and isinstance(content, str):
                match = re.match(r"\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*['\"]?(.*?)['\"]?\s*$", content, re.I)
                if match:
                    self.refresh = match[1]
        elif tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonical = attrs.get("href")
        elif tag == "input" and attrs.get("id") == "url" and len(self.product_targets) < 2:
            # A public SHEIN onelink landing declares its web destination here.
            # Keep a second occurrence so ambiguous declarations are rejected.
            self.product_targets.append((attrs.get("value") or "")[:4097])
        elif tag == "title":
            self._title = True
        elif tag == "script" and (attrs.get("type") or "").lower().partition(";")[0] == "application/ld+json":
            if len(self.scripts) < 48:
                self._script, self._script_length = [], 0

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "script" and self._script is not None:
            if self._script_length <= 800_000:
                self.scripts.append("".join(self._script))
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script_length += len(data)
            if self._script_length <= 800_000:
                self._script.append(data)
        elif self._title:
            self.title = (self.title + data)[:1000]

    def first(self, *keys):
        return next((self.meta[key][0] for key in keys if self.meta.get(key)), None)


def _retry_delay(value):
    """Honor short Retry-After values; never retry earlier than a longer one."""
    if value is None:
        return 0.25
    try:
        value = value.strip()
        if re.fullmatch(r"\d+", value):
            delay = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = max(0, date.timestamp() - time.time())
        return delay if 0 <= delay <= 2 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _restricted_route(url):
    path = urlsplit(url).path
    for _ in range(2):
        path = unquote(path)
    if re.search(r"(?:^|/)(?:ap/(?:signin|cvf)|login|signin|sign-in|auth|captcha|challenge|webdevice|config|az-request-verify)(?:/|;|$)", path, re.I):
        return "blocked"
    if re.search(r"(?:^|/)(?:error|errors)(?:/|;|$)", path, re.I):
        return "unavailable"
    return None


def _redirect_target(current: str, location: str, platform: str | None = None) -> str:
    """Use a declared public web fallback, never execute an app intent.

    https://developer.chrome.com/docs/android/intents documents the explicit
    S.browser_fallback_url extra. The same strict destination and retailer
    checks apply to it as to an ordinary HTTP redirect.
    """
    if location.startswith("intent:"):
        from .link_input import intent_fallback_url
        target = intent_fallback_url(location)
    else:
        target = _https_url(urljoin(current, location))
    return checked_product_url(target, platform) if platform else target


def _blocked_markup(markup, retailer, metadata=None, *, failed_response=False):
    if metadata is None:
        metadata = _Metadata()
        metadata.feed(markup[:HTML_LIMIT])
    return bool(
        re.search(r"^(?:access denied|robot check|just a moment|attention required|security (?:check|verification)|verifica[cç][aã]o de seguran[cç]a|403 forbidden)\b",
                  metadata.title.strip(), re.I)
        # A product header can contain an auxiliary login form or verification
        # widget. Only a failed HTTP response uses these explicit challenge
        # endpoints to suppress a retry; successful product pages do not.
        or (failed_response and re.search(r"<form\b[^>]*\baction\s*=\s*['\"][^'\"]*/errors/validateCaptcha(?:[?/'\"])", markup, re.I))
        or (failed_response and re.search(r"<script\b[^>]*\bsrc\s*=\s*['\"][^'\"]*/challenge-platform/", markup, re.I))
        or (retailer.id == "mercado_livre" and re.search(r"Para continuar.{0,300}?acesse sua conta", markup, re.S | re.I))
        or (retailer.id == "shein" and "risk_streamline_assets" in markup and not metadata.first("og:title"))
        or (retailer.id == "temu" and "static.kwcdn.com/upload-static/assets/chl/" in markup and not metadata.first("og:title"))
    )


def _text(value, limit=240):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = re.sub(r"<[^>]*>", " ", html.unescape(str(value)))
    value = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split()).strip()
    return value[:limit] or None


def number_text(value, *, zero=False, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    raw = str(value).strip()
    if len(raw) > 64:
        return None
    raw = re.sub(r"^R\$\s*", "", raw).replace("\xa0", "")
    # A comma is accepted only as an unambiguous decimal separator; ranges,
    # installment text and three-digit grouping guesses remain unknown.
    if "," in raw:
        if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}", raw):
            return None
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(raw)
        if (not value.is_finite() or value < 0 or (value == 0 and not zero) or value > maximum
                or value.as_tuple().exponent < -8):
            return None
        return format(value, "f")
    except (InvalidOperation, ValueError):
        return None


def _count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _currency(value):
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


def _images(values, url):
    values = values if isinstance(values, list) else [values]
    result = []
    for value in values[:24]:
        if isinstance(value, dict):
            value = value.get("contentUrl") or value.get("imageUrl") or value.get("url")
        if not isinstance(value, str):
            continue
        try:
            value = checked_image_url(urljoin(url, value))
        except ProviderError:
            continue
        if value not in result:
            result.append(value)
    return result[:8]


def _amazon_asin(url):
    """Read the SKU from the public desktop, mobile and legacy product paths."""
    match = re.search(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([A-Za-z0-9]{10})(?:/|$)",
                      unquote(urlsplit(url).path), re.I)
    return match[1].upper() if match else None


def _identity(retailer: Retailer, url: str, product=None):
    parts = urlsplit(url)
    path = unquote(parts.path)
    if retailer.id == "shopee" and (ids := product_ids(url)):
        return f"shopee:{ids[0]}:{ids[1]}"
    if retailer.id == "amazon" and (asin := _amazon_asin(url)):
        return f"amazon:{asin}"
    patterns = {
        "mercado_livre": r"\b(MLB-?\d+)\b", "aliexpress": r"/item/(\d+)\.html",
        "shein": r"-p-(\d+)(?:-cat-\d+)?\.html", "temu": r"-g-(\d+)\.html",
        "kabum": r"/produto/(\d+)(?:/|$)",
    }
    match = re.search(patterns.get(retailer.id, r"(?!)"), path, re.I)
    if match:
        return f"{retailer.id}:{match[1].replace('-', '').upper()}"
    # Keep variant parameters, discard only common marketing parameters.
    query = [(key, value) for key, value in parse_qsl(parts.query, max_num_fields=100)
             if not key.lower().startswith(("utm_", "aff_", "matt_")) and key.lower() not in {"tag", "spm", "affiliate"}]
    stable = urlunsplit((parts.scheme, parts.netloc.removeprefix("www."), path.rstrip("/"), urlencode(query), ""))
    return f"{retailer.id}:url:{hashlib.sha256(stable.encode()).hexdigest()[:32]}"


def _base_offer(retailer: Retailer, url: str):
    return {"platform": retailer.id, "retailer": retailer.id, "retailer_name": retailer.name,
            "identity": _identity(retailer, url), "title": None, "store": retailer.name,
            "image": None, "images": [], "price": None, "price_max": None, "currency": None,
            "rating": None, "rating_scale": None, "review_count": None, "commission": None,
            "variant": None, "categories": [], "sales": None, "discount_rate": None,
            "availability": None, "previous_price": None, "coupon": None,
            "catalog_url": url, "source_url": url, "source": "Página pública da loja",
            "fetched_at": int(time.time()), "affiliate_url": None,
            "affiliate_status": "not_converted", "link_status": "required"}


def _finish(row, method, *, status=None, reason=None, code=None):
    missing = [field for field in ("title", "price", "currency", "image", "rating") if row.get(field) is None]
    row["extraction"] = {"status": status or ("partial" if missing else "complete"),
                         "method": method, "missing": missing, "reason": reason,
                         "code": code}
    return row


def _types(node):
    values = node.get("@type", [])
    return {value.rsplit("/", 1)[-1] for value in (values if isinstance(values, list) else [values]) if isinstance(value, str)}


def _product_nodes(scripts, base_url=""):
    """Expand references defined in this document, never fetch an @id/context.

    Only page/graph roots are product candidates. Recommendations and ItemList
    children are not traversed. Variant/Offer nodes may be referenced by the
    selected product without becoming unrelated product candidates themselves.
    """
    roots, definitions, conflicts = [], {}, set()
    containers = {"@graph", "mainEntity", "about"}
    properties = {"offers", "hasVariant", "priceSpecification", "aggregateRating",
                  "brand", "image", "seller", "itemOffered"}
    remaining = 2000

    def reference(value):
        return urljoin(base_url, value) if isinstance(value, str) and 0 < len(value) <= 4096 else None

    for script in scripts:
        try:
            root = json.loads(script)
        except (ValueError, RecursionError):
            continue
        stack = [(root, 0, True)]
        while stack and remaining > 0:
            node, depth, candidate = stack.pop()
            remaining -= 1
            if depth > 16:
                continue
            if isinstance(node, list):
                stack.extend((child, depth + 1, candidate) for child in reversed(node[:100]))
            elif isinstance(node, dict):
                if candidate:
                    roots.append(node)
                ident = reference(node.get("@id"))
                if ident and len(node) > 1 and ident not in conflicts:
                    previous = definitions.get(ident, {})
                    if any(previous[key] != node[key] for key in previous.keys() & node.keys()):
                        conflicts.add(ident)
                        definitions.pop(ident, None)
                    else:
                        definitions[ident] = {**previous, **node}
                for key in containers | properties:
                    if key in node:
                        stack.append((node[key], depth + 1, key in containers))

    budget = [5000]

    def expand(node, seen=frozenset(), depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 16:
            return None
        if isinstance(node, list):
            return [expand(child, seen, depth + 1) for child in node[:100]]
        if not isinstance(node, dict):
            return node
        ident = reference(node.get("@id"))
        if ident and (ident in seen or ident in conflicts):
            return {"@id": node["@id"]}
        if ident in definitions:
            node = {**definitions[ident], **node}
        result = dict(node)
        branch = seen | {ident} if ident else seen
        for key in containers | properties:
            if key in result:
                result[key] = expand(result[key], branch, depth + 1)
        return result

    products = []
    for root in roots:
        definition = definitions.get(reference(root.get("@id")), {})
        if not (_types(root) | _types(definition)) & {"Product", "ProductGroup"}:
            continue
        node = expand(root)
        if isinstance(node, dict) and _types(node) & {"Product", "ProductGroup"} and node not in products:
            products.append(node)
    return products


def _semantic_query(url):
    return sorted((key, value) for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True, max_num_fields=100)
                  if not key.lower().startswith(("utm_", "aff_", "matt_"))
                  and key.lower() not in {"tag", "spm", "affiliate", "linkcode", "linkid", "ref", "ref_",
                                          "promoter_id", "partner_id", "url_from", "onelink", "requestid", "campaign_id"})


def _product_urls(product, base, retailer, *, graph_fragments=True):
    for key in ("url", "mainEntityOfPage", "@id"):
        value = product.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str):
            if not graph_fragments and key == "@id" and value.startswith("#"):
                return []
            try:
                candidate = checked_product_url(urljoin(base, value), retailer.id)
                # An explicit URL takes precedence over a symbolic graph @id;
                # #red/#blue identify nodes, not the currently requested URL.
                return [candidate]
            except (ProviderError, ValueError):
                return []
    return []


def _same_product_url(first, second):
    a, b = urlsplit(first), urlsplit(second)
    return (a.hostname.removeprefix("www."), unquote(a.path).rstrip("/"), _semantic_query(first)) == (
        b.hostname.removeprefix("www."), unquote(b.path).rstrip("/"), _semantic_query(second))


def _selected_variant(product, url, retailer, *, allow_selectors=True):
    variants = product.get("hasVariant")
    variants = [row for row in (variants if isinstance(variants, list) else [variants])[:100]
                if isinstance(row, dict) and "Product" in _types(row)]
    matches = [row for row in variants if any(_same_product_url(candidate, url)
               for candidate in _product_urls(row, url, retailer, graph_fragments=False))]
    selector_items = [(key.lower(), value) for key, value in _semantic_query(url)
                      if key.lower() in {"skuid", "sku", "variant", "variantid", "productid"}]
    selectors = dict(selector_items)
    ambiguous = any(value != selectors[key] for key, value in selector_items)
    fields = {"skuid": ("sku", "skuId"), "sku": ("sku",), "variant": ("sku", "productID", "variantId"),
              "variantid": ("variantId", "sku", "productID"), "productid": ("productID", "sku")}
    if not matches and selectors and allow_selectors and not ambiguous:
        matches = [row for row in variants if all(any(isinstance(row.get(field), (str, int))
                   and not isinstance(row.get(field), bool) and str(row[field]) == value for field in fields[key])
                   for key, value in selectors.items())]
    if len(matches) != 1:
        return None
    variant = matches[0]
    # Price/image/availability are deliberately not inherited from the group:
    # they can describe a different colour, size, seller or default variant.
    common = {key: product[key] for key in ("name", "brand", "category", "description") if key in product}
    selected = {**common, **variant}
    selected["_selected_variant"] = {key: _text(variant.get(key), 120) for key in ("sku", "productID", "name", "color", "size")
                                     if _text(variant.get(key), 120)}
    return selected


def _variant_requested(product, url):
    """Recognize a selection, without mistaking arbitrary tracking for a SKU."""
    if "ProductGroup" not in _types(product) or not product.get("hasVariant"):
        return False
    requested = {key.lower() for key, _ in _semantic_query(url)}
    if requested & {"skuid", "sku", "variant", "variantid", "productid"}:
        return True
    variants = product.get("hasVariant")
    retailer = retailer_for_url(url)
    for variant in (variants if isinstance(variants, list) else [variants])[:100]:
        if not isinstance(variant, dict):
            continue
        dimensions = {key for key in ("color", "size", "material", "pattern", "model") if variant.get(key) is not None}
        for candidate in _product_urls(variant, url, retailer, graph_fragments=False):
            dimensions.update(key.lower() for key, _ in _semantic_query(candidate))
        if requested & dimensions:
            return True
    return False


def _matching_product(products, urls, retailer):
    matching = []
    unlocated = []
    targets = [_identity(retailer, url) for url in urls]
    if ":url:" not in targets[0]:
        # A canonical parent/other SKU cannot authorize extracting that SKU's
        # price when the requested URL already identifies a concrete product.
        targets = [value for value in targets if value == targets[0]]
    # Carrefour's public page route identifies the SKU while its JSON-LD @id
    # uses a different product slug/ID. Match the explicit SKU, not any related
    # product merely because it is the only JSON-LD object on the page.
    page_skus = set()
    if retailer.id == "carrefour":
        for url in urls:
            if match := re.fullmatch(r"/produto/[^/]+-(\d+)/?", urlsplit(url).path):
                page_skus.add(match[1])
    for product in products:
        candidates = _product_urls(product, urls[0], retailer)
        associated = (any(_identity(retailer, candidate) in targets for candidate in candidates)
                      or any(_same_product_url(urlunsplit(urlsplit(candidate)._replace(query="")),
                                               urlunsplit(urlsplit(urls[0])._replace(query=""))) for candidate in candidates)
                      or not candidates and len(products) == 1)
        if variant := _selected_variant(product, urls[0], retailer, allow_selectors=associated):
            matching.append((50, variant))
        if candidates:
            score = 0
            for candidate in candidates:
                if _same_product_url(candidate, urls[0]):
                    score = max(score, 40)
                elif _identity(retailer, candidate) == targets[0]:
                    # An explicit contradictory selection is not the requested SKU.
                    query, actual = dict(_semantic_query(candidate)), dict(_semantic_query(urls[0]))
                    if not any(actual[key] != value for key, value in query.items() if key in actual):
                        score = max(score, 30)
                elif _identity(retailer, candidate) in targets[1:]:
                    score = max(score, 20)
            if str(product.get("sku")) in page_skus:
                score = max(score, 30)
            if score:
                matching.append((score, product))
        elif not any(product.get(key) for key in ("url", "mainEntityOfPage", "@id")):
            unlocated.append(product)
    if matching:
        score = max(value[0] for value in matching)
        winners = []
        for priority, product in matching:
            if priority == score and product not in winners:
                winners.append(product)
        return winners[0] if len(winners) == 1 else None
    return unlocated[0] if len(products) == len(unlocated) == 1 else None


def _price_fields(product, url=None):
    offers = product.get("offers")
    if url and _variant_requested(product, url):
        # A request for a concrete variant must not silently fall back to the
        # cheapest price/range of the group when that variant was not resolved.
        return {"price": None, "price_max": None, "currency": None, "availability": None}
    if not offers and "ProductGroup" in _types(product):
        variants = product.get("hasVariant")
        variants = variants if isinstance(variants, list) else []
        selected = next((value for key, value in parse_qsl(urlsplit(url or "").query) if key.lower() in {"skuid", "sku", "variant", "variantid"}), None)
        offers = []
        for variant in variants[:100]:
            if not isinstance(variant, dict) or (selected is not None and str(variant.get("sku")) != selected):
                continue
            variant_offers = variant.get("offers")
            offers.extend(variant_offers if isinstance(variant_offers, list) else [variant_offers])
    offers = offers if isinstance(offers, list) else [offers]
    amounts = []
    currencies = set()
    availability = []
    seller = None
    for offer in offers[:100]:
        if not isinstance(offer, dict):
            continue
        if _types(offer) and not _types(offer) & {"Offer", "AggregateOffer"}:
            continue
        specification = offer.get("priceSpecification")
        specification = specification if isinstance(specification, dict) else {}
        if str(specification.get("priceType", "")).rsplit("/", 1)[-1] == "ListPrice":
            specification = {}
        currency = _currency(offer.get("priceCurrency") or specification.get("priceCurrency"))
        if currency:
            currencies.add(currency)
        low = number_text(offer.get("price")) or number_text(offer.get("lowPrice")) or number_text(specification.get("price"))
        high = number_text(offer.get("highPrice"))
        if low:
            amounts.append(Decimal(low))
            if currency is None:
                currencies.add(None)
            if high and Decimal(high) >= Decimal(low):
                amounts.append(Decimal(high))
        available = str(offer.get("availability") or "").rsplit("/", 1)[-1].lower()
        if available in {"instock", "limitedavailability", "preorder", "presale", "backorder"}:
            availability.append(True)
        elif available in {"outofstock", "soldout", "discontinued"}:
            availability.append(False)
        offered_by = offer.get("seller")
        if len(offers) == 1 and isinstance(offered_by, dict):
            seller = _text(offered_by.get("name"), 120)
    single_currency = len(currencies) == 1
    return {"price": format(min(amounts), "f") if amounts and single_currency else None,
            "price_max": format(max(amounts), "f") if amounts and single_currency else None,
            "currency": next(iter(currencies)) if single_currency else None,
            "availability": any(availability) if availability else None,
            **({"store": seller} if seller else {})}


class _AmazonProductDOM(HTMLParser):
    """Read only the rendered main-product fields, never recommendations/JS."""
    TARGETS = {"productTitle", "landingImage", "acrPopover", "acrCustomerReviewText", "ASIN",
               "priceblock_ourprice", "priceblock_dealprice", "apex-pricetopay-accessibility-label"}
    PRICE_AREAS = {"corePrice_feature_div", "corePriceDisplay_desktop_feature_div", "corePrice_desktop"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.nodes = {}
        self.prices = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ident = attrs.get("id")
        classes = set((attrs.get("class") or "").split())
        captured = None
        if ident in self.TARGETS and ident not in self.nodes:
            captured = {"attrs": attrs, "text": ""}
            self.nodes[ident] = captured
        if ("a-offscreen" in classes and len(self.prices) < 20
                and any(frame[1] in self.PRICE_AREAS for frame in self.stack)
                and any("a-price" in frame[2] for frame in self.stack)
                and not any("a-text-price" in frame[2] for frame in self.stack)):
            captured = {"attrs": attrs, "text": ""}
            self.prices.append(captured)
        if tag not in self.VOID and len(self.stack) < 256:
            self.stack.append((tag, ident, classes, captured))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        if self.stack and self.stack[-1][0] in {"script", "style"}:
            return
        for frame in self.stack:
            if frame[3] is not None:
                frame[3]["text"] = (frame[3]["text"] + value)[:2000]


def _amazon_fields(markup, url, parser=None):
    asin = _amazon_asin(url)
    if not asin:
        return {}
    if parser is None:
        parser = _AmazonProductDOM()
        parser.feed(markup)
    nodes = parser.nodes
    page_asin = nodes.get("ASIN", {}).get("attrs", {}).get("value")
    if page_asin and page_asin.upper() != asin:
        return {}
    values = {}
    if title := _text(nodes.get("productTitle", {}).get("text")):
        values["title"] = title
    image = nodes.get("landingImage", {}).get("attrs", {})
    images = [image.get("data-old-hires"), image.get("data-src"), image.get("src")]
    try:
        dynamic = json.loads(image.get("data-a-dynamic-image") or "{}")
        if isinstance(dynamic, dict):
            # The final URLs in the observed element are the larger versions;
            # they are actual published URLs, never guessed/resized paths.
            images = [image.get("data-old-hires"), *list(dynamic)[-8:][::-1], *images[1:]]
    except (ValueError, RecursionError):
        pass
    if photos := _images(images, url):
        # Responsive resolutions of one photo are not a product gallery.
        values.update(images=photos[:1], image=photos[0])
    amounts = []
    for node in [*parser.prices, *[nodes[key] for key in ("priceblock_ourprice", "priceblock_dealprice", "apex-pricetopay-accessibility-label") if key in nodes]]:
        text = _text(node["text"])
        if text and re.fullmatch(r"R\$\s*[\d.,]+", text) and (amount := number_text(text)):
            amounts.append(Decimal(amount))
    # Different current amounts can reflect conditional prices or variants;
    # do not choose one without knowing which offer the visitor can purchase.
    if len(set(amounts)) == 1:
        values.update(price=format(amounts[0], "f"), price_max=format(amounts[0], "f"), currency="BRL")
    stars = nodes.get("acrPopover", {})
    label = stars.get("attrs", {}).get("title") or stars.get("text") or ""
    if rating := re.search(r"(\d+(?:[.,]\d+)?)\s*(?:de|out of)\s*(\d+(?:[.,]\d+)?)\s*(?:estrelas|stars)", label, re.I):
        value, scale = number_text(rating[1], zero=True, maximum=100), number_text(rating[2], maximum=100)
        if value and scale and Decimal(value) <= Decimal(scale):
            values.update(rating=value, rating_scale=scale)
    reviews = nodes.get("acrCustomerReviewText", {})
    text = reviews.get("attrs", {}).get("aria-label") or reviews.get("text") or ""
    if count := re.search(r"\b(\d{1,3}(?:[.,]\d{3})+|\d+)\b", text):
        values["review_count"] = _count(count[1].replace(".", "").replace(",", ""))
    return values


def _shein_landing_product(metadata, url, retailer):
    if retailer.id != "shein" or urlsplit(url).hostname != "onelink.shein.com" or len(metadata.product_targets) != 1:
        return None
    target = metadata.product_targets[0]
    try:
        parts = urlsplit(target)
        if parts.scheme == "http" and parts.netloc == "br.shein.com":
            target = urlunsplit(parts._replace(scheme="https"))
        target = checked_product_url(target, "shein")
        if urlsplit(target).hostname == "br.shein.com" and re.fullmatch(r"shein:\d+", _identity(retailer, target)):
            return target
    except (ProviderError, ValueError):
        pass
    return None


def parse_html_offer(markup: str, url: str, retailer: Retailer | None = None):
    url = checked_product_url(url)
    retailer = retailer or retailer_for_url(url)
    metadata = _Metadata()
    metadata.feed(markup[:HTML_LIMIT])
    row = _base_offer(retailer, url)
    if _blocked_markup(markup, retailer, metadata):
        return _finish(row, "public_page", status="blocked", reason="A loja pediu login ou uma verificação de acesso. Preencha os dados do produto na revisão.", code="blocked")
    amazon_parser = None
    if retailer.id == "amazon" and (requested_asin := _amazon_asin(url)):
        amazon_parser = _AmazonProductDOM()
        amazon_parser.feed(markup)
        page_asin = amazon_parser.nodes.get("ASIN", {}).get("attrs", {}).get("value")
        if page_asin and page_asin.upper() != requested_asin:
            # Neither JSON-LD nor global OG metadata may override an explicit
            # main-product SKU contradicting the requested product.
            return _finish(row, "public_page", status="not_product",
                           reason="A loja retornou um produto diferente desse link. Confira o endereço.", code="product_mismatch")
    landing_product = _shein_landing_product(metadata, url, retailer)
    canonical = landing_product or metadata.canonical or metadata.first("og:url")
    if canonical:
        try:
            canonical = checked_product_url(urljoin(url, canonical), retailer.id)
        except ProviderError:
            canonical = None
    product = _matching_product(_product_nodes(metadata.scripts, url), [url, *([canonical] if canonical else [])], retailer)
    method = "json_ld" if product else "open_graph"
    selected_variant = product.get("_selected_variant") if product else None
    unresolved_variant = bool(product and _variant_requested(product, url))
    if product:
        row.update(_price_fields(product, url))
        row["title"] = _text(product.get("name"))
        row["images"] = [] if unresolved_variant else _images(product.get("image"), url)
        if selected_variant is not None:
            row["variant"] = selected_variant
        rating = product.get("aggregateRating")
        if isinstance(rating, dict):
            scale = number_text(rating.get("bestRating", 5), maximum=100)
            value = number_text(rating.get("ratingValue"), zero=True, maximum=100)
            if scale and value and Decimal(value) <= Decimal(scale):
                row["rating"], row["rating_scale"] = value, scale
            row["review_count"] = _count(rating.get("reviewCount", rating.get("ratingCount")))
        row["sku"] = _text(product.get("sku") or product.get("productID"), 120)
        row["brand"] = _text(product.get("brand", {}).get("name") if isinstance(product.get("brand"), dict) else product.get("brand"), 120)
    row["title"] = row["title"] or _text(metadata.first("og:title", "twitter:title"))
    if not row["images"] and selected_variant is None and not unresolved_variant:
        row["images"] = _images([*metadata.meta.get("og:image:secure_url", []), *metadata.meta.get("og:image", []),
                                  *metadata.meta.get("twitter:image", [])], url)
    row["image"] = next(iter(row["images"]), None)
    if row["price"] is None and selected_variant is None and not unresolved_variant:
        og_price = number_text(metadata.first("product:price:amount", "og:price:amount"))
        if og_price:
            row["price"] = row["price_max"] = og_price
        row["currency"] = _currency(metadata.first("product:price:currency", "og:price:currency")) or row["currency"]
    elif row["currency"] is None:
        row["currency"] = _currency(metadata.first("product:price:currency", "og:price:currency"))
    if retailer.id == "amazon":
        native = _amazon_fields(markup, url, amazon_parser)
        if native:
            for key, value in native.items():
                if row.get(key) is None or row.get(key) == []:
                    row[key] = value
            method = "amazon_public_page"
    if canonical:
        # Canonical URLs often omit a selected variant. Preserve the actual
        # product URL when a query/path selection controls its price or image.
        current_identity, canonical_identity = _identity(retailer, url), _identity(retailer, canonical)
        explicit_product = ":url:" not in current_identity
        preserve = (selected_variant is not None or bool(_semantic_query(url))
                    or explicit_product and current_identity != canonical_identity)
        row["catalog_url"] = canonical if landing_product else url if preserve else canonical
        row["identity"] = _identity(retailer, row["catalog_url"])
    if retailer.id == "shopee" and (ids := product_ids(row["catalog_url"])):
        row.update(shop=ids[0], item=ids[1], catalog_url=f"https://shopee.com.br/product/{ids[0]}/{ids[1]}")
    reason = ("A variante solicitada não foi identificada. Confira os dados do produto." if unresolved_variant
              else None if row["title"] else "A página não informou os dados do produto. Você pode preenchê-los na revisão.")
    return _finish(row, method, reason=reason, code="variant_unresolved" if unresolved_variant else None)


def parse_vtex_offer(data, url: str, *, currency=None):
    """Normalize the documented public product-by-URL response, not search hits."""
    retailer = retailer_for_url(url)
    slug = unquote(urlsplit(url).path).strip("/").removesuffix("/p")
    candidates = data if isinstance(data, list) else []
    product = next((row for row in candidates[:20] if isinstance(row, dict) and row.get("linkText") == slug), None)
    if product is None:
        return None
    row = _base_offer(retailer, url)
    row["title"] = _text(product.get("productName"))
    row["sku"] = _text(product.get("productId"), 120)
    row["brand"] = _text(product.get("brand"), 120)
    prices, prior, images, stock = [], [], [], []
    currencies = set()
    items = product.get("items")
    for item in (items if isinstance(items, list) else [])[:100]:
        if not isinstance(item, dict):
            continue
        images.extend(_images(item.get("images"), url))
        sellers = item.get("sellers")
        for seller in (sellers if isinstance(sellers, list) else [])[:20]:
            commercial = seller.get("commertialOffer") if isinstance(seller, dict) else None
            if not isinstance(commercial, dict):
                continue
            quantity = _count(commercial.get("AvailableQuantity"))
            available = commercial.get("IsAvailable")
            available = available if type(available) is bool else (quantity > 0 if quantity is not None else None)
            if available is not None:
                stock.append(available)
            price = number_text(commercial.get("Price"))
            if price and available is not False:
                prices.append(Decimal(price))
                currencies.add(_currency(commercial.get("PriceCurrency") or product.get("currencyCode") or currency))
                previous = number_text(commercial.get("ListPrice"))
                prior.append(previous if previous and Decimal(previous) > Decimal(price) else None)
    row["images"] = list(dict.fromkeys(images))[:8]
    row["image"] = next(iter(row["images"]), None)
    if prices and len(currencies) == 1:
        row.update(price=format(min(prices), "f"), price_max=format(max(prices), "f"), currency=next(iter(currencies)))
        if len(prices) == 1:
            row["previous_price"] = prior[0]
    row["availability"] = any(stock) if stock else None
    return _finish(row, "vtex_public_catalogue")


def _shopee_wrapped_url(url):
    try:
        for key, value in parse_qsl(urlsplit(url).query, max_num_fields=64):
            if key.lower() not in {"url", "redirect", "redirect_url", "origin_url", "target"}:
                continue
            for _ in range(2):
                if value.startswith(("https://", "/")):
                    break
                value = unquote(value)
            candidate = checked_product_url(urljoin(url, value), "shopee")
            if product_ids(candidate):
                return candidate
    except (ValueError, ProviderError):
        pass
    return None


async def resolve_shopee_url(url: str) -> tuple[int, int]:
    url = checked_product_url(url, "shopee")
    try:
        async with asyncio.timeout(FETCH_BUDGET):
            visited = set()
            for _ in range(MAX_REDIRECTS + 1):
                url = checked_product_url(url, "shopee")
                if _restricted_route(url) == "blocked":
                    raise ProviderError("blocked", "A Shopee pediu uma verificação. Abra o link e copie o endereço completo do produto.")
                if ids := product_ids(url):
                    return ids
                if wrapped := _shopee_wrapped_url(url):
                    return product_ids(wrapped)
                if re.match(r"^/(?:universal-link/)?user/voucher-wallet(?:/|$)", urlsplit(url).path):
                    raise ProviderError("not_product", "Esse link abre cupons da Shopee. Escolha o link do produto para preparar a oferta.")
                if url in visited:
                    break
                visited.add(url)
                response = await _request_once(url, accept="text/html", limit=HTML_LIMIT)
                if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    url = _redirect_target(url, response.headers["location"], "shopee")
                    continue
                if response.status in {401, 403, 429}:
                    raise ProviderError("blocked", "A Shopee pediu uma verificação. Abra o link e copie o endereço completo do produto.")
                if response.status != 200:
                    break
                metadata = _Metadata()
                metadata.feed(response.body.decode("utf-8", "replace"))
                target = metadata.refresh or metadata.canonical or metadata.first("og:url")
                if not target:
                    break
                url = _redirect_target(url, target, "shopee")
    except TimeoutError:
        raise ProviderError("network", "A Shopee demorou para abrir esse link. Cole o endereço completo do produto.") from None
    raise ProviderError("link", "Não consegui identificar o produto. Abra o link na Shopee e copie o endereço completo da página.")


async def _public_offer(url, retailer):
    row = _base_offer(retailer, url)
    row.update(resolved_url=url, http_status=None, redirect_chain=[url])
    try:
        async with asyncio.timeout(FETCH_BUDGET):
            response = await _fetch_document(url, retailer)
            chain = list(response.redirect_chain or (response.url,))
            context = {"source_url": url, "resolved_url": chain[-1],
                       "http_status": response.status, "redirect_chain": chain}
            row.update(context)
            route_status = _restricted_route(response.url)
            social_page = retailer.id == "mercado_livre" and re.match(r"^/social(?:/|$)", urlsplit(response.url).path, re.I)
            if response.stop_reason == "blocked" or route_status == "blocked" or response.status in {401, 403, 429}:
                return _finish(row, "public_page", status="blocked", reason="A loja pediu login ou uma verificação de acesso. Não foi possível consultar o produto.", code="blocked")
            if social_page and response.status in {404, 410}:
                return _finish(row, "public_page", status="not_product", reason="Esse link abre um perfil ou uma lista do Mercado Livre. Cole o link de um produto.", code="not_product")
            if response.stop_reason == "unavailable" or route_status == "unavailable" or response.status in {404, 410}:
                if (response.status in {404, 410}
                        and urlsplit(response.url).hostname in retailer.short_domains):
                    return _finish(row, "public_page", status="unavailable", reason="O encurtador não disponibilizou o destino desse link para consulta. Abra o link e copie o endereço do produto na loja.", code="shortener_unavailable")
                return _finish(row, "public_page", status="unavailable", reason="Esse link não está disponível ou expirou. Confira o endereço do produto.", code="unavailable")
            if response.status in {408, 425} or 500 <= response.status <= 599:
                return _finish(row, "public_page", status="temporary", reason="A loja está indisponível no momento. Tente novamente em instantes.", code="temporary")
            if response.status != 200:
                return _finish(row, "public_page", status="unavailable", reason="A loja não disponibilizou essa página. Confira o endereço do produto.", code="unavailable")
            content_type = response.headers.get("content-type", "").partition(";")[0].lower()
            if content_type not in {"text/html", "application/xhtml+xml", "application/ld+json"}:
                return _finish(row, "public_page", status="not_product", reason="Esse endereço não retornou uma página de produto.", code="content_type")
            markup = response.body.decode("utf-8", "replace")
            if content_type == "application/ld+json":
                markup = '<script type="application/ld+json">' + markup + '</script>'
            row = parse_html_offer(markup, response.url, retailer)
            row.update(context)
            if row["extraction"]["status"] in {"blocked", "not_product"}:
                return row
            if social_page:
                # Some social landing pages embed an identifiable Product.
                # Keep those facts; a profile title/avatar alone is not one.
                social_metadata = _Metadata()
                social_metadata.feed(markup)
                product_metadata = (row["extraction"]["method"] == "json_ld"
                                    or (row.get("title") and row.get("price"))
                                    or (social_metadata.first("og:type") in {"product", "og:product"}
                                        and row.get("title") and (row.get("price") or row.get("image"))))
                if not product_metadata:
                    row = _base_offer(retailer, response.url)
                    row.update(context)
                    return _finish(row, "public_page", status="not_product", reason="Esse link abre um perfil ou uma lista do Mercado Livre. Cole o link de um produto.", code="not_product")
            # This is a public, documented endpoint on the SAME storefront.
            # It is only consulted when the successful HTML identifies VTEX;
            # a 403/challenge never causes a different endpoint to be tried.
            if (not all(row.get(key) for key in ("title", "price", "image")) and row.get("availability") is not False
                    and row.get("variant") is None
                    and row["extraction"].get("code") != "variant_unresolved"
                    and re.search(r"(?:vtexassets\.com|vteximg\.com\.br|vtex\.render-runtime|__RUNTIME__)", markup)
                    and re.fullmatch(r"/[^/]+/p/?", urlsplit(response.url).path)):
                slug = unquote(urlsplit(response.url).path).strip("/").removesuffix("/p")
                endpoint = urljoin(response.url, "/api/catalog_system/pub/products/search/" + quote(slug, safe="-._~") + "/p")
                try:
                    catalogue = await _fetch_document(endpoint, retailer)
                    if catalogue.status == 200:
                        vtex = parse_vtex_offer(json.loads(catalogue.body), response.url, currency=row.get("currency"))
                        if vtex:
                            for field in ("title", "image", "images", "price", "price_max", "currency", "availability", "previous_price", "brand", "sku"):
                                if row.get(field) is None or row.get(field) == []:
                                    row[field] = vtex.get(field)
                            row = _finish(row, "json_ld+vtex" if row["extraction"]["method"] == "json_ld" else "vtex_public_catalogue")
                except (ProviderError, ValueError, RecursionError):
                    pass
            if not any(row.get(key) for key in ("title", "price", "image")):
                return _finish(row, row["extraction"]["method"], status="not_product",
                               reason="A página não trouxe dados de um produto. Confira o link.", code="not_product")
            return row
    except TimeoutError:
        return _finish(row, "public_page", reason="A consulta à loja demorou demais. Você pode preencher os dados na revisão.", code="timeout")
    except ProviderError as exc:
        if exc.code in {"url", "retailer"}:
            raise
        return _finish(row, "public_page", reason=exc.message, code=exc.code)


async def _cached_public_offer(url, retailer):
    now = time.monotonic()
    cached = _cache.get(url)
    if cached and cached[0] > now:
        _cache.move_to_end(url)
        return copy.deepcopy(cached[1])
    if cached:
        _cache.pop(url, None)
    key = (asyncio.get_running_loop(), url)
    task = _inflight.get(key)
    if task is None:
        if len(_inflight) >= INFLIGHT_LIMIT:
            raise ProviderError("rate", "Há muitas consultas em andamento. Tente novamente em instantes.", retry_after=5)

        async def run():
            try:
                result = await _public_offer(url, retailer)
                # Short negative caching prevents retry storms, without keeping
                # a temporarily blocked page for the full product TTL.
                transient = result.get("extraction", {}).get("code") in {"temporary", "network", "timeout"}
                ttl = 2 if transient else (CACHE_SECONDS if result.get("title") else 20)
                _cache[url] = (time.monotonic() + ttl, copy.deepcopy(result))
                _cache.move_to_end(url)
                while len(_cache) > CACHE_LIMIT:
                    _cache.popitem(last=False)
                return result
            finally:
                _inflight.pop(key, None)

        task = asyncio.create_task(run())
        _inflight[key] = task
        # A cancelled HTTP caller must not cancel another caller's shared fetch;
        # also retrieve exceptions if every waiter disconnects.
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
    return copy.deepcopy(await asyncio.shield(task))


async def _resolve_network_product(url):
    """Follow only registered network/store hops, without extracting attribution."""
    visited = set()
    async with asyncio.timeout(8):
        for _ in range(MAX_REDIRECTS + 1):
            url = _https_url(url)
            host = urlsplit(url).hostname
            if host in BY_HOST:
                return checked_product_url(url)
            if host not in AFFILIATE_NETWORK_HOSTS:
                raise ProviderError("url", "O link redirecionou para um domínio não permitido. Cole o endereço da página do produto.")
            if url in visited:
                break
            visited.add(url)
            response = await _request_once(url, accept="text/html", limit=HTML_LIMIT)
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                url = _redirect_target(url, response.headers["location"])
                continue
            break
    raise ProviderError("link", "Abra o link de afiliado e copie o endereço final do produto para buscar os dados.")


async def extract_offer(url: str, *, shopee=None):
    """Return a preview; affiliation is ALWAYS a separate engine operation."""
    started = time.monotonic()
    source_url = _https_url(url)
    url = source_url
    if urlsplit(url).hostname in AFFILIATE_NETWORK_HOSTS:
        try:
            url = await _resolve_network_product(url)
        except TimeoutError:
            raise ProviderError("link", "O link de afiliado demorou a abrir. Cole o endereço da página do produto.") from None
    url = checked_product_url(url)
    retailer = retailer_for_url(url)
    fallback = _base_offer(retailer, url)
    fallback["source_url"] = source_url
    api_error = None
    try:
        async with asyncio.timeout(max(1, 22 - (time.monotonic() - started))):
            if retailer.id == "shopee":
                # Resolve IDs BEFORE opening the product page. /opaanlp needs
                # no second HTTP request and the API is scoped to this caller.
                ids = await resolve_shopee_url(url)
                canonical = f"https://shopee.com.br/product/{ids[0]}/{ids[1]}"
                product = {"shop": ids[0], "item": ids[1], "identity": f"shopee:{ids[0]}:{ids[1]}",
                           "catalog_url": canonical, "source_url": source_url}
                fallback.update(product)
                if shopee is not None:
                    try:
                        row = await shopee.detail(*ids)
                        row.update(source_url=source_url, platform="shopee", retailer="shopee", retailer_name="Shopee",
                                   affiliate_url=None, affiliate_status="not_converted", link_status="required")
                        return _finish(row, "shopee_affiliate_api")
                    except ProviderError as exc:
                        # Never conceal credential/rate failures as a complete
                        # catalogue response. A missing product is reviewable.
                        if exc.code not in {"product", "schema", "network"}:
                            raise
                        api_error = exc
                row = await _cached_public_offer(canonical, retailer)
                row.update(product)
                if api_error:
                    row["extraction"].update(api_unavailable=True, status="partial",
                                             reason=api_error.message + " Revise os dados antes de publicar.")
                return row
            row = await _cached_public_offer(url, retailer)
            row["source_url"] = source_url
            return row
    except TimeoutError:
        row = _finish(fallback, "shopee_affiliate_api" if shopee is not None and retailer.id == "shopee" else "public_page",
                      reason="A consulta demorou demais. Confira o link e preencha os dados na revisão.", code="timeout")
        if retailer.id == "shopee" and shopee is not None:
            row["extraction"]["api_unavailable"] = True
        return row
