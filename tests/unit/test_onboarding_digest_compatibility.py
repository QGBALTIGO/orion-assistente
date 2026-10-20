"""Historical bytes are the contract, not merely equivalent JSON values."""
from collections import deque, namedtuple
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum
import hashlib
import json
from pathlib import Path
import random
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, RootModel
import pytest

from app import onboarding_service as service


def original_bytes(value):
    # Exact pre-optimization representation, independent of service helpers.
    return json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def assert_compatible(value):
    expected = original_bytes(value)
    assert service._digest_bytes(value) == expected
    assert service.digest(value) == hashlib.sha256(expected).hexdigest()


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
    _sa_internal: str = "not part of historical JSON"


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    identifier: UUID = Field(alias="itemId")
    amount: Decimal
    internal: str = Field(alias="_sa_internal", default="not part of historical JSON")


class String(str): pass
class Integer(int): pass
class Mapping(dict): pass


@pytest.mark.parametrize("value", [
    None, True, 0, -12, 0.125, float("nan"), float("inf"), "São Paulo — ação 😃\n\"\\",
    UUID(int=7), date(2030, 1, 2), datetime(2030, 1, 2, 3, 4, 5, 6789),
    datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-4))),
    time(3, 4, 5, 6789), time(3, 4, tzinfo=timezone.utc), timedelta(days=-2, seconds=12, microseconds=340),
    Decimal("3500"), Decimal("3.500"), Decimal("1E+3"), Decimal("0.000001"), Decimal("-0.0"), Decimal("NaN"),
    b"", "ação".encode(), Status.ACTIVE, Number.ONE, DifferentPrimitive.NAMED, ComplexValue.DICT,
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
    {1, 3, 5}, frozenset({UUID(int=1), UUID(int=2)}), deque([UUID(int=1), {"_sa": 7, "keep": 9}]),
    namedtuple("Pair", "first second")(UUID(int=1), {"_sa": 2, "keep": 3}), Path("synthetic/path"),
])
def test_supported_values_have_exact_historical_bytes(value):
    assert_compatible(value)


def generated_documents():
    rng = random.Random(20261018)
    def leaf():
        return rng.choice([None, True, False, rng.randrange(-10000, 10000), rng.random(),
            "ação😃", UUID(int=rng.randrange(1, 100000)), Decimal(str(rng.randrange(1000)) + ".25"),
            datetime(2030, 1, 2, 12, tzinfo=timezone.utc), date(2030, 1, 2), time(9, 30), b"synthetic"])
    def node(depth):
        if not depth or rng.randrange(3) == 0: return leaf()
        if rng.choice([True, False]):
            result = {"field-"+str(i): node(depth-1) for i in range(rng.randrange(1, 5))}
            if rng.randrange(5) == 0: result["_sa_hidden"] = {"do not retain": leaf()}
            return result
        values = [node(depth-1) for _ in range(rng.randrange(1, 5))]
        return tuple(values) if rng.choice([True, False]) else values
    return [node(4) for _ in range(80)]


@pytest.mark.parametrize("value", generated_documents(), ids=lambda _value: None)
def test_80_nested_documents_preserve_canonical_bytes(value):
    assert_compatible(value)


@pytest.mark.parametrize("factory", [
    lambda: (value for value in [UUID(int=1), {"_sa": 2, "keep": 3}]),
    lambda: {"values": (value for value in [1, Decimal("1.25"), {"_sa": 4, "keep": 5}])},
])
def test_generators_are_never_consumed_by_a_failed_fast_attempt(factory):
    assert service._digest_bytes(factory()) == original_bytes(factory())


@pytest.mark.parametrize("value", [{1: "integer", "a": "string"}, {(1, 2): "tuple key"},
    {"bad": b"\xff"}, ComplexValue.DECIMAL, {"nested": ComplexValue.DECIMAL}])
def test_originally_rejected_values_keep_the_original_exception(value):
    with pytest.raises(Exception) as expected: original_bytes(value)
    with pytest.raises(type(expected.value)) as actual: service._digest_bytes(value)
    assert actual.value.args == expected.value.args


def test_aliases_and_cycles_take_whole_original_path_without_hanging():
    shared = {"id": UUID(int=1), "_sa": "discard"}
    assert_compatible({"one": shared, "two": shared})
    cyclic = []; cyclic.append(cyclic)
    with pytest.raises(RecursionError): original_bytes(cyclic)
    with pytest.raises(RecursionError): service._digest_bytes(cyclic)


