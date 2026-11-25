"""No external traffic: complete reports, cache races, isolation and publication dates."""
import asyncio
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.affiliate_analytics import analytics_report_valid
from affiliate_pilot import main
from affiliate_pilot.analytics import Analytics, TTL, conversion_pages
from affiliate_pilot.providers import ProviderError
from affiliate_pilot.store import Store, DEFAULT_TEMPLATE


ZONE = "America/Campo_Grande"
NOW = datetime(2026, 11, 25, 10, tzinfo=ZoneInfo(ZONE))
PASSWORD = "synthetic-password-with-more-than-24-characters"


def node(ident=1, *, amount="24.49", commission="0.7347", status="COMPLETED", when=NOW):
    return {"conversionId": ident, "purchaseTime": int(when.timestamp()), "utmContent": "----",
        "totalCommission": commission, "orders": [{"orderId": "synthetic-order-" + str(ident),
        "orderStatus": status, "items": [{"shopId": 11, "itemId": ident, "modelId": 0, "promotionId": "0",
        "itemName": "Produto de teste", "qty": 1, "actualAmount": amount, "itemTotalCommission": commission,
        "itemSellerCommission": "0", "itemShopeeCommissionCapped": commission,
        "displayItemStatus": "COMPLETED", "fraudStatus": "VERIFIED"}]}]}


def page(nodes, *, more=False, cursor=None):
    return {"conversionReport": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "scrollId": cursor}}}


@pytest.fixture
def context(tmp_path):
    key = Fernet.generate_key().decode()
    store = Store(tmp_path / "analytics.sqlite3", key)
    for name in ("first", "second"):
        store.bootstrap(name, PASSWORD, name.title())
    with store.connect() as db:
        companies = dict(db.execute("SELECT username,id FROM company"))
    versions = {name: store.save_connection(ident, "shopee", {"app_id": name, "key": "fixture"}, {"status": "connected"})
                for name, ident in companies.items()}
    now = [NOW.timestamp()]
    calls = []
    responses = {"first": page([node()]), "second": page([])}

    class Client:
        def __init__(self, app_id, secret):
            assert secret == "fixture"
            self.account = app_id

        async def graphql(self, query, variables):
            calls.append((self.account, query))
            value = responses[self.account]
            if isinstance(value, Exception):
                raise value
            return value

    return {"store": store, "companies": companies, "versions": versions, "now": now,
            "calls": calls, "responses": responses, "factory": Client, "key": key}


def service(ctx, **kwargs):
    return Analytics(ctx["store"], client_factory=ctx["factory"], clock=lambda: ctx["now"][0], **kwargs)


async def test_report_uses_real_contract_and_shared_period_snapshot(context):
    analytics = service(context)
    company = context["companies"]["first"]
    for period in ("month", "7d", "30d", "today"):
        report = await analytics.report(company, period=period, timezone=ZONE)
        assert analytics_report_valid(report, period=period)
        assert report["summary"]["orders"] == 1
        assert report["summary"]["sales_cents"] == 2449
        assert report["summary"]["commission_cents"] == 73
        assert report["summary"]["clicks"] is None
        assert report["summary"]["validated_commission_cents"] is None
        assert report["sources"]["sales"]["status"] == "ready"
        assert report["sources"]["sales"]["scope"] == "account"
        assert report["sources"]["publications"]["complete"] is True
        assert "synthetic-order" not in json.dumps(report)
        assert "fixture" not in json.dumps(report)
    assert len(context["calls"]) == 1
    query = context["calls"][0][1]
    assert "orderStatus:ALL" in query and "fraudStatus:ALL" in query
    assert "limit:20" in query and "purchaseTimeEnd:" in query
    await analytics.close()


async def test_tenants_never_share_cache_or_amounts(context):
    analytics = service(context)
    first = await analytics.report(context["companies"]["first"])
    second = await analytics.report(context["companies"]["second"])
    assert first["summary"]["orders"] == 1
    assert second["summary"]["orders"] == 0
    assert second["summary"]["commission_cents"] == 0
    assert [account for account, _ in context["calls"]] == ["first", "second"]
    await analytics.close()


async def test_cancelled_conversion_replaces_previous_snapshot(context):
    analytics = service(context)
    company = context["companies"]["first"]
    assert (await analytics.report(company))["summary"]["orders"] == 1
    context["now"][0] += TTL + 1
    context["responses"]["first"] = page([node(status="CANCELLED")])
    stale = await analytics.report(company)
    assert stale["summary"]["orders"] == 1
    assert stale["sources"]["sales"]["status"] == "syncing"
    await asyncio.gather(*analytics.tasks.values())
    fresh = await analytics.report(company)
    assert fresh["summary"]["orders"] == 0
    assert fresh["summary"]["commission_cents"] == 0
    assert fresh["sources"]["sales"]["status"] == "ready"
    await analytics.close()


