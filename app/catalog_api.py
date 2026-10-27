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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi.encoders import jsonable_encoder
from functools import lru_cache

from app.catalog_schemas import Business, RESOURCE_MODELS
from app.database import engine_pool_options, get_session_factory, tenant_transaction
from app.http_json import json_response
from app.account_business import account_membership_role

router = APIRouter(prefix="/api/v1", tags=["Operação interna"])


@dataclass(frozen=True)
class Actor:
    role: str
    tenant_id: UUID | None = None
    account_id: UUID | None = None
    membership_role: str | None = None


async def scoped_account_actor(role: str, account_id: UUID | None, route_tenant, *, checked=None) -> Actor:
    """Bind legacy catalog access to the verified account and this exact route."""
    actor = Actor(role, account_id=account_id)
    if role != "member" or account_id is None or os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        return actor
    try:
        tenant = UUID(str(route_tenant))
    except (ValueError, TypeError):
        return actor
    from app.panel_auth import VerifiedRouteMembership
    if type(checked) is VerifiedRouteMembership and checked.account_id == account_id and checked.tenant_id == tenant:
        membership = checked.role
    else:
        async with support_session_factory()() as db:
            membership = await account_membership_role(db, account_id, tenant)
    if membership not in {"owner", "editor", "viewer"}:
        return actor
    return Actor(role, tenant, account_id, membership)


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
        from app.panel_auth import panel_actor, ROUTE_MEMBERSHIP
        session = await panel_actor(request)
        account_id = session.get("account_id")
        try:
            verified_account = UUID(str(account_id)) if account_id else None
        except ValueError:
            raise HTTPException(401, "Sessão inválida") from None
        return await scoped_account_actor(session["role"], verified_account,
                                          request.path_params.get("tenant_id"),
                                          checked=request.scope.get(ROUTE_MEMBERSHIP))
    raise HTTPException(401, "Credenciais de operador inválidas", headers={"WWW-Authenticate": "Bearer"})


def authorize(actor: Actor, tenant_id: UUID, *, write: bool = False) -> None:
    if actor.role in {"support","admin"}:
        return
    if (actor.role == "member" and actor.account_id is not None and actor.tenant_id == tenant_id
            and actor.membership_role in ({"owner", "editor"} if write else {"owner", "editor", "viewer"})):
        return
    if actor.role != "viewer" or write or actor.tenant_id != tenant_id:
        raise HTTPException(403, "Operação não autorizada para este estabelecimento")


def support_only(actor: Actor) -> None:
    if actor.role not in {"support","admin"}:
        raise HTTPException(403, "Ação reservada à equipe de suporte")


def authorize_resource(actor: Actor, tenant_id: UUID, resource: str, *, write: bool = False) -> None:
    authorize(actor, tenant_id, write=write)
    if (write and resource == "authorized_staff" and actor.role == "member"
            and actor.membership_role != "owner"):
        raise HTTPException(403, "Somente o proprietário pode alterar acessos da equipe")


