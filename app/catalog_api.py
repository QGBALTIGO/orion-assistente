"""API interna de FATIA 1, restrita a operador com chave forte.

Identidade do operador é verificada antes de resolver tenant.
SUPPORT pode selecionar explicitamente tenant e a operação fica auditada.
VIEWER só enxerga VIEWER_TENANT_ID configurado no servidor, nunca no payload.
"""
import os
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi.encoders import jsonable_encoder
from functools import lru_cache

from app.catalog_schemas import Business, RESOURCE_MODELS
from app.database import get_session_factory, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["Operação interna"])


@dataclass(frozen=True)
class Actor:
    role: str
    tenant_id: UUID | None = None


async def authenticate(request: Request, authorization: str | None = Header(default=None)) -> Actor:
    support = os.getenv("SUPPORT_API_TOKEN", "")
    viewer = os.getenv("VIEWER_API_TOKEN", "")
    if len(support) < 32:
        raise HTTPException(503, "Acesso operacional não configurado")
    raw = authorization.removeprefix("Bearer ") if authorization else ""
    if authorization and authorization.startswith("Bearer ") and secrets.compare_digest(raw, support):
        return Actor("support")
    if viewer and len(viewer) >= 32 and authorization and authorization.startswith("Bearer "):
        if secrets.compare_digest(raw, viewer):
            assigned = os.getenv("VIEWER_TENANT_ID", "")
            try:
                return Actor("viewer", UUID(assigned))
            except ValueError:
                raise HTTPException(503, "Escopo do leitor não configurado") from None
    if request.cookies.get("__Host-orion_session"):
        from app.panel_auth import panel_actor
        await panel_actor(request)
        return Actor("support")
    raise HTTPException(401, "Credenciais de operador inválidas", headers={"WWW-Authenticate": "Bearer"})


def authorize(actor: Actor, tenant_id: UUID, *, write: bool = False) -> None:
    if actor.role == "support":
        return
    if write or actor.tenant_id != tenant_id:
        raise HTTPException(403, "Operação não autorizada para este estabelecimento")


def support_only(actor: Actor) -> None:
    if actor.role != "support":
        raise HTTPException(403, "Ação reservada à equipe de suporte")


@lru_cache(maxsize=1)
def support_session_factory():
    url = os.getenv("SUPPORT_DATABASE_URL")
    if not url:
        raise RuntimeError("SUPPORT_DATABASE_URL ausente")
    url = url.replace("postgres://", "postgresql://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if make_url(url).drivername != "postgresql+asyncpg" or make_url(url).username != "app_support":
        raise RuntimeError("Conexão de suporte exige papel app_support")
    options = {"pool_pre_ping": True}
    if os.getenv("PYTEST_CURRENT_TEST"):
        options["poolclass"] = NullPool
    return async_sessionmaker(create_async_engine(url, **options), expire_on_commit=False)


async def ensure_tenant(session, tenant_id: UUID):
    found = (await session.execute(
        text("SELECT id FROM tenants WHERE id = :t"), {"t": tenant_id}
    )).scalar_one_or_none()
    if not found:
        raise HTTPException(404, "Barbearia não encontrada")


def validation(model, payload: dict[str, Any]):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, jsonable_encoder(exc.errors())) from exc


def resource_model(resource: str):
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        raise HTTPException(404, "Cadastro desconhecido")
    return model


def check_resource_values(resource: str, values: dict[str, Any]):
    if resource == "working_hours":
        if values["start_time"] >= values["end_time"]:
            raise HTTPException(422, "Horário de início deve anteceder o final")
    if resource == "working_breaks":
        if values["start_time"] >= values["end_time"]:
            raise HTTPException(422, "Pausa inválida")


async def validate_break(session, values, ignore_id: UUID | None = None):
    wh = (await session.execute(text("""
        SELECT start_time, end_time FROM working_hours
        WHERE id = :id AND tenant_id = :tenant_id FOR UPDATE
    """), {"id": values["working_hour_id"], "tenant_id": values["tenant_id"]})).mappings().first()
    if wh is None:
        raise HTTPException(422, "Expediente não encontrado no estabelecimento")
    if values["start_time"] < wh["start_time"] or values["end_time"] > wh["end_time"]:
        raise HTTPException(422, "A pausa deve caber inteiramente no expediente")
    conflicting = (await session.execute(text("""
        SELECT id FROM working_breaks WHERE tenant_id = :tenant_id
          AND working_hour_id = :working_hour_id AND id != :ignore_id
          AND start_time < :end_time AND end_time > :start_time LIMIT 1
    """), {**values, "ignore_id": ignore_id or UUID(int=0)})).scalar_one_or_none()
    if conflicting:
        raise HTTPException(409, "Esta pausa se sobrepõe a outra pausa")


async def audit(session, tenant_id: UUID, action: str, resource: str, item_id: UUID):
    await session.execute(text("""
        INSERT INTO tenant_audit (tenant_id, action, entity, entity_id)
        VALUES (:tenant_id, :action, :entity, :entity_id)
    """), dict(tenant_id=tenant_id, action=action, entity=resource, entity_id=item_id))


