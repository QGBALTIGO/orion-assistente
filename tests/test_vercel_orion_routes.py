"""Offline contracts for Orion's native Vercel proxy.

The small interpreter checks the committed route order and supported predicates;
it is not a Vercel emulator. ASGI tests use Orion's real auth and media routers,
with only Redis, quota and object storage replaced. Neither these tests nor the
8 MiB stream establish Vercel's live header normalization or upload limits.
"""
import base64
import hashlib
import io
import json
import re
import socket
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app import media_api, panel_auth


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "vercel-orion"
FRONT = "https://orion-painel-iota.vercel.app"
HOST = urlsplit(FRONT).netloc
UPSTREAM = "https://orion-assistente-production.up.railway.app"
TENANT = UUID("00000000-0000-4000-8000-000000000031")
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture(scope="module")
def bundle_directory(tmp_path_factory):
    from scripts.prepare_vercel_orion import prepare_bundle

    output = tmp_path_factory.mktemp("native-vercel-contract") / "facade"
    prepare_bundle(output)
    return output


@pytest.fixture(scope="module")
def config(bundle_directory):
    source = (DEPLOY / "vercel.json").read_bytes()
    assert (bundle_directory / "vercel.json").read_bytes() == source
    return json.loads(source)


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    """No external socket/DNS is permitted, including accidental provider calls."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_dns = socket.getaddrinfo

    def allowed(address):
        # Windows asyncio creates a loopback socketpair for its own wakeup pipe.
        return isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")

    def connect(sock, address):
        assert allowed(address), "external network forbidden in proxy contracts"
        return original_connect(sock, address)

    def connect_ex(sock, address):
        assert allowed(address), "external network forbidden in proxy contracts"
        return original_connect_ex(sock, address)

    def dns(host, *args, **kwargs):
        assert host in (None, "127.0.0.1", "::1", "localhost"), "external DNS forbidden"
        return original_dns(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", dns)


def _header(headers, key):
    # A repeated field is represented as its combined value, never first-wins.
    # Live normalization still needs a platform acceptance probe.
    return ", ".join(value for name, value in headers if name.lower() == key.lower())


def _predicate(condition, headers):
    assert condition["type"] in {"host", "header"}, "unsupported route predicate"
    key = "host" if condition["type"] == "host" else condition["key"]
    value = _header(headers, key)
    expected = condition.get("value")
    if expected is None:
        return bool(value)
    assert isinstance(expected, dict) and set(expected) == {"eq"}, "require exact matching"
    return value == expected["eq"]


@dataclass
class Decision:
    destination: str
    status: int
    headers: dict
    request_headers: list
    route_index: int

    @property
    def external(self):
        return bool(urlsplit(self.destination).netloc)


def decide(config, path, method="GET", headers=None):
    """Only implement grammar actually used here; unknown features fail loudly."""
    headers = list(headers if headers is not None else [("host", HOST)])
    response_headers = {}
    for index, route in enumerate(config["routes"]):
        assert set(route) <= {"src", "methods", "has", "missing", "headers", "continue",
                              "dest", "status", "transforms"}, "unmodelled route feature"
        match = re.fullmatch(route["src"], path.split("?", 1)[0])
        if not match or method not in route.get("methods", [method]):
            continue
        if not all(_predicate(item, headers) for item in route.get("has", [])):
            continue
        if any(_predicate(item, headers) for item in route.get("missing", [])):
            continue
        response_headers.update({key.lower(): value for key, value in route.get("headers", {}).items()})
        for transform in route.get("transforms", []):
            assert transform["type"] == "request.headers"
            assert transform["op"] in {"set", "delete"}
            key = transform["target"]["key"].lower()
            headers = [(name, value) for name, value in headers if name.lower() != key]
            if transform["op"] == "set":
                headers.append((key, transform["args"]))
        if route.get("continue"):
            assert "dest" not in route, "interpreter must not ignore a continuing rewrite"
            continue
        if "dest" not in route:
            # A status-only rejection terminates before static-resource handling.
            # Rendering /403.html is not equivalent: Vercel can answer OPTIONS
            # for a static resource with an automatic 204 instead of that status.
            assert route.get("status") == 403 and not route.get("continue")
        destination = route.get("dest", "")
        destination = re.sub(r"\$(\d+)", lambda item: match.group(int(item[1])) or "", destination)
        return Decision(destination, route.get("status", 200), response_headers, headers, index)
    raise AssertionError("routes must have a terminal deny fallback")


def test_configuration_is_native_and_targets_only_the_fixed_upstream(config, bundle_directory):
    assert config["version"] == 2 and config["framework"] is None
    assert config["buildCommand"] is None and config["installCommand"] is None
    assert config["outputDirectory"] == "public"
    assert not ({"functions", "builds", "rewrites", "headers"} & config.keys())
    assert not any(path.name.lower().startswith(("middleware.", "proxy."))
                   or path.name in {"functions", "api"} for path in bundle_directory.rglob("*"))
    for route in config["routes"]:
        destination = route.get("dest", "")
        parsed = urlsplit(destination)
        if parsed.netloc:
            assert parsed.scheme == "https" and parsed.netloc == urlsplit(UPSTREAM).netloc
            assert parsed.username is None and parsed.password is None
        elif destination:
            assert destination.startswith("/") and "$" not in destination
            assert (bundle_directory / config["outputDirectory"] / destination.lstrip("/")).is_file()


@pytest.mark.parametrize("method", UNSAFE)
@pytest.mark.parametrize("path", ["/auth/login", "/auth/logout", "/auth/register",
    "/auth/password/forgot", "/auth/password/reset", "/auth/verify/resend",
    "/api/public/sales-requests", "/api/v1/qr/reconnect",
    f"/api/v1/tenants/{TENANT}/media"])
def test_exact_same_origin_mutations_are_translated_only_after_the_guard(config, method, path):
    decision = decide(config, path, method, [("host", HOST), ("origin", FRONT),
        ("Forwarded", "host=evil.example"), ("X-Forwarded-Host", "evil.example"),
        ("x-orion-csrf", "synthetic-csrf"), ("cookie", "synthetic-cookie")])
    assert decision.external and decision.destination == UPSTREAM + path
    assert _header(decision.request_headers, "origin") == UPSTREAM
    assert not _header(decision.request_headers, "forwarded")
    assert not _header(decision.request_headers, "x-forwarded-host")
    assert _header(decision.request_headers, "x-orion-csrf") == "synthetic-csrf"
    assert _header(decision.request_headers, "cookie") == "synthetic-cookie"


@pytest.mark.parametrize("origin", [None, "", "null", "https://evil.example", UPSTREAM,
    FRONT + "/", FRONT + ":443", FRONT + ".evil.example", FRONT.upper(),
    "http://" + HOST, FRONT + ", https://evil.example", "https://evil.example, " + FRONT])
@pytest.mark.parametrize("method", UNSAFE)
def test_unsafe_untrusted_or_missing_origin_never_reaches_upstream(config, origin, method):
    headers = [("host", HOST)] + ([] if origin is None else [("origin", origin)])
    decision = decide(config, "/auth/login", method, headers)
    assert not decision.external and decision.status == 403
    assert _header(decision.request_headers, "origin") == (origin or "")


@pytest.mark.parametrize("host", ["preview-orion.vercel.app", HOST + ".evil.example",
    HOST + ":443", HOST.upper(), "evil.example", ""])
def test_origin_alone_cannot_authorize_preview_or_nonexact_host(config, host):
    decision = decide(config, "/auth/login", "POST", [("host", host), ("origin", FRONT)])
    assert not decision.external and decision.status == 403


@pytest.mark.parametrize("values", [(FRONT, "https://evil.example"),
    ("https://evil.example", FRONT), (FRONT, FRONT)])
def test_repeated_origin_is_not_treated_as_one_exact_origin(config, values):
    decision = decide(config, "/auth/login", "POST",
                      [("host", HOST), *(("Origin", value) for value in values)])
    assert not decision.external and decision.status == 403


@pytest.mark.parametrize("method", ["OPTIONS", "TRACE", "CONNECT", "PROPFIND"])
@pytest.mark.parametrize("path", ["/auth/login", "/api/v1/qr/status", "/panel"])
def test_cors_and_unsupported_methods_do_not_forward(config, method, path):
    decision = decide(config, path, method, [("host", HOST), ("origin", FRONT)])
    assert not decision.external and decision.status in {403, 404}
    assert "access-control-allow-origin" not in decision.headers


def test_options_denial_terminates_before_any_static_resource(config):
    for path in ("/auth/login", "/", "/internal/status", "/panel-assets/orion-suite.js",
                 "/integrations/google/callback", "/panel-assets/%2e%2e/panel.html"):
        for origin in (None, FRONT, "https://evil.example"):
            headers = [("host", HOST)] + ([] if origin is None else [("origin", origin)])
            decision = decide(config, path, "OPTIONS", headers)
            route = config["routes"][decision.route_index]
            assert decision.status == 403 and not decision.destination and not decision.external
            assert route["methods"] == ["OPTIONS"] and "dest" not in route
            assert not route.get("continue") and not route.get("transforms")
            assert "access-control-allow-origin" not in decision.headers
            assert decision.headers["cdn-cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/internal", "/internal/legacy-central/status", "/docs",
    "/docs/oauth2-redirect", "/redoc", "/openapi.json", "/.git/config", "/.env",
    "/backups/latest.sql", "/reports/private.html", "/scripts/start_api.py",
    "/tests/test_booking.py", "/deploy/vercel-orion/vercel.json", "/app/panel_auth.py",
    "/alembic/versions/0030_legacy_admin_identity.py", "/pyproject.toml", "/webhooks/whatsapp",
    "/api/v1/internal/status", "/api/v1/tenantsx", "/auth/me/extra", "/unknown"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_private_and_unlisted_paths_are_never_proxied(config, path, method):
    decision = decide(config, path, method, [("host", HOST), ("origin", FRONT)])
    assert not decision.external and decision.status in {403, 404}


@pytest.mark.parametrize("path", ["/panel-assets/../panel.html", "/panel-assets/%2e%2e/panel.html",
    "/panel-assets/%252e%252e/panel.html", "/api/v1/tenants/%2f..%2finternal",
    "/api/v1/tenants/%5c..%5cinternal", "/api/v1/tenants/..\\internal",
    "/panel-assets\\..\\.env", "/%69nternal/status", "/internal%2fstatus",
    "/api/v1/tenants/a/../../internal", "/api/v1/tenants/%00", "//internal/status"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_raw_encoded_or_literal_traversal_is_denied_before_rewrite(config, path, method):
    decision = decide(config, path, method, [("host", HOST), ("origin", FRONT)])
    assert not decision.external and decision.status in {403, 404}


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/panel-assets/orion-suite.js", "/panel", "/auth/me", "/health", "/health/central",
    "/api/v1/qr/status", "/api/v1/tenants", "/s/synthetic-studio",
    "/app/site-preview/synthetic-studio", "/brand/orion-symbol.svg",
    "/panel-assets/orion-auth.css", f"/media/{TENANT}/{TENANT}.webp"])
def test_allowlisted_reads_need_no_origin_and_have_private_edge_cache_policy(config, path, method):
    decision = decide(config, path, method)
    assert decision.external and decision.destination == UPSTREAM + path
    assert decision.headers["cache-control"] == "private, no-store, max-age=0"
    assert decision.headers["cdn-cache-control"] == "no-store"
    assert decision.headers["vercel-cdn-cache-control"] == "no-store"
    assert decision.headers["x-vercel-enable-rewrite-caching"] == "0"


@pytest.mark.parametrize("path", ["/panel/", "/convite/", "/conectar-whatsapp/"])
def test_trailing_slash_is_normalized_inside_the_rewrite(config, path):
    assert decide(config, path).destination == UPSTREAM + path.rstrip("/")


@pytest.mark.parametrize("path", ["/", "/panel-assets/orion-suite.js"])
def test_landing_and_suite_are_live_railway_rewrites(config, bundle_directory, path):
    decision = decide(config, path)
    assert decision.external and decision.destination == UPSTREAM + path
    assert not (bundle_directory / "public/index.html").exists()
    assert not (bundle_directory / "public/orion-suite-vercel.js").exists()


def test_homepage_facade_marker_is_fixed_by_edge(config):
    decision = decide(config, "/", "GET", [
        ("host", HOST), ("x-orion-facade", "evil"), ("Forwarded", "host=evil.test"),
        ("X-Forwarded-Host", "evil.test")])
    assert decision.external and decision.destination == UPSTREAM + "/"
    assert _header(decision.request_headers, "x-orion-facade") == "vercel-iota"
    assert not _header(decision.request_headers, "forwarded")
    assert not _header(decision.request_headers, "x-forwarded-host")


@pytest.mark.parametrize("path,method", [
    (f"/api/v1/tenants/{TENANT}/google/connect", "POST"),
    ("/integrations/google/callback", "GET"), ("/integrations/google/callback/", "GET")])
def test_google_authorization_fails_locally_before_any_state_or_provider_write(config, path, method):
    decision = decide(config, path, method, [("host", HOST), ("origin", FRONT)])
    assert not decision.external and decision.status == 409
    assert decision.destination == "/google-origin.json"


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **options):
        assert options.get("nx") and options["ex"] > 0
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def aclose(self):
        pass


class RouteProxy:
    """In-process adapter; assumes native proxy uses the destination's Host."""
    def __init__(self, config, upstream):
        self.config, self.upstream = config, upstream
        self.forwarded = []

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "http"
        headers = [(name.decode("latin1"), value.decode("latin1")) for name, value in scope["headers"]]
        decision = decide(self.config, scope["raw_path"].decode("ascii"), scope["method"], headers)

        async def edge_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                kept = [(key, value) for key, value in message.get("headers", [])
                        if key.decode("latin1").lower() not in decision.headers]
                message["headers"] = kept + [(key.encode(), value.encode())
                                              for key, value in decision.headers.items()]
            await send(message)

        if not decision.external:
            await edge_send({"type": "http.response.start", "status": decision.status, "headers": []})
            await edge_send({"type": "http.response.body", "body": b"local edge response"})
            return
        destination = urlsplit(decision.destination)
        rewritten = [(key, value) for key, value in decision.request_headers if key.lower() != "host"]
        rewritten.append(("host", destination.netloc))
        upstream_scope = dict(scope, scheme="https", server=(destination.netloc, 443),
                              path=destination.path, raw_path=destination.path.encode("ascii"),
                              headers=[(key.lower().encode(), value.encode("latin1")) for key, value in rewritten])
        self.forwarded.append((scope["method"], destination.path, rewritten))
        # The original receive stream and query string are passed without buffering.
        await self.upstream(upstream_scope, receive, edge_send)


