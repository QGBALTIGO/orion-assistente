"""PostgreSQL commerce tests: real RLS, locks and constraints; no external calls."""
import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import commercial_service as service
from app.commercial_schemas import (
    Product, Variant, ReplaceStock, StockObservation, RelatedComplement,
    EstimateRequest, CreateQuote, CreateOrder, ConfirmOrder, CancelOrder,
)
from app.database import tenant_transaction
from app.tenant_access import TenantAccess

pytestmark = pytest.mark.integration
SUPPORT = TenantAccess("support")
TABLES = ("commercial_events", "commercial_orders", "commercial_quote_items", "commercial_quotes",
          "commercial_complements", "commercial_inventory", "commercial_variants", "commercial_products")


@pytest_asyncio.fixture
async def commerce():
    runtime_url, migration_url = os.getenv("APP_DATABASE_URL"), os.getenv("MIGRATION_DATABASE_URL")
    if not runtime_url or not migration_url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL URLs required")
        pytest.skip("Real PostgreSQL with migration 0011 required")
    mig = create_async_engine(migration_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    runtime = create_async_engine(runtime_url, poolclass=NullPool)
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    a, b = uuid4(), uuid4()
    try:
        async with mig.begin() as db:
            for tenant in (a, b):
                await db.execute(text("INSERT INTO tenants (id,name,slug) VALUES (:id,'Synthetic retail shop',:slug)"), {"id": tenant, "slug": f"commerce-{tenant}"})
        yield a, b, factory, mig
    finally:
        async with mig.begin() as db:
            for table in TABLES:
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": a, "b": b})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": a, "b": b})
        await runtime.dispose()
        await mig.dispose()


async def call(factory, operation, *args, **kwargs):
    async with factory() as db:
        return await operation(db, *args, **kwargs)


async def setup_item(factory, tenant, *, kind="fixed", price=1250, tracked=True, quantity=5):
    product = await call(factory, service.save_product, tenant, Product(name="Synthetic item", synonyms=["test item"], active=True), SUPPORT)
    variant = await call(factory, service.save_variant, tenant, product["id"], Variant(name="Standard", price_kind=kind,
                         unit_price_cents=price, active=True), SUPPORT)
    if tracked:
        now = service.now_utc()
        await call(factory, service.observe_stock, tenant, variant["id"], ReplaceStock(expected_revision=1,
            stock=StockObservation(mode="tracked", quantity_on_hand=quantity, observed_at=now-timedelta(seconds=1),
                                   valid_until=now+timedelta(hours=1), source="Synthetic controlled count")), SUPPORT)
    return product, variant


async def quote_order(factory, tenant, variant, *, quantity=1, contact=True):
    quote = await call(factory, service.create_quote, tenant, CreateQuote(
        items=[{"variant_id": variant["id"], "quantity": quantity}],
        contact_e164="+5511999999999" if contact else None), SUPPORT)
    order = await call(factory, service.create_order, tenant, CreateOrder(quote_id=quote["id"], idempotency_key=uuid4().hex), SUPPORT)
    return quote, order


async def confirm(factory, tenant, quote, order):
    return await call(factory, service.confirm_order, tenant, order["id"],
                      ConfirmOrder(expected_revision=order["revision"], confirmation_token=quote["confirmation_token"]), SUPPORT)


async def inventory(factory, tenant, variant_id):
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            return dict((await db.execute(text("SELECT * FROM commercial_inventory WHERE tenant_id=:t AND variant_id=:v"),
                                          {"t": tenant, "v": variant_id})).mappings().one())


