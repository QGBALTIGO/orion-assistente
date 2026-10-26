"""Tenant-owned publication, durable addresses and immutable publication receipts.

This module never sends messages or creates appointments. The caller's explicit
publication action is the only operation that changes the public snapshot.
"""
import json
import re
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from app.catalog_api import audit
from app.database import get_session_factory, tenant_transaction
from app.tenant_access import resolve_site_access, site_publication_access


RESERVED_SLUGS = frozenset({
    "admin", "api", "app", "assets", "auth", "billing", "callback", "dashboard",
    "docs", "health", "help", "internal", "login", "logout", "media", "oauth",
    "panel", "preview", "privacy", "public", "register", "robots", "settings",
    "signup", "sitemap", "static", "status", "support", "terms", "webhook", "webhooks",
})


def validate_slug(slug):
    if (not isinstance(slug, str) or not 3 <= len(slug) <= 80
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)):
        raise HTTPException(422, "Use de 3 a 80 letras minúsculas, números ou hífens no endereço")
    if slug in RESERVED_SLUGS:
        raise HTTPException(422, "Este endereço é reservado. Escolha outro identificador")
    return slug


async def reserve_slug(db, tenant, slug):
    """The primary key arbitrates all tenants and every address kind atomically.

    An empty INSERT result can mean an existing own alias or a foreign slug.
    RLS permits reading only the former; no foreign tenant identity is exposed.
    """
    if slug is None:
        return
    validate_slug(slug)
    inserted = (await db.execute(text("""INSERT INTO public_site_slugs(slug,tenant_id,kind)
        VALUES(:slug,:tenant,'draft') ON CONFLICT(slug) DO NOTHING RETURNING slug"""),
        {"slug": slug, "tenant": tenant})).scalar_one_or_none()
    if inserted is None:
        own = (await db.execute(text("SELECT slug FROM public_site_slugs WHERE slug=:slug AND tenant_id=:tenant"),
                                {"slug": slug, "tenant": tenant})).scalar_one_or_none()
        if own is None:
            raise HTTPException(409, "Este endereço já está em uso. Escolha outro identificador")


async def release_unused_drafts(db, tenant, slug):
    await db.execute(text("""DELETE FROM public_site_slugs WHERE tenant_id=:tenant
        AND kind='draft' AND slug IS DISTINCT FROM :slug"""), {"tenant": tenant, "slug": slug})


async def enable_publication_writer(db):
    """Opt into the reviewed writer within this transaction, never per session.

    Migration 0036 rejects preference writes from an older API during cutover or
    code rollback. This does not grant authority: membership and tenant RLS are
    still required, and it performs no publication or registry action itself.
    """
    await db.execute(text("SELECT set_config('app.site_publication_v2','on',true)"))


async def slug_availability(tenant, actor, slug):
    await resolve_site_access(actor, tenant)
    try:
        validate_slug(slug)
    except HTTPException as exc:
        return {"slug": slug, "available": False, "reason": exc.detail}
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                available = (await db.execute(text("SELECT public.public_site_slug_available(:slug,:tenant)"),
                                             {"slug": slug, "tenant": tenant})).scalar_one()
        return {"slug": slug, "available": available,
                **({} if available else {"reason": "Este endereço já está em uso. Escolha outro identificador"})}
    except SQLAlchemyError:
        raise HTTPException(503, "Não foi possível verificar o endereço agora. Tente novamente") from None


def _issue(code, message, section="config"):
    return {"code": code, "message": message, "section": section}


