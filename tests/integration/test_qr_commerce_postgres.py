"""Actual PG RLS, contact ownership, idempotency and customer audit roles."""
import os
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import commercial_service as commercial, qr_commerce as flow, qr_commerce_store as store
from app.commercial_schemas import Product, Variant, ReplaceVariant, ReplaceStock, StockObservation
from app.database import tenant_transaction
from app.tenant_access import TenantAccess

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def customer_commerce(monkeypatch):
    runtime_url, migration_url = os.getenv("APP_DATABASE_URL"), os.getenv("MIGRATION_DATABASE_URL")
    if not runtime_url or not migration_url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL URLs required")
        pytest.skip("Actual PostgreSQL with migration 0011 required")
    mig = create_async_engine(migration_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    runtime = create_async_engine(runtime_url, poolclass=NullPool)
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    tenant, other = uuid4(), uuid4()
    monkeypatch.setattr(store, "get_session_factory", lambda: factory)
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "only-synthetic-qr-commerce-token-" + "x" * 32)
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED", "1")
    try:
        async with mig.begin() as db:
            for ident in (tenant, other):
                await db.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:id,'Synthetic commerce',:slug)"), {"id": ident, "slug": "qr-commerce-" + ident.hex})
        async with factory() as db:
            product = await commercial.save_product(db, tenant, Product(name="Pomada", active=True), TenantAccess("support"))
            variant = await commercial.save_variant(db, tenant, product["id"], Variant(name="Pote 50g", price_kind="fixed", unit_price_cents=2500, active=True), TenantAccess("support"))
            now = commercial.now_utc()
            await commercial.observe_stock(db, tenant, variant["id"], ReplaceStock(expected_revision=1,
                stock=StockObservation(mode="tracked", quantity_on_hand=5, observed_at=now-timedelta(seconds=1),
                    valid_until=now+timedelta(hours=1), source="Synthetic known count")), TenantAccess("support"))
        scope = store.CustomerScope(tenant, "5567999999901", "initial-message")
        yield tenant, other, factory, mig, scope, product, variant
    finally:
        async with mig.begin() as db:
            for table in ("commercial_events", "commercial_orders", "commercial_quote_items", "commercial_quotes", "commercial_complements", "commercial_inventory", "commercial_variants", "commercial_products"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": tenant, "b": other})
            await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant, "b": other})
        await runtime.dispose()
        await mig.dispose()


