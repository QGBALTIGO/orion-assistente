from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
import math
import weakref
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx


SHOPEE_ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
PRODUCT_HOSTS = {"shopee.com.br", "www.shopee.com.br", "m.shopee.com.br"}
SHORT_HOSTS = {"s.shopee.com.br", "shope.ee"}
IMAGE_HOSTS = {"down-br.img.susercontent.com", "cf.shopee.com.br", "cf.shopee.com.my",
               "down-br.img.shopee.com.br", "down-ws-br.img.susercontent.com"}
BASE_FIELDS = "itemId shopId productName priceMin priceMax"
FIELDS = BASE_FIELDS + " ratingStar commissionRate shopName"
MERCHANDISING = " productCatIds sales priceDiscountRate"


class ProviderError(Exception):
    def __init__(self, code, message, *, retry_after=None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


def checked_url(url: str, hosts: set[str]) -> str:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc.lower() not in hosts
                or not parts.path.startswith("/") or len(url) > 2048
                or re.search(r"[\x00-\x20\\]", url)):
            raise ValueError
        return parts.hostname
    except (ValueError, TypeError):
        raise ProviderError("url", "Endereço HTTPS fora dos domínios permitidos.") from None


async def check_dns(host: str):
    try:
        entries = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 4
        )
        if not entries or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in entries):
            raise ValueError
    except (ValueError, TimeoutError, OSError):
        raise ProviderError("url", "O endereço externo não pôde ser validado.") from None


def product_ids(url: str) -> tuple[int, int] | None:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc.lower() not in PRODUCT_HOSTS:
            return None
        path = parts.path
        for _ in range(2):
            path = unquote(path)
        if re.search(r"[\x00-\x1f\x7f\\]", path):
            return None
        # Official short links can point at /opaanlp/shop/item. These IDs
        # describe the same product as /product/shop/item and require no page
        # fetch or browser challenge to be interpreted.
        match = re.search(r"^/(?:product|opaanlp)/(\d{1,18})/(\d{1,18})(?:/|$)", path)
        if not match:
            match = re.search(r"(?:^|[./-])i\.(\d{1,18})\.(\d{1,18})(?:/|$)", path)
        ids = tuple(map(int, match.groups())) if match else None
        return ids if ids and all(0 < value < 10**18 for value in ids) else None
    except (ValueError, TypeError, UnicodeError):
        return None


async def resolve_product(url: str) -> tuple[int, int]:
    from .marketplaces import resolve_shopee_url
    return await resolve_shopee_url(url)


async def product_image(url: str) -> bytes:
    from .marketplaces import fetch_product_image
    return await fetch_product_image(url)


def decimal_text(value) -> str | None:
    try:
        number = Decimal(str(value))
        if number.is_finite() and 0 < number <= 1_000_000:
            return str(number)
    except (InvalidOperation, ValueError):
        pass
    return None


def normalize_offer(node):
    shop, item = int(node["shopId"]), int(node["itemId"])
    title = str(node.get("productName") or "").strip()[:240]
    if not title or shop <= 0 or item <= 0:
        raise ValueError("Invalid product")
    image = node.get("imageUrl")
    if image:
        try:
            checked_url(image, IMAGE_HOSTS)
        except ProviderError:
            image = None
    categories = node.get("productCatIds")
    sales = node.get("sales")
    discount = node.get("priceDiscountRate")
    rating = decimal_text(node.get("ratingStar"))
    return {
        "shop": shop, "item": item, "identity": f"shopee:{shop}:{item}", "title": title,
        "platform": "shopee", "retailer": "shopee", "retailer_name": "Shopee",
        "store": str(node.get("shopName") or "").strip()[:120], "image": image,
        "images": [image] if image else [],
        "price": decimal_text(node.get("priceMin")) or decimal_text(node.get("price")),
        "price_max": decimal_text(node.get("priceMax")), "commission": decimal_text(node.get("commissionRate")),
        "rating": rating if rating and Decimal(rating) <= 5 else None,
        "rating_scale": "5", "review_count": None, "currency": "BRL", "variant": None,
        "categories": [item for item in categories if type(item) is int and item > 0]
                      if isinstance(categories, list) else [],
        "sales": sales if type(sales) is int and sales > 0 else None,
        "discount_rate": discount if type(discount) is int and 0 < discount < 100 else None,
        "availability": None, "previous_price": None, "coupon": None,
        "catalog_url": f"https://shopee.com.br/product/{shop}/{item}",
        "source": "Shopee Affiliate Open API", "fetched_at": int(time.time()),
    }


# Same-account requests share a lock and provider-directed cooldown.
_SHOPEE_GATES = weakref.WeakKeyDictionary()


class _ShopeeGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cooldown_until = 0.0


def _shopee_gate(app_id):
    gates = _SHOPEE_GATES.setdefault(asyncio.get_running_loop(), {})
    gate = gates.get(app_id)
    if gate is None:
        gate = gates[app_id] = _ShopeeGate()
    return gate


