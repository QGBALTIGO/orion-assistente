"""Pure selector tests for temporary sealed provider-key synchronization."""
from datetime import datetime, timezone
import pytest

from scripts.cakto_sealed_provider_key_once import select_secret

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
OFFERS = [
    {"id": "offer1", "name": "Orion Essencial", "price": "29.90",
     "type": "subscription", "status": "active", "recurrence_period": 30,
     "product": {"id": "orion-product"}},
    {"id": "offer2", "name": "Orion Profissional", "price": "89.90",
     "type": "subscription", "status": "active", "recurrence_period": 30,
     "product": {"id": "orion-product"}},
    {"id": "offer3", "name": "Orion Elite", "price": "139.90",
     "type": "subscription", "status": "active", "recurrence_period": 30,
     "product": {"id": "orion-product"}},
]
SECRET = "never-print-provider-secret-36characters"


def item(secret=SECRET, target="https://orionagenda.pro/webhooks/cakto",
         product="orion-product", app_id=42, status="active",when="2026-11-01T18:00:00Z"):
    return {
        "sentAt": when, "url": target,
        "app": {"id": app_id, "url": target, "status": status,
                "products": [{"id": product}], "fields": {"secret": secret}}
    }


def test_unique_current_orion_app_is_accepted():
    assert select_secret(OFFERS, [item(), item()], NOW) == SECRET


@pytest.mark.parametrize("records", [
    [], [item(product="unrelated")],
    [item(target="https://evil.test/webhooks/cakto")],
    [item(status="paused")], [item(when="2025-08-10T11:00:00Z")],
    [item(secret="short")],
    [item(app_id=1), item(app_id=2)],
    [item(secret="A" * 36), item(secret="B" * 36)],
])
def test_mismatches_fail_closed(records):
    with pytest.raises(RuntimeError):
        select_secret(OFFERS, records, NOW)
