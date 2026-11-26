"""Recurring authorization, lifecycle and activity with no external requests."""
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from affiliate_pilot import main
from affiliate_pilot.engine import Engine
from affiliate_pilot.store import AutomationConflict, Store
from affiliate_pilot.providers import ProviderError
from affiliate_pilot.whatsapp_transport import OrionWhatsAppTransport

TENANT = "a" * 32
RULE = "1" * 32
DESTINATIONS = [str(n) * 32 for n in (2, 3, 4)]
BASE = "http://testserver"


@pytest.mark.asyncio
@pytest.mark.parametrize("action,status,code", [("verify", 503, "connection"), ("verify", 403, "permissions"), ("send", 503, "unknown")])
async def test_whatsapp_transport_separates_outage_from_permission_and_uncertain_send(tmp_path, monkeypatch, action, status, code):
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", Fernet.generate_key().decode())
    store = Store(tmp_path / "transport.sqlite3", Fernet.generate_key().decode())
    company = store.orion_workspace(TENANT, "Automação de teste")["id"]
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"detail": "Falha sintética de teste."})

    transport = OrionWhatsAppTransport(store, client_factory=lambda **options:
        httpx.AsyncClient(transport=httpx.MockTransport(respond), **options))
    with pytest.raises(ProviderError) as error:
        await transport.call(company, action, {})
    assert error.value.code == code
    assert len(requests) == 1


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # Use a real Fernet key for the API claim, unrelated to any account.
    key = Fernet.generate_key()
    signer = Fernet(key)
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    monkeypatch.delenv("AFFILIATE_PROMOTECH_BINDING", raising=False)
    monkeypatch.delenv("AFFILIATE_BOOTSTRAP_PASSWORD", raising=False)
    monkeypatch.delenv("AFFILIATE_GROQ_API_KEY", raising=False)
    store = Store(tmp_path / "automations.sqlite3", Fernet.generate_key().decode())
    owner = store.orion_workspace(TENANT, "Automação de teste")
    company = owner["id"]
    shopee_version = store.save_connection(company, "shopee", {"app_id": "1", "key": "synthetic"}, {"status": "connected"})
    telegram_version = store.save_connection(company, "telegram", {"token": "synthetic", "bot_id": 11, "operator": 12}, {"status": "connected"})
    destinations = [
        {"mode": "telegram", "name": "Telegram normal", "chat_id": -100012345, "connection_version": telegram_version},
        {"mode": "whatsapp_channel", "name": "Canal WhatsApp", "address": "https://whatsapp.com/channel/" + "a" * 22,
         "channel_jid": "123456789123@newsletter", "connection_version": "orion:synthetic:device:1"},
        {"mode": "whatsapp_group", "name": "Grupo WhatsApp", "address": "123456789123@g.us", "connection_version": "orion:synthetic:device:1"},
    ]
    for ident, destination in zip(DESTINATIONS, destinations):
        store.put(company, "destination", ident, {**destination, "verified": True, "test": False})
    verified = []

    class WhatsApp:
        def __init__(self, _store):
            self.changed_version = False
            self.during_verify = None

        def available(self, selected_company):
            return bool(store.orion_tenant(selected_company))

        async def control(self, selected_company, path, **params):
            return {"status": "connected", "version": "orion:synthetic:device:1", "available": True}

        async def verify(self, selected_company, destination):
            assert selected_company == company
            verified.append(destination["id"])
            if self.during_verify:
                self.during_verify()
                self.during_verify = None
            return {"version": "orion:changed" if self.changed_version else destination["connection_version"],
                    "address": destination.get("channel_jid") or destination["address"]}

    class Telegram:
        async def verify_destination(self, chat_id, bot_id, operator):
            assert (bot_id, operator) == (11, 12)
            verified.append(DESTINATIONS[0])
            return {"chat_id": chat_id, "title": "Telegram normal"}

    def telegram(engine, selected_company):
        credentials, version = store.credentials(selected_company, "telegram")
        return Telegram(), credentials, version

    async def no_network(*args, **kwargs):
        raise AssertionError("Automation API tests cannot contact providers")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    monkeypatch.setattr(main, "OrionWhatsAppTransport", WhatsApp)
    monkeypatch.setattr(Engine, "telegram", telegram)
    app = main.create_app(store, sending=False, origin=BASE, start_worker=False)
    with TestClient(app) as client:
        def request(method, path, payload=None, tenant=TENANT):
            claim = {"tenant": tenant, "name": "Automação de teste", "method": method, "path": path}
            headers = {"Origin": BASE, "X-Orion-Affiliate-Access": signer.encrypt(json.dumps(claim).encode()).decode()}
            return client.request(method, path, headers=headers, **({"json": payload} if payload is not None else {}))
        yield store, company, app.state.engine, request, verified


