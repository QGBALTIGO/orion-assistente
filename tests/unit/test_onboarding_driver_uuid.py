"""Exact asyncpg UUID parity; no database, socket or provider is used here."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import random
from types import SimpleNamespace
from uuid import UUID

from asyncpg.pgproto.pgproto import UUID as DriverUUID
from fastapi.encoders import ENCODERS_BY_TYPE, jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import pytest

from app import onboarding_service as service
from test_onboarding_state_encoding import raw_catalog


def old_bytes(value):
    return json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def old_digest(value):
    return hashlib.sha256(old_bytes(value)).hexdigest()


def as_driver(value):
    if type(value) is UUID:
        return DriverUUID(str(value))
    if type(value) is dict:
        return {key: as_driver(child) for key, child in value.items()}
    if type(value) is list:
        return [as_driver(child) for child in value]
    if type(value) is tuple:
        return tuple(as_driver(child) for child in value)
    return value


def test_512_uuid_values_extremes_and_nested_mixed_facts_keep_exact_bytes_and_sha(monkeypatch):
    rng = random.Random(20261018)
    values = [0, 1, (1 << 128) - 1, 1 << 127] + [1 << bit for bit in range(128)]
    values += [rng.getrandbits(128) for _ in range(380)]
    assert len(values) == 512
    globals_before = dict(ENCODERS_BY_TYPE)
    for number in values:
        driver, standard = DriverUUID(str(UUID(int=number))), UUID(int=number)
        document = {"id": driver, "nested": [{"foreign": driver, "standard": standard}],
            "tuple": (driver, str(driver)), "price": Decimal("35.00"), "text": "ação 😃",
            "at": datetime(2030, 1, 2, tzinfo=timezone.utc), "bytes": "São Paulo".encode()}
        expected_bytes, expected_tree = old_bytes(document), jsonable_encoder(document)
        assert type(driver) is DriverUUID and jsonable_encoder(driver) == str(driver)
        assert service._digest_needs_legacy(document) is False
        with monkeypatch.context() as isolated:
            isolated.setattr(service, "jsonable_encoder", lambda value: pytest.fail("Exact driver UUID fell back"))
            assert service._digest_bytes(document) == expected_bytes
            assert service.digest(document) == hashlib.sha256(expected_bytes).hexdigest()
            actual = service._state_jsonable(document)
        assert actual == expected_tree
        assert JSONResponse(actual).body == JSONResponse(expected_tree).body
    assert ENCODERS_BY_TYPE == globals_before
    assert DriverUUID not in ENCODERS_BY_TYPE


class CustomUUID(UUID):
    def __str__(self):
        return "subclass:" + super().__str__()


class IdentifierModel(BaseModel):
    identifier: UUID = Field(alias="itemId")


@dataclass
class IdentifierRecord:
    identifier: UUID


class IdentifierEnum(Enum):
    VALUE = DriverUUID(str(UUID(int=1)))


@pytest.mark.parametrize("build", [
    lambda x: {"_sa_hidden": x, "keep": [x]},
    lambda x: {"nested": ({"_sa_hidden": x, "keep": x},)},
    lambda x: {x: "driver key", str(x): "collision"},
    lambda x: {"custom": CustomUUID(str(x)), "driver": x},
    lambda x: IdentifierModel(itemId=x),
    lambda x: IdentifierRecord(x),
    lambda x: {"set": {x, UUID(int=2)}},
])
def test_driver_values_do_not_broaden_fallback_for_keys_sa_subclasses_models_or_sets(build):
    value = build(DriverUUID(str(UUID(int=1))))
    assert service._digest_needs_legacy(value) is True
    assert service._digest_bytes(value) == old_bytes(value)
    assert service._state_jsonable(value) == jsonable_encoder(value)


def test_aliases_depth_and_cycles_keep_the_existing_fallback_contract():
    driver = DriverUUID(str(UUID(int=1)))
    shared = {"id": driver}
    aliased = {"one": shared, "two": shared}
    assert service._digest_needs_legacy(aliased) is True
    assert service._digest_bytes(aliased) == old_bytes(aliased)
    assert service._state_jsonable(aliased) == jsonable_encoder(aliased)
    deep = {"id": driver}
    for _ in range(65):
        deep = [deep]
    assert service._digest_needs_legacy(deep) is True
    assert service._digest_bytes(deep) == old_bytes(deep)
    assert service._state_jsonable(deep) == jsonable_encoder(deep)
    cycle = [driver]; cycle.append(cycle)
    with pytest.raises(RecursionError): old_bytes(cycle)
    with pytest.raises(RecursionError): service._digest_bytes(cycle)
    with pytest.raises(RecursionError): service._state_jsonable(cycle)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_driver_uuid_does_not_change_nonfinite_digest_or_http_rejection(number):
    value = {"id": DriverUUID(str(UUID(int=1))), "number": number}
    assert service._digest_bytes(value) == old_bytes(value)
    actual, expected = service._state_jsonable(value), jsonable_encoder(value)
    assert actual["id"] == expected["id"]
    assert math.isnan(actual["number"]) if math.isnan(number) else actual["number"] == expected["number"]
    with pytest.raises(ValueError): JSONResponse(expected)
    with pytest.raises(ValueError): JSONResponse(actual)


@pytest.mark.parametrize("value", [
    {"id": DriverUUID(str(UUID(int=1))), "bytes": b"\xff"},
    {DriverUUID(str(UUID(int=1))): "uuid", "a": "str", 1: "int"},
    IdentifierEnum.VALUE,
])
def test_historical_failures_remain_failures(value):
    try:
        expected = old_bytes(value)
    except Exception as error:
        with pytest.raises(type(error)) as actual:
            service._digest_bytes(value)
        assert actual.value.args == error.args
    else:
        assert service._digest_bytes(value) == expected


def test_generator_is_inspected_without_consuming_driver_values():
    def value():
        return {"ids": (DriverUUID(str(UUID(int=i))) for i in (0, 1, (1 << 128)-1))}
    assert service._digest_bytes(value()) == old_bytes(value())
    assert service._state_jsonable(value()) == jsonable_encoder(value())


@pytest.mark.parametrize("count", [113, 281, 1000])
@pytest.mark.asyncio
async def test_actual_state_assembly_with_driver_facts_keeps_http_snapshot_and_every_fingerprint(monkeypatch, count):
    facts = as_driver(raw_catalog(count))
    tenant = facts["business"]["id"]
    config = {"niche": "outro", "activity": "both", "modality": "online", "private_address": None}
    meta = {"configuration": config, "managed_ids": {}, "version": 7, "is_new": True,
        "completed_at": None, "current_step": "review"}
    class Rows:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def first(self): return self.rows[0] if self.rows else None
        def all(self): return self.rows
        def scalar_one_or_none(self): return self.rows[0] if self.rows else None
    class DB:
        async def execute(self, query, _params):
            return Rows([meta]) if "FROM business_onboarding " in str(query) else Rows([])
    async def read_facts(*args): return facts
    async def profile(*args): return {"generation": 0, "draft": None, "published": None}
    monkeypatch.setattr(service, "_facts", read_facts)
    monkeypatch.setattr(service, "_profile", profile)
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    assert service._digest_needs_legacy(facts) is False
    with monkeypatch.context() as isolated:
        isolated.setattr(service, "digest", old_digest)
        isolated.setattr(service, "_state_jsonable", jsonable_encoder)
        expected = await service._state(DB(), tenant, SimpleNamespace(role="owner"))
    with monkeypatch.context() as isolated:
        isolated.setattr(service, "jsonable_encoder", lambda value: pytest.fail("Actual driver facts fell back"))
        actual = await service._state(DB(), tenant, SimpleNamespace(role="owner"))
    assert actual == expected
    assert JSONResponse(actual).body == JSONResponse(expected).body
    assert actual["snapshot"] == expected["snapshot"]
    for key in ("professionals", "services", "products", "hours"):
        assert [item["fingerprint"] for item in actual["catalog"][key]] == [item["fingerprint"] for item in expected["catalog"][key]]
