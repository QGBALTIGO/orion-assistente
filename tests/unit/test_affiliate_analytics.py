"""The fallback reports measured operations and leaves unmeasured outcomes null."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.affiliate_analytics import analytics_report_valid, build_analytics_fallback, state_snapshot_valid


NOW = datetime(2026, 11, 25, 14, 30, tzinfo=timezone.utc)
FINANCE_METRICS = (
    "sales_cents", "commission_cents", "validated_commission_cents",
    "orders", "items", "clicks", "conversion_rate",
)


def job(identifier, status, *, destination="channel-a", changed=100, due=200):
    return {"id": identifier, "status": status, "destination": destination,
            "changed": changed, "due": due, "doc": {"offer": {"title": "Oferta " + identifier}}}


@pytest.mark.parametrize("period,instant,start,end,count", [
    ("today", "2027-01-01T03:59:59+00:00", "2026-12-31", "2026-12-31", 1),
    ("today", "2027-01-01T04:00:00+00:00", "2027-01-01", "2027-01-01", 1),
    ("7d", "2027-01-03T12:00:00+00:00", "2026-12-28", "2027-01-03", 7),
    ("30d", "2027-01-01T12:00:00+00:00", "2026-12-03", "2027-01-01", 30),
    ("month", "2026-03-01T03:59:59+00:00", "2026-02-01", "2026-02-28", 28),
    ("month", "2028-03-01T03:59:59+00:00", "2028-02-01", "2028-02-29", 29),
    ("month", "2026-03-01T04:00:00+00:00", "2026-03-01", "2026-03-01", 1),
])
def test_periods_are_inclusive_calendar_days_in_the_business_timezone(period, instant, start, end, count):
    result = build_analytics_fallback({}, period=period, now=datetime.fromisoformat(instant))
    assert result["period"] == {"key": period, "label": {"today": "Hoje", "7d": "7 dias",
                               "30d": "30 dias", "month": "Este mês"}[period],
                               "start_date": start, "end_date": end, "timezone": "America/Campo_Grande"}
    assert len(result["series"]) == count
    assert result["series"][0]["date"] == start
    assert result["series"][-1]["date"] == end


def test_requested_timezone_and_utc_updated_at():
    instant = datetime.fromisoformat("2026-12-31T23:30:00-04:00")
    result = build_analytics_fallback({}, period="today", timezone="Asia/Tokyo", now=instant)
    assert result["period"]["start_date"] == "2027-01-01"
    assert result["period"]["timezone"] == "Asia/Tokyo"
    assert result["updated_at"] == "2027-01-01T03:30:00+00:00"


@pytest.mark.parametrize("options", [
    {"period": "year"}, {"period": []}, {"timezone": "America/Not_A_City"},
    {"timezone": "../UTC"}, {"timezone": None}, {"now": datetime(2026, 1, 1)},
])
def test_invalid_period_timezone_and_naive_clock_are_rejected(options):
    with pytest.raises(ValueError):
        build_analytics_fallback({}, **options)


@pytest.mark.parametrize("connections,expected", [
    ([], "not_connected"), ([{"id": "shopee", "status": "invalid"}], "not_connected"),
    ([{"id": "shopee", "status": "connected"}], "unavailable"),
])
def test_unknown_metrics_never_become_zero_or_ready(connections, expected):
    result = build_analytics_fallback({"connection": connections}, now=NOW)
    assert result["sources"]["sales"]["status"] == expected
    assert result["sources"]["sales"]["updated_at"] is None
    assert result["sources"]["clicks"]["status"] == "unavailable"
    assert result["summary"]["published"] is None
    assert all(result["summary"][key] is None for key in FINANCE_METRICS)
    assert all(point[key] is None for point in result["series"] for key in (*FINANCE_METRICS, "published"))
    assert result["operation"]["sample_size"] == 0
    assert result["products"] == []


def test_snapshot_statuses_are_independent_of_period_and_not_historical_totals():
    statuses = ["published", "approval", "queued", "preparing", "dispatching",
                "failed", "paused", "unknown", "expired", "cancelled"]
    state = {"jobs": [job(str(i), status) for i, status in enumerate(statuses)],
             "rule": [{"active": True}, {"active": False}, {"active": True}],
             "destination": [{"id": "channel-a", "verified": True}, {"id": "channel-b", "verified": False}]}
    result = build_analytics_fallback(state, period="today", now=NOW)
    assert result["operation"] == {"published_snapshot": 1, "pending_snapshot": 4,
                                   "failed_snapshot": 1, "sample_size": 10, "sample_limit": 120}
    assert {key: result["summary"][key] for key in ("pending", "failed", "active_rules", "verified_destinations")} == {
        "pending": 4, "failed": 1, "active_rules": 2, "verified_destinations": 1,
    }
    source = result["sources"]["publications"]
    assert source["status"] == "partial" and source["complete"] is False and source["scope"] == "latest_120"
    assert "atividade" in source["reason"] and "não comprova" in source["reason"]
    assert result["summary"]["published"] is None


def test_upcoming_orders_all_pending_jobs_with_undated_last_and_recent_uses_activity_order():
    state = {"jobs": [job("late", "queued", due=40, changed=10),
                      job("approval", "approval", due=None, changed=20),
                      job("early", "dispatching", due=30, changed=30),
                      job("published", "published", due=20, changed=40),
                      job("undated", "queued", due="invalid", changed=None)]}
    result = build_analytics_fallback(state, now=NOW)
    assert [row["id"] for row in result["upcoming"]] == ["early", "late", "approval", "undated"]
    assert result["upcoming"][2]["status"] == "approval"
    assert result["upcoming"][2]["due"] is None
    assert [row["id"] for row in result["recent"]] == ["published", "early", "approval", "late", "undated"]
    assert result["recent"][0]["changed"] == 40
    assert "published_at" not in result["recent"][0]


def test_approval_without_due_keeps_the_upcoming_queue_nonempty():
    result = build_analytics_fallback({"jobs": [job("approval", "approval", due=None)]}, now=NOW)
    assert result["operation"]["pending_snapshot"] == 1
    assert len(result["upcoming"]) == 1
    assert result["upcoming"][0]["status"] == "approval"
    assert result["upcoming"][0]["due"] is None


def test_not_connected_guidance_does_not_promise_integrated_sales_reports():
    result = build_analytics_fallback({}, now=NOW)
    assert result["sources"]["sales"]["reason"] == (
        "Conecte sua conta para preparar ofertas com seu link de afiliado. "
        "Relatórios de vendas ainda não integrados."
    )


def test_destinations_aggregate_snapshot_counts_with_no_attributed_sales():
    state = {"destination": [{"id": "channel-a", "name": "Canal A", "mode": "telegram", "verified": True},
                             {"id": "channel-b", "name": "Canal B", "mode": "whatsapp_channel", "verified": False}],
             "jobs": [job("a1", "published"), job("a2", "published"), job("a3", "failed"),
                      job("b1", "queued", destination="channel-b"),
                      job("c1", "published", destination="removed-channel")]}
    result = build_analytics_fallback(state, now=NOW)
    destinations = {row["id"]: row for row in result["destinations"]}
    assert destinations["channel-a"]["published_snapshot"] == 2
    assert destinations["channel-a"]["failed_snapshot"] == 1
    assert destinations["channel-b"]["pending_snapshot"] == 1
    assert destinations["removed-channel"]["name"] == "Destino removido"
    assert destinations["removed-channel"]["published_snapshot"] == 1
    assert all(row["orders"] is None and row["commission_cents"] is None for row in destinations.values())
    assert sum(row["published_snapshot"] for row in destinations.values()) == result["operation"]["published_snapshot"]
    assert result["summary"]["verified_destinations"] == 1


def test_snapshot_is_bounded_to_120_most_recent_activity_records():
    state = {"jobs": [job(str(i), "published", changed=i) for i in range(125)]}
    result = build_analytics_fallback(state, now=NOW)
    assert result["operation"]["sample_size"] == 120
    assert result["operation"]["published_snapshot"] == 120
    assert result["recent"][0]["id"] == "124" and result["recent"][-1]["id"] == "5"
    assert result["summary"]["published"] is None


def test_projection_does_not_leak_credentials_profiles_payloads_or_mock_metrics():
    secret = "DO_NOT_EXPOSE_SYNTHETIC_PRIVATE_VALUE"
    publication = job("safe", "published")
    publication.update({"error": secret, "receipt": {"token": secret}, "raw": secret})
    publication["doc"]["offer"].update({"affiliate_url": secret, "price": 999999, "commission": 999999})
    state = {"company": {"name": secret, "email": secret}, "profile": secret,
             "connection": [{"id": "shopee", "status": "connected", "key": secret, "account": secret}],
             "destination": [{"id": "channel-a", "name": "Canal público", "mode": "telegram",
                              "token": secret, "chat_id": secret, "phone": secret}],
             "rule": [{"active": True, "secret": secret}], "jobs": [publication],
             "analytics": {"sales_cents": 999999, "clicks": 999999}, "mock": True}
    before = deepcopy(state)
    result = build_analytics_fallback(state, now=NOW)
    serialized = json.dumps(result, allow_nan=False)
    assert secret not in serialized and "999999" not in serialized and '"mock"' not in serialized
    assert state == before
    assert set(result["recent"][0]) == {"id", "title", "status", "destination_id", "destination_name", "due", "changed"}
    assert all(result["summary"][key] is None for key in FINANCE_METRICS)


def test_malformed_optional_rows_do_not_escape_as_arbitrary_data():
    state = {"jobs": [None, "invalid", {"id": {"token": "private"}, "status": [],
                                      "due": float("inf"), "changed": float("nan"), "doc": []}],
             "destination": [None, {"id": []}], "connection": None, "rule": "invalid"}
    result = build_analytics_fallback(state, now=NOW)
    assert result["operation"]["sample_size"] == 1
    assert result["recent"][0]["due"] is None and result["recent"][0]["changed"] is None
    assert result["recent"][0]["status"] == "unknown"
    assert "private" not in json.dumps(result, allow_nan=False)


def confirmed_report():
    metrics = {"commission_cents": 0, "validated_commission_cents": 0, "sales_cents": 0,
               "orders": 0, "items": 0, "clicks": 0, "published": 0}
    return {
        "period": {"key": "7d", "start_date": "2026-11-19", "end_date": "2026-11-25",
                   "timezone": "America/Campo_Grande"},
        "updated_at": NOW.isoformat(),
        "sources": {"sales": {"status": "ready"}, "clicks": {"status": "ready"},
                    "publications": {"status": "ready", "complete": True}},
        "summary": dict(metrics),
        "series": [{"date": "2026-11-25", **metrics}],
    }


def test_valid_report_accepts_confirmed_zero_counts_and_null_unknown_metrics():
    report = confirmed_report()
    assert analytics_report_valid(report, period="7d")
    report["summary"] = {key: None for key in report["summary"]}
    report["series"] = [{"date": "2026-11-25", **report["summary"]}]
    assert analytics_report_valid(report, period="7d")
    for period in ("today", "7d", "30d", "month"):
        assert analytics_report_valid(build_analytics_fallback({}, period=period, now=NOW), period=period)


@pytest.mark.parametrize("report", [
    None, [], {}, {"detail": "Synthetic upstream error"},
    {"period": {"key": "month"}, "sources": {}, "summary": {}, "series": []},
])
def test_invalid_or_empty_report_envelopes_cannot_replace_confirmed_data(report):
    assert not analytics_report_valid(report, period="month")


@pytest.mark.parametrize("path,value", [
    (("period", "key"), "month"), (("period", "start_date"), "2026-02-30"),
    (("period", "start_date"), "2026-11-26"), (("period", "start_date"), "2026-11-20"),
    (("period", "end_date"), "20261125"), (("period", "timezone"), "America/Invalid"),
    (("period", "timezone"), None), (("updated_at",), "2026-11-25T14:30:00"),
    (("updated_at",), "not-a-date"), (("updated_at",), None),
    (("sources", "sales", "status"), "invented"), (("sources", "clicks", "status"), []),
    (("sources", "publications", "complete"), 1), (("sources", "publications"), {}),
    (("sources",), {}), (("summary",), {}), (("series",), {}),
])
def test_report_contract_rejects_wrong_period_dates_sources_and_missing_fields(path, value):
    report = confirmed_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not analytics_report_valid(report, period="7d")


@pytest.mark.parametrize("key", ["commission_cents", "validated_commission_cents", "sales_cents", "orders"])
@pytest.mark.parametrize("value", ["100", True, False, 1.0, float("nan"), float("inf")])
def test_summary_and_series_require_nullable_integer_metrics(key, value):
    report = confirmed_report()
    report["summary"][key] = value
    assert not analytics_report_valid(report, period="7d")
    report = confirmed_report()
    report["series"][0][key] = value
    assert not analytics_report_valid(report, period="7d")


def test_money_adjustments_can_be_negative_but_counts_cannot():
    report = confirmed_report()
    report["summary"]["commission_cents"] = -150
    report["series"][0]["commission_cents"] = -150
    assert analytics_report_valid(report, period="7d")
    for key in ("orders", "items", "clicks", "published"):
        invalid = deepcopy(report)
        invalid["summary"][key] = -1
        assert not analytics_report_valid(invalid, period="7d")


@pytest.mark.parametrize("point", [
    None, {"date": "2026-11-25"},
    {"date": "2026-02-30"}, {"date": "2026-11-26"},
])
def test_daily_series_rejects_incomplete_or_out_of_period_points(point):
    report = confirmed_report()
    if isinstance(point, dict) and point["date"] != "2026-11-25":
        point = {**report["summary"], **point}
    report["series"] = [point]
    assert not analytics_report_valid(report, period="7d")


@pytest.mark.parametrize("key", ["destinations", "products", "upcoming", "recent"])
def test_optional_rankings_are_arrays_of_public_objects_with_valid_metrics(key):
    report = confirmed_report()
    report[key] = [{"id": "synthetic", "orders": None, "commission_cents": 0}]
    assert analytics_report_valid(report, period="7d")
    for invalid in ({}, None, [None], [{"orders": -1}], [{"commission_cents": "100"}]):
        report[key] = invalid
        assert not analytics_report_valid(report, period="7d")


def test_snapshot_contract_accepts_empty_arrays_but_rejects_missing_or_malformed_collections():
    valid = {"jobs": [], "rule": [], "destination": [], "connection": []}
    assert state_snapshot_valid(valid)
    assert state_snapshot_valid({key: [{"id": "synthetic"}] for key in valid})
    for invalid in (None, [], {}, {"detail": "Synthetic upstream error"}):
        assert not state_snapshot_valid(invalid)
    for key in valid:
        missing = dict(valid)
        del missing[key]
        assert not state_snapshot_valid(missing)
        for value in (None, {}, "invalid", [None], ["invalid"], [1]):
            assert not state_snapshot_valid({**valid, key: value})