def plan(**changes):
    return {"name": "GeekHunter", "keyword": "fone", "niches": [], "destinations": DESTINATIONS,
            "template_id": "default", "days": list(range(7)), "opens": "00:00", "closes": "00:00",
            "timezone": "America/Campo_Grande", "interval": 10, "daily": 48, "repeat_hours": 48,
            "mode": "automatic", **changes}


def activate(workspace, mode="automatic"):
    store, company, engine, request, _ = workspace
    saved = request("PUT", f"/api/rules/{RULE}", plan(mode=mode))
    assert saved.status_code == 200, saved.text
    active = request("POST", f"/api/rules/{RULE}/activate", {"active": True})
    assert active.status_code == 200, active.text
    return store.get(company, "rule", RULE)


def queued(workspace, mode="automatic", destination=DESTINATIONS[0]):
    store, company, engine, _, _ = workspace
    rule = activate(workspace, mode)
    target = store.get(company, "destination", destination)
    authorization = engine.automation_job_authorization(company, rule, target)
    ident, created = store.enqueue(company, destination, {"identity": "shopee:1:2"},
        store.get(company, "template", "default"), int(time.time()), approval=mode == "review",
        rule=rule, automation=authorization)
    assert created
    return ident, rule, authorization


def test_ten_minute_rule_activates_all_verified_orion_destinations_without_pilot_flag(workspace):
    store, company, engine, request, verified = workspace
    rule = activate(workspace)
    assert not engine.sending
    assert set(rule["automation_authorization"]["destinations"]) == set(DESTINATIONS)
    assert set(verified) == set(DESTINATIONS)
    state = request("GET", "/api/state").json()
    result = state["rule"][0]
    assert result["runtime"]["status"] == "ready"
    assert "automation_authorization" not in result
    assert result["activity"]["published_today"] == 0
    assert all(item["eligible"] for item in state["automation_destinations"])
    assert store.history(company) == [], "activation itself must not send or fabricate receipts"
    again = request("POST", f"/api/rules/{RULE}/activate", {"active": True})
    assert again.json()["revision"] == rule["revision"]
    assert again.json()["next_at"] == rule["next_at"]
    assert len(verified) == 3, "an idempotent activation must not create another occurrence"


def test_existing_active_rule_without_recurring_authorization_is_shown_as_blocked(workspace):
    store, company, _, request, _ = workspace
    store.put(company, "rule", RULE, {**plan(), "active": True, "next_at": int(time.time())})
    result = request("GET", "/api/state").json()["rule"][0]
    assert result["active"] and result["runtime"]["status"] == "blocked"
    assert result["runtime"]["can_activate"] and result["runtime"]["reason"]


def test_disconnected_whatsapp_is_reported_without_revoking_saved_administration(workspace):
    store, company, engine, request, _ = workspace
    activate(workspace)

    async def disconnected(*args, **kwargs):
        return {"status": "disconnected", "available": False, "version": "orion:synthetic:device:1"}

    engine.manual_transport.control = disconnected
    state = request("GET", "/api/state").json()
    assert state["rule"][0]["runtime"]["status"] == "blocked"
    assert state["rule"][0]["runtime"]["can_activate"] is False
    assert "Reconecte" in state["rule"][0]["runtime"]["reason"]
    capabilities = {row["id"]: row for row in state["automation_destinations"]}
    assert capabilities[DESTINATIONS[0]]["eligible"]
    assert not capabilities[DESTINATIONS[1]]["eligible"]
    assert store.get(company, "destination", DESTINATIONS[1])["verified"] is True


@pytest.mark.parametrize("problem", ["unverified", "foreign", "duplicate"])
def test_save_rejects_ineligible_or_foreign_destinations(workspace, problem):
    store, company, _, request, _ = workspace
    selection = DESTINATIONS
    if problem == "unverified":
        destination = store.get(company, "destination", DESTINATIONS[0])
        store.put(company, "destination", DESTINATIONS[0], {**destination, "verified": False})
    elif problem == "foreign":
        other = store.orion_workspace("b" * 32, "Outra empresa")["id"]
        foreign = "f" * 32
        store.put(other, "destination", foreign, {"mode": "telegram", "verified": True})
        selection = [foreign]
    else:
        selection = [DESTINATIONS[0]] * 2
    result = request("PUT", f"/api/rules/{RULE}", plan(destinations=selection))
    assert result.status_code in {404, 422}
    assert store.get(company, "rule", RULE) is None


