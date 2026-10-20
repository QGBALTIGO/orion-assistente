"""Independent old renderer oracle: exact bytes, headers, fallbacks and errors."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
import random
from uuid import UUID
from zoneinfo import ZoneInfo

from asyncpg.pgproto.pgproto import UUID as DriverUUID
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import pytest

from app.http_json import json_response


def old(value, status=200):
    return JSONResponse(jsonable_encoder(value), status_code=status)


def identical(value, status=200):
    expected, actual = old(value, status), json_response(value, status_code=status)
    assert actual.body == expected.body
    assert actual.raw_headers == expected.raw_headers
    assert actual.status_code == expected.status_code


def test_native_sql_scalars_preserve_all_existing_json_bytes_and_headers():
    identifier = "12345678-1234-4234-8234-123456789abc"
    values = [None, True, False, -17, 2**80, -0.0, 1.234e-12, 1e20, "á 日本語 <script> & \n",
        "a\x00b", b"caf\xc3\xa9", UUID(identifier), DriverUUID(identifier), Decimal("123.4500"),
        Decimal("35"), Decimal("2E+4"), date(2026, 10, 19), time(15, 30, 5, 123),
        datetime(2026, 10, 19, 15, 30, 5, 123, tzinfo=timezone.utc),
        datetime(2026, 10, 19, 15, 30, tzinfo=ZoneInfo("America/Cuiaba")),
        timedelta(days=-2, seconds=123, microseconds=12)]
    for status in (200, 201):
        identical({"items": [{"value": v} for v in values], "total": len(values)}, status)
    rng = random.Random(519)
    for _ in range(300):
        # New containers each time; random leaf types and key order exercise
        # the C encoder against the independent recursive FastAPI reference.
        value = {str(n): [rng.choice(values), {"leaf": rng.choice(values)}]
                 for n in rng.sample(range(20), rng.randint(0, 12))}
        identical(value)


def test_subclasses_models_nonstring_keys_and_sqlalchemy_keys_keep_legacy_rules():
    class Number(IntEnum): ONE = 1
    class SpecialInt(int): pass
    class SpecialDict(dict): pass
    class Item(BaseModel): value: str
    @dataclass
    class Data: value: str
    shared = {"same": 1}
    values = [Number.ONE, SpecialInt(2), SpecialDict({"a": "b"}), Item(value="v"), Data("x"),
        Path("relative/asset"), {UUID(int=1): "uuid-key", 5: "int-key"},
        {"_sa_private": "omitted", "visible": "kept"}, [shared, shared], {1, 2}, frozenset((3, 4))]
    for value in values:
        identical(value)


def test_iterators_and_custom_timezone_are_consumed_only_by_the_legacy_fallback():
    for make in (lambda: (n for n in range(3)), lambda: {"nested": (n for n in range(3))}):
        assert json_response(make()).body == old(make()).body
    calls = []
    class CustomZone(tzinfo):
        def utcoffset(self, value):
            calls.append("offset")
            return timedelta(hours=-4)
    value = datetime(2026, 10, 19, 15, tzinfo=CustomZone())
    expected = old(value).body
    count = len(calls)
    calls.clear()
    assert json_response(value).body == expected and len(calls) == count


@pytest.mark.parametrize("make", [lambda: float("nan"), lambda: float("inf"), lambda: b"\xff",
                                 lambda: "\ud800", lambda: 10**5000])
def test_invalid_scalars_keep_the_old_exception_type_and_message(make):
    with pytest.raises(Exception) as expected:
        old(make())
    with pytest.raises(type(expected.value)) as actual:
        json_response(make())
    assert str(actual.value) == str(expected.value)


def test_recursive_input_keeps_legacy_recursion_error():
    value = []
    value.append(value)
    with pytest.raises(RecursionError): old(value)
    with pytest.raises(RecursionError): json_response(value)