@lru_cache(maxsize=1)
def support_session_factory():
    url = os.getenv("SUPPORT_DATABASE_URL")
    if not url:
        raise RuntimeError("SUPPORT_DATABASE_URL ausente")
    url = url.replace("postgres://", "postgresql://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if make_url(url).drivername != "postgresql+asyncpg" or make_url(url).username != "app_support":
        raise RuntimeError("Conexão de suporte exige papel app_support")
    return async_sessionmaker(create_async_engine(url, **engine_pool_options()), expire_on_commit=False)


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


async def validate_working_hour_edit(session, values):
    """Keep linked breaks valid when an existing shift is moved or resized.

    Lock the shift before checking breaks. Creation and editing of a break
    acquire the same lock, avoiding a race that could leave invalid pauses.
    """
    current = (await session.execute(text("""
        SELECT professional_id FROM working_hours
        WHERE tenant_id=:tenant_id AND id=:id FOR UPDATE
    """), values)).scalar_one_or_none()
    if current is None:
        raise HTTPException(404, "Horário não encontrado nesta empresa")
    if current != values["professional_id"]:
        raise HTTPException(409, "Não é possível transferir este horário para outro profissional")

    invalid_pause = (await session.execute(text("""
        SELECT id FROM working_breaks
        WHERE tenant_id=:tenant_id AND working_hour_id=:id
          AND (start_time < :start_time OR end_time > :end_time)
        LIMIT 1
    """), values)).scalar_one_or_none()
    if invalid_pause is not None:
        raise HTTPException(
            409, "Ajuste ou remova as pausas que ficarão fora do novo expediente"
        )

    collision = (await session.execute(text("""
        SELECT id FROM working_hours
        WHERE tenant_id=:tenant_id AND professional_id=:professional_id
          AND weekday=:weekday AND id<>:id
        LIMIT 1
    """), values)).scalar_one_or_none()
    if collision is not None:
        raise HTTPException(409, "Já existe um expediente nesse dia. Edite o horário existente ou use uma pausa")


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


async def guard_generated_service(session, tenant_id: UUID, resource: str, item_id=None, values=None):
    """Derived package prices and eligibility are maintained by the package API."""
    if resource not in {"services", "professional_services"}:
        return
    identities = []
    if resource == "services" and item_id is not None:
        identities.append(item_id)
    if resource == "professional_services":
        if item_id is not None:
            current = (await session.execute(text("SELECT service_id FROM professional_services WHERE tenant_id=:tenant AND id=:id"),
                                             {"tenant": tenant_id, "id": item_id})).scalar_one_or_none()
            if current is not None:
                identities.append(current)
        if values and values.get("service_id") is not None:
            identities.append(values["service_id"])
    for identity in identities:
        kind = (await session.execute(text("SELECT catalog_kind FROM services WHERE tenant_id=:tenant AND id=:id"),
                                      {"tenant": tenant_id, "id": identity})).scalar_one_or_none()
        if kind is not None and kind != "base":
            raise HTTPException(409, "Edite este serviço e seus profissionais pelo cadastro de combos ou adicionais")


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
    authorize_resource(actor, tenant_id, resource)
    resource_model(resource)
    factory = get_session_factory()
    async with factory() as session:
        async with tenant_transaction(session, tenant_id):
            await ensure_tenant(session, tenant_id)
            ordering = "sort_order,name,id" if resource == "services" else "id"
            scope = " AND catalog_kind <> 'variant'" if resource == "services" else ""
            rows = (await session.execute(
                text(f"SELECT * FROM {resource} WHERE tenant_id = :t{scope} ORDER BY {ordering} LIMIT 200"),
                {"t": tenant_id}
            )).mappings().all()
            content = [dict(row) for row in rows]
    return json_response(content)


async def list_services(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    """Specific hot path; all catalog authorization and SQL stay in list_items."""
    return await list_items(tenant_id, "services", actor)


@router.post("/tenants/{tenant_id}/{resource}", status_code=201)
async def create_item(
    tenant_id: UUID, resource: str, payload: dict[str, Any],
    actor: Actor = Depends(authenticate),
):
    authorize_resource(actor, tenant_id, resource, write=True)
    values = validation(resource_model(resource), payload).model_dump()
    check_resource_values(resource, values)
    if resource == "professionals" and values["commission_mode"] == "per_service":
        raise HTTPException(422, "Cadastre comissões por serviço na gestão de profissionais")
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
                if resource == "professional_services":
                    from app.booking_service import acquire_professional_lock
                    await acquire_professional_lock(session, tenant_id, values["professional_id"])
                await guard_generated_service(session, tenant_id, resource, values=values)
                from app.management_blocks import guard_catalog_block
                await guard_catalog_block(session, tenant_id, resource, values=values)
                if resource == "working_breaks":
                    await validate_break(session, bind)
                await session.execute(statement, bind)
                if resource == "professional_services":
                    # Service editor can add a link outside the staff drawer.
                    # New base links receive an explicit percentage fallback so
                    # custom-mode earnings never depend on an absent rule.
                    await session.execute(text("""
                        INSERT INTO professional_commission_rules
                           (tenant_id,professional_id,service_id,commission_kind,
                            commission_percent,commission_fixed_cents)
                        SELECT :tenant,:professional,:service,'percent',p.commission_percent,NULL
                        FROM professionals p JOIN services svc
                          ON svc.tenant_id=p.tenant_id AND svc.id=:service
                        WHERE p.tenant_id=:tenant AND p.id=:professional
                          AND p.commission_mode='per_service' AND svc.catalog_kind='base'
                        ON CONFLICT (tenant_id,professional_id,service_id) DO NOTHING
                    """), {"tenant": tenant_id, "professional": values["professional_id"],
                           "service": values["service_id"]})
                    await session.execute(text("""
                        UPDATE professionals SET commission_config_version=commission_config_version+1
                        WHERE tenant_id=:tenant AND id=:professional
                    """), {"tenant": tenant_id, "professional": values["professional_id"]})
                await audit(session, tenant_id, "created", resource, item_id)
    except IntegrityError as exc:
        raise HTTPException(409, "Conflito de cadastro, referência ou intervalo inválido") from exc
    return jsonable_encoder({"id": item_id, "tenant_id": tenant_id, **values})


@router.put("/tenants/{tenant_id}/{resource}/{item_id}")
async def replace_item(
    tenant_id: UUID, resource: str, item_id: UUID,
    payload: dict[str, Any], actor: Actor = Depends(authenticate),
):
    authorize_resource(actor, tenant_id, resource, write=True)
    if resource == "professional_services":
        raise HTTPException(422, "Altere vínculos pela gestão de profissionais ou recadastre o serviço")
    values = validation(resource_model(resource), payload).model_dump()
    check_resource_values(resource, values)
    assignments = ", ".join(f"{field} = :{field}" for field in values)
    if resource == "professionals":
        assignments += ", commission_config_version=commission_config_version+1"
    bind = {"tenant_id": tenant_id, "id": item_id, **values}
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, tenant_id):
                await ensure_tenant(session, tenant_id)
                if resource == "professionals":
                    from app.booking_service import acquire_professional_lock
                    await acquire_professional_lock(session, tenant_id, item_id)
                    current = (await session.execute(text("""
                        SELECT commission_mode,phone_e164 FROM professionals
                        WHERE tenant_id=:tenant AND id=:id FOR SHARE
                    """), {"tenant": tenant_id, "id": item_id})).mappings().one_or_none()
                    if current is None:
                        raise HTTPException(404, "Profissional não encontrado")
                    current_mode = current["commission_mode"]
                    if "commission_mode" in payload and values["commission_mode"] != current_mode:
                        raise HTTPException(409, "Altere o modelo de comissão no cadastro de profissionais")
                    bind["commission_mode"] = current_mode
                    values["commission_mode"] = current_mode
                    if "phone_e164" not in payload:
                        # Preserve a contact saved by a newer version of the UI.
                        bind["phone_e164"] = values["phone_e164"] = current["phone_e164"]
                await guard_generated_service(session, tenant_id, resource, item_id, values)
                from app.management_blocks import guard_catalog_block
                await guard_catalog_block(session, tenant_id, resource, values=values, item_id=item_id)
                if resource == "working_hours":
                    await validate_working_hour_edit(session, bind)
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
    authorize_resource(actor, tenant_id, resource, write=True)
    resource_model(resource)
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with tenant_transaction(session, tenant_id):
                await ensure_tenant(session, tenant_id)
                link_professional = None
                if resource == "professional_services":
                    link_professional = (await session.execute(text("""
                        SELECT professional_id FROM professional_services
                        WHERE tenant_id=:tenant AND id=:id
                    """), {"tenant": tenant_id, "id": item_id})).scalar_one_or_none()
                    if link_professional is not None:
                        from app.booking_service import acquire_professional_lock
                        await acquire_professional_lock(session, tenant_id, link_professional)
                await guard_generated_service(session, tenant_id, resource, item_id)
                from app.management_blocks import guard_catalog_block
                await guard_catalog_block(session, tenant_id, resource, item_id=item_id, deleting=True)
                result = await session.execute(
                    text(f"DELETE FROM {resource} WHERE id = :id AND tenant_id = :t RETURNING id"),
                    {"id": item_id, "t": tenant_id},
                )
                if result.scalar_one_or_none() is None:
                    raise HTTPException(404, "Registro não encontrado")
                if resource == "professional_services" and link_professional is not None:
                    await session.execute(text("""
                        UPDATE professionals SET commission_config_version=commission_config_version+1
                        WHERE tenant_id=:tenant AND id=:professional
                    """), {"tenant": tenant_id, "professional": link_professional})
                await audit(session, tenant_id, "deleted", resource, item_id)
    except IntegrityError as exc:
        raise HTTPException(409, "Registro em uso por outro cadastro") from exc
    return None
