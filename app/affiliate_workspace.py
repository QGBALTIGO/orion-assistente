"""Serve the Afiliados workspace through the active Orion account session."""
import asyncio
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
import httpx
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app.affiliate_analytics import analytics_report_valid, build_analytics_fallback, state_snapshot_valid
from app.workspace_policy import AFFILIATE_NICHE
from app.affiliate_whatsapp import router as whatsapp_router


router = APIRouter()
router.include_router(whatsapp_router)
UPSTREAM = "https://orion-afiliados-piloto.187-77-255-164.sslip.io"
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
API_ACTIONS = {
    "GET": (r"state|catalog|(?:connection/)?whatsapp/(?:status|groups)|send-batches/[a-f0-9]{32}|"
            r"offers/[a-f0-9]{32}/(?:preview|card\.png)|offers/demo/(?:preview|card\.png)"),
    "POST": (r"offers|offers/demo|offers/(?:demo|[a-f0-9]{32})/(?:phrase|phrases|queue|send)|connection/telegram/verify|"
             r"destinations/[a-f0-9]{32}/verify-channel|"
             r"rules/[a-f0-9]{32}/activate|jobs/[a-f0-9]{32}"),
    "PUT": (r"platforms/(?:shopee|groq)|connection/telegram|destinations/[a-f0-9]{32}|"
            r"templates/(?:default|[a-f0-9]{32})|offers/(?:demo|[a-f0-9]{32})(?:/phrase)?|"
            r"rules/[a-f0-9]{32}|product-flag/shopee:\d+:\d+"),
    "DELETE": r"platforms/groq|connection/(?:shopee|telegram)|destinations/[a-f0-9]{32}",
}
ASSETS = {
    **{f"brands/{name}": (f"/assets/brands/{name}", mime)
       for name, mime in (("shopee.png", "image/png"), ("amazon.png", "image/png"),
                          ("mercado-livre.png", "image/png"), ("telegram.png", "image/png"),
                          ("whatsapp.svg", "image/svg+xml"))},
}
LOCAL_ASSETS = {
    "workspace.css": ("orion-affiliate-workspace.css", "text/css"),
    "workspace.js": ("orion-affiliate-workspace.js", "text/javascript"),
}


@lru_cache(maxsize=2)
def cipher(key: str):
    try:
        return Fernet(key.encode("ascii")) if key else None
    except (UnicodeError, ValueError):
        return None


def available():
    return cipher(os.getenv("ORION_AFFILIATE_WORKSPACE_KEY", "")) is not None


async def affiliate_owner(actor: Actor, tenant_id: UUID):
    if (actor.role != "member" or actor.maintenance or actor.account_id is None
            or actor.tenant_id != tenant_id or actor.membership_role != "owner"):
        raise HTTPException(403, "Entre como proprietário deste espaço de Afiliados")
    async with support_session_factory()() as db:
        company = (await db.execute(text("""
            SELECT t.name FROM tenants t
            JOIN business_onboarding o ON o.tenant_id=t.id
            JOIN tenant_memberships m ON m.tenant_id=t.id
            JOIN panel_accounts a ON a.id=m.account_id
            WHERE t.id=:tenant AND t.status='active' AND o.completed_at IS NOT NULL
              AND o.configuration->>'niche'=:niche AND m.account_id=:account
              AND m.active AND m.role='owner' AND a.verified_at IS NOT NULL
        """), {"tenant": tenant_id, "account": actor.account_id, "niche": AFFILIATE_NICHE})).scalar_one_or_none()
    if company is None:
        raise HTTPException(403, "Este espaço de Afiliados não está disponível para sua conta")
    return company


async def fetch_upstream(method, path, *, headers=None, params=None, body=None):
    try:
        async with httpx.AsyncClient(timeout=33, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, UPSTREAM + path, headers=headers, params=params,
                                     content=body) as response:
                if response.is_redirect:
                    raise ValueError
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise ValueError
                    chunks.append(chunk)
                return response.status_code, response.headers.get("content-type", "").split(";")[0], b"".join(chunks)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "O painel de Afiliados está indisponível. Tente novamente.") from None


@router.get("/afiliados-assets/{name:path}")
async def assets(name: str):
    if available() and name in LOCAL_ASSETS:
        filename, mime = LOCAL_ASSETS[name]
        content = Path(__file__).with_name("static").joinpath(filename).read_bytes()
        return Response(content, media_type=mime, headers={**NO_STORE, "Cache-Control": "public, max-age=120"})
    asset = ASSETS.get(name)
    if asset is None or not available():
        raise HTTPException(404)
    path, expected = asset
    status, mime, content = await fetch_upstream("GET", path)
    if status != 200 or mime != expected:
        raise HTTPException(502, "Componente do painel indisponível")
    return Response(content, media_type=expected, headers={**NO_STORE, "Cache-Control": "public, max-age=120"})


