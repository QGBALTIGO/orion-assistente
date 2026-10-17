"""Profile facts, preview isolation and confirmation invalidation at runtime boundaries."""
import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app import business_profile_api, business_profile_service, catalog_api, groq_api, qr_ai, qr_profile
from app.business_profile_schemas import BusinessProfile, StyleRules
from app.catalog_api import Actor, authenticate
from app.groq_ai import Intention


class EmptySession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def execute(self, *args, **kwargs):
        raise AssertionError("This boundary cannot execute a database mutation")


@pytest.mark.asyncio
async def test_disabled_profiles_preserve_legacy_runtime_and_reject_explicit_overrides_without_db(monkeypatch):
    monkeypatch.delenv("BUSINESS_PROFILES_ENABLED", raising=False)
    def forbidden():
        raise AssertionError("A disabled feature cannot access new tables")
    monkeypatch.setattr(qr_profile, "get_session_factory", forbidden)
    tenant = uuid4()
    assert await qr_profile.runtime_profile(tenant) is None
    assert await qr_profile.resolve_preview(tenant) is None
    for version, style in ((uuid4(), None), (None, StyleRules(style="direct"))):
        with pytest.raises(HTTPException) as error:
            await qr_profile.resolve_preview(tenant, version, style)
        assert error.value.status_code == 423


@pytest.fixture
def profiles(monkeypatch):
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    tenant, other, published_id, draft_id = (uuid4() for _ in range(4))
    published = BusinessProfile(business_name="Empresa publicada", address="Rua publicada, 10",
                                capabilities=["catalog_answers", "appointments"])
    draft = BusinessProfile(business_name="Empresa em rascunho", address="Rua rascunho, 20",
                            capabilities=["catalog_answers"])
    calls = []
    monkeypatch.setattr(qr_profile, "get_session_factory", lambda: EmptySession)
    async def load_published(db, t):
        calls.append(("published", t))
        assert t in {tenant, other}
        return {"id": published_id, "revision": 2, "profile": published} if t == tenant else None
    async def load_version(db, t, v):
        calls.append(("version", t, v))
        if t != tenant or v != draft_id:
            raise HTTPException(404, "Versão não encontrada neste estabelecimento")
        return {"id": draft_id, "revision": 3, "profile": draft.model_dump(mode="json")}
    monkeypatch.setattr(qr_profile, "published_profile_snapshot", load_published)
    monkeypatch.setattr(qr_profile, "profile_version", load_version)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_runtime_uses_published_only_and_explicit_preview_never_publishes_draft(profiles):
    h = profiles
    runtime = await qr_profile.runtime_profile(h.tenant)
    assert runtime["id"] == h.published_id and runtime["profile"] is h.published
    assert await qr_profile.runtime_profile(h.other) is None
    preview = await qr_profile.resolve_preview(h.tenant, h.draft_id, StyleRules(style="direct"))
    assert preview["id"] == h.draft_id and preview["profile"].address == "Rua rascunho, 20"
    assert preview["profile"].style_rules.style == "direct"
    assert h.draft.style_rules.style == h.published.style_rules.style == "warm"
    assert (await qr_profile.runtime_profile(h.tenant))["id"] == h.published_id
    assert all(call[0] in {"published", "version"} for call in h.calls)
    with pytest.raises(HTTPException) as foreign:
        await qr_profile.resolve_preview(h.other, h.draft_id)
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_context_override_is_tenant_bound_and_reset_restores_published_facts(profiles):
    h = profiles
    override = await qr_profile.resolve_preview(h.tenant, h.draft_id)
    token = qr_profile.preview_snapshot.set(override)
    try:
        assert (await qr_profile.runtime_profile(h.tenant))["id"] == h.draft_id
        with pytest.raises(HTTPException) as foreign:
            await qr_profile.runtime_profile(h.other)
        assert foreign.value.status_code == 403
    finally:
        qr_profile.preview_snapshot.reset(token)
    assert qr_profile.preview_snapshot.get() is None
    assert (await qr_profile.runtime_profile(h.tenant))["id"] == h.published_id


