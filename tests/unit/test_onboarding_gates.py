"""Capability gates stay inert for legacy and fail closed for new incomplete setup."""
from contextlib import asynccontextmanager
from datetime import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import onboarding_service as service


class DB:
    def __init__(self, row):
        self.row, self.calls = row, []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: self.row))


def product_facts():
    pro, item, variant = uuid4(), uuid4(), uuid4()
    return {"professionals": [], "services": [], "links": [], "hours": [], "breaks": [],
            "products": [{"id": item, "active": True}],
            "variants": [{"id": variant, "product_id": item, "active": True, "price_kind": "on_request", "unit_price_cents": None}]}


@pytest.mark.parametrize("capability", ["appointments", "rescheduling", "public_site", "product_orders", "assistant", "whatsapp", "google"])
@pytest.mark.asyncio
async def test_flag_off_is_noop_without_new_table_dependency(capability, monkeypatch):
    monkeypatch.delenv("ONBOARDING_ENABLED", raising=False)
    db = DB(None)
    await service.require_operational_ready(db, uuid4(), capability)
    assert not db.calls


@pytest.mark.parametrize("row", [None, {"is_new": False, "completed_at": None, "configuration": {}}])
@pytest.mark.asyncio
async def test_metadata_absent_or_legacy_never_blocks_old_operations(row, monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    db = DB(row)
    await service.require_operational_ready(db, uuid4(), "appointments")
    assert len(db.calls) == 1


@pytest.mark.parametrize("capability", ["appointments", "rescheduling", "public_site", "product_orders", "assistant", "whatsapp", "google"])
@pytest.mark.asyncio
async def test_new_company_cannot_activate_a_capability_before_completion(capability, monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    db = DB({"is_new": True, "completed_at": None, "configuration": {}})
    with pytest.raises(HTTPException) as denied:
        await service.require_operational_ready(db, uuid4(), capability)
    assert denied.value.status_code == 409


@pytest.mark.parametrize("capability,allowed", [("appointments", False), ("rescheduling", False), ("public_site", False),
    ("google", False), ("product_orders", True), ("assistant", True), ("whatsapp", True), ("catalog_answers", True)])
@pytest.mark.asyncio
async def test_products_only_uses_product_readiness_without_creating_agenda(capability, allowed, monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    async def facts(_db, _tenant): return product_facts()
    monkeypatch.setattr(service, "_facts", facts)
    db = DB({"is_new": True, "completed_at": "known", "configuration": {"activity": "products", "niche": "loja", "modality": "online"}})
    if allowed:
        await service.require_operational_ready(db, uuid4(), capability)
    else:
        with pytest.raises(HTTPException) as denied:
            await service.require_operational_ready(db, uuid4(), capability)
        assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_previous_completion_does_not_override_current_product_deactivation(monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    async def facts(_db, _tenant):
        result = product_facts()
        result["products"][0]["active"] = False
        return result
    monkeypatch.setattr(service, "_facts", facts)
    db = DB({"is_new": True, "completed_at": "known", "configuration": {"activity": "products", "niche": "loja", "modality": "online"}})
    with pytest.raises(HTTPException):
        await service.require_operational_ready(db, uuid4(), "product_orders")


@pytest.mark.asyncio
async def test_real_order_validation_calls_guard_even_without_fulfillment_selection(monkeypatch):
    from app.commercial_fulfillment import validate
    called = []
    async def deny(db, tenant, capability):
        called.append((tenant, capability))
        raise HTTPException(409, "Incomplete")
    monkeypatch.setattr(service, "require_operational_ready", deny)
    tenant = uuid4()
    with pytest.raises(HTTPException):
        await validate(DB(None), tenant, {"fulfillment_mode": None}, confirming=True)
    assert called == [(tenant, "product_orders")]


@pytest.mark.asyncio
async def test_new_booking_gate_precedes_customer_hold_or_expiration_writes(monkeypatch):
    from plan_fixture import install_plan_fixture
    install_plan_fixture(monkeypatch,"essencial")
    from app import booking_service as booking
    queries = []
    class BookingDB:
        async def execute(self, query, params):
            queries.append(str(query))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
    @asynccontextmanager
    async def tx(db, tenant): yield db
    async def professional_lock(*args): pass
    async def deny(*args): raise HTTPException(409, "Incomplete")
    monkeypatch.setattr(booking, "tenant_transaction", tx)
    monkeypatch.setattr(booking, "acquire_professional_lock", professional_lock)
    monkeypatch.setattr(service, "require_operational_ready", deny)
    from datetime import datetime, timezone
    with pytest.raises(HTTPException):
        await booking.book_appointment(BookingDB(), uuid4(), uuid4(), [uuid4()], "+5567999999901", "Sintético", datetime.now(timezone.utc), "synthetic-gate")
    assert len(queries) == 1 and "idempotency_key" in queries[0]
    assert not any("UPDATE" in q or "INSERT" in q for q in queries)
