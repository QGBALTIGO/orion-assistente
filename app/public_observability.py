"""Process-local public runtime metrics with a finite, identity-free vocabulary."""
import asyncio
import errno
import json
import logging
import math
import threading
import time
from datetime import datetime, timezone

import asyncpg
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, TimeoutError as PoolTimeoutError

logger = logging.getLogger("orion.public_runtime")
ROUTES = ("page", "catalogue", "availability", "booking")
STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx", "other")
STATUS_CODES = (200, 201, 204, 301, 302, 304, 307, 308, 400, 401, 403, 404, 408, 409, 422, 428, 429, 500, 502, 503, 504)
DURATION_BOUNDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
CACHE_KINDS = ("catalogue", "page")
READINESS_STALE_SECONDS = 60
PUBLIC_READ_TIMEOUT_SECONDS = 15.0
PUBLIC_WRITE_TIMEOUT_SECONDS = 75.0  # Includes the public gate's bounded 60-second wait.
_NETWORK_ERRNOS = {errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.ENETUNREACH,
                   errno.EHOSTUNREACH, errno.ENETDOWN, errno.ETIMEDOUT, errno.EPIPE}
_WINDOWS_NETWORK_ERRORS = {64, 121, 1225, 10050, 10051, 10052, 10053, 10054, 10060, 10061, 10064, 10065}
_NETWORK_TEXT_ERRNOS = _NETWORK_ERRNOS | {32, 100, 101, 103, 104, 110, 111, 113}