@pytest.mark.asyncio
async def test_concurrent_text_routes_keep_context_per_request_and_restore_even_on_error(profiles, monkeypatch):
    h = profiles
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "1")
    app = FastAPI()
    app.include_router(qr_ai.router)
    account = uuid4()
    app.dependency_overrides[authenticate] = lambda: Actor("support", account_id=account)
    gate = asyncio.Event()
    observed = []
    async def run(t, phone, body, namespace, **kwargs):
        before = await qr_profile.runtime_profile(t)
        observed.append((body, before["id"], namespace))
        if len(observed) == 2:
            gate.set()
        await gate.wait()
        after = await qr_profile.runtime_profile(t)
        assert after["id"] == before["id"]
        if body == "erro":
            raise HTTPException(503, "Falha simulada")
        return {"answer": before["profile"].business_name, "step": "information"}
    monkeypatch.setattr(qr_ai, "run_locked", run)
    path = f"/api/v1/tenants/{h.tenant}/ai/conversation-preview"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        current, draft = await asyncio.gather(
            client.post(path, json={"text": "atual", "session": "session-current-01", "request_id": str(uuid4())}),
            client.post(path, json={"text": "erro", "session": "session-draft-001", "profile_version": str(h.draft_id)}),
        )
    assert current.status_code == 200 and current.json()["answer"] == "Empresa publicada"
    assert draft.status_code == 503
    assert {(body, v) for body, v, _ in observed} == {("atual", h.published_id), ("erro", h.draft_id)}
    assert all(namespace.startswith(f"preview:{account}:") for _, _, namespace in observed)
    assert qr_profile.preview_snapshot.get() is None


@pytest.mark.asyncio
async def test_published_reader_has_no_draft_fallback_and_version_lookup_is_scoped(monkeypatch):
    tenant, version = uuid4(), uuid4()
    calls = []
    @asynccontextmanager
    async def transaction(db, t):
        assert t == tenant
        yield
    monkeypatch.setattr(business_profile_service, "tenant_transaction", transaction)
    class Session(EmptySession):
        async def execute(self, statement, params):
            calls.append((str(statement), params))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
    assert await business_profile_service.published_profile_snapshot(Session(), tenant) is None
    assert "v.id=s.published_version_id" in calls[0][0]
    assert "draft_version_id" not in calls[0][0]
    with pytest.raises(HTTPException) as missing:
        await business_profile_service.profile_version(Session(), tenant, version)
    assert missing.value.status_code == 404
    assert calls[1][1] == {"tenant": tenant, "version": version}
    assert "tenant_id=:tenant AND id=:version" in calls[1][0]


def test_revision_tracks_facts_and_permissions_without_inventing_or_mutating_catalog():
    business = {"name": "Salão", "timezone": "America/Cuiaba", "profile_id": str(uuid4()),
                "profile": BusinessProfile(capabilities=["catalog_answers", "appointments"]).model_dump(mode="json")}
    catalog = [{"id": uuid4(), "name": "Corte", "price_cents": 3500, "duration_min": 30}]
    professionals = [{"id": uuid4(), "name": "Bruno"}]
    original = copy.deepcopy((business, catalog, professionals))
    revision = qr_profile.catalog_revision(business, catalog, professionals)
    assert revision == qr_profile.catalog_revision(*copy.deepcopy(original))
    changes = [lambda b, c, p: c[0].update(price_cents=4000), lambda b, c, p: c[0].update(duration_min=50),
               lambda b, c, p: p[0].update(name="Carlos"), lambda b, c, p: b.update(profile_id=str(uuid4())),
               lambda b, c, p: b["profile"].update(capabilities=[])]
    for change in changes:
        values = copy.deepcopy(original)
        change(*values)
        assert qr_profile.catalog_revision(*values) != revision
    assert (business, catalog, professionals) == original


