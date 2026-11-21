"""Opt-in, authenticated metadata from the single controlled public ingress.

This authenticates proxy metadata, never a user or tenant. Ordinary forwarded
headers remain untrusted and cannot enable the feature.
"""
import base64
import binascii
import hmac
import ipaddress
import os
import re

from starlette.types import ASGIApp, Receive, Scope, Send


_TOKEN_HEADER = b"x-orion-ingress-token"
_CLIENT_HEADER = b"x-orion-client-ip"
_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


def _configuration(token: str, public_host: str) -> tuple[bytes | None, bytes]:
    if not token and not public_host:
        return None, b""
    if not token or not public_host:
        raise ValueError("ORION_INGRESS_TOKEN and ORION_INGRESS_PUBLIC_HOST must be configured together")
    if not _TOKEN_FORMAT.fullmatch(token):
        raise ValueError("ORION_INGRESS_TOKEN must be a canonical token_urlsafe(32) value")
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("ORION_INGRESS_TOKEN must be a canonical token_urlsafe(32) value") from None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != token:
        raise ValueError("ORION_INGRESS_TOKEN must be a canonical token_urlsafe(32) value")
    host = public_host.lower()
    if (not public_host.isascii() or len(host) > 253 or "." not in host
            or any(not _HOST_LABEL.fullmatch(label) for label in host.split("."))):
        raise ValueError("ORION_INGRESS_PUBLIC_HOST must be an ASCII hostname without URL, port or whitespace")
    return token.encode("ascii"), host.encode("ascii")


def _client_ip(raw: bytes) -> str | None:
    # A plain IPv6 literal, including an embedded IPv4 tail, has at most45 bytes.
    if not raw or len(raw) > 45 or b"%" in raw:
        return None
    try:
        return ipaddress.ip_address(raw.decode("ascii")).compressed
    except (UnicodeDecodeError, ValueError):
        return None


class AuthenticatedIngressMiddleware:
    """Apply fixed HTTPS ingress metadata and an authenticated canonical IP.

    Both settings default to empty. A partial or malformed configuration fails
    middleware initialization without including either configured value in the
    error. The dedicated headers are stripped even when forwarding is disabled
    or rejected, so application handlers never receive the ingress credential.
    """

    def __init__(self, app: ASGIApp, *, token: str | None = None, public_host: str | None = None):
        self.app = app
        self._token, self._public_host = _configuration(
            os.getenv("ORION_INGRESS_TOKEN", "") if token is None else token,
            os.getenv("ORION_INGRESS_PUBLIC_HOST", "") if public_host is None else public_host,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        tokens: list[bytes] = []
        clients: list[bytes] = []
        headers: list[tuple[bytes, bytes]] = []
        original_headers = scope.get("headers", [])
        for name, value in original_headers:
            lowered = name.lower()
            if lowered == _TOKEN_HEADER:
                tokens.append(value)
            elif lowered == _CLIENT_HEADER:
                clients.append(value)
            else:
                headers.append((name, value))

        client_ip = None
        if (self._token is not None and len(tokens) == 1 and len(clients) == 1
                and len(tokens[0]) == len(self._token)
                and hmac.compare_digest(self._token, tokens[0])):
            client_ip = _client_ip(clients[0])

        if client_ip is not None:
            headers = [(name, value) for name, value in headers if name.lower() != b"host"]
            headers.append((b"host", self._public_host))

        if len(headers) != len(original_headers) or client_ip is not None:
            forwarded_scope = dict(scope)
            forwarded_scope["headers"] = headers
            if client_ip is not None:
                peer = scope.get("client")
                forwarded_scope["client"] = (client_ip, peer[1] if peer else 0)
                forwarded_scope["scheme"] = "wss" if scope["type"] == "websocket" else "https"
            scope = forwarded_scope

        await self.app(scope, receive, send)
