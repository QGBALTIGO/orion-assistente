"""Render ordinary SQL payloads with the existing JSON bytes and safe fallback."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from asyncpg.pgproto.pgproto import UUID as DriverUUID
from fastapi.encoders import ENCODERS_BY_TYPE, jsonable_encoder
from fastapi.responses import JSONResponse, Response

_SCALARS = frozenset((str, int, float, bool, type(None), bytes, date, datetime,
                      time, timedelta, Decimal, UUID, DriverUUID))
_CONTAINERS = frozenset((dict, list, tuple))
_TEMPORALS = frozenset((datetime, time))
_TIMEZONES = (type(None), timezone, ZoneInfo)


def ordinary_json_tree(value):
    pending, seen, checked_keys = [(value, 1)], set(), set()
    while pending:
        current, depth = pending.pop()
        kind = type(current)
        if kind in _CONTAINERS:
            if depth > 64 or id(current) in seen:
                return False
            seen.add(id(current))
            if kind is dict:
                for key in current:
                    if type(key) is not str:
                        return False
                    # Cache only exact strings, within this walk. Catalog rows
                    # repeat the same column names; subclasses must still take
                    # the legacy encoder before hashing/equality can run hooks.
                    if key not in checked_keys:
                        if key.startswith("_sa"):
                            return False
                        if len(checked_keys) < 128:
                            checked_keys.add(key)
            for child in current.values() if kind is dict else current:
                child_kind = type(child)
                if child_kind in _CONTAINERS:
                    pending.append((child, depth + 1))
                elif child_kind not in _SCALARS:
                    return False
                elif child_kind in _TEMPORALS and type(child.tzinfo) not in _TIMEZONES:
                    return False
        elif kind not in _SCALARS:
            return False
        elif kind in _TEMPORALS and type(current.tzinfo) not in _TIMEZONES:
            return False
    return True


def _scalar(value):
    if type(value) is DriverUUID:
        return str(value)
    return ENCODERS_BY_TYPE[type(value)](value)


def json_response(value, *, status_code=200):
    """No model, iterator, subclass or SQLAlchemy-specific key takes the fast path."""
    if ordinary_json_tree(value):
        try:
            body = json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":"), default=_scalar).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            pass
        else:
            return Response(body, status_code=status_code, media_type="application/json")
    return JSONResponse(jsonable_encoder(value), status_code=status_code)