class StateRedis:
    def __init__(self):
        self.values, self.ttls = {}, {}
    async def get(self, key):
        return self.values.get(key)
    async def set(self, key, value, ex=None):
        self.values[key], self.ttls[key] = value, ex
    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def engine(monkeypatch):
    tenant, cut, pro, hold = (uuid4() for _ in range(4))
    redis, namespace, writes = StateRedis(), "runtime-test-001", []
    key = f"orion:qr:ai:{tenant}:{namespace}"
    business = {"name": "Salão", "timezone": "America/Cuiaba", "profile_id": str(uuid4()),
                "profile": BusinessProfile(capabilities=["catalog_answers", "appointments", "rescheduling", "human_handoff_request"]).model_dump(mode="json")}
    catalog = [{"id": cut, "name": "Corte", "price_cents": 3500, "duration_min": 30}]
    pros = [{"id": pro, "name": "Bruno"}]
    async def data(t):
        assert t == tenant
        return business, catalog, pros
    async def confirm(db, t, h):
        writes.append(("confirm", t, h))
        return {"start_at": datetime.now(ZoneInfo("America/Cuiaba")) + timedelta(days=1),
                "total_cents": 3500, "protocol": "LOCAL-ONLY"}
    async def forbidden(*a, **k):
        raise AssertionError("No reservation or customer write is authorized by this information request")
    async def classify(**kwargs):
        return Intention(intent="perguntar", service_ids=[cut])
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: EmptySession)
    monkeypatch.setattr(qr_ai, "confirm_hold", confirm)
    monkeypatch.setattr(qr_ai, "interpret", classify)
    for name in ("book_appointment", "reschedule_appointment", "remember_name"):
        monkeypatch.setattr(qr_ai, name, forbidden)
    def seed(step="confirm", *, legacy=False):
        redis.values[key] = json.dumps({"step": step, "hold": str(hold), "services": [str(cut)],
            "professional": str(pro), "customer_name": "Cliente", "confirmation_summary": "Resumo: Corte, R$ 35,00.",
            "confirmation_expires_at": datetime.now().timestamp() + 240})
        if not legacy:
            redis.values[key + ":catalog-context"] = qr_profile.catalog_revision(business, catalog, pros)
        else:
            redis.values.pop(key + ":catalog-context", None)
    async def turn(body="SIM", preview=False):
        return await qr_ai.turn(tenant, "5567999999999", body, redis, namespace, preview)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["price", "duration", "profile", "capability"])
async def test_changed_catalog_or_published_profile_cannot_confirm_an_old_summary(engine, change):
    h = engine
    h.seed()
    if change == "price":
        h.catalog[0]["price_cents"] = 4500
    elif change == "duration":
        h.catalog[0]["duration_min"] = 60
    elif change == "profile":
        h.business["profile_id"] = str(uuid4())
    else:
        h.business["profile"]["capabilities"] = ["catalog_answers"]
    result = await h.turn()
    assert result["step"] == "context_changed" and not h.writes
    assert h.key not in h.redis.values


@pytest.mark.asyncio
async def test_legacy_confirmation_without_revision_is_not_trusted_but_human_pause_survives(engine):
    h = engine
    h.seed(legacy=True)
    result = await h.turn()
    assert result["step"] == "context_changed" and not h.writes
    h.seed("human", legacy=True)
    result = await h.turn("Oi")
    assert result == {"answer": None, "step": "human"}
    assert json.loads(h.redis.values[h.key])["step"] == "human"


@pytest.mark.asyncio
async def test_published_capability_denial_blocks_confirmation_and_rescheduling(engine):
    h = engine
    h.business["profile"]["capabilities"] = ["catalog_answers"]
    h.seed()
    result = await h.turn()
    assert result["step"] == "capability_unavailable" and not h.writes
    result = await h.turn("Quero remarcar meu horário")
    assert result["step"] == "capability_unavailable" and not h.writes
    assert "R$ 35" in (await h.turn("Quanto custa o Corte?"))["answer"]


def test_only_published_facts_answer_faq_address_or_policy_without_fabricated_values():
    profile = BusinessProfile(address="Rua cadastrada, 10", contact_phone="+5567999999999",
        faqs=[{"question": "Tem estacionamento?", "answer": "Não temos estacionamento próprio."}],
        policies=[{"topic": "retirada", "description": "Consulte a equipe antes de retirar."}]).model_dump(mode="json")
    assert qr_profile.factual_answer(profile, "Onde fica?") == "Rua cadastrada, 10"
    assert qr_profile.factual_answer(profile, "Tem estacionamento?") == "Não temos estacionamento próprio."
    assert qr_profile.factual_answer(profile, "Qual a política de retirada?") == "Consulte a equipe antes de retirar."
    assert qr_profile.factual_answer(profile, "Tem desconto de 50%?") is None
    profile["faqs"].append({"question": "Tem estacionamento?", "answer": "Resposta conflitante"})
    assert qr_profile.factual_answer(profile, "Tem estacionamento?") is None