async def test_provider_failure_keeps_previous_confirmed_values_and_date(context):
    analytics = service(context)
    company = context["companies"]["first"]
    before = await analytics.report(company)
    context["now"][0] += TTL + 1
    context["responses"]["first"] = ProviderError("network", "fixture outage")
    await analytics.report(company)
    await asyncio.gather(*analytics.tasks.values())
    after = await analytics.report(company)
    assert after["summary"]["commission_cents"] == before["summary"]["commission_cents"]
    assert after["sources"]["sales"]["updated_at"] == before["sources"]["sales"]["updated_at"]
    assert after["sources"]["sales"]["status"] == "error"
    assert "fixture outage" not in json.dumps(after)
    assert len(context["calls"]) == 2  # error backoff is also tenant-scoped
    await analytics.close()


async def test_cold_failure_and_disconnected_account_have_unknown_finance(context):
    analytics = service(context)
    company = context["companies"]["first"]
    context["responses"]["first"] = ProviderError("access", "fixture denial")
    report = await analytics.report(company)
    assert report["summary"]["orders"] is None
    assert report["summary"]["commission_cents"] is None
    assert report["sources"]["sales"]["status"] == "error"
    context["store"].revoke(company, "shopee")
    report = await analytics.report(company)
    assert report["sources"]["sales"]["status"] == "not_connected"
    assert report["summary"]["orders"] is None
    assert len(context["calls"]) == 1
    await analytics.close()


async def test_cold_sync_is_shared_and_cannot_publish_after_credential_rotation(context):
    started, release = asyncio.Event(), asyncio.Event()

    class Slow:
        def __init__(self, *args): pass
        async def graphql(self, *args):
            started.set()
            await release.wait()
            return page([node()])

    context["factory"] = Slow
    analytics = service(context, first_wait=0.01)
    company = context["companies"]["first"]
    cold = await analytics.report(company)
    await started.wait()
    assert cold["sources"]["sales"]["status"] == "syncing"
    assert cold["summary"]["commission_cents"] is None
    again = await analytics.report(company)
    assert len(analytics.tasks) == 1 and again["sources"]["sales"]["status"] == "syncing"
    old = context["versions"]["first"]
    context["store"].save_connection(company, "shopee", {"app_id": "replacement", "key": "fixture"}, {})
    release.set()
    await asyncio.gather(*analytics.tasks.values())
    assert analytics.cached(company, ZONE, old) is None
    await analytics.close()


async def test_rotation_during_wait_discards_former_account_result(context):
    company = context["companies"]["first"]

    class Rotating:
        def __init__(self, *args): pass
        async def graphql(self, *args):
            context["store"].revoke(company, "shopee")
            return page([node()])

    context["factory"] = Rotating
    analytics = service(context)
    report = await analytics.report(company)
    assert report["sources"]["sales"]["status"] == "not_connected"
    assert report["summary"]["commission_cents"] is None
    await analytics.close()


async def test_midnight_rollover_starts_new_window_after_previous_sync_finishes(context):
    release = asyncio.Event()
    calls = []

    class AcrossMidnight:
        def __init__(self, *args): pass
        async def graphql(self, query, _):
            calls.append(query)
            if len(calls) == 1:
                await release.wait()
            return page([])

    context["factory"] = AcrossMidnight
    context["now"][0] = NOW.replace(hour=23, minute=59, second=59).timestamp()
    analytics = service(context, first_wait=0.01)
    company = context["companies"]["first"]
    assert (await analytics.report(company))["sources"]["sales"]["status"] == "syncing"
    context["now"][0] += 2
    analytics.first_wait = 1
    next_day = asyncio.create_task(analytics.report(company))
    await asyncio.sleep(0)
    release.set()
    during = await next_day
    assert during["sources"]["sales"]["status"] == "syncing"
    await asyncio.gather(*analytics.tasks.values())
    report = await analytics.report(company)
    assert report["sources"]["sales"]["status"] == "ready"
    assert report["period"]["end_date"] == "2026-11-26"
    assert len(calls) == 2
    assert re.search(r"purchaseTimeEnd:(\d+)", calls[0])[1] != re.search(r"purchaseTimeEnd:(\d+)", calls[1])[1]
    await analytics.close()


async def test_pagination_consumes_cursor_and_rejects_partial_success():
    calls = []

    class Client:
        async def graphql(self, query, _):
            calls.append(query)
            return page([node(len(calls))], more=len(calls) == 1, cursor='cursor"safe')

    rows = await conversion_pages(Client(), NOW - timedelta(days=1), NOW + timedelta(days=1))
    assert len(rows) == 2 and len(calls) == 2
    assert 'scrollId:"cursor\\"safe"' in calls[1]

    class MissingCursor:
        async def graphql(self, *args): return page([node()], more=True)

    with pytest.raises(ProviderError, match="paginação"):
        await conversion_pages(MissingCursor(), NOW - timedelta(days=1), NOW)

    class Repeated:
        async def graphql(self, *args): return page([node()], more=True, cursor="same")

    with pytest.raises(ProviderError, match="paginação"):
        await conversion_pages(Repeated(), NOW - timedelta(days=1), NOW)


