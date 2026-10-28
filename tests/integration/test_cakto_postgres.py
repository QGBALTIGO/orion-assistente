"""Cakto receipts isolated with an ephemeral PostgreSQL CI schema and fake orders."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_receipts_deduplicate_without_pii_or_entitlement(monkeypatch):
    url = os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL de testes é obrigatório")
        pytest.skip("PostgreSQL não disponível")
    secret = "synthetic-webhook-secret-do-not-use-outside-tests"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    order = "test-cakto-" + uuid4().hex
    external_offer = "test-offer"
    payload = {
        "secret": secret, "event": "purchase_approved",
        "data": [{"id": order, "status": "paid", "offer_type": "main",
                  "offer": {"id": external_offer},
                  "customer": {"name": "synthetic", "docNumber": "11111111111"},
                  "card": {"lastDigits": "0000"}},
                 {"id": "test-bump-" + uuid4().hex,
                  "status": "paid", "offer_type": "orderbump"}]
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    signature = "v1=" + hmac.new(
        secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    headers = {"X-Cakto-Signature": signature, "X-Cakto-Timestamp": stamp}
    engine = create_async_engine(
        url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for _ in range(2):
                result = await client.post(
                    "/webhooks/cakto", content=raw, headers=headers
                )
                assert result.status_code == 200, result.text
        async with engine.connect() as db:
            rows = (await db.execute(text("""
                SELECT external_order_id,event_type,verification_state,
                       external_offer_id FROM cakto_webhook_events
                WHERE external_order_id=:order_id
            """), {"order_id": order})).all()
            assert len(rows) == 1
            assert tuple(rows[0]) == (order, "purchase_approved", "pending", external_offer)
            no_bump = (await db.execute(text("""
                SELECT count(*) FROM cakto_webhook_events WHERE
                external_order_id LIKE 'test-bump-%'
            """))).scalar_one()
            assert no_bump == 0
            tables = (await db.execute(text("""
                SELECT relrowsecurity,relforcerowsecurity,
                       has_table_privilege('app_support',oid,'INSERT'),
                       has_table_privilege('app_runtime',oid,'SELECT')
                FROM pg_class WHERE oid='public.cakto_webhook_events'::regclass
            """))).one()
            assert tuple(tables) == (True, True, True, False)
    finally:
        async with engine.begin() as db:
            await db.execute(text("""
                DELETE FROM cakto_webhook_events WHERE external_order_id=:order_id
            """), {"order_id": order})
        await engine.dispose()