@pytest.mark.asyncio
async def test_estimate_quote_draft_confirmation_and_cancellation_are_distinct(commerce):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a)
    estimate = await call(factory, service.estimate, a, EstimateRequest(items=[{"variant_id": variant["id"], "quantity": 2}]))
    assert estimate["total_cents"] == 2500
    async with factory() as db:
        async with tenant_transaction(db, a):
            assert (await db.execute(text("SELECT count(*) FROM commercial_quotes"))).scalar_one() == 0
            assert (await db.execute(text("SELECT count(*) FROM commercial_orders"))).scalar_one() == 0
    quote, order = await quote_order(factory, a, variant, quantity=2)
    assert order["status"] == "draft" and order["payment_status"] == "not_verified"
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0
    visible_quote = await call(factory, service.get_quote, a, quote["id"])
    assert "confirmation_token" not in visible_quote and "confirmation_digest" not in visible_quote
    with pytest.raises(HTTPException) as wrong:
        await call(factory, service.confirm_order, a, order["id"], ConfirmOrder(expected_revision=1, confirmation_token="x" * 43), SUPPORT)
    assert wrong.value.status_code == 403
    confirmed = await confirm(factory, a, quote, order)
    assert confirmed["status"] == "confirmed" and confirmed["payment_status"] == "not_verified"
    assert (await confirm(factory, a, quote, order))["id"] == confirmed["id"]
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 2
    with pytest.raises(HTTPException):
        now = service.now_utc()
        await call(factory, service.observe_stock, a, variant["id"], ReplaceStock(expected_revision=3,
            stock=StockObservation(mode="tracked", quantity_on_hand=1, observed_at=now,
                                   valid_until=now+timedelta(minutes=30), source="Synthetic invalid count")), SUPPORT)
    await call(factory, service.observe_stock, a, variant["id"], ReplaceStock(expected_revision=3, stock=StockObservation()), SUPPORT)
    cancelled = await call(factory, service.cancel_order, a, order["id"], CancelOrder(expected_revision=2), SUPPORT)
    assert cancelled["status"] == "cancelled" and cancelled["payment_status"] == "not_verified"
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0
    assert (await call(factory, service.cancel_order, a, order["id"], CancelOrder(expected_revision=2), SUPPORT))["status"] == "cancelled"
    with pytest.raises(HTTPException):
        await confirm(factory, a, quote, order)
    async with factory() as db:
        async with tenant_transaction(db, a):
            for table in ("appointments", "billing_events", "subscriptions"):
                assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
            details = (await db.execute(text("SELECT details::text FROM commercial_events"))).scalars().all()
            assert all(quote["confirmation_token"] not in value for value in details)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,price,tracked,contact", [
    ("fixed", 1000, False, True), ("from", 1000, True, True),
    ("on_request", None, True, True), ("custom", None, True, True),
    ("fixed", 1000, True, False),
])
async def test_missing_stock_exact_price_or_identity_prevents_confirmation(commerce, kind, price, tracked, contact):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a, kind=kind, price=price, tracked=tracked)
    quote, order = await quote_order(factory, a, variant, contact=contact)
    with pytest.raises(HTTPException) as denied:
        await confirm(factory, a, quote, order)
    assert denied.value.status_code == 409
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0
    assert (await call(factory, service.get_order, a, order["id"]))["status"] == "draft"


@pytest.mark.asyncio
async def test_price_change_requires_new_proposal_and_confirmation(commerce):
    a, _, factory, _ = commerce
    product, variant = await setup_item(factory, a)
    quote, order = await quote_order(factory, a, variant)
    await call(factory, service.save_variant, a, product["id"], Variant(name="Standard", price_kind="fixed", unit_price_cents=1500, active=True),
               SUPPORT, variant_id=variant["id"], expected_revision=1)
    with pytest.raises(HTTPException) as stale:
        await confirm(factory, a, quote, order)
    assert stale.value.status_code == 409
    assert (await call(factory, service.get_quote, a, quote["id"]))["total_cents"] == 1250
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0
    fresh_quote, fresh_order = await quote_order(factory, a, variant)
    assert fresh_quote["total_cents"] == 1500
    with pytest.raises(HTTPException):
        await confirm(factory, a, quote, fresh_order)  # Old consent cannot approve new terms.
    assert (await confirm(factory, a, fresh_quote, fresh_order))["status"] == "confirmed"


@pytest.mark.asyncio
async def test_expired_quote_or_stock_cannot_be_confirmed(commerce, monkeypatch):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a)
    quote, order = await quote_order(factory, a, variant)
    monkeypatch.setattr(service, "now_utc", lambda: quote["expires_at"] + timedelta(seconds=1))
    with pytest.raises(HTTPException):
        await confirm(factory, a, quote, order)
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0


@pytest.mark.asyncio
async def test_stock_expiry_blocks_even_an_unexpired_quote(commerce, monkeypatch):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a, tracked=False)
    now = service.now_utc()
    await call(factory, service.observe_stock, a, variant["id"], ReplaceStock(expected_revision=1,
        stock=StockObservation(mode="tracked", quantity_on_hand=3, observed_at=now-timedelta(seconds=1),
                               valid_until=now+timedelta(minutes=2), source="Short-lived count")), SUPPORT)
    quote, order = await quote_order(factory, a, variant)
    monkeypatch.setattr(service, "now_utc", lambda: now + timedelta(minutes=3))
    assert quote["expires_at"] > service.now_utc()
    with pytest.raises(HTTPException):
        await confirm(factory, a, quote, order)
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0


@pytest.mark.asyncio
async def test_stock_change_requires_new_proposal_and_prevents_stale_count(commerce):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a, tracked=False)
    quote, order = await quote_order(factory, a, variant)
    now = service.now_utc()
    observation = ReplaceStock(expected_revision=1, stock=StockObservation(mode="tracked", quantity_on_hand=3,
        observed_at=now, valid_until=now+timedelta(minutes=30), source="New verified manual count"))
    await call(factory, service.observe_stock, a, variant["id"], observation, SUPPORT)
    with pytest.raises(HTTPException):
        await call(factory, service.observe_stock, a, variant["id"], observation, SUPPORT)
    with pytest.raises(HTTPException):
        await confirm(factory, a, quote, order)
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 0


