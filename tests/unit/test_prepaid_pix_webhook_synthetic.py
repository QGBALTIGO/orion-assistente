"""Synthetic Cakto V1 webhook must not generate payments."""
import pytest
from scripts.test_orion_prepaid_pix_webhook_once import _find_hook
from app.prepaid_pix_api import PIX_EVENTS,TARGET


class FakeProvider:
    def __init__(self,more=False):
        self.more=more
        self.read=[]
    async def _get(self,path,**kwargs):
        self.read.append(path)
        assert path=="/webhook/"
        hooks=[{
            "id":301, "status":"active", "url":TARGET,
            "fields":{"secret":"synthetic-prepaid-v1-key-length-36abc"},
            "products":[{"id":"synthetic-prepaid-product"}],
            "events":[{"custom_id":code} for code in PIX_EVENTS],
        }]
        if self.more:
            hooks.append({**hooks[0],"id":302})
        return {"results":hooks,"next":None}


@pytest.mark.asyncio
async def test_exact_one_prepaid_hook_is_selected_read_only(monkeypatch):
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET",
                       "synthetic-prepaid-v1-key-length-36abc")
    fake=FakeProvider()
    assert await _find_hook(fake,{"synthetic-prepaid-product"})==301
    assert fake.read==["/webhook/"]


@pytest.mark.asyncio
async def test_duplicate_or_wrong_product_hook_is_rejected(monkeypatch):
    monkeypatch.setenv("CAKTO_PIX_WEBHOOK_SECRET",
                       "synthetic-prepaid-v1-key-length-36abc")
    with pytest.raises(ValueError):
        await _find_hook(FakeProvider(more=True),{"synthetic-prepaid-product"})
    with pytest.raises(ValueError):
        await _find_hook(FakeProvider(),{"unrelated-product"})
