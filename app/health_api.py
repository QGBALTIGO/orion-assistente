"""Readiness checks use runtime dependencies without exposing their identities."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import get_session_factory
from app.panel_auth import _request_redis
from app.public_observability import observe_readiness

router = APIRouter()
CHECK_TIMEOUT_SECONDS = 2.0


async def _postgres_probe():
    async with get_session_factory()() as session:
        return (await session.execute(text("SELECT 1"))).scalar_one() == 1


async def _redis_probe(request):
    cache, owned = _request_redis(request)
    try:
        return bool(await cache.ping())
    finally:
        if owned:
            await cache.aclose()


async def _bounded_probe(probe):
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            healthy = await probe()
        return "ok" if healthy else "unavailable"
    except TimeoutError:
        return "timeout"
    except Exception:
        # The exception text may contain a DSN, username or provider response.
        return "unavailable"


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request):
    postgres, redis = await asyncio.gather(_bounded_probe(_postgres_probe),
                                          _bounded_probe(lambda: _redis_probe(request)))
    dependencies = {"postgres": postgres, "redis": redis}
    healthy = all(status == "ok" for status in dependencies.values())
    observe_readiness(dependencies)
    return JSONResponse({"status": "ready" if healthy else "unavailable", "dependencies": dependencies},
                        status_code=200 if healthy else 503,
                        headers={"Cache-Control": "no-store"})
