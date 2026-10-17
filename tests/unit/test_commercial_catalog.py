"""Commercial invariants without a database, network, appointments or payments."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.catalog_api import authenticate
from app.commercial_api import router
from app.commercial_schemas import Product, Variant, StockObservation, EstimateRequest, CreateQuote, ConfirmOrder
from app.commercial_service import calculate

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def line(**overrides):
    return {"product_id": uuid4(), "product_name": "Synthetic product", "product_revision": 1,
            "variant_id": uuid4(), "variant_name": "Blue", "variant_revision": 1, "attributes": {"color": "blue"},
            "price_kind": "fixed", "unit_price_cents": 1250, "quantity": 2,
            "mode": "tracked", "quantity_on_hand": 5, "reserved_quantity": 1,
            "observed_at": NOW - timedelta(minutes=5), "valid_until": NOW + timedelta(minutes=20),
            "source": "Synthetic manual count", "inventory_revision": 2, **overrides}


def test_defaults_do_not_publish_product_price_or_stock():
    assert not Product(name="Item").active
    variant = Variant(name="Standard")
    assert not variant.active and variant.price_kind == "on_request" and variant.unit_price_cents is None
    assert StockObservation().mode == "unknown" and StockObservation().quantity_on_hand is None


@pytest.mark.parametrize("payload", [
    {"name": "x", "price_kind": "fixed"}, {"name": "x", "price_kind": "from"},
    {"name": "x", "price_kind": "on_request", "unit_price_cents": 0},
    {"name": "x", "price_kind": "custom", "unit_price_cents": 100},
    {"name": "x", "price_kind": "fixed", "unit_price_cents": -1},
    {"name": "x", "tenant_id": str(uuid4())},
])
def test_price_contract_rejects_missing_or_fabricated_values(payload):
    with pytest.raises(ValidationError):
        Variant.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"mode": "unknown", "quantity_on_hand": 4}, {"mode": "tracked", "quantity_on_hand": 4},
    {"mode": "tracked", "quantity_on_hand": 4, "observed_at": NOW.replace(tzinfo=None), "valid_until": NOW + timedelta(minutes=5), "source": "count"},
    {"mode": "tracked", "quantity_on_hand": 4, "observed_at": NOW, "valid_until": NOW + timedelta(hours=25), "source": "count"},
    {"mode": "tracked", "quantity_on_hand": 4, "observed_at": NOW, "valid_until": NOW, "source": "count"},
    {"mode": "tracked", "quantity_on_hand": 4, "observed_at": NOW, "valid_until": NOW + timedelta(minutes=5), "source": " "},
])
def test_stock_requires_explicit_bounded_observation(payload):
    with pytest.raises(ValidationError):
        StockObservation.model_validate(payload)


def test_server_calculation_accounts_for_reserved_units_without_rounding():
    result = calculate([line()], NOW)
    assert result["total_cents"] == result["minimum_total_cents"] == 2500
    assert result["items"][0]["stock"]["available_quantity"] == 4
    assert result["confirmation_requirements_met"] is True


@pytest.mark.parametrize("overrides,limitation", [
    ({"mode": "unknown", "quantity_on_hand": None, "observed_at": None, "valid_until": None, "source": None}, "unknown_stock"),
    ({"valid_until": NOW}, "stale_stock"),
    ({"observed_at": NOW + timedelta(seconds=1)}, "stale_stock"),
    ({"quantity_on_hand": 2, "reserved_quantity": 1}, "insufficient_stock"),
])
def test_unknown_stale_and_insufficient_stock_never_confirm(overrides, limitation):
    result = calculate([line(**overrides)], NOW)
    assert result["confirmation_requirements_met"] is False
    assert limitation in result["limitations"]
    if limitation != "insufficient_stock":
        assert result["items"][0]["stock"]["available_quantity"] is None


@pytest.mark.parametrize("kind,price,minimum", [("from", 1000, 2000), ("on_request", None, None), ("custom", None, None)])
def test_indicative_prices_do_not_become_exact_totals(kind, price, minimum):
    result = calculate([line(price_kind=kind, unit_price_cents=price)], NOW)
    assert result["total_cents"] is None and result["minimum_total_cents"] == minimum
    assert "non_fixed_price" in result["limitations"]
    assert result["confirmation_requirements_met"] is False


def test_mixed_quote_never_exposes_partial_sum_as_full_price():
    result = calculate([line(), line(price_kind="custom", unit_price_cents=None)], NOW)
    assert result["total_cents"] is None and result["minimum_total_cents"] is None


def test_client_cannot_supply_prices_totals_payments_or_duplicate_variants():
    variant = uuid4()
    valid = {"items": [{"variant_id": variant, "quantity": 2}]}
    for override in ({"total_cents": 1}, {"payment_status": "paid"}, {"discount_percent": 90}):
        with pytest.raises(ValidationError):
            CreateQuote.model_validate({**valid, **override})
    with pytest.raises(ValidationError):
        EstimateRequest(items=valid["items"] * 2)
    with pytest.raises(ValidationError):
        ConfirmOrder(expected_revision=1, confirmation_token="not-a-real-token")


@pytest.mark.asyncio
async def test_unlinked_members_and_viewers_cannot_mutate_commerce(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    app = FastAPI()
    app.include_router(router)
    tenant = uuid4()
    base = f"/api/v1/tenants/{tenant}/commerce"
    app.dependency_overrides[authenticate] = lambda: SimpleNamespace(role="member", account_id=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(base + "/products")).status_code == 403
        assert (await client.post(base + "/products", json={"name": "Denied"})).status_code == 403
        app.dependency_overrides[authenticate] = lambda: SimpleNamespace(role="viewer", tenant_id=tenant)
        assert (await client.post(base + "/products", json={"name": "Denied"})).status_code == 403
        assert (await client.post(base + "/orders", json={"quote_id": str(uuid4()), "idempotency_key": "synthetic-123"})).status_code == 403


@pytest.mark.asyncio
async def test_commerce_is_disabled_until_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_CATALOG_ENABLED", raising=False)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate] = lambda: SimpleNamespace(role="support")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/v1/tenants/{uuid4()}/commerce/products")).status_code == 423


def test_commercial_migration_compiles_without_touching_legacy_tables():
    import importlib.util
    from io import StringIO
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0011_commercial_catalog.py"
    spec = importlib.util.spec_from_file_location("commercial_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    for legacy in ("appointments", "services", "customers", "subscriptions", "panel_accounts", "tenants"):
        assert f"ALTER TABLE {legacy} " not in sql and f"UPDATE {legacy} " not in sql
    assert "DROP TABLE" not in sql
    for table in ("commercial_products", "commercial_variants", "commercial_inventory", "commercial_complements", "commercial_quotes", "commercial_quote_items", "commercial_orders", "commercial_events"):
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    for table in ("commercial_quotes", "commercial_quote_items", "commercial_events"):
        assert f"GRANT SELECT,INSERT ON {table} TO app_runtime" in sql