@router.get("/tenants")
async def list_tenants(actor: Actor = Depends(authenticate)):
    support_only(actor)
    factory = support_session_factory()
    async with factory() as session:
        async with session.begin():
            rows = (await session.execute(text("""
                SELECT id, name, slug, timezone, status FROM tenants
                ORDER BY name LIMIT 200
            """))).mappings().all()
            return jsonable_encoder(rows)


@router.post("/tenants", status_code=201)
async def create_tenant(payload: dict[str, Any], actor: Actor = Depends(authenticate)):
    support_only(actor)
    values = validation(Business, payload).model_dump()
    key = uuid4()
    factory = support_session_factory()
    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(text("""
                    INSERT INTO tenants (id, name, slug, timezone)
                    VALUES (:id, :name, :slug, :timezone)
                """), {"id": key, **values})
                await session.execute(text("""
                    INSERT INTO platform_audit (tenant_id, action)
                    VALUES (:id, 'tenant_created')
                """), {"id": key})
    except IntegrityError as exc:
        raise HTTPException(409, "Slug já utilizado ou cadastro inválido") from exc
    return {"id": str(key), **values, "status": "active"}


@router.get("/tenants/{tenant_id}/audit")
async def list_audit(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    factory = get_session_factory()
    async with factory() as session:
        async with tenant_transaction(session, tenant_id):
            await ensure_tenant(session, tenant_id)
            rows = (await session.execute(text("""
                SELECT action, entity, entity_id, happened_at FROM tenant_audit
                WHERE tenant_id = :t ORDER BY happened_at DESC LIMIT 100
            """), {"t": tenant_id})).mappings().all()
            return jsonable_encoder(rows)


@router.get("/tenants/{tenant_id}/{resource}")
async def list_items(tenant_id: UUID, resource: str, actor: Actor = Depends(authenticate)):
    authorize(actor, tenant_id)
    resource_model(resource)
    factory = get_session_factory()
    async with factory() as session:
        async with tenant_transaction(session, tenant_id):
            await ensure_tenant(session, tenant_id)
            rows = (await session.execute(
                text(f"SELECT * FROM {resource} WHERE tenant_id = :t ORDER BY id LIMIT 200"),
                {"t": tenant_id}
            )).mappings().all()
            return jsonable_encoder(rows)


@router.post("/tenants/{tenant_id}/{resource}", status_code=201)
async def create_item(
    tenant_id: UUID, resource: str, payload: dict[str, Any],
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    values = validation(resource_model(resource), payload).model_dump()
    check_resource_values(resource, values)
    item_id = uuid4()
    bind = {"id": item_id, "tenant_id": tenant_id, **values}
    fields = list(bind)
    statement = text(
        f"INSERT INTO {resource} (" + ", ".join(fields) + ") VALUES (" +
        ", ".join(":" + k for k in fields) + ")"
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, tenant_id):
                await ensure_tenant(session, tenant_id)
                if resource == "working_breaks":
                    await validate_break(session, bind)
                await session.execute(statement, bind)
                await audit(session, tenant_id, "created", resource, item_id)
    except IntegrityError as exc:
        raise HTTPException(409, "Conflito de cadastro, referência ou intervalo inválido") from exc
    return jsonable_encoder({"id": item_id, "tenant_id": tenant_id, **values})


@router.put("/tenants/{tenant_id}/{resource}/{item_id}")
async def replace_item(
    tenant_id: UUID, resource: str, item_id: UUID,
    payload: dict[str, Any], actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    values = validation(resource_model(resource), payload).model_dump()
    check_resource_values(resource, values)
    assignments = ", ".join(f"{field} = :{field}" for field in values)
    bind = {"tenant_id": tenant_id, "id": item_id, **values}
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, tenant_id):
                await ensure_tenant(session, tenant_id)
                if resource == "working_breaks":
                    await validate_break(session, bind, item_id)
                result = await session.execute(text(
                    f"UPDATE {resource} SET {assignments} WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
                ), bind)
                if result.scalar_one_or_none() is None:
                    raise HTTPException(404, "Registro não encontrado")
                await audit(session, tenant_id, "updated", resource, item_id)
    except IntegrityError as exc:
        raise HTTPException(409, "Cadastro ou vínculo inválido") from exc
    return jsonable_encoder({"id": item_id, "tenant_id": tenant_id, **values})


@router.delete("/tenants/{tenant_id}/{resource}/{item_id}", status_code=204)
async def delete_item(
    tenant_id: UUID, resource: str, item_id: UUID,
    actor: Actor = Depends(authenticate),
):
    authorize(actor, tenant_id, write=True)
    resource_model(resource)
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, tenant_id):
                await ensure_tenant(session, tenant_id)
                result = await session.execute(
                    text(f"DELETE FROM {resource} WHERE id = :id AND tenant_id = :t RETURNING id"),
                    {"id": item_id, "t": tenant_id},
                )
                if result.scalar_one_or_none() is None:
                    raise HTTPException(404, "Registro não encontrado")
                await audit(session, tenant_id, "deleted", resource, item_id)
    except IntegrityError as exc:
        raise HTTPException(409, "Registro em uso por outro cadastro") from exc
    return None
