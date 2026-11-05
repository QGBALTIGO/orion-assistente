"""Explicit URL-tag discovery with synthetic data and pinned public egress.

No business/contact values enter this helper. This is independent of a live
WhatsApp socket; automatic rule execution remains in the bridge outbox.
The caller owns durable idempotency and must never retry an uncertain POST.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import json
import logging
import re
import socket
from typing import Awaitable, Callable
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
import httpx

from app.subscription_device_schemas import destination
from app.subscription_chatbot_tags import discover_tags, MAX_TAGS


URL_TAG_ERRORS = {'CHATBOT_URL_INVALID': 'Informe uma URL HTTPS pública válida e o método GET ou POST.',
 'CHATBOT_URL_PRIVATE': 'A URL não pode apontar para uma rede privada ou endereço reservado.',
 'CHATBOT_URL_AUTH': 'O servidor externo exige autenticação ou recusou o acesso à consulta de tags.',
 'CHATBOT_URL_METHOD': 'O servidor externo não aceita o método selecionado. Confira GET ou POST.',
 'CHATBOT_URL_RESPONSE': 'O servidor externo não retornou uma resposta JSON válida de sucesso.',
 'CHATBOT_URL_REDIRECT': 'A URL redireciona para outro endereço. Use diretamente o endpoint HTTPS final.',
 'CHATBOT_URL_CONTENT_TYPE': 'O endpoint deve responder com Content-Type application/json.',
 'CHATBOT_URL_JSON': 'O endpoint deve retornar objeto ou lista JSON válida, dentro dos limites de tags e '
                     'tamanho.',
 'CHATBOT_URL_TOO_LARGE': 'A resposta da URL ultrapassa 512 KB.',
 'CHATBOT_URL_INCOMPLETE': 'A resposta da URL chegou incompleta. Confira o servidor externo.',
 'CHATBOT_URL_TIMEOUT': 'A consulta demorou demais. Confira o resultado antes de tentar novamente.',
 'CHATBOT_URL_UNAVAILABLE': 'Não foi possível consultar a URL. Confira o endereço, HTTPS e disponibilidade '
                            'do servidor.'}

MAX_BYTES = 512 * 1024
_private_http = ContextVar("subscription_chatbot_private_http", default=False)


class _PrivateRequestLogs(logging.Filter):
    def filter(self, record):
        # HTTPX INFO includes the full URL; httpcore DEBUG can include response
        # headers. Suppress only this task's private discovery request, without
        # changing logging levels or hiding another concurrent task's records.
        return not _private_http.get()


_private_request_logs = _PrivateRequestLogs()
for _logger_name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
                     "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_logger_name).addFilter(_private_request_logs)


@contextmanager
def _private_request_logging():
    token = _private_http.set(True)
    try:
        yield
    finally:
        _private_http.reset(token)


SYNTHETIC_CONTEXT = {
    "appName": "com.whatsapp", "messageDateTime": 0,
    "devicePhone": "5511000000000", "deviceName": "Exemplo Orion",
    "senderName": "Contato de exemplo", "senderMessage": "Consulta de tags",
    "senderPhone": "5511000000001", "userAgent": "Orion-Chatbot/1",
}
_DENIED4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4"))
_GLOBAL6 = ipaddress.ip_network("2000::/3")
_DENIED6 = tuple(ipaddress.ip_network(value) for value in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20"))


def _error(status: int, code: str, message: str):
    return HTTPException(status, {"code": code, "message": message})


def _public_address(value: str):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 4:
        return not any(address in network for network in _DENIED4)
    return address in _GLOBAL6 and not any(address in network for network in _DENIED6)


async def _resolve(host: str, port: int) -> str:
    try:
        direct = ipaddress.ip_address(host)
    except ValueError:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM), timeout=3)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    else:
        addresses = [str(direct)]
    if not addresses or len(addresses) > 32 or any(not _public_address(value) for value in addresses):
        raise _error(422, "CHATBOT_URL_PRIVATE", "Use uma URL pública válida.")
    return addresses[0]


def _reject_constant(_):
    raise ValueError("non_json_number")


async def read_url_tags(url: str, method: str, *, request_id: UUID,
                        authorize: Callable[[], Awaitable[None]]) -> list[str]:
    """Return renderable JSON paths only; no redirects, retries or real PII."""
    try:
        destination(url)
        if method not in {"GET", "POST"} or not isinstance(request_id, UUID) or "\\" in url:
            raise ValueError()
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host.endswith(".") or host.split(".")[-1] in {"test", "invalid", "onion"}:
            raise ValueError()
        original = httpx.URL(url)
        if method == "GET":
            original = original.copy_merge_params(SYNTHETIC_CONTEXT)
            if len(str(original)) > 2048:
                raise ValueError()
    except (ValueError, TypeError):
        raise _error(422, "CHATBOT_URL_INVALID", "Informe uma URL pública válida e o método GET ou POST.") from None
    response_body = bytearray()
    try:
        async with asyncio.timeout(8):
            address = await _resolve(host, original.port or (443 if original.scheme == "https" else 80))
            await authorize()
            # HTTPX connects to a literal validated IP. HTTP Host and TLS SNI
            # retain the original origin; certificate validation stays enabled.
            pinned = original.copy_with(host=address)
            headers = {"Host": original.netloc.decode("ascii"), "Accept": "application/json",
                       "Accept-Encoding": "identity", "User-Agent": "Orion-Chatbot/1",
                       "X-Orion-Operation-Id": str(request_id)}
            with _private_request_logging():
                async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3),
                                             follow_redirects=False, trust_env=False) as client:
                    async with client.stream(method, pinned, headers=headers,
                                              extensions={"sni_hostname": host},
                                              **({"json": SYNTHETIC_CONTEXT} if method == "POST" else {})) as response:
                        mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        encoding = response.headers.get("content-encoding", "identity").strip().lower()
                        declared = response.headers.get("content-length")
                        code = ("CHATBOT_URL_AUTH" if response.status_code in {401, 403} else
                                "CHATBOT_URL_METHOD" if response.status_code == 405 else
                                "CHATBOT_URL_REDIRECT" if 300 <= response.status_code < 400 else
                                "CHATBOT_URL_RESPONSE" if not 200 <= response.status_code < 300 or encoding not in {"", "identity"} else
                                "CHATBOT_URL_CONTENT_TYPE" if not (mime == "application/json" or re.fullmatch(r"application/[a-z0-9.+-]+\+json", mime)) else None)
                        if code:
                            raise _error(422, code, URL_TAG_ERRORS[code])
                        if declared is not None and (not re.fullmatch(r"[0-9]+", declared) or int(declared) > MAX_BYTES):
                            raise _error(413, "CHATBOT_URL_TOO_LARGE", "A resposta da URL ultrapassa o limite permitido.")
                        async for chunk in response.aiter_bytes():
                            if len(response_body) + len(chunk) > MAX_BYTES:
                                raise _error(413, "CHATBOT_URL_TOO_LARGE", "A resposta da URL ultrapassa o limite permitido.")
                            response_body.extend(chunk)
                        if declared is not None and len(response_body) != int(declared):
                            raise _error(422, "CHATBOT_URL_INCOMPLETE", "A URL retornou uma resposta incompleta.")
            await authorize()
            try:
                data = json.loads(response_body.decode("utf-8-sig"), parse_constant=_reject_constant)
                tags = discover_tags(data)
            except (ValueError, RecursionError, UnicodeDecodeError):
                raise _error(422, "CHATBOT_URL_JSON", "A URL não retornou um objeto ou lista JSON com campos válidos dentro dos limites.") from None
            return tags
    except (TimeoutError, httpx.TimeoutException):
        raise _error(504, "CHATBOT_URL_TIMEOUT", "A consulta da URL demorou demais. Confira o resultado antes de tentar novamente.") from None
    except (httpx.HTTPError, OSError):
        raise _error(502, "CHATBOT_URL_UNAVAILABLE", "Não foi possível consultar a URL. Confira o resultado antes de tentar novamente.") from None
    finally:
        response_body[:] = b"\x00" * len(response_body)
