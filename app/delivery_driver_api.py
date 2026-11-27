"""Driver portal uses the panel's verified sessions and CSRF checks, independently of staff roles."""
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError

from app import delivery_driver as driver
from app import delivery_logistics as logistics
from app.database import get_session_factory, tenant_transaction
from app.http_json import json_response
from app.panel_auth import panel_actor
from app.plan_permissions import lock_plan


router=APIRouter(tags=["Entregador"])
prefix="/api/v1/entregador/{tenant_id}"
static=Path(__file__).parent/"static"
headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff"}


async def run(tenant,claims,operation,*args):
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await lock_plan(db,tenant)
                await logistics.lock_policy(db,tenant,exclusive=operation is driver.order_action)
                person=await driver.identity(db,tenant,claims)
                result=await operation(db,tenant,person,*args)
    except IntegrityError:
        raise HTTPException(409,"A operação mudou. Consulte a fila de entregas.") from None
    response=json_response(result)
    response.headers.update(headers)
    return response


@router.get(prefix+"/queue")
async def queue(tenant_id: UUID,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.dashboard)


@router.put(prefix+"/shift")
async def shift(tenant_id: UUID,payload: driver.Shift,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.shift,payload)


@router.post(prefix+"/location")
async def begin_position(tenant_id: UUID,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.start_position)


@router.put(prefix+"/location")
async def position(tenant_id: UUID,payload: driver.Position,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.set_position,payload)


@router.delete(prefix+"/location")
async def stop_position(tenant_id: UUID,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.clear_position)


@router.post(prefix+"/orders/{order_id}/actions")
async def action(tenant_id: UUID,order_id: UUID,payload: driver.Action,claims: dict=Depends(panel_actor)):
    return await run(tenant_id,claims,driver.order_action,order_id,payload)


@router.get("/entregador/{tenant_id}/sw.js",include_in_schema=False)
async def service_worker(tenant_id: UUID):
    return FileResponse(static/"orion-delivery-driver-sw.js",media_type="text/javascript",headers=headers)


@router.get("/entregador/{tenant_id}/manifest.webmanifest",include_in_schema=False)
async def manifest(tenant_id: UUID):
    root=f"/entregador/{tenant_id}"
    response=json_response({"name":"Orion Entregador","short_name":"Entregador","id":root,"start_url":root+"/",
        "scope":root+"/","display":"standalone","background_color":"#0b0b0c","theme_color":"#0b0b0c",
        "icons":[{"src":f"/pwa/icon-{n}.png","sizes":f"{n}x{n}","type":"image/png","purpose":"any"} for n in (192,512)]})
    response.headers.update(headers)
    response.headers["Content-Type"]="application/manifest+json"
    return response


@router.get("/entregador/{tenant_id}/",include_in_schema=False)
@router.get("/entregador/{tenant_id}",include_in_schema=False)
async def portal(tenant_id: UUID):
    return FileResponse(static/"orion-delivery-driver.html",headers={**headers,
        "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "Permissions-Policy":"geolocation=(self)"})