def _seconds(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 0.0
    return min(value, 3600.0) if math.isfinite(value) and value >= 0 else 0.0


class _Histogram:
    def __init__(self):
        self.count = 0
        self.total = 0.0
        self.maximum = 0.0
        self.buckets = [0] * (len(DURATION_BOUNDS) + 1)

    def observe(self, seconds):
        seconds = _seconds(seconds)
        self.count += 1
        self.total += seconds
        self.maximum = max(self.maximum, seconds)
        for index, bound in enumerate(DURATION_BOUNDS):
            if seconds <= bound:
                self.buckets[index] += 1
        self.buckets[-1] += 1

    def snapshot(self):
        return {"count": self.count, "sum_seconds": round(self.total, 6),
                "max_seconds": round(self.maximum, 6),
                "buckets": [{"le_seconds": bound, "count": self.buckets[index]}
                            for index, bound in enumerate((*DURATION_BOUNDS, None))]}


class PublicRuntimeStatistics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.routes = {route: {"classes": {code: 0 for code in STATUS_CLASSES},
                               "codes": {str(code): 0 for code in STATUS_CODES} | {"other": 0},
                               "duration": _Histogram(),
                               "duration_by_status": {code: _Histogram() for code in STATUS_CLASSES}}
                       for route in ROUTES}
        self.gate = {"admitted": 0, "rejected": 0, "wait": _Histogram()}
        self.pool_wait = _Histogram()
        self.dependency_failures = {"connection": 0, "pool_timeout": 0, "deadline": 0}
        self.cache = {kind: {"hit": 0, "miss": 0} for kind in CACHE_KINDS}
        self.readiness = {"status": "unknown", "dependencies": {"postgres": "unknown", "redis": "unknown"},
                          "checks": 0, "failures": 0, "last_checked_at": None}
        self.checked_monotonic = None

    def snapshot(self):
        with self.lock:
            warnings = []
            if self.checked_monotonic is None:
                warnings.append("readiness_not_checked")
            elif time.monotonic() - self.checked_monotonic > READINESS_STALE_SECONDS:
                warnings.append("readiness_stale")
            for dependency, status in self.readiness["dependencies"].items():
                if status in {"unavailable", "timeout"}:
                    warnings.append(dependency + "_unavailable")
            if self.pool_wait.count == 0:
                warnings.append("pool_wait_not_instrumented")
            if any(self.dependency_failures.values()):
                warnings.append("public_dependency_failures_observed")
            return {"schema_version": 1, "aggregation": "process", "workers_merged": False,
                    "uptime_seconds": round(time.monotonic() - self.started, 3),
                    "requests": {route: {"status_classes": dict(stats["classes"]),
                                         "status_codes": dict(stats["codes"]),
                                         "duration_seconds": stats["duration"].snapshot(),
                                         "duration_by_status_class": {code: histogram.snapshot()
                                                                      for code, histogram in stats["duration_by_status"].items()}}
                                 for route, stats in self.routes.items()},
                    "gate": {"admitted": self.gate["admitted"], "rejected": self.gate["rejected"],
                             "wait_seconds": self.gate["wait"].snapshot()},
                    "pool_wait": {"available": self.pool_wait.count > 0,
                                  "seconds": self.pool_wait.snapshot()},
                    "dependency_failures": dict(self.dependency_failures),
                    "cache": {kind: dict(counts) for kind, counts in self.cache.items()},
                    "readiness": {**self.readiness, "dependencies": dict(self.readiness["dependencies"])},
                    "warnings": warnings}


_runtime = PublicRuntimeStatistics()


def runtime_snapshot():
    return _runtime.snapshot()


def observe_gate(wait_seconds, *, admitted=True):
    with _runtime.lock:
        _runtime.gate["admitted" if admitted else "rejected"] += 1
        _runtime.gate["wait"].observe(wait_seconds)


def observe_pool_wait(wait_seconds):
    """Only call with a measured connection queue wait, never query duration."""
    with _runtime.lock:
        _runtime.pool_wait.observe(wait_seconds)


def observe_cache(hit, *, kind="catalogue"):
    if kind not in CACHE_KINDS:
        return
    with _runtime.lock:
        _runtime.cache[kind]["hit" if hit else "miss"] += 1


def observe_readiness(dependencies):
    # Never let a dependency exception, connection URL or arbitrary label enter
    # either the support response or the structured log.
    statuses = {name: dependencies.get(name) if dependencies.get(name) in {"ok", "unavailable", "timeout"}
                else "unavailable" for name in ("postgres", "redis")}
    healthy = all(status == "ok" for status in statuses.values())
    with _runtime.lock:
        _runtime.readiness.update(status="ready" if healthy else "unavailable", dependencies=statuses,
                                  checks=_runtime.readiness["checks"] + 1,
                                  failures=_runtime.readiness["failures"] + (not healthy),
                                  last_checked_at=datetime.now(timezone.utc).isoformat())
        _runtime.checked_monotonic = time.monotonic()
    logger.info(json.dumps({"event": "dependency_readiness", "status": "ready" if healthy else "unavailable",
                            **statuses}, separators=(",", ":")))


def _route_key(path):
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "s":
        return "page"
    if len(parts) in {5, 6} and parts[:4] == ["api", "v1", "public", "sites"]:
        if len(parts) == 5:
            return "catalogue"
        return {"availability": "availability", "appointments": "booking"}.get(parts[5])
    if (len(parts) == 7 and parts[:4] == ["api", "v1", "public", "sites"]
            and parts[5:] == ["appointments", "recover"]):
        return "booking"
    return None


def _connection_failure(error):
    if isinstance(error, (ConnectionError, TimeoutError, asyncpg.PostgresConnectionError)):
        return True
    state = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if isinstance(state, str) and (state.startswith("08") or state in {"57P01", "57P02", "57P03", "53300", "28P01"}):
        return True
    if isinstance(error, asyncpg.InterfaceError):
        return str(error) == "connection is closed"
    if isinstance(error, OSError):
        if getattr(error, "errno", None) in _NETWORK_ERRNOS | _WINDOWS_NETWORK_ERRORS or getattr(error, "winerror", None) in _WINDOWS_NETWORK_ERRORS:
            return True
        # asyncio may combine IPv4/IPv6 connection attempts into an OSError
        # whose only error codes are in its message. Inspect finite known codes;
        # never emit that message, which may contain the connection destination.
        if type(error) is OSError and getattr(error, "errno", None) is None:
            return (any("[WinError " + str(code) + "]" in str(error) for code in _WINDOWS_NETWORK_ERRORS)
                    or any("[Errno " + str(code) + "]" in str(error) for code in _NETWORK_TEXT_ERRNOS))
    return False


def _dependency_failure_kind(error):
    # A uniqueness/exclusion or SQL/programming failure remains a server error
    # unless its normal route explicitly maps it to a business conflict.
    if isinstance(error, IntegrityError):
        return None
    if isinstance(error, PoolTimeoutError):
        return "pool_timeout"
    if isinstance(error, DBAPIError):
        return "connection" if error.connection_invalidated or _connection_failure(error.orig) else None
    return "connection" if _connection_failure(error) else None


def _observe_dependency_failure(kind):
    with _runtime.lock:
        _runtime.dependency_failures[kind] += 1


def _unavailable_response():
    return JSONResponse({"detail": {"code": "dependency_unavailable", "retryable": True,
        "message": "Agendamento temporariamente indisponível. Tente novamente em instantes."}},
        status_code=503, headers={"Retry-After": "2", "Cache-Control": "no-store"})


def _observe_request(route, status, seconds):
    if route not in ROUTES:
        return
    status = status if type(status) is int and 100 <= status <= 599 else 500
    group = str(status // 100) + "xx"
    with _runtime.lock:
        stats = _runtime.routes[route]
        stats["classes"][group if group in STATUS_CLASSES else "other"] += 1
        stats["codes"][str(status) if status in STATUS_CODES else "other"] += 1
        stats["duration"].observe(seconds)
        stats["duration_by_status"][group if group in STATUS_CLASSES else "other"].observe(seconds)
    logger.info(json.dumps({"event": "public_request", "route": route, "status": status,
                            "duration_ms": round(_seconds(seconds) * 1000, 3)}, separators=(",", ":")))


class PublicRuntimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        route = _route_key(scope.get("path", "")) if scope.get("type") == "http" else None
        if route is None:
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status = 500
        response_started = False
        async def tracked_send(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
            await send(message)
        creating = route == "booking" and not scope.get("path", "").rstrip("/").endswith("/recover")
        deadline = asyncio.timeout(PUBLIC_WRITE_TIMEOUT_SECONDS if creating else PUBLIC_READ_TIMEOUT_SECONDS)
        try:
            async with deadline:
                await self.app(scope, receive, tracked_send)
        except asyncio.CancelledError:
            status = 503
            raise
        except Exception as error:
            kind = "deadline" if isinstance(error, TimeoutError) and deadline.expired() else _dependency_failure_kind(error)
            if kind is None:
                status = 500
                raise
            status = 503
            _observe_dependency_failure(kind)
            if response_started:
                raise
            await _unavailable_response()(scope, receive, tracked_send)
        finally:
            _observe_request(route, status, time.monotonic() - started)
