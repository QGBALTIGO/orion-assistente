"""Isolated real HTTPS API + PostgreSQL + Redis + Chromium qualification.

No app dependency is replaced. Fixtures may seed synthetic accounts/legacy
records, and all assertions read the migrated PostgreSQL database directly.
The file is inert during the normal unit/integration collection.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic#E2E-2026-Only"
LOCAL = {"localhost", "127.0.0.1", "::1"}


def redact_text(value):
    value = value.replace(PASSWORD, "[redacted-password]")
    value = re.sub(r"\$argon2(?:id|i|d)\$[A-Za-z0-9$=,+/.~-]+", "[redacted-password-hash]", value)
    value = re.sub(r"pbkdf2_sha256\$[A-Za-z0-9$_=-]+", "[redacted-password-hash]", value)
    # Opaque panel cookies and CSRF values are 32-byte urlsafe tokens (43 chars).
    return re.sub(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", "[redacted-token]", value)


def sanitize_trace(source, destination):
    """Preserve a usable trace without login payloads/cookies or filled values."""
    sensitive = {"headers", "cookies", "cookie", "authorization", "csrf", "password", "value", "postdata", "postdatabuffer", "requestbody", "responsebody"}
    def clean(value):
        if isinstance(value, dict):
            return {key: ([] if key.lower() in {"headers", "cookies"} else "[redacted]") if key.lower() in sensitive else clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return redact_text(value) if isinstance(value, str) else value
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as safe:
        for member in original.infolist():
            data = original.read(member)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                safe.writestr(member.filename, data)
                continue  # PNG/font binary; browser screenshots mask secrets.
            lines = []
            for line in text.splitlines():
                try:
                    lines.append(json.dumps(clean(json.loads(line)), ensure_ascii=False))
                except (ValueError, TypeError):
                    lines.append(redact_text(line))
            safe.writestr(member.filename, "\n".join(lines).encode("utf-8"))


@pytest.fixture
def trace_sanitizer():
    return sanitize_trace


def database_url(name, role):
    from sqlalchemy.engine import make_url
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is mandatory for real E2E qualification; no skip/fallback")
    parsed = make_url(value)
    if parsed.host not in LOCAL or parsed.username != role or not (parsed.database or "").endswith("_e2e"):
        pytest.fail(f"{name} must use loopback, {role}, and a dedicated *_e2e database")
    return value, parsed


@pytest.fixture(scope="session")
def qualification_server(request):
    if os.environ.get("ORION_E2E_ENABLED") != "1":
        pytest.fail("Explicit ORION_E2E_ENABLED=1 required; this suite never silently skips")
    runtime, runtime_url = database_url("APP_DATABASE_URL", "app_runtime")
    migration, migration_url = database_url("MIGRATION_DATABASE_URL", "app_migrator")
    support, support_url = database_url("SUPPORT_DATABASE_URL", "app_support")
    if len({(p.host, p.port or 5432, p.database) for p in (runtime_url, migration_url, support_url)}) != 1:
        pytest.fail("All E2E roles must point to the same dedicated PostgreSQL database")
    redis_url = os.environ.get("ORION_E2E_REDIS_URL", "")
    redis_parts = urlsplit(redis_url)
    if redis_parts.scheme != "redis" or redis_parts.hostname not in LOCAL or redis_parts.path != "/14":
        pytest.fail("ORION_E2E_REDIS_URL must be an isolated loopback Redis /14")
    import psycopg
    from redis import Redis
    migration_dsn = migration_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(migration_dsn) as db:
        if db.execute("SELECT to_regclass('public.business_onboarding'),to_regclass('public.qr_channels')").fetchone() != ("business_onboarding", "qr_channels"):
            pytest.fail("Migrate the dedicated E2E database to head before running the browser suite")
    cache = Redis.from_url(redis_url)
    assert cache.ping(), "Real Redis is required"
    cache.close()
    artifacts = Path(os.environ.get("ORION_E2E_ARTIFACTS", ROOT / "test-results" / "e2e")).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    # Do not inherit provider credentials, public deployment URLs or email flags.
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL"}}
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secrets.token_bytes(32), salt, 200_000)
    env.update({
        "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1",
        "APP_DATABASE_URL": runtime, "MIGRATION_DATABASE_URL": migration,
        "SUPPORT_DATABASE_URL": support, "REDIS_URL": redis_url,
        "SUPPORT_API_TOKEN": "synthetic-e2e-only-00000000000000000000000000000000000000000000000000",
        "PANEL_ADMIN_USERNAME": "synthetic-disabled-operator",
        "PANEL_ADMIN_PASSWORD_HASH": "pbkdf2_sha256$200000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode(),
        "PANEL_REGISTRATION_ENABLED": "1", "PANEL_EMAIL_ENABLED": "0", "PANEL_TEST_MODE": "0",
        "PANEL_PUBLIC_URL": origin, "ONBOARDING_ENABLED": "1", "BUSINESS_PROFILES_ENABLED": "1",
        "COMMERCIAL_CATALOG_ENABLED": "1", "COMMERCIAL_ORDERS_ENABLED": "0",
        "COMMERCIAL_FULFILLMENT_ENABLED": "0", "QR_CHANNELS_ENABLED": "0",
        "MARKETING_EXECUTION_ENABLED": "0", "MARKETING_RECONCILIATION_ENABLED": "0",
        "GOOGLE_LIVE_SYNC": "0", "GROQ_PROBE_ON_START": "0", "GROQ_ENABLED": "0",
        "PANEL_LEGACY_ADMIN_IDENTITY_ENABLED": "0",
    })
    native_admin = None
    mode = getattr(request, "param", None)
    if mode is not None:
        if mode != "synthetic_native_admin":
            pytest.fail("Unknown E2E server opt-in; arbitrary environment overrides are forbidden")
        # Only explicitly selected tests receive a known synthetic environment
        # operator. The ordinary qualification fixture remains disabled/random.
        username = "synthetic-native-e2e-" + uuid4().hex[:12]
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, 200_000)
        password_hash = "pbkdf2_sha256$200000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()
        native_admin = SimpleNamespace(username=username, password=PASSWORD,
                                       password_hash=password_hash, tenant_id=str(uuid4()))
        env.update({"PANEL_ADMIN_USERNAME": username, "PANEL_ADMIN_PASSWORD_HASH": password_hash,
                    "PANEL_LEGACY_ADMIN_IDENTITY_ENABLED": "1",
                    "PANEL_OPERATOR_TENANT_ID": native_admin.tenant_id})
    with tempfile.TemporaryDirectory(prefix="orion-e2e-tls-") as private_dir:
        cert, key = Path(private_dir) / "cert.pem", Path(private_dir) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"], check=True, capture_output=True)
        with (artifacts / "api.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                "--port", str(port), "--ssl-keyfile", str(key), "--ssl-certfile", str(cert), "--no-access-log"], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail("Real API exited during startup; inspect E2E api.log")
                    try:
                        with urlopen(origin + "/health", context=ssl._create_unverified_context(), timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, TimeoutError):
                        time.sleep(0.15)
                else:
                    pytest.fail("Real HTTPS API did not become healthy in 45s")
                yield SimpleNamespace(origin=origin, dsn=migration_dsn, artifacts=artifacts,
                                      native_admin=native_admin)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                output.flush()
                text = (artifacts / "api.log").read_text(encoding="utf-8", errors="replace")
                for secret in (runtime_url.password, migration_url.password, support_url.password, env["SUPPORT_API_TOKEN"], env["PANEL_ADMIN_PASSWORD_HASH"]):
                    if secret:
                        text = text.replace(secret, "[redacted-ci-credential]")
                (artifacts / "api.log").write_text(redact_text(text), encoding="utf-8")


@pytest.fixture(scope="session")
def chromium(qualification_server):
    # Missing dependency/browser is a runner failure, never a skip.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


class Journey:
    def __init__(self, browser, server, name):
        self.server, self.name = server, name
        self.context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900})
        self.context.set_default_timeout(15_000)
        self.events, self.errors, self.external = [], [], []
        self.context.route("**/*", self._guard_network)
        self.context.on("page", self._observe)
        self.page = self.context.new_page()
        self.context.tracing.start(screenshots=True, snapshots=True, sources=False)

    def _guard_network(self, route):
        if urlsplit(route.request.url).netloc != urlsplit(self.server.origin).netloc:
            self.external.append(urlsplit(route.request.url).hostname)
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _observe(self, page):
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("response", lambda response: self.events.append({"method": response.request.method,
            "path": urlsplit(response.url).path, "status": response.status}) if "/auth/" in response.url or "/api/" in response.url else None)

    def query(self, sql, params=()):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.server.dsn, row_factory=dict_row) as db:
            return db.execute(sql, params).fetchall()

    def scalar(self, sql, params=()):
        return next(iter(self.query(sql, params)[0].values()))

    def account(self, *, verified=False):
        from argon2 import PasswordHasher
        identifier = uuid4()
        email = f"e2e-{identifier.hex}@example.test"
        self.query("INSERT INTO panel_accounts(id,name,email,password_hash,verified_at) VALUES(%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END) RETURNING id",
                   (identifier, "Pessoa sintética E2E", email, PasswordHasher().hash(PASSWORD), verified))
        return {"id": identifier, "email": email}

    def tenant(self, account, *, role="owner", configured=False, name=None):
        identifier = uuid4()
        label = name or "Negócio sintético " + identifier.hex[:8]
        self.query("INSERT INTO tenants(id,name,slug,timezone) VALUES(%s,%s,%s,'America/Cuiaba') RETURNING id", (identifier, label, "e2e-" + identifier.hex))
        self.query("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(%s,%s,%s) RETURNING id", (identifier, account["id"], role))
        if configured:
            pro, service = uuid4(), uuid4()
            self.query("INSERT INTO professionals(id,tenant_id,name) VALUES(%s,%s,'Profissional legado E2E') RETURNING id", (pro, identifier))
            self.query("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(%s,%s,'Serviço legado E2E',40,3500) RETURNING id", (service, identifier))
            self.query("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(%s,%s,%s) RETURNING id", (identifier, pro, service))
            self.query("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(%s,%s,0,'09:00','18:00') RETURNING id", (identifier, pro))
        return {"id": str(identifier), "name": label}

    def invitation(self, owner, tenant, invitee):
        token = secrets.token_urlsafe(32)
        fingerprint = hashlib.sha256(json.dumps({"email": invitee["email"], "role": "editor"}, sort_keys=True).encode()).hexdigest()
        row = self.query("""INSERT INTO management_team_invitations(tenant_id,email,role,token_digest,idempotency_key,fingerprint,expires_at,created_by)
            VALUES(%s,%s,'editor',%s,%s,%s,now()+interval '1 day',%s) RETURNING id""",
            (tenant["id"], invitee["email"], hashlib.sha256(token.encode()).hexdigest(), str(uuid4()), fingerprint, owner["id"]))[0]
        return {"id": str(row["id"]), "token": token}

    def login(self, account, page=None):
        from playwright.sync_api import expect
        page = page or self.page
        page.goto(self.server.origin + "/panel")
        page.locator("#username").fill(account["email"])
        page.locator("#password").fill(PASSWORD)
        with page.expect_response(lambda r: urlsplit(r.url).path == "/auth/login" and r.request.method == "POST") as result:
            page.locator("#authSubmitLogin").click()
        assert result.value.status == 200
        expect(page.locator("#appView")).to_be_visible()
        return page

    def api(self, path, *, method="GET", data=None, page=None):
        # An adversarial request from the real authenticated browser, not an
        # alternate server/test client. Domain APIs and session/CSRF are real.
        return (page or self.page).evaluate("""async ({path,method,data})=>{
            const me=await fetch('/auth/me',{credentials:'same-origin'}).then(r=>r.json());
            const response=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Orion-CSRF':me.csrf},...(data===null?{}:{body:JSON.stringify(data)})});
            return {status:response.status,data:await response.json()};
        }""", {"path": path, "method": method, "data": data})

    def step(self, name):
        from playwright.sync_api import expect
        button = self.page.locator(f'#orionSetup [data-ob-step="{name}"]')
        expect(button).to_be_enabled()
        button.click()
        expect(button).to_have_attribute("aria-current", "step")

    def submit(self, form, suffix, *, status=200, page=None):
        page = page or self.page
        with page.expect_response(lambda r: urlsplit(r.url).path.endswith(suffix) and r.request.method != "GET") as result:
            page.locator(f'#orionSetup [data-ob-form="{form}"] button[type="submit"]').click()
        assert result.value.status == status, f"{suffix}: HTTP {result.value.status}: {result.value.text()}"
        data = result.value.json()
        if status < 300:
            page.wait_for_function("()=>document.querySelector('#orionSetup')?.getAttribute('aria-busy')!=='true'")
        return data

    def create(self, *, activity="services", name="Negócio criado no navegador E2E"):
        from playwright.sync_api import expect
        form = self.page.locator('#orionSetup [data-ob-form="create"]')
        expect(form).to_be_visible()
        form.locator('[name="name"]').fill(name)
        form.locator('[name="niche"]').select_option("barbearia")
        form.locator('[name="activity"]').select_option(activity)
        form.locator('[name="timezone"]').select_option("America/Cuiaba")
        return self.submit("create", "/onboarding/businesses", status=201)

    def location(self, *, private=False):
        self.step("location")
        form = self.page.locator('#orionSetup [data-ob-form="location"]')
        form.locator('[name="modality"]').select_option("onsite" if private else "online")
        if private:
            for name, value in {"street": "Rua sintética E2E", "neighborhood": "Centro E2E", "city": "Cuiabá", "state": "MT"}.items():
                form.locator(f'[name="{name}"]').fill(value)
            form.locator('[name="no_number"]').check()
        return self.submit("location", "/steps/location")

    def professional(self):
        self.step("professionals")
        self.page.locator('[data-ob-action="add-professional"]').click()
        self.page.locator('#orionSetup [data-ob-form="professional"] [name="name"]').fill("Profissional cadastrado E2E")
        return self.submit("professional", "/steps/professionals")

    def service(self):
        self.step("catalog")
        self.page.locator('[data-ob-action="add-service"]').click()
        form = self.page.locator('#orionSetup [data-ob-form="service"]')
        for name, value in {"name": "Serviço cadastrado E2E", "price": "35,00", "duration_min": "40", "prep_min": "5", "cleanup_min": "10"}.items():
            form.locator(f'[name="{name}"]').fill(value)
        form.locator('[name="professional_ids"]').first.check()
        return self.submit("service", "/steps/catalog")

    def product(self):
        self.step("catalog")
        self.page.locator('[data-ob-action="add-product"]').click()
        form = self.page.locator('#orionSetup [data-ob-form="product"]')
        form.locator('[name="name"]').fill("Produto cadastrado E2E")
        form.locator('[name="variant_name"]').fill("Apresentação real E2E")
        form.locator('[name="price_kind"]').select_option("fixed")
        form.locator('[name="price"]').fill("19,90")
        return self.submit("product", "/steps/catalog")

    def hours(self):
        self.step("hours")
        form = self.page.locator('#orionSetup [data-ob-form="hours"]')
        form.locator('[name="weekdays"][value="0"]').check()
        form.locator('[name="weekdays"][value="2"]').check()
        for name, value in {"start_time": "09:00", "end_time": "18:00"}.items():
            form.locator(f'[name="{name}"]').fill(value)
        form.locator('[name="has_break"]').check()
        form.locator('[name="break_start"]').fill("12:00")
        form.locator('[name="break_end"]').fill("13:00")
        return self.submit("hours", "/steps/hours")

    def complete(self):
        from playwright.sync_api import expect
        self.step("review")
        self.page.locator('#orionSetup [name="acknowledged"]').check()
        state = self.submit("complete", "/onboarding/complete")
        assert state["status"] == "complete"
        expect(self.page.locator('[data-ob-action="open-dashboard"]')).to_be_visible()
        return state

    def no_activation(self, tenant):
        row = self.query("SELECT bot_enabled FROM tenants WHERE id=%s", (tenant,))[0]
        assert row["bot_enabled"] is False
        for table in ("appointments", "authorized_staff", "whatsapp_channels", "qr_channels", "google_connections", "commercial_orders", "automation_jobs"):
            assert self.scalar(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)) == 0, table
        assert self.scalar("SELECT count(*) FROM business_profile_state WHERE tenant_id=%s AND published_version_id IS NOT NULL", (tenant,)) == 0
        assert self.scalar("SELECT count(*) FROM management_site_preferences WHERE tenant_id=%s AND public_enabled", (tenant,)) == 0


@pytest.fixture
def journey(chromium, qualification_server, request):
    h = Journey(chromium, qualification_server, request.node.name)
    try:
        yield h
    finally:
        failed = bool(getattr(request.node, "rep_call", None) and request.node.rep_call.failed)
        folder = qualification_server.artifacts / request.node.name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "http.json").write_text(redact_text(json.dumps({"flow": request.node.name, "http": h.events, "page_errors": h.errors, "external_blocked": h.external}, indent=2)), encoding="utf-8")
        try:
            if failed:
                h.page.screenshot(path=str(folder / "failure.png"), full_page=True,
                                  mask=[h.page.locator('input[type="password"]')])
                with tempfile.TemporaryDirectory(prefix="orion-e2e-private-trace-") as private_trace:
                    raw = Path(private_trace) / "raw.zip"
                    h.context.tracing.stop(path=str(raw))
                    sanitize_trace(raw, folder / "trace.zip")
            else:
                h.context.tracing.stop()
        finally:
            h.context.close()
        assert not h.external, "External browser requests were blocked: " + repr(h.external)
        assert not h.errors, "Real browser page errors: " + repr(h.errors)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        report.longrepr = redact_text(str(report.longrepr))
    setattr(item, "rep_" + report.when, report)