@router.get("/afiliados/{tenant_id}")
async def workspace_page(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await affiliate_owner(actor, tenant_id)
    if not available():
        raise HTTPException(503, "O painel de Afiliados está indisponível no momento")
    return Response(status_code=303, headers={**NO_STORE, "Location": "/painel?aba=dashboard"})


@router.get("/afiliados/{tenant_id}/api/analytics", include_in_schema=False)
async def workspace_analytics(
    tenant_id: UUID,
    period: Literal["today", "7d", "30d", "month"] = "month",
    actor: Actor = Depends(authenticate),
):
    """Read the provider report, or describe the limited operational snapshot.

    An absent report endpoint is a capability gap, never an empty sales report.
    Both reads use the same verified owner and tenant-bound signed claims as
    the existing workspace. No credentials or report data are kept in the UI.
    """
    company = await affiliate_owner(actor, tenant_id)
    signer = cipher(os.getenv("ORION_AFFILIATE_WORKSPACE_KEY", ""))
    if signer is None:
        raise HTTPException(503, "O painel de Afiliados está indisponível no momento")
    try:
        async with asyncio.timeout(25):
            return await _read_workspace_analytics(signer, tenant_id, company, period)
    except TimeoutError:
        raise HTTPException(504, "A atualização dos relatórios demorou mais que o esperado. Tente novamente.") from None


async def _read_workspace_analytics(signer, tenant_id, company, period):
    async with support_session_factory()() as db:
        timezone = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:tenant"),
                                    {"tenant": tenant_id})).scalar_one_or_none()
    timezone = timezone or "America/Campo_Grande"

    async def read(path, params=None):
        claim = json.dumps({"tenant": tenant_id.hex, "name": company,
                            "method": "GET", "path": path}).encode()
        headers = {"X-Orion-Affiliate-Access": signer.encrypt(claim).decode()}
        return await fetch_upstream("GET", path, headers=headers, params=params)

    status, mime, content = await read("/api/analytics", {"period": period, "timezone": timezone})
    if status == 200:
        try:
            report = json.loads(content) if mime == "application/json" else None
            valid = analytics_report_valid(report, period=period)
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise HTTPException(502, "O relatório de Afiliados retornou uma resposta inesperada")
        return Response(content, media_type="application/json", headers=NO_STORE)
    if status not in {404, 405, 501}:
        # An authorization or transient provider failure must not masquerade
        # as a successful, empty report and replace confirmed cached values.
        raise HTTPException(status if status in {401, 403, 429} else 502,
                            "Não foi possível atualizar os relatórios de Afiliados")

    status, mime, content = await read("/api/state")
    if status != 200 or mime != "application/json":
        raise HTTPException(status if status in {401, 403, 429} else 502,
                            "Não foi possível atualizar a atividade de Afiliados")
    try:
        state = json.loads(content)
        if not state_snapshot_valid(state):
            raise ValueError
    except (ValueError, UnicodeError):
        raise HTTPException(502, "A atividade de Afiliados retornou uma resposta inesperada") from None
    report = build_analytics_fallback(state, period=period,
                                      timezone=timezone)
    return Response(json.dumps(report, ensure_ascii=False), media_type="application/json", headers=NO_STORE)


@router.api_route("/afiliados/{tenant_id}/api/{path:path}", methods=list(API_ACTIONS), include_in_schema=False)
async def workspace_api(tenant_id: UUID, path: str, request: Request, actor: Actor = Depends(authenticate)):
    if not re.fullmatch(API_ACTIONS[request.method], path):
        raise HTTPException(404)
    if not (signer := cipher(os.getenv("ORION_AFFILIATE_WORKSPACE_KEY", ""))):
        raise HTTPException(503, "O painel de Afiliados está indisponível no momento")
    company = await affiliate_owner(actor, tenant_id)
    upstream_path = "/api/" + path
    claim = json.dumps({"tenant": tenant_id.hex, "name": company,
                        "method": request.method, "path": upstream_path}).encode()
    headers = {"X-Orion-Affiliate-Access": signer.encrypt(claim).decode()}
    body = None
    if request.method != "GET":
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REQUEST:
                raise HTTPException(413, "A solicitação ultrapassou o tamanho permitido")
        body = bytes(body) or None
        headers.update({"Origin": UPSTREAM, "Content-Type": "application/json"})
    status, mime, content = await fetch_upstream(request.method, upstream_path, headers=headers,
                                                 params=request.query_params.multi_items(), body=body)
    if mime not in {"application/json", "image/png"}:
        raise HTTPException(502, "Resposta inesperada do painel de Afiliados")
    return Response(content, status_code=status, media_type=mime, headers=NO_STORE)
