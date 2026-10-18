"""Final state encoding keeps the original values, HTTP bytes and failures."""
from collections import deque, namedtuple
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import Path
import random
import struct
from types import SimpleNamespace
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, RootModel
import pytest

from app import onboarding_service as service


def same_tree(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            same_tree(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            same_tree(left, right)
    elif isinstance(expected, float):
        # Includes signed zero and the sign/payload of nonfinite floats.
        assert struct.pack("!d", actual) == struct.pack("!d", expected)
    else:
        assert actual == expected


def assert_parity(value):
    expected = jsonable_encoder(value)
    actual = service._state_jsonable(value)
    same_tree(actual, expected)
    try:
        response = JSONResponse(expected)
    except (TypeError, ValueError, OverflowError) as error:
        with pytest.raises(type(error)) as raised:
            JSONResponse(actual)
        assert raised.value.args == error.args
    else:
        converted = JSONResponse(actual)
        assert converted.body == response.body
        assert converted.raw_headers == response.raw_headers


class Status(Enum):
    ACTIVE = "active"


class Number(IntEnum):
    ONE = 1


class DifferentPrimitive(int, Enum):
    def __new__(cls, number, label):
        obj = int.__new__(cls, number)
        obj._value_ = label
        return obj
    NAMED = (7, "seven")


class ComplexValue(Enum):
    DECIMAL = Decimal("1.5")
    DICT = {"_sa_untouched_enum_value": 7}


@dataclass
class Record:
    identifier: UUID
    happened_at: datetime
    _sa_internal: str = "discard"


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    identifier: UUID = Field(alias="itemId")
    amount: Decimal
    internal: str = Field(alias="_sa_internal", default="discard")


class String(str): pass
class Integer(int): pass
class Mapping(dict): pass
class Values(list): pass


@pytest.mark.parametrize("value", [
    None, True, False, 0, -12, 0.125, -0.0, float("nan"), -float("nan"),
    float("inf"), -float("inf"), "São Paulo — ação 😃\n\"\\", "\ud800",
    UUID(int=7), date(2030, 1, 2), datetime(2030, 1, 2, 3, 4, 5, 6789),
    datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-4))),
    time(3, 4, 5, 6789), time(3, 4, tzinfo=timezone.utc), timedelta(days=-2, seconds=12, microseconds=340),
    Decimal("3500"), Decimal("3.500"), Decimal("1E+3"), Decimal("0.000001"), Decimal("-0.0"),
    Decimal("NaN"), Decimal("Infinity"), {"nested": [Decimal("-Infinity")]},
    b"", "ação".encode(), Status.ACTIVE, Number.ONE, DifferentPrimitive.NAMED, ComplexValue.DICT,
    ComplexValue.DECIMAL, {"nested": ComplexValue.DECIMAL},
    {"items": [UUID(int=1), date(2030, 1, 2), Decimal("35.00"), b"value"]},
    {"a": {"_sa_instance_state": "discard", "keep": 1}},
    {"a": [{"_sa": {"secret": "discard"}, "keep": 2}]},
    {"a": ({"_sa_other": 3, "nested": [{"_sa": 4, "keep": 5}]},)},
    {UUID(int=1): "uuid key"}, {UUID(int=1): 1, str(UUID(int=1)): 2},
    {True: "boolean key", False: "other boolean"}, {3: "integer", 1: "first"},
    Record(UUID(int=3), datetime(2030, 1, 2, tzinfo=timezone.utc)),
    Model(identifier=UUID(int=4), amount=Decimal("35.00")),
    RootModel[dict]({"_sa": "discard", "keep": [UUID(int=8)]}),
    {"kind": String("special"), "number": Integer(7)}, Mapping({"_sa_hidden": 1, "value": 2}),
    Values([UUID(int=1), {"_sa": 2, "keep": 3}]), {1, 3, 5},
    frozenset({UUID(int=1), UUID(int=2)}), deque([UUID(int=1), {"_sa": 7, "keep": 9}]),
    namedtuple("Pair", "first second")(UUID(int=1), {"_sa": 2, "keep": 3}), Path("synthetic/path"),
])
def test_scalars_containers_aliases_and_http_representation_match_original(value):
    assert_parity(value)


