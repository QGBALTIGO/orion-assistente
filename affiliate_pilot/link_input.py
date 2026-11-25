"""Turn a human share message into one strictly validated product URL.

This module never fetches a URL or executes a deep link. Store/redirect/image
validation remains in marketplaces; only the offer entry point accepts prose.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from .providers import ProviderError


INPUT_LIMIT = 8192
URL_LIMIT = 4096
INVISIBLE_BOUNDARIES = "\u200b\u2060\ufeff"


class ProductLinkChoice(Exception):
    def __init__(self, candidates):
        self.candidates = candidates
        self.message = "Há mais de um link nesta mensagem. Escolha o produto para gerar a prévia."
        super().__init__(self.message)


def _checked_source_url(value: str) -> str:
    # Imports stay local: redirect resolution may reuse intent_fallback_url.
    from .marketplaces import AFFILIATE_NETWORK_HOSTS, BY_HOST, _https_url

    checked = _https_url(value)
    if urlsplit(checked).hostname not in BY_HOST and urlsplit(checked).hostname not in AFFILIATE_NETWORK_HOSTS:
        raise ProviderError("retailer", "Essa loja ainda não está na lista de lojas aceitas.")
    # The strict validator deliberately omits fragments for HTTP. Keep the
    # user's fragment and query bytes in source_url, including repeated keys,
    # plus signs and percent encoding. Do not parse/re-encode affiliate tags.
    suffix = re.split(r"^[^:]+://[^/?#]+", value, maxsplit=1)[-1]
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    result = "https://" + urlsplit(checked).hostname + suffix
    if len(result) > URL_LIMIT:
        raise ProviderError("url", "O link do produto pode ter até 4.096 caracteres.")
    return result


def intent_fallback_url(value: str) -> str:
    """Read an explicit Android browser fallback as data, never as an action."""
    if (not isinstance(value, str) or len(value) > INPUT_LIMIT
            or re.search(r"[\x00-\x20\x7f\\]", value)
            or not value.startswith("intent:") or "#Intent;" not in value
            or not value.endswith((";end", ";end;"))):
        raise ProviderError("url", "Esse link de aplicativo não contém um endereço HTTPS válido do produto.")
    if value.endswith(";end;"):
        value = value[:-1]
    fields = value.split("#Intent;", 1)[1][:-4].split(";")
    fallbacks = [field.split("=", 1)[1] for field in fields if field.startswith("S.browser_fallback_url=")]
    if len(fallbacks) != 1 or not fallbacks[0] or re.search(r"%(?![\da-fA-F]{2})", fallbacks[0]):
        raise ProviderError("url", "Esse link de aplicativo não contém um único endereço HTTPS do produto.")
    try:
        # One decode, not unquote_plus: a '+' may belong to an affiliate tag.
        fallback = unquote(fallbacks[0], errors="strict")
    except (UnicodeError, ValueError):
        raise ProviderError("url", "O endereço de retorno desse link de aplicativo é inválido.") from None
    return _checked_source_url(fallback)


def _unwrap(value: str) -> str:
    pairs = {"<": ">", "(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}
    for _ in range(4):
        if len(value) > 1 and pairs.get(value[0]) == value[-1]:
            value = value[1:-1].strip()
            continue
        markdown = re.fullmatch(r"\[[^\]\r\n]*\]\((\S+)\)", value)
        if markdown:
            value = markdown[1]
            continue
        break
    return value


def _sentence_punctuation(value: str) -> str:
    """Trim prose only after a recognisable path, never a query/fragment."""
    from .marketplaces import AFFILIATE_NETWORK_HOSTS, BY_HOST

    if "?" in value or "#" in value:
        return value
    trimmed = value.rstrip(".,;:!…")
    if trimmed == value:
        return value
    try:
        parts = urlsplit(trimmed if "://" in trimmed else "https://" + trimmed.lstrip("/"))
        retailer = BY_HOST.get(parts.hostname)
    except ValueError:
        return value
    if not retailer and parts.hostname not in AFFILIATE_NETWORK_HOSTS:
        return value
    is_short = parts.hostname in AFFILIATE_NETWORK_HOSTS or parts.hostname in retailer.short_domains
    short_token = is_short and re.fullmatch(r"/(?:[\w-]+/)*[\w-]+/?", parts.path, re.ASCII)
    product_path = re.search(r"\.html?$", parts.path, re.I)
    if retailer and retailer.id == "amazon":
        product_path = product_path or re.search(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/[a-z0-9]{10}/?$", parts.path, re.I)
    elif retailer and retailer.id == "mercado_livre":
        product_path = product_path or re.search(r"/(?:p/)?MLB-?\d+/?$", parts.path, re.I)
    elif retailer and retailer.id == "shopee":
        product_path = product_path or re.search(r"(?:/product/\d+/\d+|-i\.\d+\.\d+)/?$", parts.path, re.I)
    return trimmed if short_token or product_path else value


def _normalise_token(value: str) -> str:
    value = _sentence_punctuation(_unwrap(value.strip(INVISIBLE_BOUNDARIES)).strip(INVISIBLE_BOUNDARIES))
    if value.startswith("intent:"):
        return intent_fallback_url(value)
    if value.lower().startswith("http://"):
        # Legacy shared links are upgraded locally, before strict host checks;
        # no HTTP request is ever sent and the authority is not repaired.
        value = "https://" + value[7:]
    elif value.startswith("//"):
        value = "https:" + value
    elif not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        value = "https://" + value
    return _checked_source_url(value)


def _message_tokens(value: str):
    from .marketplaces import AFFILIATE_NETWORK_HOSTS, BY_HOST

    hosts = "|".join(re.escape(host) for host in sorted(set(BY_HOST) | AFFILIATE_NETWORK_HOSTS, key=len, reverse=True))
    # Consume complete URI-like tokens even for unrecognised schemes/hosts so
    # an embedded URL in an attacker-controlled query is never mined out.
    pattern = re.compile(
        r"(?<![\w@./:%?&=+#\\-])(?:"
        r"[a-z][a-z0-9+.-]{1,31}:[^\s<>]+|"
        r"//[^\s<>]+|"
        r"(?:" + hosts + r")(?=$|[/?#\s)\]},.!;:>])[^\s<>]*)", re.I,
    )
    for match in pattern.finditer(value):
        token = match[0]
        prefix = value[match.start() - 1] if match.start() else ""
        leading = re.search(r"\S*\Z", value[:match.start()])[0]
        if re.search(r"[/@:=?&%#\\]", leading):
            continue
        closing = {"(": ")", "[": "]", "{": "}", "'": "'", '"': '"', "`": "`", "“": "”", "‘": "’"}.get(prefix)
        if closing:
            # A matching outer wrapper may be followed by sentence punctuation.
            ending = re.search(re.escape(closing) + r"[.,;:!…]*$", token)
            if ending and (prefix in {'"', "'", "`", "“", "‘"} or token.count(closing) > token.count(prefix)):
                token = token[:ending.start()]
        yield token


def normalize_product_input(value: str) -> str:
    """Accept one product link, or ask the caller to choose before any fetch."""
    if not isinstance(value, str) or not value.strip() or len(value) > INPUT_LIMIT:
        raise ProviderError("url", "Cole um link ou uma mensagem de até 8.192 caracteres.")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise ProviderError("url", "A mensagem contém caracteres inválidos.")
    value = value.strip()
    direct_error = None
    try:
        return _normalise_token(value)
    except ProviderError as exc:
        direct_error = exc
    candidates = {}
    for token in _message_tokens(value):
        try:
            url = _normalise_token(token)
        except ProviderError:
            continue
        if url not in candidates:
            from .marketplaces import BY_HOST
            retailer = BY_HOST.get(urlsplit(url).hostname)
            candidates[url] = {"url": url, "retailer": retailer.id if retailer else None,
                               "retailer_name": retailer.name if retailer else "Link de oferta"}
    if len(candidates) > 1:
        raise ProductLinkChoice(list(candidates.values()))
    if candidates:
        return next(iter(candidates))
    # Preserve useful strict-validator errors for a single address.
    if not re.search(r"\s", value):
        raise direct_error
    raise ProviderError("url", "Não encontrei um link de produto de uma das lojas compatíveis nessa mensagem.")