def test_connection_change_during_activation_never_creates_authorization(workspace):
    store, company, engine, request, _ = workspace
    assert request("PUT", f"/api/rules/{RULE}", plan()).is_success
    engine.manual_transport.changed_version = True
    result = request("POST", f"/api/rules/{RULE}/activate", {"active": True})
    assert result.status_code == 409
    rule = store.get(company, "rule", RULE)
    assert not rule["active"] and "automation_authorization" not in rule


def test_credential_rotation_between_remote_proof_and_commit_is_atomic(workspace):
    store, company, engine, request, _ = workspace
    assert request("PUT", f"/api/rules/{RULE}", plan()).is_success
    engine.manual_transport.during_verify = lambda: store.save_connection(
        company, "shopee", {"app_id": "2", "key": "rotated"}, {"status": "connected"})
    result = request("POST", f"/api/rules/{RULE}/activate", {"active": True})
    assert result.status_code == 409
    rule = store.get(company, "rule", RULE)
    assert not rule["active"] and "automation_authorization" not in rule


def test_pause_cancels_pending_and_revokes_dispatch_authorization(workspace):
    store, company, engine, request, _ = workspace
    ident, _, _ = queued(workspace)
    job = store.claim(telegram=False)
    assert job and job["id"] == ident
    paused = request("POST", f"/api/rules/{RULE}/activate", {"active": False})
    assert paused.is_success and not paused.json()["active"]
    assert store.history(company, ident)[0]["status"] == "cancelled"
    _, shopee_version = store.credentials(company, "shopee")
    _, telegram_version = store.credentials(company, "telegram")
    assert not store.dispatch(job, job["doc"], shopee_version, telegram_version, automation=True)


def test_review_waits_for_explicit_approval_even_with_pilot_flag_disabled(workspace):
    store, company, _, request, _ = workspace
    ident, _, _ = queued(workspace, "review")
    assert store.claim(telegram=False) is None
    response = request("POST", f"/api/jobs/{ident}", {"action": "approve"})
    assert response.is_success, response.text
    job = store.claim(telegram=False)
    assert job["doc"]["automation"]["approved_at"] > 0
    _, shopee_version = store.credentials(company, "shopee")
    _, telegram_version = store.credentials(company, "telegram")
    assert store.dispatch(job, job["doc"], shopee_version, telegram_version, automation=True)
    assert store.history(company, ident)[0]["status"] == "dispatching"


def test_changed_destination_cannot_be_enqueued_or_dispatched(workspace):
    store, company, _, _, _ = workspace
    ident, rule, authorization = queued(workspace)
    destination = store.get(company, "destination", DESTINATIONS[0])
    store.put(company, "destination", DESTINATIONS[0], {**destination, "chat_id": -100099999})
    with pytest.raises(AutomationConflict):
        store.enqueue(company, DESTINATIONS[0], {"identity": "shopee:1:3"}, {}, int(time.time()),
                      approval=False, rule=rule, automation=authorization)
    job = store.claim(telegram=False)
    _, shopee_version = store.credentials(company, "shopee")
    _, telegram_version = store.credentials(company, "telegram")
    assert not store.dispatch(job, job["doc"], shopee_version, telegram_version, automation=True)


def test_rule_activity_counts_receipts_separately_from_preparation(workspace):
    store, company, _, request, _ = workspace
    ident, rule, _ = queued(workspace)
    state = request("GET", "/api/state").json()["rule"][0]
    assert state["activity"]["pending"] == 1 and state["activity"]["published_today"] == 0
    job = store.claim(telegram=False)
    _, shopee_version = store.credentials(company, "shopee")
    _, telegram_version = store.credentials(company, "telegram")
    assert store.dispatch(job, job["doc"], shopee_version, telegram_version, automation=True)
    store.finish(job, "published", receipt="synthetic-confirmed-receipt")
    state = request("GET", "/api/state").json()["rule"][0]
    assert state["activity"]["published_today"] == 1 and state["activity"]["pending"] == 0
    assert state["activity"]["last_published_at"] is not None
    assert request("POST", f"/api/rules/{RULE}/activate", {"active": False}).is_success
    assert store.history(company, ident)[0]["status"] == "published"


@pytest.mark.parametrize("daily", [84,144])
def test_schedule_can_cover_a_full_ten_minute_window(workspace,daily):
    store,company,_,request,_=workspace
    saved=request("PUT",f"/api/rules/{RULE}",plan(opens="07:00",closes="21:00",
                                                   timezone="America/Sao_Paulo",daily=daily))
    assert saved.status_code==200,saved.text
    assert store.get(company,"rule",RULE)["daily"]==daily
    assert request("GET","/api/state").json()["automation_schedule_v2"] is True
    assert request("PUT",f"/api/rules/{RULE}",plan(daily=145)).status_code==422
