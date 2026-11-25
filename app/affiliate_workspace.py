"""Serve the Afiliados workspace through the active Orion account session."""
from functools import lru_cache
import json
import os
import re
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
import httpx
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app.workspace_policy import AFFILIATE_NICHE


router = APIRouter()
UPSTREAM = "https://orion-afiliados-piloto.187-77-255-164.sslip.io"
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
API_ACTIONS = {
    "GET": r"state|catalog|offers/[a-f0-9]{32}/(?:preview|card\.png)|offers/demo/(?:preview|card\.png)",
    "POST": (r"offers|offers/demo|offers/(?:demo|[a-f0-9]{32})/(?:phrase|queue)|connection/telegram/verify|"
             r"rules/[a-f0-9]{32}/activate|jobs/[a-f0-9]{32}"),
    "PUT": (r"platforms/(?:shopee|groq)|connection/telegram|destinations/[a-f0-9]{32}|"
            r"templates/(?:default|[a-f0-9]{32})|offers/(?:demo|[a-f0-9]{32})/phrase|"
            r"rules/[a-f0-9]{32}|product-flag/shopee:\d+:\d+"),
    "DELETE": r"platforms/groq|connection/(?:shopee|telegram)|destinations/[a-f0-9]{32}",
}
ASSETS = {
    "pilot.js": ("/assets/pilot.js", "text/javascript"),
    "orion-icons.js": ("/assets/orion-icons.js", "text/javascript"),
    "pilot.css": ("/assets/pilot.css", "text/css"),
    **{f"design/{name}.css": (f"/design/{name}.css", "text/css")
       for name in ("orion-suite", "orion-auth", "orion-subscription-dashboard")},
    **{f"brands/{name}": (f"/assets/brands/{name}", mime)
       for name, mime in (("shopee.png", "image/png"), ("amazon.png", "image/png"),
                          ("mercado-livre.png", "image/png"), ("telegram.png", "image/png"),
                          ("whatsapp.svg", "image/svg+xml"))},
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
    asset = ASSETS.get(name)
    if asset is None or not available():
        raise HTTPException(404)
    path, expected = asset
    status, mime, content = await fetch_upstream("GET", path)
    if status != 200 or (mime != expected and (expected, mime) != ("text/javascript", "application/javascript")):
        raise HTTPException(502, "Componente do painel indisponível")
    return Response(content, media_type=expected, headers={**NO_STORE, "Cache-Control": "public, max-age=120"})


@router.get("/afiliados/{tenant_id}")
async def workspace_page(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    await affiliate_owner(actor, tenant_id)
    if not available():
        raise HTTPException(503, "O painel de Afiliados está indisponível no momento")
    status, mime, content = await fetch_upstream("GET", "/")
    if status != 200 or mime != "text/html" or b'id="appView"' not in content:
        raise HTTPException(502, "Página de Afiliados indisponível")
    prefix = f"/api/v1/afiliados/{tenant_id}/api/"
    content = (content.replace(b'<head>', f'<head><meta name="orion-affiliate-api" content="{prefix}">'.encode(), 1)
               .replace(b'href="/design/', b'href="/api/v1/afiliados-assets/design/')
               .replace(b'href="/assets/', b'href="/api/v1/afiliados-assets/')
               .replace(b'src="/assets/', b'src="/api/v1/afiliados-assets/'))
    headers = {**NO_STORE, "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
               "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; "
               "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}
    return Response(content, media_type="text/html", headers=headers)


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
