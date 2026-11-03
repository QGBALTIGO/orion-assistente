"""Cakto merchant catalog provisions only unique Pix offers with no payments."""
import os
import pytest

from app.prepaid_pix_api import PIX_NAMES,prepaid_offers
from app.plan_catalog import CODES
from scripts import provision_prepaid_pix_once as provision

FAKE_SECRET="synthetic-32-plus-character-webhook-key-fakex"


class Provider:
    configured=True
    def __init__(self):
        self.products=[]
        self.offers=[]
        self.hooks=[]
        self.posts=[]
    async def token(self):return "synthetic-oauth"
    def granted_scopes(self,needed):
        assert frozenset({"read","write","payments","products","offers","webhooks"})==needed
        return True
    async def list_offers(self):return list(self.offers)
    async def _get(self,path,**kw):
        if path=="/products/":return {"results":list(self.products),"next":None}
        if path=="/webhook/":return {"results":list(self.hooks),"next":None}
        raise AssertionError("unexpected GET "+path)
    async def fake_post(self,client,path,data):
        assert client is self
        self.posts.append((path,data))
        if path=="/products/":
            product={"id":"synthetic-product-2026","name":PIX_NAMES["essencial"],
                     "type":"unique","status":"active","paymentMethods":["pix"],
                     "offers":[]}
            offer={"id":"default-ess","name":PIX_NAMES["essencial"],"price":"29.90",
                    "product":product["id"],"status":"active","type":"unique"}
            product["offers"].append(offer)
            self.products.append(product)
            self.offers.append(offer)
            return product
        if path=="/offers/":
            code=next(c for c,n in PIX_NAMES.items() if n==data["name"])
            offer={"id":"new-"+code,"name":data["name"],"price":data["price"],
                   "product":data["product"],"status":"active","type":"unique"}
            self.offers.append(offer)
            return offer
        if path=="/webhook/":
            hook={"id":1234,"url":data["url"],"status":"active",
                  "products":[{"id":data["products"][0]}],
                  "events":[{"custom_id":code} for code in data["events"]],
                  "fields":{"secret":FAKE_SECRET}}
            self.hooks.append(hook)
            return hook
        raise AssertionError("A payment or unknown write is forbidden")


@pytest.mark.asyncio
async def test_provider_creates_single_unique_product_three_offers_and_one_webhook(monkeypatch,capsys):
    client=Provider()
    monkeypatch.setattr(provision,"CaktoClient",lambda:client)
    monkeypatch.setattr(provision,"_post",client.fake_post)
    monkeypatch.setenv("ORION_PIX_PROVISION_ONCE","1")
    monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED","0")
    await provision.provision()
    assert [path for path,_ in client.posts]==[
        "/products/","/offers/","/offers/","/webhook/"
    ]
    assert set(prepaid_offers(client.offers))==set(CODES)
    assert all(o["type"]=="unique" for o in client.offers)
    output=capsys.readouterr().out
    assert "orion_pix_catalog_and_webhook_ready:3/3" in output
    assert "orion_pix_encrypted_secret_v1:" in output
    assert FAKE_SECRET not in output
    assert not any(path=="/payments/" for path,_ in client.posts)
    # A second run sees existing resources; it never posts a duplicate.
    client.posts.clear()
    await provision.provision()
    assert client.posts==[]
    assert FAKE_SECRET not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_provisioning_rejects_missing_approval_before_any_write(monkeypatch):
    client=Provider()
    monkeypatch.setattr(provision,"CaktoClient",lambda:client)
    monkeypatch.setattr(provision,"_post",client.fake_post)
    monkeypatch.setenv("ORION_PIX_PROVISION_ONCE","1")
    monkeypatch.setenv("ORION_PIX_PREPAID_ENABLED","1")
    with pytest.raises(RuntimeError,match="must_be_disabled"):
        await provision.provision()
    assert client.posts==[]


def test_webhook_missing_any_required_event_must_not_be_accepted():
    hook={"id":3,"status":"active","url":provision.TARGET,
          "products":[{"id":"synthetic-product-2026"}],
          "events":[{"custom_id":"purchase_approved"}],
          "fields":{"secret":FAKE_SECRET}}
    with pytest.raises(RuntimeError,match="event_coverage"):
        provision._validate_webhook(hook,"synthetic-product-2026")