def test_common_catalog_values_skip_jsonable_walk_but_sa_keeps_whole_legacy_path(monkeypatch):
    original = service.jsonable_encoder; calls = []
    def observed(value): calls.append(value); return original(value)
    monkeypatch.setattr(service, "jsonable_encoder", observed)
    ordinary = {"id": UUID(int=1), "price": Decimal("35.00"), "day": date(2030, 1, 2), "hours": [time(9), time(18)]}
    assert_compatible(ordinary); assert not calls
    special = {"a": [{"nested": ({"_sa_private": 1, "keep": 2},)}]}
    assert_compatible(special)
    assert calls and all(value is special for value in calls)


def catalog(count):
    tenant, item = UUID(int=1), UUID(int=2)
    now = datetime(2030, 1, 2, 3, 4, 5, 6789, tzinfo=timezone.utc)
    pros = [{"id": UUID(int=100+i), "tenant_id": tenant, "name": "Pessoa sintética " + str(i),
        "active": True, "accepts_appointments": True, "created_at": now} for i in range(count)]
    hours = [{"id": UUID(int=10000+i), "tenant_id": tenant, "professional_id": pro["id"],
        "weekday": 0, "start_time": time(9), "end_time": time(18)} for i, pro in enumerate(pros)]
    return {"business": {"id": tenant, "name": "Empresa sintética", "timezone": "America/Cuiaba", "status": "active"},
        "professionals": pros, "services": [{"id": item, "tenant_id": tenant, "name": "Serviço", "price_cents": 3500,
            "duration_min": 40, "prep_min": 5, "cleanup_min": 10, "active": True}],
        "links": [{"id": UUID(int=20000+i), "tenant_id": tenant, "professional_id": pro["id"], "service_id": item} for i, pro in enumerate(pros)],
        "hours": hours, "breaks": [{"id": UUID(int=30000+i), "tenant_id": tenant, "working_hour_id": hour["id"],
            "start_time": time(12), "end_time": time(13)} for i, hour in enumerate(hours)], "products": [], "variants": []}


@pytest.mark.parametrize("count", [200, 281, 1000])
def test_all_entity_fingerprints_and_full_snapshot_keep_historical_hashes(count):
    facts = catalog(count); configuration = {"niche": "outro", "activity": "services", "modality": "online"}; managed = {}
    before = deepcopy(facts)
    for key, table in [("professionals", "professionals"), ("services", "services"), ("hours", "working_hours")]:
        for row in facts[key]:
            related = {}
            if table == "services": related["professional_ids"] = sorted(str(x["professional_id"]) for x in facts["links"] if x["service_id"] == row["id"])
            elif table == "working_hours": related["breaks"] = [b for b in facts["breaks"] if b["working_hour_id"] == row["id"]]
            expected = hashlib.sha256(original_bytes({"row": row, **related})).hexdigest()
            assert service._entity_fingerprint(facts, table, row) == expected
    expected = hashlib.sha256(original_bytes({"configuration": {"private_address": None, **configuration}, "managed_ids": managed, "facts": facts})).hexdigest()
    assert service._snapshot(configuration, managed, facts) == expected
    assert facts == before


GOLDEN_OPERATION = {"actor": UUID(int=1), "step": "location", "data": {"modality": "online"}, "supplied": {"modality": "online"}}
GOLDEN_SCALARS = {"id": UUID(int=7), "day": date(2030, 1, 2), "clock": time(9, 30), "price": Decimal("35.00"),
    "body": "ação 😃", "bytes": "São Paulo".encode(), "at": datetime(2030, 1, 2, 3, 4, 5, 6789, tzinfo=timezone(timedelta(hours=-4)))}


@pytest.mark.parametrize("value,expected", [
    (GOLDEN_OPERATION, "98dc40ad8a8ec4a649b7325d56c7033bcf75075a6bcf70038f549d1ab589dc6f"),
    (GOLDEN_SCALARS, "974355695bee03e54db4ae89879d0374a46ed0631004a3dae591daaaeb5b190a"),
])
def test_precalculated_historical_sha_allows_replay_without_new_fingerprint(value, expected):
    assert hashlib.sha256(original_bytes(value)).hexdigest() == expected
    assert service.digest(value) == expected
