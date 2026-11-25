"""Financial correctness with synthetic IDs and no network, store, or provider calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pytest

from affiliate_pilot.analytics_report import aggregate_conversions


ZONE = "America/Campo_Grande"
START = datetime(2026, 11, 1, tzinfo=ZoneInfo(ZONE))
END = datetime(2026, 11, 26, tzinfo=ZoneInfo(ZONE))
PURCHASE = int(datetime(2026, 11, 24, 14, tzinfo=timezone.utc).timestamp())


def line(item_id="1", *, amount="10.00", commission="1.00", qty=1,
         status="COMPLETED", fraud="VERIFIED", promotion="campaign", **overrides):
    return {"shopId": 100, "itemId": item_id, "modelId": 0, "promotionId": promotion,
            "itemName": "Produto sintético " + str(item_id), "itemPrice": amount,
            "actualAmount": amount, "refundAmount": "0", "itemTotalCommission": commission,
            "itemSellerCommission": "0", "itemShopeeCommissionCapped": commission,
            "qty": qty, "displayItemStatus": status, "fraudStatus": fraud, **overrides}


def order(identifier="synthetic-order-1", *, status="COMPLETED", items=None):
    return {"orderId": identifier, "orderStatus": status, "items": items if items is not None else [line()]}


def conversion(identifier=1, *, orders=None, purchased=PURCHASE, tag="----", **overrides):
    orders = orders if orders is not None else [order()]
    items = [item for row in orders for item in row["items"]]
    amount = lambda key: str(sum((Decimal(item[key]) for item in items), Decimal(0)))
    return {"conversionId": identifier, "purchaseTime": purchased, "clickTime": purchased - 50,
            "utmContent": tag, "orders": orders, "totalCommission": amount("itemTotalCommission"),
            "sellerCommission": amount("itemSellerCommission"),
            "shopeeCommissionCapped": amount("itemShopeeCommissionCapped"), **overrides}


def aggregate(nodes, **options):
    return aggregate_conversions(nodes, start=START, end=END, timezone=ZONE, **options)


def test_verified_live_amount_shape_with_synthetic_identifiers():
    # Amounts observed in an authorized report; all identifiers/titles are synthetic.
    first = line(10, amount="5.39", commission="0.22638", itemPrice="8.99",
                 itemSellerCommission="0.06468", itemShopeeCommissionCapped="0.1617")
    second = line(20, amount="24.49", commission="0.7347")
    nodes = [conversion(1, orders=[order("synthetic-a", items=[first])]),
             conversion(2, orders=[order("synthetic-b", items=[second])])]
    result = aggregate(nodes)
    assert result["summary"] == {"sales_cents": 2988, "commission_cents": 96,
                                 "orders": 2, "items": 2, "validated_commission_cents": None,
                                 "clicks": None, "published": None}
    assert result["partial"] is False and result["issues"] == []
    assert result["scope"] == "account" and result["destinations"] == []
    assert result["counts"]["unattributed_orders"] == 2
    assert len(result["series"]) == 25
    assert result["series"][23]["commission_cents"] == 96
    assert {row["item_id"] for row in result["products"]} == {"10", "20"}


def test_line_amount_and_commission_are_not_multiplied_by_quantity():
    result = aggregate([conversion(orders=[order(items=[line(amount="100.00", commission="4.50", qty=3)])])])
    assert result["summary"]["sales_cents"] == 10000
    assert result["summary"]["commission_cents"] == 450
    assert result["summary"]["items"] == 3
    assert result["summary"]["orders"] == 1
    assert not result["partial"]


def test_partial_refund_is_not_deducted_a_second_time():
    item = line(amount="80.00", commission="8.00", qty=2,
                status="COMPLETED_PARTIAL_REFUNDED", refundAmount="20.00", itemPrice="50.00")
    result = aggregate([conversion(orders=[order(items=[item])])])
    assert result["summary"]["sales_cents"] == 8000
    assert result["summary"]["commission_cents"] == 800
    assert result["counts"]["refund_items"] == 1
    assert not result["partial"]


def test_mixed_orders_and_cancelled_or_fraudulent_lines_are_excluded():
    rows = [
        order("completed", items=[line("1"), line("2", status="CANCEL"), line("3", fraud="FRAUD")]),
        order("pending", status="PENDING", items=[line("4", amount="20.00", commission="2.00", qty=2,
                                                        status="TO_BE_COMPLETED", fraud="UNVERIFIED")]),
        order("unpaid", status="UNPAID", items=[line("5")]),
        order("cancelled", status="CANCELLED", items=[line("6")]),
    ]
    result = aggregate([conversion(orders=rows)])
    assert result["summary"]["orders"] == 2
    assert result["summary"]["items"] == 3
    assert result["summary"]["sales_cents"] == 3000
    assert result["summary"]["commission_cents"] == 300
    assert result["counts"]["excluded_orders"] == 2
    assert result["counts"]["excluded_items"] == 2
    assert not result["partial"]


def test_only_excluded_orders_produce_confirmed_zero_eligible_totals():
    result = aggregate([conversion(orders=[order(status="CANCELLED")])])
    assert {key: result["summary"][key] for key in ("orders", "items", "sales_cents", "commission_cents")} == {
        "orders": 0, "items": 0, "sales_cents": 0, "commission_cents": 0,
    }
    assert result["summary"]["validated_commission_cents"] is None
    assert not result["partial"]


def test_exact_duplicates_are_idempotent_at_conversion_order_and_item_levels():
    single = conversion()
    repeated = deepcopy(single)
    repeated["orders"][0]["items"] *= 2
    repeated["orders"] *= 2
    before = deepcopy(repeated)
    expected = aggregate([single])
    actual = aggregate([repeated, deepcopy(repeated)])
    assert actual["summary"] == expected["summary"]
    assert actual["series"] == expected["series"]
    assert actual["products"] == expected["products"]
    assert actual["partial"] is False
    assert repeated == before
    assert actual["counts"]["duplicate_conversions"] == 1
    assert actual["counts"]["duplicate_orders"] == 1


def test_duplicate_order_across_conversion_ids_is_not_charged_twice():
    result = aggregate([conversion(1), conversion(2)])
    assert result["summary"]["orders"] == 1
    assert result["summary"]["sales_cents"] == 1000
    assert result["summary"]["commission_cents"] == 100
    assert result["counts"]["duplicate_orders"] == 1
    assert not result["partial"]


def test_distinct_promotions_preserve_distinct_lines_of_the_same_variant():
    rows = [line(promotion="campaign-a"), line(promotion="campaign-b")]
    result = aggregate([conversion(orders=[order(items=rows)])])
    assert result["summary"]["items"] == 2
    assert result["summary"]["sales_cents"] == 2000
    assert result["summary"]["orders"] == 1
    assert len(result["products"]) == 1
    assert result["products"][0]["orders"] == 1


def test_conflicting_duplicate_conversion_is_partial_independent_of_input_order():
    initial = conversion()
    conflicting = conversion(orders=[order(status="CANCELLED")])
    for nodes in ([initial, conflicting], [conflicting, initial]):
        result = aggregate(nodes)
        assert result["partial"]
        assert "conflicting_conversion" in result["issues"]
        assert all(result["summary"][key] is None for key in ("orders", "items", "commission_cents", "sales_cents"))


def test_conflicting_duplicate_line_does_not_choose_an_arbitrary_amount():
    first = line()
    second = line(amount="30.00")
    result = aggregate([conversion(orders=[order(items=[first, second])])])
    assert result["partial"]
    assert "conflicting_item" in result["issues"]
    assert result["summary"]["sales_cents"] is None


def test_decimal_rounding_occurs_after_aggregation_not_per_line():
    rows = [line("1", amount="0.005", commission="0.005"), line("2", amount="0.005", commission="0.005")]
    result = aggregate([conversion(orders=[order(items=rows)])])
    assert result["summary"]["sales_cents"] == 1
    assert result["summary"]["commission_cents"] == 1
    assert not result["partial"]


@pytest.mark.parametrize("field,value,unknown,known", [
    ("actualAmount", None, "sales_cents", "commission_cents"),
    ("actualAmount", "not-money", "sales_cents", "commission_cents"),
    ("actualAmount", "NaN", "sales_cents", "commission_cents"),
    ("actualAmount", "Infinity", "sales_cents", "commission_cents"),
    ("actualAmount", True, "sales_cents", "commission_cents"),
    ("actualAmount", 1.5, "sales_cents", "commission_cents"),
    ("itemTotalCommission", None, "commission_cents", "sales_cents"),
    ("qty", None, "items", "sales_cents"),
    ("qty", True, "items", "sales_cents"),
    ("qty", -1, "items", "sales_cents"),
])
def test_missing_or_malformed_values_make_only_affected_metrics_unknown(field, value, unknown, known):
    node = conversion()
    node["orders"][0]["items"][0][field] = value
    result = aggregate([node])
    assert result["partial"]
    assert result["summary"][unknown] is None
    assert result["summary"][known] is not None
    assert result["products"][0][unknown] is None
    assert result["series"][23][unknown] is None
    assert result["series"][0][unknown] == 0
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kind", ["conversion_total", "conversion_components", "item_components", "missing_total"])
def test_unreconciled_commission_never_invents_cap_allocation(kind):
    node = conversion()
    if kind == "conversion_total":
        node["totalCommission"] = "0.50"
        node["shopeeCommissionCapped"] = "0.50"
    elif kind == "conversion_components":
        node["sellerCommission"] = "5.00"
    elif kind == "item_components":
        node["orders"][0]["items"][0]["itemSellerCommission"] = "2.00"
    else:
        del node["totalCommission"]
    result = aggregate([node])
    assert result["partial"] and result["issues"]
    assert result["summary"]["commission_cents"] is None
    assert result["summary"]["sales_cents"] == 1000
    assert result["summary"]["orders"] == 1


@pytest.mark.parametrize("kind", ["order", "item", "fraud"])
def test_unknown_eligibility_does_not_become_confirmed_zero(kind):
    node = conversion()
    if kind == "order":
        node["orders"][0]["orderStatus"] = "UNRECOGNIZED"
    else:
        field = "displayItemStatus" if kind == "item" else "fraudStatus"
        node["orders"][0]["items"][0][field] = "UNRECOGNIZED"
    result = aggregate([node])
    assert result["partial"]
    assert result["summary"]["orders"] is None
    assert result["summary"]["sales_cents"] is None


def test_period_is_half_open_and_daily_buckets_use_requested_timezone():
    start = datetime(2027, 1, 1, tzinfo=ZoneInfo(ZONE))
    end = datetime(2027, 1, 3, tzinfo=ZoneInfo(ZONE))
    stamps = [int(start.timestamp()) - 1, int(start.timestamp()),
              int(end.timestamp()) - 1, int(end.timestamp())]
    nodes = [conversion(i + 1, orders=[order(str(i))], purchased=stamp) for i, stamp in enumerate(stamps)]
    result = aggregate_conversions(nodes, start=start, end=end, timezone=ZONE)
    assert result["summary"]["orders"] == 2
    assert result["summary"]["sales_cents"] == 2000
    assert [row["date"] for row in result["series"]] == ["2027-01-01", "2027-01-02"]
    assert [row["orders"] for row in result["series"]] == [1, 1]
    assert result["counts"]["nodes_outside_period"] == 2


def test_empty_successful_report_has_real_zero_sales_but_no_click_or_validated_claim():
    start = datetime(2028, 2, 1, tzinfo=ZoneInfo(ZONE))
    end = datetime(2028, 3, 1, tzinfo=ZoneInfo(ZONE))
    result = aggregate_conversions([], start=start, end=end, timezone=ZONE)
    assert len(result["series"]) == 29
    assert result["summary"]["sales_cents"] == 0
    assert result["summary"]["commission_cents"] == 0
    assert result["summary"]["clicks"] is None
    assert result["summary"]["validated_commission_cents"] is None
    assert not result["partial"]


def test_exact_tenant_scoped_attribution_never_uses_prefixes_or_foreign_tags():
    tag = "orionj" + "a" * 32
    foreign = "orionj" + "b" * 32
    mapping = {tag: {"destination_id": "owned-destination", "name": "Canal autorizado", "private": "DO_NOT_LEAK"},
               tag + "----": {"destination_id": "owned-destination", "name": "Canal autorizado"}}
    tags = [tag, tag + "----", foreign, "prefix" + tag, tag + "-unknown", "----"]
    nodes = [conversion(i + 1, orders=[order(str(i))], tag=value) for i, value in enumerate(tags)]
    result = aggregate(nodes, attribution=mapping)
    assert result["summary"]["orders"] == 6
    assert result["counts"]["attributed_orders"] == 2
    assert result["counts"]["unattributed_orders"] == 4
    assert len(result["destinations"]) == 1
    assert result["destinations"][0]["id"] == "owned-destination"
    assert result["destinations"][0]["orders"] == 2
    assert result["destinations"][0]["commission_cents"] == 200
    serialized = json.dumps(result)
    assert foreign not in serialized and "DO_NOT_LEAK" not in serialized
    assert not result["partial"]


def test_click_time_and_mcn_net_fields_do_not_change_gross_commission_or_click_counts():
    node = conversion(netCommission="0.90", mcnManagementFee="0.10", clickTime=1)
    result = aggregate([node])
    assert result["summary"]["commission_cents"] == 100
    assert result["summary"]["clicks"] is None
    assert all(row["clicks"] is None for row in result["series"])
    assert result["summary"]["validated_commission_cents"] is None


def test_malformed_conversion_cannot_fabricate_empty_successful_totals():
    result = aggregate([{"detail": "not a conversion"}])
    assert result["partial"]
    assert all(result["summary"][key] is None for key in ("orders", "items", "sales_cents", "commission_cents"))
    assert all(row["sales_cents"] is None for row in result["series"])


@pytest.mark.parametrize("options", [
    {"start": START.replace(tzinfo=None)}, {"end": START}, {"end": START - timedelta(seconds=1)},
    {"timezone": "America/Invalid"}, {"attribution": []},
])
def test_invalid_period_or_attribution_contract_is_rejected(options):
    arguments = {"start": START, "end": END, "timezone": ZONE, **options}
    with pytest.raises(ValueError):
        aggregate_conversions([], **arguments)