@pytest.fixture
def application(monkeypatch, config):
    password = "synthetic-only-password"
    salt, rounds = b"vercel-orion-test-salt", 200_000
    encoded = "pbkdf2_sha256$" + str(rounds) + "$" + base64.urlsafe_b64encode(salt).decode() + "$"
    encoded += base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)).decode()
    for key, value in {"PANEL_ADMIN_USERNAME": "synthetic-admin", "PANEL_ADMIN_PASSWORD_HASH": encoded,
        "PANEL_LEGACY_ADMIN_IDENTITY_ENABLED": "0", "BUSINESS_PROFILES_ENABLED": "0",
        "SUPPORT_API_TOKEN": "synthetic-support-token-" + "s" * 40}.items():
        monkeypatch.setenv(key, value)
    redis = MemoryRedis()
    monkeypatch.setattr(panel_auth, "_redis", lambda: redis)
    stored, quotas = [], []
    monkeypatch.setattr(media_api, "_storage", lambda: (object(), "synthetic-memory-only"))

    async def quota(tenant):
        quotas.append(tenant)

    def put(tenant, image, body):
        stored.append((tenant, image, body))

    monkeypatch.setattr(media_api, "_upload_limit", quota)
    monkeypatch.setattr(media_api, "_put", put)
    app = FastAPI()
    app.include_router(panel_auth.router)
    app.include_router(media_api.router)
    proxy = RouteProxy(config, app)
    return proxy, app, redis, stored, quotas, {"username": "synthetic-admin", "password": password}


