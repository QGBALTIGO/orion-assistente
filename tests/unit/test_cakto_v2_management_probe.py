"""V2 webhook management capability probe performs GETs only."""
import pytest
from scripts import probe_cakto_v2_management_read_only as testee


class Fake:
    def __init__(self,body):
        self.body=body
        self.requests=[]
    async def _get(self,path,**params):
        self.requests.append((path,params))
        if path=="/webhook/event_history/":
            return {"results":[{"app":{"id":123,"status":"active",
                    "url":"https://orionagenda.pro/webhooks/cakto",
                    "products":[{"id":"orion-product"}]},
                    "url":"https://orionagenda.pro/webhooks/cakto"}],"next":None}
        return self.body


@pytest.mark.asyncio
async def test_exact_existing_webhook_read_succeeds_without_write(monkeypatch):
    monkeypatch.setattr(testee,"product_reference",lambda offers:"orion-product")
    data=Fake({"id":123,"url":"https://orionagenda.pro/webhooks/cakto","status":"active"})
    result=await testee.check(data,[])
    assert result=="readable_by_public_api"
    assert len(data.requests)==2
    assert all(path.startswith("/webhook/") for path,_ in data.requests)


@pytest.mark.asyncio
async def test_wrong_webhook_target_is_never_accepted(monkeypatch):
    monkeypatch.setattr(testee,"product_reference",lambda offers:"orion-product")
    fake=Fake({"id":123,"url":"https://external.invalid/callback","status":"active"})
    assert await testee.check(fake,[])=="not_same_webhook"


@pytest.mark.asyncio
async def test_unverified_product_cannot_be_probed(monkeypatch):
    monkeypatch.setattr(testee,"product_reference",lambda offers:None)
    fake=Fake({})
    assert await testee.check(fake,[])=="product_unverified"
    assert fake.requests==[]