@pytest.mark.parametrize("value", [
    {"bad": b"\xff"}, Decimal("sNaN"), {(1, 2): "tuple key"},
])
def test_original_encoding_errors_remain_unchanged(value):
    with pytest.raises(Exception) as expected:
        jsonable_encoder(value)
    with pytest.raises(type(expected.value)) as actual:
        service._state_jsonable(value)
    assert actual.value.args == expected.value.args


def generated_documents():
    rng = random.Random(20261018)
    def leaf():
        return rng.choice([None, True, False, rng.randrange(-10000, 10000), rng.random(),
            "ação😃", UUID(int=rng.randrange(1, 100000)), Decimal(str(rng.randrange(1000)) + ".25"),
            datetime(2030, 1, 2, 12, tzinfo=timezone.utc), date(2030, 1, 2), time(9, 30), b"synthetic"])
    def node(depth):
        if not depth or rng.randrange(3) == 0:
            return leaf()
        if rng.choice([True, False]):
            result = {"field-"+str(i): node(depth-1) for i in range(rng.randrange(1, 5))}
            if rng.randrange(5) == 0:
                result["_sa_hidden"] = {"do not retain": leaf()}
            return result
        values = [node(depth-1) for _ in range(rng.randrange(1, 5))]
        return tuple(values) if rng.choice([True, False]) else values
    return [node(4) for _ in range(40)]


@pytest.mark.parametrize("value", generated_documents())
def test_nested_documents_preserve_values_key_order_and_exact_http_bytes(value):
    assert_parity(value)


def test_regular_values_skip_original_encoder_without_mutating_input(monkeypatch):
    value = {"id": UUID(int=1), "amount": Decimal("35.00"), "at": datetime(2030, 1, 2, tzinfo=timezone.utc),
        "day": date(2030, 1, 2), "hours": [time(9), time(18)], "metadata": {"first": None, "second": "ação"}}
    before = deepcopy(value)
    monkeypatch.setattr(service, "jsonable_encoder", lambda value: pytest.fail("Unnecessary original tree walk"))
    assert_parity(value)
    assert value == before


@pytest.mark.parametrize("factory", [
    lambda: {"_sa_private": "discard", "keep": 1},
    lambda: Mapping({"value": 1}),
    lambda: Model(identifier=UUID(int=3), amount=Decimal("35.00")),
    lambda: {"value": Status.ACTIVE},
    lambda: {"value": -float("nan")},
    lambda: {"value": float("inf")},
    lambda: {"value": 10**5000},
])
def test_complex_or_native_rejected_values_use_whole_original_once(monkeypatch, factory):
    value = factory()
    calls = []
    def observe(item):
        calls.append(item)
        return jsonable_encoder(item)
    monkeypatch.setattr(service, "jsonable_encoder", observe)
    assert_parity(value)
    assert len(calls) == 1 and calls[0] is value


def test_shared_containers_keep_original_whole_walk(monkeypatch):
    shared = {"id": UUID(int=1)}
    value = {"one": shared, "two": shared}
    calls = []
    def observe(item):
        calls.append(item)
        return jsonable_encoder(item)
    monkeypatch.setattr(service, "jsonable_encoder", observe)
    assert_parity(value)
    assert len(calls) == 1 and calls[0] is value


def test_cycles_preserve_original_recursion_failure():
    value = []; value.append(value)
    with pytest.raises(RecursionError):
        jsonable_encoder(value)
    with pytest.raises(RecursionError):
        service._state_jsonable(value)


@pytest.mark.parametrize("fails", [False, True])
def test_iterator_is_consumed_once_and_never_by_an_abandoned_native_attempt(monkeypatch, fails):
    visits = []
    def values():
        visits.append("first")
        yield UUID(int=1)
        visits.append("second")
        if fails:
            raise RuntimeError("synthetic iterator failure")
        yield {"_sa": "discard", "keep": Decimal("1.25")}
    value = {"values": values()}
    calls = []
    def observe(item):
        calls.append(item)
        return jsonable_encoder(item)
    monkeypatch.setattr(service, "jsonable_encoder", observe)
    if fails:
        with pytest.raises(RuntimeError, match="synthetic iterator failure"):
            service._state_jsonable(value)
    else:
        assert service._state_jsonable(value) == {"values": [str(UUID(int=1)), {"keep": 1.25}]}
    assert visits == ["first", "second"]
    assert len(calls) == 1 and calls[0] is value


