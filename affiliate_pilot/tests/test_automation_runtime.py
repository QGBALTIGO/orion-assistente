"""Recurring Orion offers: synthetic catalog, SQLite and transports; no external sends."""
import asyncio
import io
import re
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from PIL import Image

from affiliate_pilot.engine import Engine
from affiliate_pilot.niches import PUBLIC_NICHES
from affiliate_pilot.promotech import PromoTech
from affiliate_pilot.providers import ProviderError, Shopee, normalize_offer
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


def projected_catalog(ctx, *, fallback=False, count=1, detail_changes=None):
    """Return only requested GraphQL fields, unlike a fully populated catalog mock."""
    provider = Shopee("synthetic", "unused")
    queries, details = [], []
    photo = "https://down-br.img.susercontent.com/file/synthetic"

    def product(item):
        return {"itemId": item, "shopId": 11, "productName": "Halter sintético " + str(item),
                "priceMin": "59.90", "priceMax": "59.90", "ratingStar": "4.8", "commissionRate": "0.1",
                "shopName": "Loja sintética", "productCatIds": [100637], "sales": 150,
                "priceDiscountRate": 10, "imageUrl": photo}

    async def graphql(query, variables):
        queries.append(query)
        if query.startswith("mutation Short"):
            return {"generateShortLink": {"shortLink": "https://s.shopee.com.br/synthetic"}}
        fields = re.search(r"nodes\{([^}]+)\}", query)[1].split()
        if query.startswith("query Offers"):
            if fallback and "imageUrl" in fields:
                raise ProviderError("schema", "Consulta ampla indisponível no catálogo sintético")
            nodes = [product(8 + offset) for offset in range(count)]
        else:
            assert query.startswith("query Detail")
            item = int(re.search(r"itemId:(\d+)", query)[1])
            details.append(item)
            node = product(item)
            if detail_changes:
                node.update(detail_changes(item))
            nodes = [node]
        return {"productOfferV2": {"nodes": [{field: node[field] for field in fields if field in node} for node in nodes]}}

    provider.graphql = graphql
    ctx["engine"].shopee = lambda company: (provider, ctx["store"].credentials(company, "shopee")[1])
    ctx["store"].put(ctx["company"], "template", TEMPLATE, {**ctx["template"], "media": "card"})
    return queries, details, photo


@pytest.mark.parametrize("fallback", [False, True])
def test_real_graphql_projection_prepares_cards_and_enriches_only_missing_search_images(context, fallback):
    ctx = context
    queries, details, photo = projected_catalog(ctx, fallback=fallback)
    jobs = plan(ctx, save_rule(ctx, niches=["esportes"]))
    assert "imageUrl" in queries[0]
    assert details == ([8] if fallback else [])
    assert len(jobs) == 2 and all(job["status"] == "queued" for job in jobs)
    assert all(job["doc"]["offer"]["image"] == photo and job["product"] == "shopee:11:8" for job in jobs)
    assert ctx["store"].list(ctx["company"], "decision")[0]["status"] == "prepared"


@pytest.mark.parametrize("changes", [
    {"imageUrl": None}, {"itemId": 999}, {"productCatIds": [100535]}, {"priceMin": "199.90", "priceMax": "199.90"},
])
def test_card_enrichment_skips_missing_images_wrong_identity_category_or_ineligible_price(context, changes):
    ctx = context
    _, details, photo = projected_catalog(ctx, fallback=True, count=2,
        detail_changes=lambda item: changes if item == 8 else {})
    jobs = plan(ctx, save_rule(ctx, niches=["esportes"], max_price=100))
    assert details == [8, 9]
    assert len(jobs) == 2
    assert all(job["product"] == "shopee:11:9" and job["doc"]["offer"]["image"] == photo for job in jobs)


def test_card_enrichment_is_bounded_and_reports_missing_images_without_queueing_placeholders(context):
    ctx = context
    _, details, _ = projected_catalog(ctx, fallback=True, count=8, detail_changes=lambda item: {"imageUrl": None})
    assert plan(ctx, save_rule(ctx, niches=["esportes"])) == []
    assert details == [8, 9, 10, 11]
    decision = ctx["store"].list(ctx["company"], "decision")[0]
    assert decision["status"] == "empty" and decision["jobs_created"] == 0
    assert "imagem" in decision["reason"]


@pytest.mark.parametrize("photo_result", ["valid", "invalid", "error", "timeout"])
def test_recurring_telegram_card_requires_downloaded_photo_and_never_sends_placeholder(context, monkeypatch, photo_result):
    ctx = context
    ctx["store"].put(ctx["company"], "template", TEMPLATE, {**ctx["template"], "media": "card"})
    downloaded, media_sent = [], []
    photo = io.BytesIO()
    Image.new("RGB", (80, 80), "#cc0000").save(photo, "PNG")

    async def download(url):
        downloaded.append(url)
        if photo_result == "error":
            raise ProviderError("image", "A foto não está disponível.")
        if photo_result == "timeout":
            raise TimeoutError
        return photo.getvalue() if photo_result == "valid" else b"not an image"

    monkeypatch.setattr("affiliate_pilot.engine.product_image", download)
    transport, account, version = ctx["engine"].telegram(ctx["company"])
    publish = transport.publish

    async def capture(chat_id, text, media=None, **kwargs):
        media_sent.append(media)
        return await publish(chat_id, text, media, **kwargs)

    transport.publish = capture
    ctx["engine"].telegram = lambda company: (transport, account, version)
    assert len(plan(ctx, save_rule(ctx, selected=[TELEGRAM], niches=["moda"]))) == 1
    result = dispatch(ctx)
    assert downloaded == [ctx["fresh"]["image"]]
    if photo_result == "valid":
        assert result["status"] == "published" and len(ctx["sent"]) == len(media_sent) == 1
        with Image.open(io.BytesIO(media_sent[0])) as card:
            assert card.size == (900, 970) and card.getpixel((450, 396)) == (204, 0, 0)
    else:
        assert result["status"] == ("queued" if photo_result == "timeout" else "failed")
        assert not ctx["sent"] and not media_sent
        assert "foto" in result["error"].lower()


