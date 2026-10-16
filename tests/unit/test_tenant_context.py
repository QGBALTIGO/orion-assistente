from contextlib import suppress
from uuid import uuid4

import pytest

from app.database import tenant_transaction


@pytest.mark.asyncio
async def test_reject_untrusted_or_absent_tenant_id() -> None:
    for invalid in [None, "qualquer-string", "", 123]:
        with pytest.raises(ValueError):
            async with tenant_transaction(None, invalid):  # type: ignore[arg-type]
                pass


@pytest.mark.asyncio
async def test_transaction_requires_real_session() -> None:
    # Um UUID não substitui uma sessão autenticada e aberta no servidor.
    with pytest.raises(AttributeError):
        async with tenant_transaction(None, uuid4()):  # type: ignore[arg-type]
            pass