@pytest.mark.asyncio
async def test_customer_contact_ownership_and_message_idempotency(customer_commerce):
    tenant, other, factory, _, scope, _, variant = customer_commerce
    items = [{"variant_id": variant["id"], "quantity": 2}]
    quote = await store.create_quote(scope, items)
    repeated = await store.create_quote(scope, items)
    assert repeated["id"] == quote["id"] and repeated["confirmation_token"] == quote["confirmation_token"]
    order = await store.create_order(scope, quote["id"])
    assert (await store.create_order(scope, quote["id"]))["id"] == order["id"]
    assert "confirmation_token" not in await store.get_quote(scope, quote["id"])
    for stranger in (store.CustomerScope(tenant, "5567999999902", "new-message"),
                     store.CustomerScope(other, scope.phone, "new-message")):
        for operation in (
            lambda: store.get_quote(stranger, quote["id"]),
            lambda: store.get_order(stranger, order["id"]),
            lambda: store.create_order(stranger, quote["id"]),
            lambda: store.confirm_order(stranger, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id="initial-message"),
            lambda: store.cancel_order(stranger, order["id"], revision=1, summary_message_id="initial-message"),
        ):
            with pytest.raises(HTTPException) as denied:
                await operation()
            assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as same_turn:
        await store.confirm_order(scope, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id=scope.message_id)
    assert same_turn.value.status_code == 409
    accepted = store.CustomerScope(tenant, scope.phone, "fresh-acceptance")
    done = await store.confirm_order(accepted, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id=scope.message_id)
    assert done["status"] == "confirmed" and done["payment_status"] == "not_verified"
    await store.confirm_order(accepted, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id=scope.message_id)
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            assert (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"), {"id": variant["id"]})).scalar_one() == 2
            assert (await db.execute(text("SELECT count(*) FROM commercial_quotes"))).scalar_one() == 1
            assert (await db.execute(text("SELECT count(*) FROM commercial_orders"))).scalar_one() == 1
            assert (await db.execute(text("SELECT count(*) FROM commercial_events WHERE actor_role='customer' AND action='customer_confirmed'"))).scalar_one() == 1
    cancelled = store.CustomerScope(tenant, scope.phone, "cancel-acceptance")
    result = await store.cancel_order(cancelled, order["id"], revision=done["revision"], summary_message_id="cancel-summary")
    assert result["status"] == "cancelled"
    await store.cancel_order(cancelled, order["id"], revision=done["revision"], summary_message_id="cancel-summary")
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            assert (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"), {"id": variant["id"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_price_changes_and_unknown_stock_block_customer_confirmation(customer_commerce):
    tenant, _, factory, _, scope, product, variant = customer_commerce
    quote = await store.create_quote(scope, [{"variant_id": variant["id"], "quantity": 1}])
    order = await store.create_order(scope, quote["id"])
    async with factory() as db:
        await commercial.save_variant(db, tenant, product["id"], Variant(name="Pote 50g", price_kind="fixed", unit_price_cents=3000, active=True),
            TenantAccess("support"), variant_id=variant["id"], expected_revision=1)
    accepted = store.CustomerScope(tenant, scope.phone, "new-acceptance")
    with pytest.raises(HTTPException) as stale:
        await store.confirm_order(accepted, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id=scope.message_id)
    assert stale.value.status_code == 409
    async with factory() as db:
        await commercial.observe_stock(db, tenant, variant["id"], ReplaceStock(expected_revision=2, stock=StockObservation()), TenantAccess("support"))
    newer = store.CustomerScope(tenant, scope.phone, "new-quote")
    second = await store.create_quote(newer, [{"variant_id": variant["id"], "quantity": 1}])
    second_order = await store.create_order(newer, second["id"])
    with pytest.raises(HTTPException) as unknown:
        await store.confirm_order(accepted, second_order["id"], revision=1, token=second["confirmation_token"], summary_message_id=newer.message_id)
    assert unknown.value.status_code == 409


@pytest.mark.asyncio
async def test_actual_preview_writes_no_quotes_orders_or_stock(customer_commerce):
    tenant, _, factory, _, scope, _, variant = customer_commerce
    first = await flow.handle(tenant, scope.phone, "quero comprar duas pomada", message_id="preview-1", preview=True, allow_orders=True)
    result = await flow.handle(tenant, scope.phone, "CONFIRMO", message_id="preview-2", state=first["state"], preview=True, allow_orders=True)
    assert result["step"] == "commerce_preview_complete"
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            assert (await db.execute(text("SELECT count(*) FROM commercial_quotes"))).scalar_one() == 0
            assert (await db.execute(text("SELECT count(*) FROM commercial_orders"))).scalar_one() == 0
            assert (await db.execute(text("SELECT count(*) FROM commercial_events WHERE actor_role='customer'"))).scalar_one() == 0
            assert (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"), {"id": variant["id"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_summary_validation_inside_transaction_blocks_all_mutation_kinds(customer_commerce):
    tenant, _, factory, _, scope, _, variant = customer_commerce
    items=[{"variant_id":variant["id"],"quantity":1}]
    def refuse(_):
        raise flow.SummaryTooLong("Synthetic presentation cap")
    with pytest.raises(flow.SummaryTooLong):
        await store.create_quote(scope,items,validate_summary=refuse)
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            assert (await db.execute(text("SELECT count(*) FROM commercial_quotes"))).scalar_one()==0
    quote=await store.create_quote(scope,items)
    with pytest.raises(flow.SummaryTooLong):
        await store.create_order(scope,quote["id"],validate_summary=refuse)
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            assert (await db.execute(text("SELECT count(*) FROM commercial_orders"))).scalar_one()==0
    order=await store.create_order(scope,quote["id"])
    fresh=store.CustomerScope(tenant,scope.phone,"new-consent")
    with pytest.raises(flow.SummaryTooLong):
        await store.confirm_order(fresh,order["id"],revision=1,token=quote["confirmation_token"],summary_message_id=scope.message_id,validate_summary=refuse)
    with pytest.raises(flow.SummaryTooLong):
        await store.cancel_order(fresh,order["id"],revision=1,summary_message_id=scope.message_id,validate_summary=refuse)
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            assert (await db.execute(text("SELECT status FROM commercial_orders"))).scalar_one()=="draft"
            assert (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"),{"id":variant["id"]})).scalar_one()==0
            assert (await db.execute(text("SELECT count(*) FROM commercial_events WHERE action IN ('customer_confirmed','customer_cancelled')"))).scalar_one()==0


@pytest.mark.asyncio
async def test_independent_information_reads_are_scoped_and_never_create_commerce(customer_commerce):
    from app.qr_commerce_information import readonly_information

    tenant, other, factory, _, scope, _, variant = customer_commerce
    profile = {"capabilities": ["catalog_answers"]}
    answer = await readonly_information(scope, "Vocês vendem pomada?", kind="catalog", profile=profile)
    assert "Pomada" in answer and "R$" not in answer
    stock = await readonly_information(scope, "Tem pomada em estoque?", kind="stock", profile=profile)
    assert "5 unidade(s) livre(s)" in stock and "não reserva" in stock
    stranger = store.CustomerScope(other, scope.phone, "other-tenant-read")
    assert "Não encontrei esse produto cadastrado" in await readonly_information(stranger, "Vendem pomada?", kind="catalog", profile=profile)
    assert await store.stock_information(stranger, [variant["id"]]) == {}
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            for table in ("commercial_quotes", "commercial_orders"):
                assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
            assert (await db.execute(text("SELECT count(*) FROM commercial_events WHERE actor_role='customer'"))).scalar_one() == 0
            assert (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"), {"id": variant["id"]})).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "order", "confirm", "cancel"])
async def test_newer_ingress_blocks_commerce_before_first_write(customer_commerce, monkeypatch, operation):
    from app import qr_intake_revision as intake

    tenant, _, factory, _, scope, _, variant = customer_commerce
    items = [{"variant_id": variant["id"], "quantity": 2}]
    quote = await store.create_quote(scope, items) if operation != "quote" else None
    order = await store.create_order(scope, quote["id"]) if operation in {"confirm", "cancel"} else None
    current = store.CustomerScope(tenant, scope.phone, "new-confirmation")
    if operation == "cancel":
        order = await store.confirm_order(current, order["id"], revision=1, token=quote["confirmation_token"], summary_message_id=scope.message_id)
    current = store.CustomerScope(tenant, scope.phone, "interrupted-message")

    async def counts():
        async with factory() as db:
            async with tenant_transaction(db, tenant):
                return tuple([(await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                    for table in ("commercial_quotes", "commercial_quote_items", "commercial_orders", "commercial_events")]) + (
                    (await db.execute(text("SELECT reserved_quantity FROM commercial_inventory WHERE variant_id=:id"), {"id": variant["id"]})).scalar_one(),
                    (await db.execute(text("SELECT status FROM commercial_orders WHERE tenant_id=:t"), {"t": tenant})).scalar_one_or_none())

    before = await counts()
    calls = []
    class NewerRevision:
        async def eval(self, *args):
            calls.append(args)
            return 0
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "1")
    token = intake.bind(tenant, scope.phone, [current.message_id], 1, NewerRevision())
    try:
        with pytest.raises(intake.Superseded) as superseded:
            if operation == "quote":
                await store.create_quote(current, items)
            elif operation == "order":
                await store.create_order(current, quote["id"])
            elif operation == "confirm":
                await store.confirm_order(current, order["id"], revision=order["revision"], token=quote["confirmation_token"], summary_message_id=scope.message_id)
            else:
                await store.cancel_order(current, order["id"], revision=order["revision"], summary_message_id=scope.message_id)
        assert superseded.value.not_committed is True
    finally:
        intake.reset(token)
    assert len(calls) == 1
    assert await counts() == before
