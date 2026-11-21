"""Authenticated ingress metadata without databases, Redis or provider traffic."""
import asyncio
import base64
import copy

from fastapi import HTTPException
from starlette.requests import Request
import pytest

from app.ingress import AuthenticatedIngressMiddleware
from app.panel_auth import _same_origin


TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
PUBLIC_HOST = "orionagenda.pro"
ORIGIN_HOST = "orion-assistente-production.up.railway.app"
PEER = ("10.0.0.17", 43210)
TOKEN_HEADER = b"x-orion-ingress-token"
IP_HEADER = b"x-orion-client-ip"


@pytest.fixture(autouse=True)
def disabled_environment(monkeypatch):
    monkeypatch.delenv("ORION_INGRESS_TOKEN", raising=False)
    monkeypatch.delenv("ORION_INGRESS_PUBLIC_HOST", raising=False)


def scope(*extra, origin=b"https://orionagenda.pro", kind="http"):
    headers = [
        (b"host", ORIGIN_HOST.encode()),
        (b"cookie", b"__Host-orion_session=synthetic-session; other=unchanged"),
        (b"x-orion-csrf", b"synthetic-csrf"),
        (b"x-forwarded-for", b"198.51.100.250, 127.0.0.1"),
        (b"forwarded", b"for=198.51.100.251;host=attacker.invalid;proto=http"),
        (b"x-forwarded-host", b"attacker.invalid"),
        (b"x-forwarded-proto", b"http"),
        (b"x-orion-public-host", b"attacker.invalid"),
    ]
    if origin is not None:
        headers.append((b"origin", origin))
    return {
        "type": kind, "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https", "path": "/s/empresa-sintetica",
        "raw_path": b"/s/empresa-sintetica", "root_path": "",
        "query_string": b"check=a%2Bb&space=x+y&empty=&check=second",
        "headers": headers + list(extra), "client": PEER,
        "server": ("origin.internal", 8080), "state": {"existing": "unchanged"},
    }


def credential(client=b"203.0.113.10", token=None):
    return [(TOKEN_HEADER, TOKEN.encode() if token is None else token), (IP_HEADER, client)]


async def invoke(request_scope, **configuration):
    captured = []
    sent = []
    payload = {"type": "http.request", "body": b"synthetic body", "more_body": False}
    response = [
        {"type": "http.response.start", "status": 200,
         "headers": [(b"set-cookie", b"__Host-orion_session=synthetic; Secure; HttpOnly; Path=/"),
                     (b"location", b"/painel?unchanged=1")]},
        {"type": "http.response.body", "body": b"unchanged response"},
    ]
    if request_scope["type"] == "websocket":
        payload = {"type": "websocket.connect"}
        response = [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1000}]
    elif request_scope["type"] == "lifespan":
        payload = {"type": "lifespan.startup"}
        response = [{"type": "lifespan.startup.complete"}]

    async def receive():
        return payload

    async def send(message):
        sent.append(message)

    async def downstream(received, recv, output):
        captured.append(received)
        assert await recv() is payload
        for message in response:
            await output(message)

    middleware = AuthenticatedIngressMiddleware(downstream, **configuration)
    await middleware(request_scope, receive, send)
    assert sent == response
    return captured[0]


def configured():
    return {"token": TOKEN, "public_host": PUBLIC_HOST}


@pytest.mark.parametrize("client,canonical", [
    (b"203.0.113.10", "203.0.113.10"),
    (b"2001:0DB8:0000:0000:0000:0000:0000:0010", "2001:db8::10"),
    (b"::1", "::1"),
])
@pytest.mark.asyncio
async def test_authenticated_ip_is_canonical_and_host_only_comes_from_configuration(client, canonical):
    original = scope(*credential(client))
    before = copy.deepcopy(original)
    received = await invoke(original, **configured())
    assert received is not original and original == before
    assert received["client"] == (canonical, PEER[1])
    assert Request(received).url.netloc == PUBLIC_HOST
    assert [(name, value) for name, value in received["headers"] if name.lower() == b"host"] == [
        (b"host", PUBLIC_HOST.encode())]
    assert not {TOKEN_HEADER, IP_HEADER}.intersection(name.lower() for name, _ in received["headers"])
    for key, value in before.items():
        if key not in {"client", "headers"}:
            assert received[key] == value
    assert [(name, value) for name, value in received["headers"] if name != b"host"] == [
        (name, value) for name, value in before["headers"]
        if name.lower() not in {TOKEN_HEADER, IP_HEADER, b"host"}]


@pytest.mark.asyncio
async def test_concurrent_authenticated_clients_keep_distinct_request_scopes():
    requests = [scope(*credential(ip)) for ip in (b"203.0.113.10", b"203.0.113.11")]
    results = await asyncio.gather(*(invoke(item, **configured()) for item in requests))
    assert [Request(item).client.host for item in results] == ["203.0.113.10", "203.0.113.11"]
    assert all(item["client"] == PEER for item in requests)