async def technical_issues(db, tenant, site, *, content=None, infrastructure=True):
    """Check facts, not occupancy: a full calendar today is not a publishing ban."""
    from app.onboarding_service import _facts, _fitting_bindings, require_operational_ready
    from app.public_site_api import catalogue, signing_key, _redis

    issues = []
    facts = await _facts(db, tenant)
    if facts["business"]["status"] != "active":
        issues.append(_issue("business_inactive", "A empresa precisa estar ativa para disponibilizar o site", "business"))
    if not site or not (site.get("title") or "").strip():
        issues.append(_issue("title_required", "Informe o nome público da empresa", "config"))
    try:
        validate_slug((site or {}).get("slug"))
    except HTTPException as exc:
        issues.append(_issue("slug_invalid", exc.detail, "config"))
    try:
        await require_operational_ready(db, tenant, "public_site")
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("pending"):
            issues.extend(_issue(item.get("code", "setup_pending"), item["message"], item.get("step", "business"))
                          for item in detail["pending"])
        else:
            issues.append(_issue("setup_pending", str(detail), "business"))
    content = content if content is not None else await catalogue(db, tenant, preview=True)
    public_professionals = {str(p["id"]) for p in content["professionals"]}
    active_pros = [p for p in facts["professionals"] if str(p["id"]) in public_professionals
                   and p.get("active") and p.get("accepts_appointments")]
    public_ids = {str(s["id"]) for s in content["services"]}
    active_services = [s for s in facts["services"] if str(s["id"]) in public_ids]
    fitting_services, _ = _fitting_bindings(active_pros, active_services, facts)
    if not active_pros:
        issues.append(_issue("professionals_required", "Cadastre um profissional ativo que aceite agendamentos", "professionals"))
    if not active_services:
        issues.append(_issue("services_required", "Cadastre um serviço ativo e vincule-o a um profissional", "services"))
    elif not fitting_services:
        issues.append(_issue("hours_required", "Configure horários e pausas do profissional com tempo para atender um serviço vinculado", "hours"))
    if infrastructure:
        try:
            signing_key()
        except HTTPException:
            issues.append(_issue("booking_signing_unavailable", "O agendamento está temporariamente indisponível. Tente novamente em instantes", "config"))
        cache = _redis()
        try:
            await cache.ping()
        except (RedisError, OSError):
            issues.append(_issue("redis_unavailable", "O agendamento está temporariamente indisponível. Tente novamente em instantes", "config"))
        finally:
            await cache.aclose()
    # A single prerequisite can be found by both onboarding and site checks.
    return list({(item["code"], item["message"]): item for item in issues}.values())


async def publication_status(tenant, actor):
    from app.management_models import site as read_site
    from app.public_site_api import catalogue
    access = await resolve_site_access(actor, tenant)
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                row = await read_site(db, tenant)
                content = await catalogue(db, tenant, preview=True)
                issues = await technical_issues(db, tenant, row, content=content)
                history = (await db.execute(text("""SELECT version,published_at,action
                    FROM public_site_publications WHERE tenant_id=:tenant ORDER BY id DESC LIMIT 30"""),
                    {"tenant": tenant})).mappings().all()
                state = ("pending_changes" if row["has_unpublished_changes"] else "published") if row["published"] else (
                    "unpublished" if row.get("public_version", 0) else "ready" if row["version"] and not issues else
                    "blocked" if row["version"] and issues else "not_published")
                if row["published"] and issues:
                    state = "blocked"
                return {"state": state, "can_edit": access.role in {"owner", "editor", "admin", "support"},
                        "can_publish": access.role == "owner", "ready": not issues, "issues": issues,
                        "draft_version": row["version"], "public_version": row.get("public_version", 0),
                        "published_at": row.get("public_enabled_at"), "public_url": row["public_url"],
                        "history": [dict(item) for item in history]}
    except SQLAlchemyError:
        raise HTTPException(503, "A publicação está temporariamente indisponível. Tente novamente") from None


async def apply_publication(tenant, payload, actor):
    try:
        async with site_publication_access(actor, tenant) as access:
            return await _apply_publication(tenant, payload, access)
    except SQLAlchemyError:
        raise HTTPException(503, "A publicação está temporariamente indisponível. Tente novamente") from None


