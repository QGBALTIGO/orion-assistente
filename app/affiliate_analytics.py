"""Honest dashboard metadata from the affiliate service's limited state snapshot.

This fallback does not infer sales from catalog prices or publication activity.
It deliberately leaves period totals and daily points unknown: ``changed`` is
an activity timestamp, and the state endpoint is not a complete event history.
"""
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone as datetime_timezone
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SAMPLE_LIMIT = 120
PERIOD_LABELS = {"today": "Hoje", "7d": "7 dias", "30d": "30 dias", "month": "Este mês"}
PENDING_STATUSES = frozenset({"approval", "queued", "preparing", "dispatching"})
JOB_STATUSES = PENDING_STATUSES | {"published", "paused", "failed", "expired", "cancelled", "unknown"}
DESTINATION_MODES = frozenset({"telegram", "whatsapp_group", "whatsapp_channel"})
UNKNOWN_METRICS = (
    "sales_cents", "commission_cents", "validated_commission_cents",
    "orders", "items", "clicks", "conversion_rate",
)
REPORT_METRICS = (
    "commission_cents", "validated_commission_cents", "sales_cents",
    "orders", "items", "clicks", "published",
)
COUNT_METRICS = frozenset({
    "orders", "items", "clicks", "published", "pending", "failed",
    "active_rules", "verified_destinations", "published_snapshot",
    "pending_snapshot", "failed_snapshot",
})
SOURCE_STATUSES = frozenset({"ready", "not_connected", "unavailable", "error", "syncing", "partial"})


def _iso_date(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def _aware_iso_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _report_metrics_valid(row, *, required=False):
    if not isinstance(row, Mapping) or (required and any(key not in row for key in REPORT_METRICS)):
        return False
    for key in set(REPORT_METRICS) | COUNT_METRICS:
        if key not in row or row[key] is None:
            continue
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, int) or (key in COUNT_METRICS and value < 0):
            return False
    return True


def state_snapshot_valid(state):
    """Reject error envelopes or missing collections before deriving zero counts."""
    return isinstance(state, Mapping) and all(
        isinstance(state.get(key), list) and all(isinstance(row, Mapping) for row in state[key])
        for key in ("jobs", "rule", "destination", "connection")
    )


def analytics_report_valid(report, *, period):
    """Check the public report contract before it can replace a valid UI cache."""
    if not isinstance(report, Mapping) or not isinstance(period, str) or period not in PERIOD_LABELS:
        return False
    window = report.get("period")
    if not isinstance(window, Mapping) or window.get("key") != period:
        return False
    start, end = _iso_date(window.get("start_date")), _iso_date(window.get("end_date"))
    if start is None or end is None or start > end:
        return False
    if period == "month":
        if start != end.replace(day=1):
            return False
    elif (end - start).days != {"today": 0, "7d": 6, "30d": 29}[period]:
        return False
    try:
        ZoneInfo(window.get("timezone"))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    if not _aware_iso_timestamp(report.get("updated_at")):
        return False

    sources = report.get("sources")
    if not isinstance(sources, Mapping):
        return False
    for name in ("sales", "clicks", "publications"):
        source = sources.get(name)
        if not isinstance(source, Mapping):
            return False
        status = source.get("status")
        if not isinstance(status, str) or status not in SOURCE_STATUSES:
            return False
    if not isinstance(sources["publications"].get("complete"), bool):
        return False
    if not _report_metrics_valid(report.get("summary"), required=True):
        return False
    series = report.get("series")
    if not isinstance(series, list):
        return False
    for point in series:
        if not _report_metrics_valid(point, required=True):
            return False
        day = _iso_date(point.get("date"))
        if day is None or not start <= day <= end:
            return False
    for name in ("destinations", "products", "upcoming", "recent"):
        if name in report and (
            not isinstance(report[name], list)
            or not all(_report_metrics_valid(row) for row in report[name])
        ):
            return False
    return True


def _records(value):
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, (list, tuple)) else []


def _text(value, *, limit=300):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _epoch(value):
    # The existing state contract uses Unix seconds, not arbitrary date strings.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 253402300799 or not math.isfinite(value):
        return None
    return value


def _destination(row):
    mode = _text(row.get("mode"), limit=32)
    return {
        "id": _text(row.get("id"), limit=128) or None,
        "name": _text(row.get("name"), limit=160) or "Destino sem nome",
        "mode": mode if mode in DESTINATION_MODES else "unknown",
        "verified": row.get("verified") is True,
        "published_snapshot": 0,
        "pending_snapshot": 0,
        "failed_snapshot": 0,
        "orders": None,
        "commission_cents": None,
    }


def _job(row, destinations):
    doc = row.get("doc")
    offer = doc.get("offer") if isinstance(doc, Mapping) else None
    title = offer.get("title") if isinstance(offer, Mapping) else None
    destination_id = _text(row.get("destination"), limit=128) or None
    destination = destinations.get(destination_id)
    status = _text(row.get("status"), limit=32)
    return {
        "id": _text(row.get("id"), limit=128) or None,
        "title": _text(title) or "Oferta sem título",
        "status": status if status in JOB_STATUSES else "unknown",
        "destination_id": destination_id,
        "destination_name": destination["name"] if destination else (
            "Destino removido" if destination_id else "Sem destino"
        ),
        "due": _epoch(row.get("due")),
        "changed": _epoch(row.get("changed")),
    }


