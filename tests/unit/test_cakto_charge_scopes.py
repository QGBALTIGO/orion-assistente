"""A payment must be refused before any provider write when token grants are insufficient."""
import pytest
from fastapi import HTTPException
from app.orion_direct_checkout import require_verified_webhooks


class FakeCakto:
    def __init__(self,granted):
        self.granted=granted
        self.reads=0
    async def list_offers(self):
        self.reads+=1
        return []
    def granted_scopes(self,required):
        assert {"write","payments","card_tokens","subscriptions"}.issubset(required)
        return self.granted


@pytest.mark.parametrize("granted",[None,False])
@pytest.mark.asyncio
async def test_missing_credential_grants_block_provider_post(granted):
    client=FakeCakto(granted)
    with pytest.raises(HTTPException) as exc:
        await require_verified_webhooks(client)
    assert exc.value.status_code==409
    assert "autorizada" in exc.value.detail
    assert client.reads==1