async def sign_in(client, payload):
    response = await client.post("/auth/login", headers={"origin": FRONT}, json=payload)
    assert response.status_code == 200, response.text
    return response


async def test_actual_asgi_login_cookie_origin_csrf_and_logout(application):
    proxy, _, redis, _, _, payload = application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url=FRONT) as client:
        login = await sign_in(client, payload)
        cookie = SimpleCookie()
        cookie.load(login.headers["set-cookie"])
        value = cookie[panel_auth.COOKIE]
        assert value["secure"] and value["httponly"] and value["samesite"] == "strict"
        assert value["path"] == "/" and not value["domain"]
        assert len(redis.values) == 1
        assert (await client.get("/auth/me")).json()["authenticated"]
        csrf = login.json()["csrf"]
        assert (await client.post("/auth/logout", headers={"origin": FRONT})).status_code == 403
        before = len(proxy.forwarded)
        bad = await client.post("/auth/logout", headers={"origin": "https://evil.example", "x-orion-csrf": csrf})
        assert bad.status_code == 403 and len(proxy.forwarded) == before
        assert (await client.get("/auth/me")).status_code == 200
        logout = await client.post("/auth/logout", headers={"origin": FRONT, "x-orion-csrf": csrf})
        assert logout.status_code == 200 and not redis.values
        assert (await client.get("/auth/me")).status_code == 401
        for response in (login, bad, logout):
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["cdn-cache-control"] == "no-store"
            assert response.headers["vercel-cdn-cache-control"] == "no-store"
        for method, _, headers in proxy.forwarded:
            if method == "POST":
                assert _header(headers, "origin") == UPSTREAM
                assert _header(headers, "host") == urlsplit(UPSTREAM).netloc


