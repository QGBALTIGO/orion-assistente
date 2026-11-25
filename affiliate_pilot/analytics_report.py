"""Pure aggregation of Shopee conversion reports, without credentials or I/O.

The caller supplies every fetched page for one authenticated affiliate account.
The period is half-open, and uses purchaseTime rather than clickTime. Monetary
strings are accumulated as Decimal and rounded only when emitting cents.
actualAmount and itemTotalCommission each describe one returned line: neither is
multiplied by qty, and refundAmount is never deducted a second time.

Attribution is an exact utmContent -> {destination_id, name} mapping already
restricted to the current tenant by the caller. No prefix, separator, product,
timestamp, or channel-name heuristic can grant attribution here.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ELIGIBLE_ORDERS = frozenset({"PENDING", "COMPLETED"})
EXCLUDED_ORDERS = frozenset({"UNPAID", "CANCELLED"})
ELIGIBLE_ITEMS = frozenset({"TO_BE_COMPLETED", "COMPLETED", "COMPLETED_PARTIAL_REFUNDED"})
EXCLUDED_ITEMS = frozenset({"CANCEL", "CANCELLED", "FRAUD"})
ELIGIBLE_FRAUD = frozenset({"UNVERIFIED", "VERIFIED"})
MEASURED = frozenset({"commission_cents", "sales_cents", "orders", "items"})
CENT = Decimal("0.01")
MAX_SAFE_CENTS = 2**53 - 1
ITEM_FIELDS = (
    "shopId", "itemId", "modelId", "promotionId", "qty", "displayItemStatus", "fraudStatus",
    "actualAmount", "itemTotalCommission", "itemSellerCommission", "itemShopeeCommissionCapped",
)
NODE_FIELDS = (
    "conversionId", "purchaseTime", "utmContent", "totalCommission", "sellerCommission",
    "shopeeCommissionCapped",
)


def _identifier(value):
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip():
        return value
    return None


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    if isinstance(value, str) and (not value or len(value) > 80 or value != value.strip()):
        return None
    try:
        result = Decimal(value)
        if result.is_finite() and abs(result) <= Decimal(MAX_SAFE_CENTS) / 100:
            return result
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return None


def _cents(value):
    with localcontext() as context:
        context.prec = 80
        result = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return result if abs(result) <= MAX_SAFE_CENTS else None


def _sum(values):
    with localcontext() as context:
        context.prec = 80
        return sum(values, Decimal(0))


def _quantity(value):
    return value if type(value) is int and 0 < value <= MAX_SAFE_CENTS else None


def _status(value):
    return value.strip().upper() if isinstance(value, str) else ""


def _purchase_time(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int:
        return None
    try:
        return datetime.fromtimestamp(value, datetime_timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _signature(row, fields):
    return json.dumps({key: row.get(key) for key in fields}, sort_keys=True, default=str,
                      ensure_ascii=True, separators=(",", ":"))


def _order_signature(order):
    items = order.get("items")
    if isinstance(items, list) and all(isinstance(item, Mapping) for item in items):
        items = sorted(set(_signature(item, ITEM_FIELDS) for item in items))
    return json.dumps({"orderId": order.get("orderId"), "orderStatus": order.get("orderStatus"),
                       "items": items}, sort_keys=True, default=str, separators=(",", ":"))


def _node_signature(node):
    orders = node.get("orders")
    if isinstance(orders, list) and all(isinstance(order, Mapping) for order in orders):
        orders = sorted(set(_order_signature(order) for order in orders))
    return _signature(node, NODE_FIELDS) + json.dumps(orders, sort_keys=True, default=str)


class _Bucket:
    def __init__(self):
        self.sales = Decimal(0)
        self.commission = Decimal(0)
        self.items = 0
        self.orders = set()
        self.unknown = set()

    def add(self, item, order_id, *, commission_ok):
        self.orders.add(order_id)
        amount = _amount(item.get("actualAmount"))
        commission = _amount(item.get("itemTotalCommission"))
        quantity = _quantity(item.get("qty"))
        if amount is None:
            self.unknown.add("sales_cents")
        else:
            self.sales = _sum((self.sales, amount))
        if commission is None or not commission_ok:
            self.unknown.add("commission_cents")
        else:
            self.commission = _sum((self.commission, commission))
        if quantity is None:
            self.unknown.add("items")
        else:
            self.items += quantity

    def result(self):
        values = {"sales_cents": _cents(self.sales), "commission_cents": _cents(self.commission),
                  "orders": len(self.orders), "items": self.items if self.items <= MAX_SAFE_CENTS else None,
                  "validated_commission_cents": None, "clicks": None, "published": None}
        values.update({metric: None for metric in self.unknown})
        return values


def aggregate_conversions(nodes, *, start, end, timezone, attribution=None):
    """Return summary, daily series, financial rankings, and safe diagnostics.

    Unknown fields make the affected aggregate metric null, not a subtotal
    presented as a full total. Conflicting duplicate identities are excluded and
    their affected metrics are unknown. `partial` and issue codes explain this.
    Identical records are idempotent. IDs in rankings are always strings.

    Conversion totals and optional commission components are reconciled at the
    precision displayed by this dashboard, after Decimal aggregation. A mismatch
    leaves commission unknown; it never allocates a cap across eligible lines.
    Eligible amounts remain estimated, including orders already COMPLETED.
    """
    if not isinstance(nodes, (list, tuple)):
        raise ValueError("Conversion nodes must be a list")
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
           for value in (start, end)) or start >= end:
        raise ValueError("An ordered, aware half-open period is required")
    try:
        zone = ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValueError("Invalid report timezone") from None
    if attribution is not None and not isinstance(attribution, Mapping):
        raise ValueError("Attribution must be a tenant-scoped exact mapping")
    attribution = attribution or {}

    first = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    days = { (first + timedelta(days=offset)).isoformat(): _Bucket()
             for offset in range((last - first).days + 1) }
    summary, products, destinations = _Bucket(), {}, {}
    issues, counts = Counter(), Counter(nodes_received=len(nodes))
    attributed_orders, unattributed_orders = set(), set()
    unlocated_unknown = set()

    def uncertain(day=None, metrics=MEASURED):
        summary.unknown.update(metrics)
        unlocated_unknown.update(metrics)
        for bucket in ([days[day]] if day in days else days.values()):
            bucket.unknown.update(metrics)

    def issue(code, day=None, *, metrics=()):
        issues[code] += 1
        if metrics:
            uncertain(day, metrics)

    # Resolve conversion duplicates before processing any financial values.
    conversions, blocked_conversions = {}, set()
    for node in nodes:
        if not isinstance(node, Mapping):
            issue("invalid_conversion", metrics=MEASURED)
            continue
        purchased = _purchase_time(node.get("purchaseTime"))
        if purchased is None:
            issue("invalid_purchase_time", metrics=MEASURED)
            continue
        if not start <= purchased < end:
            counts["nodes_outside_period"] += 1
            continue
        day = purchased.astimezone(zone).date().isoformat()
        identifier = _identifier(node.get("conversionId"))
        if identifier is None:
            issue("missing_conversion_id", day, metrics=MEASURED)
            continue
        signature = _node_signature(node)
        previous = conversions.get(identifier)
        if previous:
            if previous["signature"] == signature:
                counts["duplicate_conversions"] += 1
            else:
                blocked_conversions.add(identifier)
                issue("conflicting_conversion", day, metrics=MEASURED)
                uncertain(previous["day"])
            continue
        conversions[identifier] = {"node": node, "day": day, "purchased": purchased,
                                   "signature": signature, "commission_ok": True}

    orders, blocked_orders = {}, set()
    for conversion_id, record in conversions.items():
        if conversion_id in blocked_conversions:
            continue
        counts["unique_conversions"] += 1
        node, day = record["node"], record["day"]
        incoming_orders = node.get("orders")
        if not isinstance(incoming_orders, list) or not incoming_orders:
            issue("missing_orders", day, metrics=MEASURED)
            continue
        total = _amount(node.get("totalCommission"))
        line_total = Decimal(0)
        reconcilable = total is not None
        seen_lines = {}
        for order in incoming_orders:
            if not isinstance(order, Mapping) or (order_id := _identifier(order.get("orderId"))) is None:
                issue("missing_order_id", day, metrics=MEASURED)
                reconcilable = False
                continue
            items = order.get("items")
            if not isinstance(items, list) or not items:
                issue("missing_items", day, metrics=MEASURED)
                reconcilable = False
                continue
            unique_items, blocked_items = {}, set()
            for item in items:
                if not isinstance(item, Mapping):
                    issue("invalid_item", day, metrics=MEASURED)
                    reconcilable = False
                    continue
                identity = tuple(_identifier(item.get(key)) for key in ("shopId", "itemId", "modelId"))
                promotion = item.get("promotionId", "")
                if any(value is None for value in identity) or not isinstance(promotion, str):
                    issue("missing_item_identity", day, metrics=MEASURED)
                    reconcilable = False
                    continue
                item_key = (*identity, promotion)
                signature = _signature(item, ITEM_FIELDS)
                if item_key in unique_items:
                    if unique_items[item_key][1] == signature:
                        counts["duplicate_items"] += 1
                    else:
                        blocked_items.add(item_key)
                        issue("conflicting_item", day, metrics=MEASURED)
                        reconcilable = False
                    continue
                unique_items[item_key] = (item, signature)
            clean_items = []
            for item_key, (item, signature) in unique_items.items():
                if item_key in blocked_items:
                    continue
                clean_items.append(item)
                line_identity = (order_id, *item_key)
                if line_identity in seen_lines:
                    if seen_lines[line_identity] != signature:
                        reconcilable = False
                    continue
                seen_lines[line_identity] = signature
                commission = _amount(item.get("itemTotalCommission"))
                if commission is None:
                    reconcilable = False
                else:
                    line_total = _sum((line_total, commission))
                component_keys = ("itemSellerCommission", "itemShopeeCommissionCapped")
                if any(key in item for key in component_keys):
                    components = [_amount(item.get(key)) for key in component_keys]
                    if commission is None or any(value is None for value in components) or (
                        _cents(_sum(components)) != _cents(commission)
                    ):
                        reconcilable = False
                        issue("item_commission_components_mismatch", day)

            signature = (record["purchased"], node.get("utmContent"), _order_signature(order))
            previous = orders.get(order_id)
            if previous:
                if previous["signature"] == signature:
                    counts["duplicate_orders"] += 1
                else:
                    blocked_orders.add(order_id)
                    issue("conflicting_order", day, metrics=MEASURED)
                    uncertain(previous["day"])
                continue
            orders[order_id] = {"order": order, "items": clean_items, "day": day,
                                "conversion": record, "signature": signature}

        component_keys = ("sellerCommission", "shopeeCommissionCapped")
        if any(key in node for key in component_keys):
            components = [_amount(node.get(key)) for key in component_keys]
            if total is None or any(value is None for value in components) or (
                _cents(_sum(components)) != _cents(total)
            ):
                reconcilable = False
                issue("conversion_commission_components_mismatch", day)
        if total is None or not reconcilable:
            record["commission_ok"] = False
            issue("commission_reconciliation_unavailable", day)
        elif _cents(line_total) != _cents(total):
            record["commission_ok"] = False
            issue("conversion_commission_mismatch", day)

    for order_id, record in orders.items():
        if order_id in blocked_orders:
            continue
        order, day, conversion = record["order"], record["day"], record["conversion"]
        status = _status(order.get("orderStatus"))
        if status in EXCLUDED_ORDERS:
            counts["excluded_orders"] += 1
            continue
        if status not in ELIGIBLE_ORDERS:
            issue("unknown_order_status", day, metrics=MEASURED)
            continue
        counts["eligible_orders_observed"] += 1
        token = conversion["node"].get("utmContent")
        target = attribution.get(token) if isinstance(token, str) and token.strip("-") else None
        destination_id = _identifier(target.get("destination_id")) if isinstance(target, Mapping) else None
        destination = None
        if destination_id is not None:
            if destination_id not in destinations:
                name = target.get("name")
                destinations[destination_id] = {
                    "id": destination_id, "name": name[:160] if isinstance(name, str) else "Destino identificado",
                    "bucket": _Bucket(),
                }
            destination = destinations[destination_id]["bucket"]
        for item in record["items"]:
            item_status, fraud = _status(item.get("displayItemStatus")), _status(item.get("fraudStatus"))
            if item_status in EXCLUDED_ITEMS or fraud == "FRAUD":
                counts["excluded_items"] += 1
                continue
            if item_status not in ELIGIBLE_ITEMS or fraud not in ELIGIBLE_FRAUD:
                issue("unknown_item_eligibility", day, metrics=MEASURED)
                if destination:
                    destination.unknown.update(MEASURED)
                continue
            shop, item_id = _identifier(item.get("shopId")), _identifier(item.get("itemId"))
            product_key = f"shopee:{shop}:{item_id}"
            if product_key not in products:
                title = item.get("itemName")
                products[product_key] = {"id": product_key, "shop_id": shop, "item_id": item_id,
                                         "title": title[:240] if isinstance(title, str) else "Produto sem título",
                                         "bucket": _Bucket()}
            buckets = [summary, days[day], products[product_key]["bucket"]]
            if destination:
                buckets.append(destination)
                attributed_orders.add(order_id)
            else:
                unattributed_orders.add(order_id)
            for bucket in buckets:
                bucket.add(item, order_id, commission_ok=conversion["commission_ok"])
            counts["eligible_items_observed"] += 1
            for field, code in (("actualAmount", "missing_actual_amount"),
                                ("itemTotalCommission", "missing_item_commission")):
                if _amount(item.get(field)) is None:
                    issue(code, day)
            if _quantity(item.get("qty")) is None:
                issue("invalid_quantity", day)
            refund = _amount(item.get("refundAmount"))
            if (refund is not None and refund > 0) or item_status == "COMPLETED_PARTIAL_REFUNDED":
                counts["refund_items"] += 1

    def ranking(rows):
        for row in rows.values():
            row["bucket"].unknown.update(unlocated_unknown)
        result = [{**{key: value for key, value in row.items() if key != "bucket"},
                   **row["bucket"].result()} for row in rows.values()]
        return sorted(result, key=lambda row: (row["commission_cents"] is None,
                                               -(row["commission_cents"] or 0), row["id"]))

    counts["attributed_orders"] = len(attributed_orders)
    counts["unattributed_orders"] = len(unattributed_orders)
    summary_values = summary.result()
    if any(summary_values[key] is None and key not in summary.unknown for key in MEASURED):
        issue("numeric_overflow")
    return {
        "summary": summary_values,
        "series": [{"date": day, **bucket.result()} for day, bucket in days.items()],
        "products": ranking(products),
        "destinations": ranking(destinations),
        "scope": "account",
        "partial": bool(issues),
        "issues": sorted(issues),
        "issue_counts": dict(sorted(issues.items())),
        "counts": dict(sorted(counts.items())),
    }
