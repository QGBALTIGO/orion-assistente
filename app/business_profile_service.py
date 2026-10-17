"""Transactional profile revisions. Publish/rollback never change the booking catalog."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from app.business_profile_schemas import BusinessProfile, SaveDraft, SelectVersion
from app.database import tenant_transaction
from app.tenant_access import TenantAccess, check_access


async def _tenant(session, tenant_id):
    row = (await session.execute(text("SELECT id,timezone FROM tenants WHERE id=:tenant"),
                                 {"tenant": tenant_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Estabelecimento não encontrado")
    return row


async def _state(session, tenant_id, expected):
    await _tenant(session, tenant_id)
    await session.execute(text("""
        INSERT INTO business_profile_state (tenant_id) VALUES (:tenant)
        ON CONFLICT (tenant_id) DO NOTHING
    """), {"tenant": tenant_id})
    state = (await session.execute(text("""
        SELECT draft_version_id,published_version_id,generation
        FROM business_profile_state WHERE tenant_id=:tenant FOR UPDATE
    """), {"tenant": tenant_id})).mappings().one()
    if state["generation"] != expected:
        raise HTTPException(409, "O perfil mudou. Atualize antes de salvar ou publicar")
    return state


async def _event(session, tenant_id, version_id, action, access):
    await session.execute(text("""
        INSERT INTO business_profile_events
            (tenant_id,version_id,action,actor_account_id,actor_role)
        VALUES (:tenant,:version,:action,:account,:role)
    """), {"tenant": tenant_id, "version": version_id, "action": action,
           "account": access.account_id, "role": access.role})
    await session.execute(text("""
        INSERT INTO tenant_audit (tenant_id,action,entity,entity_id)
        VALUES (:tenant,:action,'business_profile',:version)
    """), {"tenant": tenant_id, "action": action, "version": version_id})


async def read_profile(session, tenant_id: UUID):
    async with tenant_transaction(session, tenant_id):
        tenant = await _tenant(session, tenant_id)
        state = (await session.execute(text("""
            SELECT draft_version_id,published_version_id,generation
            FROM business_profile_state WHERE tenant_id=:tenant
        """), {"tenant": tenant_id})).mappings().first()
        result = {"generation": state["generation"] if state else 0,
                  "draft": None, "published": None,
                  "scheduling_timezone": tenant["timezone"], "warnings": []}
        if state:
            for label in ("draft", "published"):
                version_id = state[label + "_version_id"]
                if version_id:
                    row = (await session.execute(text("""
                        SELECT id,revision,profile,created_at FROM business_profile_versions
                        WHERE tenant_id=:tenant AND id=:version
                    """), {"tenant": tenant_id, "version": version_id})).mappings().one()
                    result[label] = dict(row)
                    if row["profile"].get("timezone") not in (None, tenant["timezone"]):
                        result["warnings"].append(label + "_timezone_differs_from_scheduling")
                    if row["profile"].get("hours"):
                        result["warnings"].append(label + "_business_hours_do_not_change_professional_availability")
        return result


async def published_profile(session, tenant_id: UUID) -> BusinessProfile | None:
    """Shared engine entrypoint: no draft fallback and no fictional business facts."""
    snapshot = await published_profile_snapshot(session, tenant_id)
    return snapshot["profile"] if snapshot else None


async def published_profile_snapshot(session, tenant_id: UUID):
    """A version key lets conversation state detect changed published settings."""
    async with tenant_transaction(session, tenant_id):
        row = (await session.execute(text("""
            SELECT v.id,v.revision,v.profile FROM business_profile_state s
            JOIN business_profile_versions v
              ON v.tenant_id=s.tenant_id AND v.id=s.published_version_id
            WHERE s.tenant_id=:tenant
        """), {"tenant": tenant_id})).mappings().first()
    if row is None:
        return None
    return {"id": row["id"], "revision": row["revision"],
            "profile": BusinessProfile.model_validate(row["profile"])}


async def save_draft(session, tenant_id: UUID, payload: SaveDraft, access: TenantAccess):
    check_access(access.role, "draft")
    version_id = uuid4()
    async with tenant_transaction(session, tenant_id):
        await _state(session, tenant_id, payload.expected_generation)
        revision = (await session.execute(text("""
            SELECT coalesce(max(revision),0)+1 FROM business_profile_versions
            WHERE tenant_id=:tenant
        """), {"tenant": tenant_id})).scalar_one()
        await session.execute(text("""
            INSERT INTO business_profile_versions
                (id,tenant_id,revision,profile,created_by,actor_role)
            VALUES (:id,:tenant,:revision,CAST(:profile AS jsonb),:account,:role)
        """), {"id": version_id, "tenant": tenant_id, "revision": revision,
               "profile": payload.profile.model_dump_json(), "account": access.account_id,
               "role": access.role})
        await session.execute(text("""
            UPDATE business_profile_state
            SET draft_version_id=:version,generation=generation+1,updated_at=now()
            WHERE tenant_id=:tenant
        """), {"tenant": tenant_id, "version": version_id})
        await _event(session, tenant_id, version_id, "draft_saved", access)
    return {"version_id": version_id, "revision": revision,
            "generation": payload.expected_generation + 1, "status": "draft"}


async def select_version(session, tenant_id: UUID, payload: SelectVersion,
                         access: TenantAccess, *, rollback: bool = False):
    check_access(access.role, "publish")
    async with tenant_transaction(session, tenant_id):
        state = await _state(session, tenant_id, payload.expected_generation)
        row = (await session.execute(text("""
            SELECT revision FROM business_profile_versions
            WHERE tenant_id=:tenant AND id=:version
        """), {"tenant": tenant_id, "version": payload.version_id})).mappings().first()
        if row is None:
            raise HTTPException(404, "Versão não encontrada neste estabelecimento")
        if rollback:
            published_before = (await session.execute(text("""
                SELECT 1 FROM business_profile_events WHERE tenant_id=:tenant
                  AND version_id=:version AND action IN ('published','rollback') LIMIT 1
            """), {"tenant": tenant_id, "version": payload.version_id})).scalar_one_or_none()
            if not published_before:
                raise HTTPException(409, "Só é possível restaurar uma versão já publicada")
            if state["published_version_id"] == payload.version_id:
                raise HTTPException(409, "Esta versão já está publicada")
        elif state["draft_version_id"] != payload.version_id:
            raise HTTPException(409, "Publique somente o rascunho atual")
        # Rollback preserves the current draft for later review.
        await session.execute(text("""
            UPDATE business_profile_state SET published_version_id=:version,
                draft_version_id=CASE WHEN :rollback THEN draft_version_id ELSE NULL END,
                generation=generation+1,updated_at=now() WHERE tenant_id=:tenant
        """), {"tenant": tenant_id, "version": payload.version_id, "rollback": rollback})
        await _event(session, tenant_id, payload.version_id,
                     "rollback" if rollback else "published", access)
    return {"version_id": payload.version_id, "revision": row["revision"],
            "generation": payload.expected_generation + 1, "status": "published"}


async def profile_history(session, tenant_id: UUID):
    async with tenant_transaction(session, tenant_id):
        await _tenant(session, tenant_id)
        rows = (await session.execute(text("""
            SELECT v.id,v.revision,v.created_at,EXISTS(
                SELECT 1 FROM business_profile_events e
                WHERE e.tenant_id=v.tenant_id AND e.version_id=v.id
                  AND e.action IN ('published','rollback')) AS published_before
            FROM business_profile_versions v WHERE v.tenant_id=:tenant
            ORDER BY v.revision DESC LIMIT 100
        """), {"tenant": tenant_id})).mappings().all()
        return [dict(row) for row in rows]


async def profile_version(session, tenant_id: UUID, version_id: UUID):
    """Allow reviewing a historical revision before selecting it for rollback."""
    async with tenant_transaction(session, tenant_id):
        row = (await session.execute(text("""
            SELECT id,revision,profile,created_at FROM business_profile_versions
            WHERE tenant_id=:tenant AND id=:version
        """), {"tenant": tenant_id, "version": version_id})).mappings().first()
        if row is None:
            raise HTTPException(404, "Versão não encontrada neste estabelecimento")
        return dict(row)