@pytest.mark.parametrize("origins", [[], ["https://evil.example"], ["null"],
    [FRONT, "https://evil.example"], ["https://evil.example", FRONT], [FRONT, FRONT]])
async def test_rejected_login_never_calls_auth_or_changes_sessions(application, origins):
    proxy, _, redis, _, _, payload = application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url=FRONT) as client:
        response = await client.post("/auth/login", headers=[("origin", value) for value in origins], json=payload)
    assert response.status_code == 403 and not proxy.forwarded and not redis.values
    assert "set-cookie" not in response.headers


async def test_upstream_origin_check_is_real_and_has_not_been_mocked(application):
    _, app, redis, _, _, payload = application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=UPSTREAM) as client:
        response = await client.post("/auth/login", headers={"origin": FRONT}, json=payload)
    assert response.status_code == 403 and not redis.values


async def test_existing_upstream_session_is_preserved_and_not_shared_between_hosts(application):
    proxy, app, redis, _, _, payload = application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=UPSTREAM) as old:
        signed = await old.post("/auth/login", headers={"origin": UPSTREAM}, json=payload)
        assert signed.status_code == 200
        old_entries = dict(redis.values)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url=FRONT,
                                     cookies=old.cookies) as new:
            assert (await new.get("/auth/me")).status_code == 401
            assert redis.values == old_entries
            await sign_in(new, payload)
            assert len(redis.values) == 2
            assert all(redis.values[key] == value for key, value in old_entries.items())
        assert (await old.get("/auth/me")).status_code == 200