def test_late_rule_keeps_its_original_ten_minute_grid(context):
    ctx=context
    original=ctx["clock"][0]
    rule=save_rule(ctx, selected=[GROUP])
    ctx["clock"][0]+=95
    jobs=plan(ctx,rule)
    assert len(jobs)==1 and jobs[0]["occurrence"]==original
    assert ctx["store"].get(ctx["company"],"rule",RULE)["next_at"]==original+600


def test_no_burst_of_missed_occurrences_after_outage(context):
    ctx=context
    original=ctx["clock"][0]
    rule=save_rule(ctx,selected=[GROUP])
    ctx["clock"][0]+=4*600+20
    assert plan(ctx,rule)==[]
    assert ctx["store"].get(ctx["company"],"rule",RULE)["next_at"]==original+5*600
    assert ctx["store"].list(ctx["company"],"decision")[0]["status"]=="missed"


def test_repeated_top_products_do_not_hide_a_new_candidate(context):
    ctx=context
    rule=save_rule(ctx,selected=[GROUP],niches=["esportes"])
    provider,version=ctx["engine"].shopee(ctx["company"])
    items=[{**ctx["fresh"],"item":i,"identity":f"shopee:11:{i}","categories":[100017]}
           for i in range(8,15)]
    prepared=[]
    async def search(*args,**kwargs):
        return items
    async def ready(company,row):
        prepared.append(row["identity"])
        return {**row,"affiliate_url":"https://s.shopee.com.br/synthetic","link_version":version}
    provider.search=search
    ctx["engine"].shopee=lambda company:(provider,version)
    ctx["engine"].ready_offer=ready
    for item in items[:6]:
        job,_=ctx["store"].enqueue(ctx["company"],GROUP,item,ctx["template"],
                                    ctx["clock"][0]-600,approval=False)
        with ctx["store"].connect(True) as db:
            db.execute("UPDATE job SET status='published',published_at=? WHERE id=?",
                       (ctx["clock"][0]-600,job))
    # This test only controls the category search result; all eligibility,
    # destination proof, repeat-window and enqueue logic remain real.
    from unittest.mock import patch
    with patch("affiliate_pilot.engine.search_niche",side_effect=search):
        jobs=plan(ctx,rule)
    queued=[j for j in jobs if j["status"]=="queued"]
    assert len(queued)==1 and queued[0]["product"]==items[-1]["identity"]
    assert prepared==[items[-1]["identity"]]


@pytest.mark.asyncio
async def test_thirty_due_rules_do_not_block_the_independent_publication_lane():
    """Synthetic workload: no provider requests or actual messages."""
    remaining={str(i):{} for i in range(30)}
    started=asyncio.Event()
    release=asyncio.Event()
    sent=asyncio.Event()
    completed=asyncio.Event()
    planned=[]
    active=0
    peak=0

    class Queue:
        job={"id":"synthetic","company":"synthetic"}
        def due_rules(self,now,*,limit=100):
            return [("synthetic",key,rule) for key,rule in list(remaining.items())[:limit]]
        def claim(self,**kwargs):
            job,self.job=self.job,None
            return job

    engine=Engine(Queue(),sending=False)
    async def plan_one(company,key,rule,now):
        nonlocal active,peak
        remaining.pop(key,None)
        active+=1
        peak=max(peak,active)
        if peak==3:
            started.set()
        try:
            await release.wait()
            planned.append(key)
            if len(planned)==30:
                completed.set()
        finally:
            active-=1

    async def dispatch_one(job):
        sent.set()

    engine.plan_rule=plan_one
    engine.dispatch_job=dispatch_one
    task=asyncio.create_task(engine.run())
    try:
        await asyncio.wait_for(started.wait(),2)
        await asyncio.wait_for(sent.wait(),2)
        assert planned==[] and active==3
        release.set()
        await asyncio.wait_for(completed.wait(),3)
        assert len(set(planned))==30 and peak==3
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_shopee_rate_limit_shared_by_account_and_respected(monkeypatch):
    from affiliate_pilot.providers import Shopee,ProviderError
    requests=[]
    class Reply:
        async def __call__(self,query,variables):
            requests.append(query)
            raise ProviderError("rate","synthetic rate limit",retry_after=120)
    a=Shopee("shared-synthetic","unused")
    b=Shopee("shared-synthetic","unused")
    monkeypatch.setattr(a,"_graphql",Reply())
    monkeypatch.setattr(b,"_graphql",Reply())
    for client in (a,b):
        with pytest.raises(ProviderError) as caught:
            await client.graphql("synthetic",{})
        assert caught.value.code=="rate" and caught.value.retry_after>=119
    assert len(requests)==1
