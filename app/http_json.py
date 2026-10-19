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


def _ordinary(value):
    pending, seen = [(value, 1)], set()
    while pending:
        current, depth = pending.pop()
        kind = type(current)
        if kind in _CONTAINERS:
            if depth > 64 or id(current) in seen:
                return False
            seen.add(id(current))
            if kind is dict:
                for key in current:
                    if type(key) is not str or key.startswith("_sa"):
                        return False
            for child in current.values() if kind is dict else current:
                child_kind = type(child)
                if child_kind in _CONTAINERS:
                    pending.append((child, depth + 1))
                elif child_kind not in _SCALARS:
                    return False
                elif child_kind in (datetime, time) and type(child.tzinfo) not in (type(None), timezone, ZoneInfo):
                    return False
        elif kind not in _SCALARS:
            return False
        elif kind in (datetime, time) and type(current.tzinfo) not in (type(None), timezone, ZoneInfo):
            return False
    return True


def _scalar(value):
    if type(value) is DriverUUID:
        return str(value)
    return ENCODERS_BY_TYPE[type(value)](value)


def json_response(value, *, status_code=200):
    """No model, iterator, subclass or SQLAlchemy-specific key takes the fast path."""
    if _ordinary(value):
        try:
            body = json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":"), default=_scalar).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            pass
        else:
            return Response(body, status_code=status_code, media_type="application/json")
    return JSONResponse(jsonable_encoder(value), status_code=status_code)