@pytest.mark.parametrize("response", [None, {}, {"conversionReport": None},
    {"conversionReport": {"nodes": [], "pageInfo": {"hasNextPage": "false"}}},
    {"conversionReport": {"nodes": [None], "pageInfo": {"hasNextPage": False}}}])
async def test_invalid_report_envelopes_never_become_empty_success(response):
    class Client:
        async def graphql(self, *args): return response
    with pytest.raises(ProviderError):
        await conversion_pages(Client(), NOW - timedelta(days=1), NOW)


async def test_publication_totals_use_complete_history_and_local_dates(context):
    store, company = context["store"], context["companies"]["first"]
    start = int(NOW.replace(hour=0).timestamp())
    with store.connect(True) as db:
        for i in range(131):
            company_id = context["companies"]["second"] if i == 130 else company
            published = start - 1 if i == 129 else start + 1
            db.execute("""INSERT INTO job(id,company,destination,product,occurrence,due,expires,status,doc,changed,published_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (f"{i:032x}", company_id, "d"*32, f"p{i}", i, start, start+3600,
                "published", json.dumps({"offer": {"title": "Item"}}), start+20, published))
        for i in range(121):
            db.execute("""INSERT INTO job(id,company,destination,product,occurrence,due,expires,status,doc,changed)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (f"{i+200:032x}", company, "d"*32, f"q{i}", i, start, start+3600,
                "queued", "{}", start+30))
    analytics = service(context)
    report = await analytics.report(company, period="today")
    assert report["summary"]["published"] == 129
    assert report["series"][0]["published"] == 129
    assert report["summary"]["pending"] == 121
    assert report["operation"]["sample_size"] == 120
    assert len(report["upcoming"]) == 120
    assert report["destinations"][0]["published"] == 129
    assert report["sources"]["publications"]["scope"] == "full_history"
    await analytics.close()


def test_publication_timestamp_is_persisted_once_and_backfilled(context):
    store, company = context["store"], context["companies"]["first"]
    offer = {"identity": "shopee:11:8", "title": "Item"}
    ident, _ = store.enqueue(company, "d"*32, offer, DEFAULT_TEMPLATE, int(NOW.timestamp()), approval=False)
    with store.connect(True) as db:
        db.execute("UPDATE job SET status='dispatching',attempts=1 WHERE id=?", (ident,))
    job = store.history(company, ident)[0]
    store.finish(job, "published", receipt="synthetic-receipt")
    row = store.history(company, ident)[0]
    assert row["published_at"] == row["changed"]
    original = row["published_at"]
    store.finish(job, "failed")
    assert store.history(company, ident)[0]["published_at"] == original
    with store.connect(True) as db:
        db.execute("UPDATE job SET published_at=NULL WHERE id=?", (ident,))
    reopened = Store(store.path, context["key"])
    assert reopened.history(company, ident)[0]["published_at"] == original


def test_attribution_only_accepts_current_tenant_and_observed_native_tags(context):
    store = context["store"]
    expected = None
    for i, name in enumerate(("first", "first", "second")):
        company = context["companies"][name]
        ident = f"{i+1:032x}"
        offer = {"tracking_token": "orionj"+ident, "tracking_status": "verified", "utm_content": "native"+str(i),
                 "link_version": context["versions"][name] if i != 1 else "old-version"}
        with store.connect(True) as db:
            db.execute("""INSERT INTO job(id,company,destination,product,occurrence,due,expires,status,doc,changed)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (ident, company, "d"*32, "product", i, 1, 2, "published", json.dumps({"offer": offer}), 1))
        if i == 0: expected = ident
    analytics = service(context)
    mapping = analytics.attribution(context["companies"]["first"], context["versions"]["first"], [])
    assert list(mapping) == ["native0"]
    assert mapping["native0"]["job_id"] == expected


def test_api_requires_current_identity_and_validates_period_and_timezone(context, monkeypatch):
    monkeypatch.setattr(main, "Analytics", lambda store: service(context))
    with TestClient(main.create_app(context["store"], origin="http://testserver", start_worker=False)) as client:
        assert client.get("/api/analytics").status_code == 401
        assert context["calls"] == []
        assert client.post("/api/login", headers={"Origin": "http://testserver"},
                           json={"username": "first", "password": PASSWORD}).status_code == 200
        for query in ("?period=all", "?timezone=Invalid/Timezone", "?timezone=" + "x"*65):
            assert client.get("/api/analytics"+query).status_code == 422
        response = client.get("/api/analytics?period=7d&company="+context["companies"]["second"])
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["summary"]["orders"] == 1
        assert len(context["calls"]) == 1
