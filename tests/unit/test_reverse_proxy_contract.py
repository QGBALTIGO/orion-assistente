"""Synthetic ASGI contract for the fixed loopback SSH ingress.

No Orion application, sockets, SSH process, database or provider is imported.
This verifies Uvicorn's trust boundary, not the deployed Nginx configuration.
"""
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from scripts.reverse_ingress import api_arguments


async def observed_scope(peer, *, headers=None, kind="http"):
    observed = {}

    async def synthetic_app(scope, _receive, _send):
        observed.update(scope)

    async def receive():
        raise AssertionError("The scope-only fixture must not consume a request")

    async def send(_message):
        raise AssertionError("The scope-only fixture must not send a request")

    scope = {
        "type": kind,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws" if kind == "websocket" else "http",
        "method": "GET",
        "path": "/synthetic",
        "raw_path": b"/synthetic",
        "query_string": b"test=only",
        "root_path": "",
        "server": ("127.0.0.1", 8080),
        "client": peer,
        "headers": headers if headers is not None else [
            (b"host", b"orionagenda.pro"),
            (b"origin", b"https://orionagenda.pro"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-for", b"198.51.100.27"),
        ],
    }
    middleware = ProxyHeadersMiddleware(synthetic_app, trusted_hosts="127.0.0.1,::1")
    await middleware(scope, receive, send)
    return observed


@pytest.mark.parametrize("loopback", ["127.0.0.1", "::1"])
async def test_fixed_local_forward_preserves_browser_https_host_origin_and_client(loopback):
    scope = await observed_scope((loopback, 43210))

    assert scope["scheme"] == "https"
    assert scope["client"] == ("198.51.100.27", 0)
    assert dict(scope["headers"])[b"host"] == b"orionagenda.pro"
    assert dict(scope["headers"])[b"origin"] == b"https://orionagenda.pro"
    assert scope["path"] == "/synthetic"
    assert scope["query_string"] == b"test=only"


@pytest.mark.parametrize("untrusted", ["203.0.113.45", "fd00::45", "127.0.0.2"])
async def test_forwarding_headers_from_a_non_trusted_peer_do_not_replace_metadata(untrusted):
    peer = (untrusted, 43210)
    scope = await observed_scope(peer)

    assert scope["scheme"] == "http"
    assert scope["client"] == peer


async def test_forwarded_host_does_not_override_the_actual_public_host():
    scope = await observed_scope(("127.0.0.1", 43210), headers=[
        (b"host", b"orionagenda.pro"),
        (b"origin", b"https://orionagenda.pro"),
        (b"x-forwarded-host", b"attacker.invalid"),
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-for", b"198.51.100.27"),
    ])

    assert dict(scope["headers"])[b"host"] == b"orionagenda.pro"
    assert dict(scope["headers"])[b"origin"] == b"https://orionagenda.pro"
    assert scope["server"] == ("127.0.0.1", 8080)


async def test_unrecognized_forwarded_scheme_is_not_accepted_even_from_loopback():
    scope = await observed_scope(("127.0.0.1", 43210), headers=[
        (b"host", b"orionagenda.pro"),
        (b"x-forwarded-proto", b"https, http"),
    ])

    assert scope["scheme"] == "http"
    assert scope["client"] == ("127.0.0.1", 43210)


async def test_local_websocket_forward_uses_secure_scheme_and_original_peer():
    scope = await observed_scope(("127.0.0.1", 43210), kind="websocket")

    assert scope["scheme"] == "wss"
    assert scope["client"] == ("198.51.100.27", 0)


async def test_missing_peer_cannot_authenticate_forwarding_headers():
    scope = await observed_scope(None)

    assert scope["scheme"] == "http"
    assert scope["client"] is None


def test_reverse_ingress_launcher_explicitly_limits_forwarding_trust_to_loopback(monkeypatch):
    # An inherited broad setting must not widen the opt-in tunnel's boundary.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    arguments = api_arguments("8080")

    assert "--proxy-headers" in arguments
    assert "--no-proxy-headers" not in arguments
    assert arguments[arguments.index("--forwarded-allow-ips") + 1] == "127.0.0.1,::1"
    assert arguments[arguments.index("--port") + 1] == "8080"