@pytest.mark.parametrize("extra", [
    [],
    [(IP_HEADER, b"203.0.113.10")],
    [(TOKEN_HEADER, TOKEN.encode())],
    credential(token=b"incorrect"),
    credential(token=b"\xff" * 43),
    credential(token=b" " + TOKEN.encode()),
    credential() + [(TOKEN_HEADER, TOKEN.encode())],
    credential() + [(b"X-Orion-Ingress-Token", TOKEN.encode())],
    credential() + [(IP_HEADER, b"203.0.113.10")],
    credential() + [(b"X-Orion-Client-IP", b"203.0.113.11")],
    credential(token=TOKEN.encode() + b", " + TOKEN.encode()),
])
@pytest.mark.asyncio
async def test_missing_spoofed_or_duplicate_credentials_cannot_override_peer_or_host(extra):
    original = scope(*extra)
    received = await invoke(original, **configured())
    assert received["client"] == PEER
    assert Request(received).url.netloc == ORIGIN_HOST
    assert not {TOKEN_HEADER, IP_HEADER}.intersection(name.lower() for name, _ in received["headers"])


@pytest.mark.parametrize("client", [
    b"", b"unknown", b"example.invalid", b"203.0.113.10, 203.0.113.11",
    b"203.0.113.10:443", b"[2001:db8::1]", b"[2001:db8::1]:443", b"fe80::1%eth0",
    b" 203.0.113.10", b"203.0.113.10 ", b"203.0.113.10\r\nx: value", b"\xff",
    b"203.0.113.999", b"203.0.113.010", b"127.1", b"0x7f000001", b"1" * 46,
])
@pytest.mark.asyncio
async def test_invalid_ip_rejects_the_entire_forwarding_pair_even_with_the_correct_token(client):
    original = scope(*credential(client))
    original["scheme"] = "http"
    received = await invoke(original, **configured())
    assert received["client"] == PEER
    assert Request(received).url.netloc == ORIGIN_HOST
    assert received["scheme"] == "http"


@pytest.mark.parametrize("configuration", [{}, {"token": "", "public_host": ""}])
@pytest.mark.asyncio
async def test_disabled_feature_ignores_even_matching_credentials_and_strips_them(configuration):
    original = scope(*credential())
    original["scheme"] = "http"
    received = await invoke(original, **configuration)
    assert received["client"] == PEER
    assert Request(received).url.netloc == ORIGIN_HOST
    assert received["scheme"] == "http"
    assert not {TOKEN_HEADER, IP_HEADER}.intersection(name.lower() for name, _ in received["headers"])


@pytest.mark.parametrize("token,host", [
    (TOKEN, ""), ("", PUBLIC_HOST),
    ("short", PUBLIC_HOST), (TOKEN + "=", PUBLIC_HOST), ("A" * 42 + "B", PUBLIC_HOST),
    ("\N{LATIN SMALL LETTER E WITH ACUTE}" * 43, PUBLIC_HOST), (" " + TOKEN, PUBLIC_HOST),
    (TOKEN, "https://private.invalid"), (TOKEN, "private.invalid:443"),
    (TOKEN, "user@private.invalid"), (TOKEN, "private.invalid/path"),
    (TOKEN, "private.invalid?x=1"), (TOKEN, "private.invalid#fragment"),
    (TOKEN, "private.invalid\r\nX: value"), (TOKEN, " private.invalid"),
    (TOKEN, "private.invalid "), (TOKEN, "private.invalid."),
    (TOKEN, "localhost"), (TOKEN, "bad_label.invalid"), (TOKEN, "-bad.invalid"),
    (TOKEN, "bad-.invalid"), (TOKEN, "private..invalid"),
    (TOKEN, "\N{KELVIN SIGN}.invalid"), (TOKEN, "a" * 254),
])
def test_invalid_configuration_fails_without_exposing_configured_values(token, host):
    with pytest.raises(ValueError) as exc:
        AuthenticatedIngressMiddleware(None, token=token, public_host=host)
    if len(token) >= 10:
        assert token not in str(exc.value)
    if host:
        assert host not in str(exc.value)


@pytest.mark.asyncio
async def test_environment_pair_enables_the_feature_and_normalizes_configured_host(monkeypatch):
    monkeypatch.setenv("ORION_INGRESS_TOKEN", TOKEN)
    monkeypatch.setenv("ORION_INGRESS_PUBLIC_HOST", PUBLIC_HOST.upper())
    received = await invoke(scope(*credential()))
    assert Request(received).url.netloc == PUBLIC_HOST
    assert received["client"][0] == "203.0.113.10"