@pytest.mark.asyncio
async def test_feature_guard_rejects_profile_routes_before_any_database_access(monkeypatch):
    monkeypatch.delenv("BUSINESS_PROFILES_ENABLED", raising=False)
    def forbidden():
        raise AssertionError("Feature-off routes must not require migrated tables")
    monkeypatch.setattr(business_profile_api, "get_session_factory", forbidden)
    monkeypatch.setattr(business_profile_api, "support_session_factory", forbidden)
    app = FastAPI()
    app.include_router(business_profile_api.router)
    app.dependency_overrides[authenticate] = lambda: Actor("support")
    tenant, version = uuid4(), uuid4()
    base = f"/api/v1/tenants/{tenant}/business/profile"
    routes = [("GET", "/api/v1/account/businesses", None), ("GET", base, None),
              ("GET", base + "/history", None), ("GET", base + "/versions/" + str(version), None),
              ("PUT", base + "/draft", {"expected_generation": 0, "profile": {}}),
              ("POST", base + "/publish", {"expected_generation": 0, "version_id": str(version)})]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, path, payload in routes:
            result = await client.request(method, path, json=payload)
            assert result.status_code == 423, (path, result.text)


def test_main_registers_real_preview_audio_and_business_routes():
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert {"/api/v1/tenants/{tenant_id}/ai/conversation-preview",
            "/api/v1/tenants/{tenant_id}/ai/conversation-preview/audio",
            "/api/v1/tenants/{tenant_id}/business/profile"} <= paths


@pytest.mark.asyncio
async def test_tenant_data_keeps_real_scheduling_timezone_and_catalog_when_profile_is_overridden(profiles, monkeypatch):
    h = profiles
    service, professional = uuid4(), uuid4()
    catalog = [{"id": service, "name": "Corte real", "price_cents": 3500, "duration_min": 30}]
    pros = [{"id": professional, "name": "Bruno real"}]
    queries = []
    @asynccontextmanager
    async def transaction(db, t):
        assert t == h.tenant
        yield
    class Session(EmptySession):
        async def execute(self, statement, params):
            assert params == {"t": h.tenant}
            sql = str(statement)
            queries.append(sql)
            assert sql.lstrip().startswith("SELECT")
            if "FROM tenants" in sql:
                value = {"name": "Nome original", "timezone": "America/Campo_Grande"}
                return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: value))
            rows = catalog if "FROM services" in sql else pros
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: Session)
    monkeypatch.setattr(qr_ai, "tenant_transaction", transaction)
    h.draft.timezone = "America/Sao_Paulo"
    override = await qr_profile.resolve_preview(h.tenant, h.draft_id)
    token = qr_profile.preview_snapshot.set(override)
    try:
        business, services, professionals = await qr_ai.tenant_data(h.tenant)
    finally:
        qr_profile.preview_snapshot.reset(token)
    assert business["name"] == "Empresa em rascunho"
    assert business["timezone"] == "America/Campo_Grande"
    assert business["profile"]["timezone"] == "America/Sao_Paulo"
    assert services == catalog and professionals == pros and len(queries) == 3


@pytest.mark.asyncio
async def test_status_authorizes_member_before_returning_truthful_feature_flags(monkeypatch):
    tenant, account = uuid4(), uuid4()
    actor = Actor("member", account_id=account)
    queries = []
    class Session(EmptySession):
        async def execute(self, statement, params):
            queries.append(params)
            assert params["account"] == account
            role = "viewer" if params["tenant"] == tenant else None
            return SimpleNamespace(scalar_one_or_none=lambda: role)
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: Session)
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("GROQ_PREVIEW_ENABLED", "1")
    monkeypatch.setenv("QR_AI_AUDIO_ENABLED", "0")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic")
    app = FastAPI()
    app.include_router(groq_api.router)
    app.dependency_overrides[authenticate] = lambda: actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get(f"/api/v1/tenants/{tenant}/ai/status")
        denied = await client.get(f"/api/v1/tenants/{uuid4()}/ai/status")
    assert result.status_code == 200 and denied.status_code == 403
    assert result.json()["configured"] and result.json()["preview_enabled"]
    assert result.json()["runtime_connected"] and not result.json()["audio_preview_enabled"]
    assert len(queries) == 2
