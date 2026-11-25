"""Recurring Orion offers: synthetic catalog, SQLite and transports; no external sends."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from affiliate_pilot.engine import Engine
from affiliate_pilot.niches import PUBLIC_NICHES
from affiliate_pilot.promotech import PromoTech
from affiliate_pilot.providers import ProviderError, normalize_offer
from affiliate_pilot.store import AutomationConflict, Store, destination_identity


RULE = "1" * 32
WHATSAPP = "2" * 32
TELEGRAM = "3" * 32
GROUP = "4" * 32
TEMPLATE = "5" * 32


@pytest.fixture
def context(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Automation tests must never access a real provider")

    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    clock = [int(datetime(2026, 11, 25, 13, 5, tzinfo=timezone.utc).timestamp())]
    monkeypatch.setattr("affiliate_pilot.engine.time.time", lambda: clock[0])
    store = Store(tmp_path / "automation.sqlite3", Fernet.generate_key().decode())
    company = store.orion_workspace("a" * 32, "PromoTech sintética")["id"]
    shopee_version = store.save_connection(company, "shopee", {"app_id": "1", "key": "synthetic"}, {})
    telegram_version = store.save_connection(company, "telegram", {
        "token": "synthetic", "bot_id": 99, "operator": 100,
    }, {})
    template = store.put(company, "template", TEMPLATE, {
        "name": "Somente texto", "brand": "PromoTech", "layout": "simple", "media": "text",
        "text": "{produto}\n{preco}\n{link}", "style": "plain",
    })
    destinations = [
        store.put(company, "destination", WHATSAPP, {
            "name": "Canal sintético", "mode": "whatsapp_channel", "verified": True, "test": False,
            "address": "https://whatsapp.com/channel/ABCDEFGHIJKLMNOPQRSTUV", "channel_jid": "123456789@newsletter",
            "connection_version": "orion:synthetic-device:1",
        }),
        store.put(company, "destination", TELEGRAM, {
            "name": "Telegram sintético", "mode": "telegram", "verified": True, "test": False,
            "chat_id": -100001, "connection_version": telegram_version,
        }),
        store.put(company, "destination", GROUP, {
            "name": "Grupo sintético", "mode": "whatsapp_group", "verified": True, "test": False,
            "address": "120363000000001@g.us", "connection_version": "orion:synthetic-device:1",
        }),
    ]
    offer = normalize_offer({
        "itemId": 8, "shopId": 11, "productName": "Produto sintético", "priceMin": "59.90", "priceMax": "59.90",
        "shopName": "Loja sintética", "productCatIds": [100017], "imageUrl": "https://down-br.img.susercontent.com/file/synthetic",
    })
    ctx = {"store": store, "company": company, "clock": clock, "template": template,
           "destinations": destinations, "shopee_version": shopee_version, "telegram_version": telegram_version,
           "fresh": offer, "searches": [], "details": [], "sent": [], "verify_calls": [],
           "proof_hook": None, "send_error": None, "verify_error": None}

    class Catalog:
        async def search(self, keyword, limit, category, sort):
            ctx["searches"].append(category)
            return [{**ctx["fresh"], "categories": [category]}]

        async def affiliate_link(self, row, tag):
            return "https://s.shopee.com.br/synthetic"

        async def detail(self, shop, item):
            ctx["details"].append((shop, item))
            return dict(ctx["fresh"])

        async def tracked_affiliate_link(self, row, token):
            return {"affiliate_url": "https://s.shopee.com.br/synthetic", "tracking_token": token,
                    "tracking_status": "verified", "utm_content": token + "----"}

    def before_publish(ident, mode, text):
        current = store.history(company, ident)[0]
        assert current["status"] == "dispatching"
        assert "manual" not in current["doc"]
        assert current["doc"]["offer"]["tracking_token"] == "orionj" + ident
        assert current["doc"]["offer"]["affiliate_url"] in text
        ctx["sent"].append({"job": ident, "mode": mode, "text": text})
        if ctx["send_error"]:
            raise ProviderError(ctx["send_error"], "Resposta sintética sem confirmação")

    def verified(destination):
        ctx["verify_calls"].append(destination["id"])
        if ctx["verify_error"]:
            raise ProviderError(ctx["verify_error"], "Conexão sintética indisponível")
        if ctx["proof_hook"]:
            ctx["proof_hook"]()

    class TelegramTransport:
        async def verify_destination(self, chat_id, bot_id, operator):
            assert bot_id == 99 and operator == 100
            verified(destinations[1])
            return {"chat_id": chat_id, "title": "Telegram sintético"}

        async def publish(self, chat_id, text, media=None, **kwargs):
            assert chat_id == destinations[1]["chat_id"]
            jobs = [row for row in store.history(company) if row["status"] == "dispatching"]
            assert len(jobs) == 1
            before_publish(jobs[0]["id"], "telegram", text)
            return 123

    class WhatsAppTransport:
        def available(self, selected):
            return selected == company

        async def verify(self, selected, destination):
            assert selected == company
            verified(destination)
            return {"version": destination["connection_version"], "address": destination.get("channel_jid") or destination["address"]}

        async def publish(self, ident, selected, destination, text, link, media, thumbnail=None):
            assert selected == company and destination["id"] in {WHATSAPP, GROUP}
            assert link in text
            before_publish(ident, destination["mode"], text)
            return {"job_id": ident, "accepted": True, "state": "sent"}

    legacy = PromoTech({"company": "b" * 32, "destination": "c" * 32, "rule": "d" * 32,
                       "jid": "999999999@newsletter", "version": "e" * 64, "invite": "F" * 22}, "unused", 1)
    engine = Engine(store, sending=False, promotech=legacy, manual_transport=WhatsAppTransport())
    engine.shopee = lambda selected: (Catalog(), store.credentials(selected, "shopee")[1])
    engine.telegram = lambda selected: (TelegramTransport(), {"bot_id": 99, "operator": 100},
                                        store.credentials(selected, "telegram")[1])
    ctx["engine"] = engine
    return ctx


def save_rule(ctx, *, mode="automatic", selected=None, proof=True, **changes):
    destinations = selected or [WHATSAPP, TELEGRAM]
    rule = {"id": RULE, "name": "GeekHunter sintética", "keyword": "", "niches": list(PUBLIC_NICHES),
            "sort": 2, "blocked": [], "template_id": TEMPLATE, "destinations": destinations,
            "days": list(range(7)), "opens": "09:00", "closes": "21:00", "timezone": "America/Cuiaba",
            "interval": 10, "daily": 48, "repeat_hours": 48, "mode": mode, "active": True,
            "next_at": ctx["clock"][0], "revision": uuid4().hex, **changes}
    if proof:
        rule["automation_authorization"] = {"revision": rule["revision"], "authorized_at": ctx["clock"][0],
            "destinations": {ident: destination_identity(ctx["store"].get(ctx["company"], "destination", ident))
                             for ident in destinations}, "shopee_version": ctx["shopee_version"]}
    ctx["store"].put(ctx["company"], "rule", RULE, rule)
    return rule


def plan(ctx, rule):
    asyncio.run(ctx["engine"].plan_rule(ctx["company"], RULE, rule, ctx["clock"][0]))
    return ctx["store"].history(ctx["company"])


def dispatch(ctx):
    job = ctx["store"].claim(telegram=False, promotech=ctx["engine"].promotech.binding)
    assert job is not None
    asyncio.run(ctx["engine"].dispatch_job(job))
    return ctx["store"].history(ctx["company"], job["id"])[0]


def test_authorized_ten_minute_rule_sends_only_selected_whatsapp_and_telegram_with_global_pilot_off(context):
    ctx = context
    rule = save_rule(ctx)
    assert len(rule["niches"]) == 29
    assert ctx["engine"].automation_rule_runtime(ctx["company"], rule)["status"] == "ready"
    jobs = plan(ctx, rule)
    assert len(jobs) == 2 and all(job["status"] == "queued" for job in jobs)
    assert {job["destination"] for job in jobs} == {WHATSAPP, TELEGRAM}
    assert all(job["doc"]["automation"]["revision"] == rule["revision"] and "manual" not in job["doc"] for job in jobs)
    assert dispatch(ctx)["status"] == "published"
    assert dispatch(ctx)["status"] == "published"
    assert {sent["mode"] for sent in ctx["sent"]} == {"whatsapp_channel", "telegram"}
    assert ctx["store"].claim(telegram=False) is None
    decision = ctx["store"].list(ctx["company"], "decision")[0]
    assert decision["rule_id"] == RULE and decision["executed_at"] == ctx["clock"][0]
    assert decision["status"] == "prepared" and decision["jobs_created"] == 2
    assert set(decision["job_ids"]) == {job["id"] for job in jobs}
    assert ctx["store"].get(ctx["company"], "rule", RULE)["next_at"] == ctx["clock"][0] + 600


def test_verified_whatsapp_group_is_an_explicit_recurring_destination(context):
    rule = save_rule(context, selected=[GROUP])
    assert plan(context, rule)[0]["status"] == "queued"
    assert dispatch(context)["status"] == "published"
    assert [sent["mode"] for sent in context["sent"]] == ["whatsapp_group"]


@pytest.mark.parametrize("mode", ["automatic", "review"])
def test_active_rule_without_recurring_proof_is_blocked_and_never_silently_becomes_review(context, mode):
    rule = save_rule(context, mode=mode, proof=False)
    runtime = context["engine"].automation_rule_runtime(context["company"], rule)
    assert runtime["status"] == "blocked" and runtime["can_activate"] is True
    assert plan(context, rule) == []
    assert context["searches"] == [] and context["sent"] == []
    decision = context["store"].list(context["company"], "decision")[0]
    assert decision["status"] == "blocked" and decision["jobs_created"] == 0 and decision["reason"]


def test_review_jobs_wait_for_the_specific_explicit_approval(context):
    rule = save_rule(context, mode="review")
    assert context["engine"].automation_rule_runtime(context["company"], rule)["status"] == "review"
    jobs = plan(context, rule)
    assert all(job["status"] == "approval" for job in jobs)
    assert context["store"].claim(telegram=False) is None
    chosen = next(job for job in jobs if job["destination"] == TELEGRAM)
    assert context["store"].approve_automation(context["company"], chosen["id"])
    assert dispatch(context)["status"] == "published"
    assert context["store"].claim(telegram=False) is None
    assert len(context["sent"]) == 1
    assert context["store"].history(context["company"], next(job["id"] for job in jobs if job["id"] != chosen["id"]))[0]["status"] == "approval"


@pytest.mark.parametrize("change", ["paused", "revision", "removed_destination", "destination_version", "shopee_account"])
def test_changed_authorization_stops_before_transport_and_does_not_unverify_a_destination(context, change):
    ctx = context
    rule = save_rule(ctx, selected=[WHATSAPP])
    plan(ctx, rule)
    job = ctx["store"].claim(telegram=False)
    if change == "destination_version":
        dest = ctx["store"].get(ctx["company"], "destination", WHATSAPP)
        ctx["store"].put(ctx["company"], "destination", WHATSAPP, {**dest, "connection_version": "orion:changed-device:2"})
    elif change == "shopee_account":
        ctx["store"].save_connection(ctx["company"], "shopee", {"app_id": "2", "key": "changed"}, {})
    else:
        changes = {"paused": {"active": False}, "revision": {"revision": uuid4().hex}, "removed_destination": {"destinations": []}}[change]
        ctx["store"].put(ctx["company"], "rule", RULE, {**rule, **changes})
    asyncio.run(ctx["engine"].dispatch_job(job))
    assert ctx["store"].history(ctx["company"], job["id"])[0]["status"] == "paused"
    assert ctx["store"].get(ctx["company"], "destination", WHATSAPP)["verified"] is True
    assert ctx["verify_calls"] == [] and ctx["sent"] == []


def test_rule_paused_during_provider_verification_is_rechecked_at_the_dispatch_boundary(context):
    ctx = context
    rule = save_rule(ctx, selected=[TELEGRAM])
    plan(ctx, rule)
    ctx["proof_hook"] = lambda: ctx["store"].put(ctx["company"], "rule", RULE, {**rule, "active": False})
    assert dispatch(ctx)["status"] == "paused"
    assert ctx["sent"] == []
    assert ctx["store"].get(ctx["company"], "destination", TELEGRAM)["verified"] is True


def test_provider_work_crossing_the_closing_time_never_publishes_outside_the_window(context):
    ctx = context
    ctx["clock"][0] = int(datetime(2026, 11, 26, 0, 59, 50, tzinfo=timezone.utc).timestamp())
    rule = save_rule(ctx, selected=[WHATSAPP])
    plan(ctx, rule)
    ctx["proof_hook"] = lambda: ctx["clock"].__setitem__(0, ctx["clock"][0] + 11)
    assert dispatch(ctx)["status"] == "expired"
    assert ctx["verify_calls"] == [WHATSAPP] and ctx["details"]
    assert ctx["sent"] == []


def test_recurring_proof_cannot_authorize_a_different_company(context):
    ctx = context
    rule = save_rule(ctx, selected=[WHATSAPP])
    proof = ctx["engine"].automation_job_authorization(ctx["company"], rule, ctx["destinations"][0])
    other = ctx["store"].orion_workspace("9" * 32, "Outra empresa sintética")["id"]
    assert ctx["engine"].automation_job_authorization(other, rule, ctx["destinations"][0]) is None
    with pytest.raises(AutomationConflict):
        ctx["store"].enqueue(other, WHATSAPP, ctx["fresh"], ctx["template"], ctx["clock"][0],
                             approval=False, rule=rule, automation=proof)
    assert ctx["store"].history(other) == []


@pytest.mark.parametrize("limit", ["daily", "interval", "window"])
def test_daily_limit_interval_and_window_are_enforced_before_any_transport(context, limit):
    ctx = context
    rule = save_rule(ctx, selected=[WHATSAPP], daily=1 if limit == "daily" else 48)
    plan(ctx, rule)
    if limit == "window":
        ctx["clock"][0] = int(datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc).timestamp())
        # Keep the claimed occurrence alive solely to exercise the independent window guard.
        with ctx["store"].connect(True) as db:
            db.execute("UPDATE job SET expires=?", (ctx["clock"][0] + 1800,))
    else:
        prior, _ = ctx["store"].enqueue(ctx["company"], WHATSAPP,
            {**ctx["fresh"], "identity": "shopee:11:7"}, ctx["template"], ctx["clock"][0] - 60, approval=False)
        with ctx["store"].connect(True) as db:
            db.execute("UPDATE job SET status='published',changed=?,published_at=? WHERE id=?",
                       (ctx["clock"][0] - 60, ctx["clock"][0] - 60, prior))
    result = dispatch(ctx)
    assert result["status"] == ("queued" if limit == "interval" else "expired")
    if limit == "interval":
        assert result["due"] == ctx["clock"][0] + 540
    assert ctx["verify_calls"] == [] and ctx["sent"] == []


def test_unknown_recurring_receipt_is_terminal_and_keeps_the_tracking_snapshot(context):
    ctx = context
    rule = save_rule(ctx, selected=[WHATSAPP])
    plan(ctx, rule)
    ctx["send_error"] = "unknown"
    result = dispatch(ctx)
    assert result["status"] == "unknown"
    assert result["doc"]["offer"]["tracking_token"] == "orionj" + result["id"]
    assert ctx["store"].claim(telegram=False) is None
    assert len(ctx["sent"]) == 1


def test_connection_failure_before_dispatch_retries_without_invalidating_the_destination(context):
    ctx = context
    plan(ctx, save_rule(ctx, selected=[WHATSAPP]))
    ctx["verify_error"] = "connection"
    result = dispatch(ctx)
    assert result["status"] == "queued" and result["due"] == ctx["clock"][0] + 30
    assert ctx["store"].get(ctx["company"], "destination", WHATSAPP)["verified"] is True
    ctx["clock"][0] += 30
    ctx["verify_error"] = None
    assert dispatch(ctx)["status"] == "published"
    assert len(ctx["sent"]) == 1


@pytest.mark.parametrize("change", ["category", "price", "blocked_product", "identity"])
def test_product_revalidation_never_publishes_a_different_or_ineligible_product(context, change):
    ctx = context
    plan(ctx, save_rule(ctx, selected=[WHATSAPP], max_price=100))
    if change == "category":
        ctx["fresh"] = {**ctx["fresh"], "categories": [100535]}
    elif change == "price":
        ctx["fresh"] = {**ctx["fresh"], "price": "199.90", "price_max": "199.90"}
    elif change == "identity":
        ctx["fresh"] = {**ctx["fresh"], "identity": "shopee:11:999", "item": 999}
    else:
        ctx["store"].put(ctx["company"], "product_flag", ctx["fresh"]["identity"], {"blocked": True})
    result = dispatch(ctx)
    assert result["status"] == ("failed" if change == "identity" else "expired")
    assert ctx["sent"] == []


def test_authorization_changed_during_enqueue_records_a_block_instead_of_review(context, monkeypatch):
    ctx = context
    rule = save_rule(ctx, selected=[WHATSAPP])
    original = ctx["store"].enqueue

    def changed(*args, **kwargs):
        ctx["store"].put(ctx["company"], "rule", RULE, {**rule, "active": False})
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx["store"], "enqueue", changed)
    assert plan(ctx, rule) == []
    decision = ctx["store"].list(ctx["company"], "decision")[0]
    assert decision["status"] == "blocked" and decision["jobs_created"] == 0