def raw_catalog(count):
    tenant, item = UUID(int=1), UUID(int=2)
    now = datetime(2030, 1, 2, 3, 4, 5, 6789, tzinfo=timezone.utc)
    pros = [{"id": UUID(int=100+i), "tenant_id": tenant, "name": "Pessoa sintética " + str(i),
        "commission_percent": Decimal("12.50"), "active": True, "accepts_appointments": True,
        "created_at": now, "image_url": None} for i in range(count)]
    hours = [{"id": UUID(int=10000+i), "tenant_id": tenant, "professional_id": pro["id"],
        "weekday": 0, "start_time": time(9), "end_time": time(18)} for i, pro in enumerate(pros)]
    return {"business": {"id": tenant, "name": "Empresa sintética", "timezone": "America/Cuiaba", "status": "active"},
        "professionals": pros, "services": [{"id": item, "tenant_id": tenant, "name": "Serviço", "price_cents": 3500,
            "duration_min": 40, "prep_min": 5, "cleanup_min": 10, "active": True}],
        "links": [{"id": UUID(int=20000+i), "tenant_id": tenant, "professional_id": pro["id"], "service_id": item} for i, pro in enumerate(pros)],
        "hours": hours, "breaks": [],
        "products": [{"id": UUID(int=3), "tenant_id": tenant, "name": "Produto sintético", "active": True}],
        "variants": [{"id": UUID(int=4), "tenant_id": tenant, "product_id": UUID(int=3),
            "active": True, "price_kind": "on_request", "unit_price_cents": None, "attributes": {"size": "250 ml"}}]}


@pytest.mark.parametrize("count", [113, 200, 281, 1000])
@pytest.mark.asyncio
async def test_actual_state_values_fingerprints_snapshot_and_http_bytes_are_identical(monkeypatch, count):
    """Run actual state assembly; synthetic SQL results do not claim PG proof."""
    facts = raw_catalog(count)
    before = deepcopy(facts)
    tenant = facts["business"]["id"]
    configuration = {"niche": "outro", "activity": "both", "modality": "online", "private_address": None}
    meta = {"configuration": configuration, "managed_ids": {}, "version": 7, "is_new": True,
        "completed_at": None, "current_step": "review"}
    operations = [{"key": "synthetic-operation", "step": "channels", "version": 7,
        "result": {"generation": 2, "version_id": str(UUID(int=5))}}]
    class Rows:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def first(self): return self.rows[0] if self.rows else None
        def all(self): return self.rows
        def scalar_one_or_none(self): return self.rows[0] if self.rows else None
    class Database:
        async def execute(self, query, _params):
            sql = str(query)
            if "FROM business_onboarding_operations" in sql: return Rows(operations)
            if "FROM business_onboarding " in sql: return Rows([meta])
            return Rows([])
    async def read_facts(*args): return facts
    async def read_profile(*args):
        return {"generation": 2, "draft": {"faqs": [{"question": "Preço?", "answer": "Sob consulta."}],
            "style_rules": {"tone": "warm"}, "capabilities": ["catalog_answers"]}, "published": None}
    monkeypatch.setattr(service, "_facts", read_facts)
    monkeypatch.setattr(service, "_profile", read_profile)
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    native = service._state_jsonable
    monkeypatch.setattr(service, "_state_jsonable", jsonable_encoder)
    expected = await service._state(Database(), tenant, SimpleNamespace(role="owner"))
    monkeypatch.setattr(service, "_state_jsonable", native)
    actual = await service._state(Database(), tenant, SimpleNamespace(role="owner"))
    same_tree(actual, expected)
    assert JSONResponse(actual).body == JSONResponse(expected).body
    assert actual["snapshot"] == expected["snapshot"]
    assert actual["catalog"]["professionals"][0]["commission_percent"] == 12.5
    assert actual["catalog"]["hours"][0]["start_time"] == "09:00:00"
    assert actual["catalog"]["products"][0]["variants"][0]["unit_price_cents"] is None
    assert actual["operations"] == operations
    assert not actual["pending"]
    assert facts == before