@pytest.mark.asyncio
async def test_last_unit_is_reserved_once_under_real_concurrency(commerce):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a, quantity=1)
    first_quote, first_order = await quote_order(factory, a, variant)
    second_quote, second_order = await quote_order(factory, a, variant)
    results = await asyncio.gather(confirm(factory, a, first_quote, first_order),
                                   confirm(factory, a, second_quote, second_order), return_exceptions=True)
    assert sum(isinstance(value, dict) and value["status"] == "confirmed" for value in results) == 1
    rejected = next(value for value in results if isinstance(value, HTTPException))
    assert rejected.status_code == 409
    assert (await inventory(factory, a, variant["id"]))["reserved_quantity"] == 1


@pytest.mark.asyncio
async def test_order_creation_is_idempotent_and_bound_to_one_quote(commerce):
    a, _, factory, _ = commerce
    _, variant = await setup_item(factory, a)
    quote = await call(factory, service.create_quote, a, CreateQuote(items=[{"variant_id": variant["id"], "quantity": 1}]), SUPPORT)
    payload = CreateOrder(quote_id=quote["id"], idempotency_key="same-request-123")
    orders = await asyncio.gather(*(call(factory, service.create_order, a, payload, SUPPORT) for _ in range(2)))
    assert orders[0]["id"] == orders[1]["id"]
    with pytest.raises(HTTPException):
        await call(factory, service.create_order, a, CreateOrder(quote_id=quote["id"], idempotency_key="different-key-123"), SUPPORT)
    other_quote = await call(factory, service.create_quote, a, CreateQuote(items=[{"variant_id": variant["id"], "quantity": 2}]), SUPPORT)
    with pytest.raises(HTTPException):
        await call(factory, service.create_order, a, CreateOrder(quote_id=other_quote["id"], idempotency_key="same-request-123"), SUPPORT)


@pytest.mark.asyncio
async def test_tenant_rls_composite_fks_and_immutable_quotes(commerce):
    a, b, factory, mig = commerce
    product, variant = await setup_item(factory, a)
    quote, order = await quote_order(factory, a, variant)
    for operation, item_id in ((service.get_quote, quote["id"]), (service.get_order, order["id"])):
        with pytest.raises(HTTPException) as hidden:
            await call(factory, operation, b, item_id)
        assert hidden.value.status_code == 404
    with pytest.raises(DBAPIError):
        await call(factory, service.save_variant, b, product["id"], Variant(name="Foreign"), SUPPORT)
    async with factory() as db:
        async with db.begin():
            for table in TABLES:
                assert (await db.execute(text(f"SELECT * FROM {table}"))).all() == []
    for sql in ("UPDATE commercial_quotes SET total_cents=1 WHERE tenant_id=:t",
                "DELETE FROM commercial_quote_items WHERE tenant_id=:t",
                "DELETE FROM commercial_events WHERE tenant_id=:t"):
        async with factory() as db:
            with pytest.raises(DBAPIError):
                async with tenant_transaction(db, a):
                    await db.execute(text(sql), {"t": a})
    async with mig.connect() as db:
        for table in TABLES:
            row = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:table AS regclass)"), {"table": table})).one()
            assert row == (True, True)
            assert (await db.execute(text("SELECT has_table_privilege('app_support',:table,'SELECT')"), {"table": table})).scalar_one() is False


@pytest.mark.asyncio
async def test_postgres_rejects_null_values_that_would_bypass_price_and_stock_checks(commerce):
    a, _, factory, _ = commerce
    product, variant = await setup_item(factory, a, tracked=False)
    async with factory() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, a):
                await db.execute(text("""
                    INSERT INTO commercial_variants (tenant_id,product_id,name,price_kind,unit_price_cents)
                    VALUES (:t,:p,'Invalid fixed price','fixed',NULL)
                """), {"t": a, "p": product["id"]})
    async with factory() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, a):
                await db.execute(text("""
                    UPDATE commercial_inventory SET mode='tracked',quantity_on_hand=NULL,
                        observed_at=now(),valid_until=now()+interval '1 hour',source='Invalid count'
                    WHERE tenant_id=:t AND variant_id=:v
                """), {"t": a, "v": variant["id"]})


@pytest.mark.asyncio
async def test_only_explicit_approved_active_complements_are_returned(commerce):
    a, b, factory, _ = commerce
    source, _ = await setup_item(factory, a)
    target, _ = await setup_item(factory, a)
    foreign, _ = await setup_item(factory, b)
    payload = RelatedComplement(target_product_id=target["id"], reason="Owner-approved compatible accessory")
    await call(factory, service.save_complement, a, source["id"], payload, SUPPORT)
    assert await call(factory, service.approved_complements, a, source["id"]) == []
    relation = await call(factory, service.save_complement, a, source["id"], payload.model_copy(update={"approved": True}), SUPPORT)
    related = await call(factory, service.approved_complements, a, source["id"])
    assert len(related) == 1 and related[0]["approved_relation_id"] == relation["id"]
    with pytest.raises(DBAPIError):
        await call(factory, service.save_complement, a, source["id"], RelatedComplement(target_product_id=foreign["id"], reason="Cross-tenant attempt", approved=True), SUPPORT)
