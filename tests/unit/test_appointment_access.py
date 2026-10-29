import hashlib
import secrets
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.appointment_access import AccessIdentity, bind, guard, parse_token


def test_protocol_is_never_an_access_credential():
    for value in ('AG-ABCDEF', '123', '', None, str(uuid4())+'.'+('a'*42)):
        with pytest.raises(HTTPException) as exc:
            parse_token(value)
        assert exc.value.status_code == 404


def test_token_is_independent_256_bit_secret_with_tenant_scope():
    tenant = uuid4()
    raw = str(tenant)+'.'+secrets.token_urlsafe(32)
    assert parse_token(raw) == (tenant, hashlib.sha256(raw.encode()).hexdigest())
    assert parse_token(str(uuid4())+raw[36:])[1] != parse_token(raw)[1]


@pytest.mark.asyncio
async def test_guard_is_noop_without_customer_scope():
    await guard(None, uuid4())


@pytest.mark.asyncio
async def test_cross_tenant_scope_is_rejected_before_sql(monkeypatch):
    monkeypatch.setenv('APPOINTMENT_COMMUNICATIONS_ENABLED', '1')
    with bind(AccessIdentity(uuid4(), uuid4(), uuid4(), 'a'*64)):
        with pytest.raises(HTTPException):
            await guard(None, uuid4())