def build_analytics_fallback(state, *, period="month", timezone="America/Campo_Grande", now=None):
    """Build the public analytics shape without a database or provider calls.

    Period dates are inclusive calendar dates in the requested IANA timezone.
    Snapshot counters describe the current returned sample independently of
    that period. Upcoming jobs retain overdue and undated pending entries.
    ``now`` must be an aware datetime when supplied, making tests deterministic.
    Only explicitly selected public display fields leave this function.
    """
    if not isinstance(state, Mapping):
        raise ValueError("Estado de Afiliados inválido")
    if not isinstance(period, str) or period not in PERIOD_LABELS:
        raise ValueError("Período de analytics inválido")
    try:
        zone = ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValueError("Fuso horário de analytics inválido") from None
    instant = datetime.now(datetime_timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("O instante de analytics deve conter fuso horário")

    end_date = instant.astimezone(zone).date()
    if period == "month":
        start_date = end_date.replace(day=1)
    elif period == "today":
        start_date = end_date
    else:
        start_date = end_date - timedelta(days={"7d": 6, "30d": 29}[period])
    updated_at = instant.astimezone(datetime_timezone.utc).isoformat()

    destinations = {}
    for row in _records(state.get("destination")):
        public = _destination(row)
        if public["id"] is not None:
            destinations[public["id"]] = public
    verified_destinations = sum(row["verified"] for row in destinations.values())

    jobs = [_job(row, destinations) for row in _records(state.get("jobs"))]
    jobs.sort(key=lambda job: job["changed"] if job["changed"] is not None else -1, reverse=True)
    jobs = jobs[:SAMPLE_LIMIT]
    published = sum(job["status"] == "published" for job in jobs)
    pending = sum(job["status"] in PENDING_STATUSES for job in jobs)
    failed = sum(job["status"] == "failed" for job in jobs)

    for job in jobs:
        destination_id = job["destination_id"]
        if destination_id not in destinations:
            destinations[destination_id] = {
                **_destination({"id": destination_id}),
                "name": job["destination_name"],
            }
        destination = destinations[destination_id]
        if job["status"] == "published":
            destination["published_snapshot"] += 1
        elif job["status"] in PENDING_STATUSES:
            destination["pending_snapshot"] += 1
        elif job["status"] == "failed":
            destination["failed_snapshot"] += 1

    connected = any(
        row.get("id") == "shopee" and row.get("status") == "connected"
        for row in _records(state.get("connection"))
    )
    missing_metrics = {key: None for key in UNKNOWN_METRICS}
    day_count = (end_date - start_date).days + 1
    series = [
        {"date": (start_date + timedelta(days=offset)).isoformat(), **missing_metrics, "published": None}
        for offset in range(day_count)
    ]
    return {
        "period": {
            "key": period,
            "label": PERIOD_LABELS[period],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": zone.key,
        },
        "updated_at": updated_at,
        "sources": {
            "sales": {
                "status": "unavailable" if connected else "not_connected",
                "reason": (
                    "Conta Shopee conectada. Os relatórios de vendas e comissões ainda não estão disponíveis no Orion."
                    if connected else (
                        "Conecte sua conta para preparar ofertas com seu link de afiliado. "
                        "Relatórios de vendas ainda não integrados."
                    )
                ),
                "updated_at": None,
            },
            "clicks": {
                "status": "unavailable",
                "reason": "O Orion ainda não recebe um relatório de cliques dos links publicados.",
                "updated_at": None,
            },
            "publications": {
                "status": "partial",
                "complete": False,
                "scope": "latest_120",
                "reason": (
                    "Amostra de até 120 registros recentes. A data de alteração indica atividade, "
                    "não comprova quando ocorreu a publicação e não permite totalizar o período."
                ),
                "updated_at": updated_at,
            },
        },
        "summary": {
            **missing_metrics,
            "published": None,
            "pending": pending,
            "failed": failed,
            "active_rules": sum(row.get("active") is True for row in _records(state.get("rule"))),
            "verified_destinations": verified_destinations,
        },
        "operation": {
            "published_snapshot": published,
            "pending_snapshot": pending,
            "failed_snapshot": failed,
            "sample_size": len(jobs),
            "sample_limit": SAMPLE_LIMIT,
        },
        "series": series,
        "upcoming": sorted(
            (job for job in jobs if job["status"] in PENDING_STATUSES),
            key=lambda job: (job["due"] is None, job["due"] if job["due"] is not None else 0),
        ),
        "recent": jobs,
        "destinations": sorted(
            destinations.values(),
            key=lambda row: (-row["published_snapshot"], -row["pending_snapshot"], row["name"].casefold()),
        ),
        "products": [],
    }
