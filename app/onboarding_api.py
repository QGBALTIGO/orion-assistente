"""Authenticated setup API; GET routes never create or activate anything."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.catalog_api import Actor, authenticate
from app import onboarding_schemas as schemas
from app import onboarding_service as service

router = APIRouter(prefix="/api/v1", tags=["Configuração inicial"])


@router.get("/onboarding/entry")
async def entry(tenant_id: UUID | None = None, actor: Actor = Depends(authenticate)):
    return JSONResponse(await service.entry(actor, tenant_id))


@router.post("/onboarding/businesses", status_code=201)
async def create_business(payload: schemas.CreateBusiness, actor: Actor = Depends(authenticate)):
    return JSONResponse(await service.create_business(actor, payload), status_code=201)


@router.get("/tenants/{tenant_id}/onboarding")
async def state(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    return JSONResponse(await service.get_state(actor, tenant_id))


@router.put("/tenants/{tenant_id}/onboarding/steps/{step_id}")
async def save_step(tenant_id: UUID, step_id: str, payload: schemas.SaveStep, actor: Actor = Depends(authenticate)):
    try:
        return JSONResponse(await service.save_step(actor, tenant_id, step_id, payload))
    except ValidationError as exc:
        raise HTTPException(422, jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})) from None


@router.post("/tenants/{tenant_id}/onboarding/complete")
async def complete(tenant_id: UUID, payload: schemas.Complete, actor: Actor = Depends(authenticate)):
    return JSONResponse(await service.complete(actor, tenant_id, payload))
