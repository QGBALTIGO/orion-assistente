"""Admin Cakto connection must recognize dashboard-created V2 webhooks."""
from datetime import datetime, timezone
import pytest

from app import cakto_api
from scripts.probe_orion_webhooks_read_only import REQUIRED_EVENTS

OFFERS=[
    {"id":"offer_ess","name":"Orion Essencial","price":"29.90",
     "status":"active","type":"subscription","recurrence_period":30,
     "product":{"id":"product-orion"}},
    {"id":"offer_pro","name":"Orion Profissional","price":"89.90",
     "status":"active","type":"subscription","recurrence_period":30,
     "product":{"id":"product-orion"}},
    {"id":"offer_elite","name":"Orion Elite","price":"139.90",
     "status":"active","type":"subscription","recurrence_period":30,
     "product":{"id":"product-orion"}},
]
SECRET="synthetic-orion-test-secret-with-length-48"


class Provider:
    configured=True

    def __init__(self,good: bool):
        self.good=good

    async def list_offers(self):
        return OFFERS

    async def _get(self,path,**params):
        if path=="/webhook/":
            return {"results":[],"next":None}
        if path!="/webhook/event_history/":
            raise AssertionError(path)
        url="https://orionagenda.pro/webhooks/cakto"
        records=[]
        for event in REQUIRED_EVENTS:
            records.append({
                "url":url,
                "event_status":200 if self.good else 401,
                "event_id":event,
                "sentAt":datetime.now(timezone.utc).isoformat(),
                "payload":{"secret":SECRET,"event":event},
                "app":{"id":314,"url":url,"status":"active",
                       "products":[{"id":"product-orion"}],
                       "fields":{"secret":SECRET}},
            })
        return {"results":records,"next":None}


@pytest.mark.asyncio
async def test_dashboard_v2_history_is_accepted_without_inventory(monkeypatch):
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",SECRET)
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda: Provider(True))
    monkeypatch.setattr(cakto_api,"support_only",lambda _:None)
    monkeypatch.setattr(cakto_api,"direct_environment_ready",lambda:False)
    status=await cakto_api.admin_cakto_connection(object())
    assert status["api_connected"] is True
    assert status["webhook_offer_coverage"] == 3
    assert status["webhook_status"] == "registered"
    assert status["webhook_source"] == "verified_v2_history"
    assert status["billing_ready"] is False
    assert SECRET not in str(status)


@pytest.mark.asyncio
async def test_failed_v2_deliveries_do_not_signal_ready(monkeypatch):
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET",SECRET)
    monkeypatch.setattr(cakto_api,"cached_cakto_client",lambda: Provider(False))
    monkeypatch.setattr(cakto_api,"support_only",lambda _:None)
    monkeypatch.setattr(cakto_api,"direct_environment_ready",lambda:False)
    status=await cakto_api.admin_cakto_connection(object())
    assert status["webhook_offer_coverage"] == 0
    assert status["webhook_status"] == "incomplete"
    assert status["billing_ready"] is False