class ChunkedImage(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body, self.yielded = body, 0

    async def __aiter__(self):
        for start in range(0, len(self.body), 65_537):
            chunk = self.body[start:start + 65_537]
            self.yielded += len(chunk)
            yield chunk


def _png_with_size(length):
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "#526bc6").save(output, format="PNG")
    data = output.getvalue()
    return data + b"\0" * (length - len(data))


@pytest.mark.parametrize("excess,expected", [(0, 201), (1, 413)])
async def test_actual_media_route_streams_eight_mib_and_keeps_the_backend_limit(application, excess, expected):
    proxy, _, _, stored, quotas, payload = application
    stream = ChunkedImage(_png_with_size(media_api.MAX_INPUT + excess))
    assert media_api.MAX_INPUT == 8 * 1024 * 1024
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url=FRONT) as client:
        login = await sign_in(client, payload)
        response = await client.post(f"/api/v1/tenants/{TENANT}/media", content=stream, headers={
            "origin": FRONT, "x-orion-csrf": login.json()["csrf"],
            "content-type": "image/png", "x-orion-image-kind": "service"})
    assert response.status_code == expected, response.text
    assert stream.yielded == media_api.MAX_INPUT + excess and quotas == [TENANT]
    if excess:
        assert not stored
    else:
        assert len(stored) == 1 and stored[0][0] == TENANT
        assert response.json()["url"].startswith(f"/media/{TENANT}/")
        with Image.open(io.BytesIO(stored[0][2])) as result:
            assert result.format == "WEBP" and result.size == (32, 32)
        assert len(stored[0][2]) < media_api.MAX_INPUT


async def test_media_csrf_is_checked_before_quota_stream_or_storage(application):
    proxy, _, _, stored, quotas, payload = application
    stream = ChunkedImage(_png_with_size(1024))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url=FRONT) as client:
        await sign_in(client, payload)
        response = await client.post(f"/api/v1/tenants/{TENANT}/media", content=stream, headers={
            "origin": FRONT, "content-type": "image/png", "x-orion-image-kind": "service"})
    assert response.status_code == 403
    assert not stored and not quotas and stream.yielded == 0