@pytest.mark.parametrize("token,origin,expected_host,allowed", [
    (TOKEN.encode(), b"https://orionagenda.pro", PUBLIC_HOST, True),
    (b"incorrect", b"https://orionagenda.pro", ORIGIN_HOST, False),
    (TOKEN.encode(), b"https://attacker.invalid", PUBLIC_HOST, False),
    (TOKEN.encode(), b"https://orionagenda.pro.attacker.invalid", PUBLIC_HOST, False),
])
@pytest.mark.asyncio
async def test_real_same_origin_policy_uses_fixed_authenticated_host_without_rewriting_origin(
        token, origin, expected_host, allowed):
    received = await invoke(scope(*credential(token=token), origin=origin), **configured())
    request = Request(received)
    assert request.url.netloc == expected_host
    assert request.headers["origin"].encode() == origin
    assert request.cookies["__Host-orion_session"] == "synthetic-session"
    assert request.headers["x-orion-csrf"] == "synthetic-csrf"
    if allowed:
        _same_origin(request)
    else:
        with pytest.raises(HTTPException) as exc:
            _same_origin(request)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_valid_ingress_never_synthesizes_an_origin_or_requires_a_peer_port():
    original = scope(*credential(), origin=None)
    original["client"] = None
    received = await invoke(original, **configured())
    assert received["client"] == ("203.0.113.10", 0)
    assert "origin" not in Request(received).headers
    assert original["client"] is None


@pytest.mark.asyncio
async def test_valid_ingress_replaces_all_transport_host_headers_with_one_fixed_host():
    received = await invoke(scope(*credential(), (b"Host", b"another.invalid")), **configured())
    assert [(name, value) for name, value in received["headers"] if name.lower() == b"host"] == [
        (b"host", PUBLIC_HOST.encode())]


@pytest.mark.asyncio
async def test_websocket_metadata_is_authenticated_without_touching_upgrade_or_cookie():
    original = scope(*credential(b"2001:db8::20"), (b"upgrade", b"websocket"), kind="websocket")
    original["scheme"] = "ws"
    received = await invoke(original, **configured())
    assert received["client"] == ("2001:db8::20", PEER[1])
    assert received["scheme"] == "wss" and original["scheme"] == "ws"
    assert (b"upgrade", b"websocket") in received["headers"]
    assert (b"cookie", b"__Host-orion_session=synthetic-session; other=unchanged") in received["headers"]


@pytest.mark.asyncio
async def test_lifespan_passes_the_original_scope_through_untouched():
    original = {"type": "lifespan", "state": {"existing": True}}
    received = await invoke(original, **configured())
    assert received is original


@pytest.mark.parametrize("token,scheme,expected_scheme", [
    (TOKEN.encode(), "http", "https"),
    (b"incorrect", "http", "http"),
    (b"incorrect", "https", "https"),
])
@pytest.mark.asyncio
async def test_only_authenticated_ingress_establishes_the_fixed_https_scheme(token, scheme, expected_scheme):
    original = scope(*credential(token=token))
    original["scheme"] = scheme
    received = await invoke(original, **configured())
    assert received["scheme"] == expected_scheme
    assert original["scheme"] == scheme


@pytest.mark.parametrize("token,expected", [
    (TOKEN, "https://orionagenda.pro/auth/login?next=%2Fpainel&check=a%2Bb"),
    ("incorrect", "http://" + ORIGIN_HOST + "/auth/login?next=%2Fpainel&check=a%2Bb"),
])
@pytest.mark.asyncio
async def test_real_slash_redirect_never_downgrades_an_authenticated_https_ingress(token, expected):
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Route

    async def login(_request):
        pytest.fail("This test must inspect the slash redirect without following it")

    app = AuthenticatedIngressMiddleware(
        Starlette(routes=[Route("/auth/login", login, methods=["POST"])]), **configured())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://" + ORIGIN_HOST, follow_redirects=False) as client:
        response = await client.post("/auth/login/?next=%2Fpainel&check=a%2Bb", content=b"synthetic",
            headers={"X-Orion-Ingress-Token": token, "X-Orion-Client-IP": "203.0.113.10",
                     "X-Forwarded-Proto": "https", "X-Forwarded-Host": PUBLIC_HOST})
    assert response.status_code == 307
    assert response.headers["location"] == expected


def test_production_app_registers_middleware_and_health_stays_minimal():
    from fastapi.testclient import TestClient
    from app.main import app

    assert any(item.cls is AuthenticatedIngressMiddleware for item in app.user_middleware)
    response = TestClient(app).get("/health", headers={
        "X-Orion-Ingress-Token": TOKEN, "X-Orion-Client-IP": "203.0.113.10"})
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert "ingress" not in response.text and TOKEN not in response.text