class Shopee:
    def __init__(self, app_id: str, secret: str):
        self.app_id, self.secret = app_id, secret

    async def graphql(self, query, variables):
        gate = _shopee_gate(self.app_id)
        async with gate.lock:
            remaining = gate.cooldown_until - time.monotonic()
            if remaining > 0:
                raise ProviderError("rate", "A Shopee limitou as consultas. Aguarde a liberação.",
                                    retry_after=math.ceil(remaining))
            try:
                return await self._graphql(query, variables)
            except ProviderError as exc:
                if exc.code == "rate":
                    gate.cooldown_until = time.monotonic() + max(90, exc.retry_after or 90)
                raise

    async def _graphql(self, query, variables):
        body = json.dumps({"query": query, "variables": variables}, ensure_ascii=False, separators=(",", ":"))
        timestamp = int(time.time())
        signed = hashlib.sha256(f"{self.app_id}{timestamp}{body}{self.secret}".encode()).hexdigest()
        headers = {
            "Authorization": f"SHA256 Credential={self.app_id},Timestamp={timestamp},Signature={signed}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=9, follow_redirects=False, trust_env=False) as client:
                response = await client.post(SHOPEE_ENDPOINT, content=body.encode(), headers=headers)
            if response.status_code in {401, 403}:
                raise ProviderError("access", "A Shopee recusou o acesso. Confira as credenciais ou a permissão da conta.")
            if response.status_code == 429:
                try:
                    retry = max(90, int(response.headers.get("Retry-After", "90")))
                except (ValueError, TypeError):
                    retry = 90
                raise ProviderError("rate", "A Shopee limitou as consultas.", retry_after=retry)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("network", "A Shopee não respondeu como esperado. Tente novamente.") from None
        if isinstance(data, dict) and any(error.get("extensions", {}).get("code") in {10000, "10000"}
                                          for error in data.get("errors") or []):
            raise ProviderError("schema", "A Shopee retornou erro interno para um campo desta consulta.")
        if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
            raise ProviderError("access", "A Shopee não liberou a operação. Confira o acesso de afiliado e as credenciais.")
        return data["data"]

    async def search(self, keyword, limit=12, *, category=None, sort=2):
        if type(sort) is not int or sort not in range(1, 6):
            raise ProviderError("filter", "Selecione uma ordenação Shopee válida.")
        params = [f"limit:{min(24, max(2, limit))}", f"sortType:{sort}"]
        if keyword or not category:
            params.append("keyword:" + json.dumps((keyword or "mouse")[:80], ensure_ascii=True))
        if category is not None:
            if type(category) is not int or not 0 < category < 2**31:
                raise ProviderError("filter", "Selecione uma categoria Shopee válida.")
            params.append(f"productCatId:{category}")
        def query(arguments, fields):
            return "query Offers{productOfferV2(" + ",".join(arguments) + "){nodes{" + fields + "}}}"
        try:
            data = await self.graphql(query(params, FIELDS + MERCHANDISING + " imageUrl"), {})
            rows = self.nodes(data)
        except ProviderError as exc:
            if exc.code != "schema":
                raise
            fallback = [p for p in params if not p.startswith("productCatId:")]
            fields = BASE_FIELDS + (" productCatIds" if category else "")
            try:
                data = await self.graphql(query(fallback, fields), {})
            except ProviderError as fallback_error:
                if fallback_error.code != "schema" or limit <= 3:
                    raise
                data = await self.graphql(query(["limit:3", *fallback[1:]], fields), {})
            rows = self.nodes(data)
        return [row for row in rows if category in row["categories"]] if category else rows

    @staticmethod
    def nodes(data):
        result = []
        for node in (data.get("productOfferV2") or {}).get("nodes") or []:
            try:
                result.append(normalize_offer(node))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    async def detail(self, shop, item):
        if type(shop) is not int or type(item) is not int or not (0 < shop < 10**18 and 0 < item < 10**18):
            raise ProviderError("product", "Identificador Shopee inválido.")
        # The BR endpoint accepts Int64 literals for these two IDs; JSON
        # variables with the same Int64 declaration return provider error 10010.
        query = ("query Detail{productOfferV2(shopId:%d,itemId:%d,limit:1){nodes{%%s}}}" % (shop, item))
        try:
            data = await self.graphql(query % (FIELDS + MERCHANDISING + " imageUrl"), {})
        except ProviderError as exc:
            if exc.code != "schema":
                raise
            try:
                data = await self.graphql(query % (FIELDS + " imageUrl"), {})
            except ProviderError as legacy:
                if legacy.code != "schema":
                    raise
                data = await self.graphql(query % BASE_FIELDS, {})
        match = next((row for row in self.nodes(data) if (row["shop"], row["item"]) == (shop, item)), None)
        if not match:
            raise ProviderError("product", "O produto não está disponível no catálogo de afiliados consultado.")
        return match

    async def affiliate_link(self, offer, sub_id):
        query = ("mutation Short($origin:String!,$sub:[String!]){"
                 "generateShortLink(input:{originUrl:$origin,subIds:$sub}){shortLink}}")
        data = await self.graphql(query, {"origin": offer["catalog_url"], "sub": [sub_id]})
        link = (data.get("generateShortLink") or {}).get("shortLink")
        try:
            checked_url(link, SHORT_HOSTS)
        except ProviderError:
            raise ProviderError("attribution", "A Shopee não devolveu o link de afiliado desta conta.") from None
        return link

    async def tracked_affiliate_link(self, offer, token):
        """Keep attribution returned by Shopee for one server-created job.

        The native UTM serialization is deliberately not reconstructed here.
        A valid short link remains usable when its attribution cannot be read.
        """
        if not isinstance(token, str) or not re.fullmatch(r"orionj[a-f0-9]{32}", token):
            raise ProviderError("attribution", "Identificador de publicação inválido.")
        checked_url(offer["catalog_url"], PRODUCT_HOSTS)
        query = ("mutation Short($origin:String!,$sub:[String!]){"
                 "generateShortLink(input:{originUrl:$origin,subIds:$sub}){shortLink longLink}}")
        data = await self.graphql(query, {"origin": offer["catalog_url"], "sub": [token]})
        result = data.get("generateShortLink")
        result = result if isinstance(result, dict) else {}
        link = result.get("shortLink")
        try:
            if not isinstance(link, str):
                raise ProviderError("attribution", "Link de afiliado inválido.")
            checked_url(link, SHORT_HOSTS)
        except ProviderError:
            raise ProviderError("attribution", "A Shopee não devolveu o link de afiliado desta conta.") from None

        utm_content = None
        try:
            native = result.get("longLink")
            if not isinstance(native, str):
                raise ValueError("Missing native tracking link")
            checked_url(native, PRODUCT_HOSTS)
            values = [value for key, value in parse_qsl(
                urlsplit(native).query, keep_blank_values=True, errors="strict", max_num_fields=32,
            ) if key == "utm_content"]
            if len(values) == 1:
                value = values[0]
                # Confirm our complete token without assuming how empty sub-ID
                # slots are serialized. Store the returned value verbatim.
                if (len(value) <= 512 and re.fullmatch(r"[\x21-\x7e]+", value)
                        and value.startswith(token)
                        and (len(value) == len(token) or not value[len(token)].isalnum())):
                    utm_content = value
        except (ProviderError, ValueError, TypeError, UnicodeError):
            pass
        return {"affiliate_url": link, "tracking_token": token, "utm_content": utm_content,
                "tracking_status": "verified" if utm_content is not None else "unavailable"}


class Telegram:
    def __init__(self, token):
        self.token = token

    async def call(self, method, *, dispatch=False, data=None, **payload):
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
                url = f"https://api.telegram.org/bot{self.token}/{method}"
                if data:
                    response = await client.post(url, data=payload, files={"photo": ("oferta.png", data, "image/png")})
                else:
                    response = await client.post(url, json=payload)
            if response.status_code == 429:
                retry = min(3600, max(10, int(response.json().get("parameters", {}).get("retry_after", 60))))
                raise ProviderError("rate", "O Telegram limitou a frequência.", retry_after=retry)
            if response.status_code >= 500 and dispatch:
                raise ProviderError("unknown", "O Telegram pode ter recebido a publicação. Confira o destino.")
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("unknown" if dispatch else "network",
                                "O Telegram não confirmou a operação.") from None
        if not result.get("ok"):
            raise ProviderError("permissions", "O Telegram recusou a operação. Confira a conta e as permissões.")
        return result["result"]

    async def verify_destination(self, chat_id, bot_id, operator):
        chat = await self.call("getChat", chat_id=chat_id)
        if chat.get("type") not in {"group", "supergroup", "channel"}:
            raise ProviderError("permissions", "Escolha um grupo ou canal, e não uma conversa privada.")
        bot = await self.call("getChatMember", chat_id=chat["id"], user_id=bot_id)
        admin = await self.call("getChatMember", chat_id=chat["id"], user_id=operator)
        if any(person.get("status") not in {"creator", "administrator"} for person in (bot, admin)):
            raise ProviderError("permissions", "O bot e sua conta Telegram precisam administrar o destino.")
        if chat["type"] == "channel" and bot.get("status") != "creator" and not bot.get("can_post_messages"):
            raise ProviderError("permissions", "Dê ao bot a permissão de publicar mensagens no canal.")
        return {"chat_id": chat["id"], "title": chat.get("title", "Telegram"), "type": chat["type"],
                "media": True, "checked_at": int(time.time())}

    async def publish(self, chat_id, text, image=None, *, html=False):
        formatting = {"parse_mode": "HTML"} if html else {}
        if image:
            sent = await self.call("sendPhoto", dispatch=True, data=image, chat_id=str(chat_id), caption=text, **formatting)
        else:
            sent = await self.call("sendMessage", dispatch=True, chat_id=chat_id, text=text,
                                   link_preview_options={"is_disabled": True}, **formatting)
        if not isinstance(sent.get("message_id"), int) or sent.get("chat", {}).get("id") != chat_id:
            raise ProviderError("unknown", "O retorno não confirmou a publicação no destino esperado.")
        return sent["message_id"]