async def _apply_publication(tenant, payload, access):
    from app.management_models import site as read_site
    from app.public_site_api import PUBLIC_FIELDS, catalogue
    if access.role != "owner":
        raise HTTPException(403, "Somente o proprietário pode publicar ou retirar este site")
    if payload.restore_previous and not payload.enabled:
        raise HTTPException(422, "A restauração exige reativar a publicação anterior")
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant, repeatable_read=True):
                await enable_publication_writer(db)
                current = (await db.execute(text("SELECT * FROM management_site_preferences WHERE tenant_id=:tenant FOR UPDATE"),
                                            {"tenant": tenant})).mappings().one_or_none()
                if not current or current["version"] != payload.expected_version:
                    raise HTTPException(409, "As preferências mudaram. Salve e atualize antes de publicar")
                current = dict(current)
                snapshot = current.get("public_snapshot")
                slug = current.get("public_slug")
                if payload.restore_previous:
                    if current["public_enabled"] or not snapshot:
                        raise HTTPException(409, "Não há uma publicação retirada do ar para reativar")
                    candidate = dict(snapshot)
                else:
                    candidate = current
                if payload.enabled:
                    content = await catalogue(db, tenant, preview=True)
                    if payload.restore_previous:
                        from app.public_site_rendering import project_catalogue_visuals
                        content = project_catalogue_visuals(content, candidate)
                    issues = await technical_issues(db, tenant, candidate, content=content)
                    if issues:
                        infrastructure_failure = any(item["code"] in {"redis_unavailable", "booking_signing_unavailable"} for item in issues)
                        raise HTTPException(503 if infrastructure_failure else 409,
                                            {"message": "Corrija as pendências para publicar", "issues": issues})
                    slug = candidate["slug"]
                    await reserve_slug(db, tenant, slug)
                    await db.execute(text("UPDATE public_site_slugs SET kind='alias' WHERE tenant_id=:tenant AND kind='canonical' AND slug<>:slug"),
                                     {"tenant": tenant, "slug": slug})
                    await db.execute(text("UPDATE public_site_slugs SET kind='canonical' WHERE tenant_id=:tenant AND slug=:slug"),
                                     {"tenant": tenant, "slug": slug})
                    if not payload.restore_previous:
                        snapshot = {key: current[key] for key in PUBLIC_FIELDS}
                        snapshot["_catalogue"] = {key: content[key] for key in ("services", "professionals", "opening_hours")}
                        snapshot = jsonable_encoder(snapshot)
                version = current["version"] + 1
                public_version = version if payload.enabled else current["public_version"]
                # now() is the transaction start, potentially before a booking
                # released our publication row lock. Persist the actual decision
                # time consistently in both preferences and immutable history.
                published_at = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
                await db.execute(text("""UPDATE management_site_preferences SET public_enabled=:enabled,
                    public_enabled_at=CASE WHEN :enabled THEN :published_at ELSE public_enabled_at END,
                    public_disabled_at=CASE WHEN :enabled THEN NULL ELSE :published_at END,
                    public_slug=:slug,public_snapshot=CAST(:snapshot AS jsonb),
                    public_version=:public_version,version=:version,updated_at=now() WHERE tenant_id=:tenant"""),
                    {"tenant": tenant, "enabled": payload.enabled, "slug": slug,
                     "snapshot": json.dumps(snapshot), "public_version": public_version, "version": version,
                     "published_at": published_at})
                action = "restore" if payload.restore_previous else "publish" if payload.enabled else "unpublish"
                await db.execute(text("""INSERT INTO public_site_publications
                    (tenant_id,version,action,snapshot,slug,actor_account_id,published_at)
                    VALUES(:tenant,:version,:action,CAST(:snapshot AS jsonb),:slug,:actor,:published_at)"""),
                    {"tenant": tenant, "version": version, "action": action, "snapshot": json.dumps(snapshot),
                     "slug": slug, "actor": access.account_id, "published_at": published_at})
                await audit(db, tenant, "site_" + action, "management_site_preferences", tenant)
                return await read_site(db, tenant)
    except IntegrityError:
        raise HTTPException(409, "Este endereço público já está em uso. Escolha outro endereço e salve") from None
    except SQLAlchemyError as exc:
        original = getattr(exc, "orig", None)
        if getattr(original, "sqlstate", None) in {"40001", "40P01"}:
            raise HTTPException(409, "Os dados mudaram durante a publicação. Atualize e tente novamente") from None
        raise HTTPException(503, "A publicação está temporariamente indisponível. Nenhuma alteração foi confirmada") from None
