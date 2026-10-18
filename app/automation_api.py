"""Explicit owner review, consent and activation; GET never starts delivery."""
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit, support_session_factory
from app.management_api import management_session
from app.management_models import request_identity
from app.tenant_access import resolve_tenant_access
from app.automation_runtime import base_labels, contact_facts, execution_enabled, execution_authorized, reconciliation_enabled, queue_notification, settings_for
from app.automation_policy import AUDIENCE_SQL, feature_status, unavailable_requested

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management/marketing", tags=["Execução revisada de automações"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExecutionSave(Strict):
    expected_version: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=120)
    enabled: StrictBool
    channel_id: UUID | None = None
    daily_limit: StrictInt = Field(default=20, ge=1, le=100)
    acknowledged: StrictBool = False
    expected_preferences_version: StrictInt | None = Field(default=None, ge=1)


class ContactConsent(Strict):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    marketing_opt_in: StrictBool
    operational_opt_in: StrictBool
    expected_updated_at: datetime | None = None
    evidence: str = Field(min_length=8, max_length=200)


class CampaignApproval(Strict):
    expected_version: StrictInt = Field(ge=1)
    expected_snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=120)
    acknowledged: StrictBool


class ContactPause(Strict):
    contact_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    paused: StrictBool


async def owner(tenant_id, actor):
    access = await resolve_tenant_access(actor, tenant_id, "publish")
    if actor.account_id is None:
        raise HTTPException(409, "Entre com a conta pessoal proprietária para autorizar envios")
    # Global administrative access does not silently attest a business owner's consent.
    async with support_session_factory()() as db:
        result = (await db.execute(text("""SELECT p.auth_version FROM tenant_memberships m JOIN panel_accounts p ON p.id=m.account_id
          WHERE m.tenant_id=:t AND m.account_id=:account AND m.active AND m.role='owner'"""),
          {"t": tenant_id, "account": actor.account_id})).scalar_one_or_none()
    if result is None:
        raise HTTPException(403, "A autorização de envio pertence ao proprietário atual deste negócio")
    return result


@asynccontextmanager
async def owner_write_session(tenant, actor):
    await resolve_tenant_access(actor, tenant, "publish")
    if not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Entre com a conta proprietária")
    async with support_session_factory()() as auth:
        async with auth.begin():
            version = (await auth.execute(text("""SELECT p.auth_version FROM tenant_memberships m
                JOIN panel_accounts p ON p.id=m.account_id WHERE m.tenant_id=:t AND m.account_id=:a
                AND m.active AND m.role='owner' FOR SHARE OF m,p"""),
                {"t": tenant, "a": actor.account_id})).scalar_one_or_none()
            if version is None:
                raise HTTPException(403, "A autorização pertence ao proprietário atual")
            async with management_session(tenant, actor, write=True) as db:
                db.info["automation_owner_auth_version"] = version
                yield db


async def checked_channel(tenant, channel_id):
    from app.qr_channels import outbound_channel_credentials
    if channel_id is None:
        raise HTTPException(422, "Escolha uma conexão QR autorizada")
    try:
        return await outbound_channel_credentials(tenant, channel_id)
    except HTTPException:
        raise
    except (ValueError, RuntimeError):
        raise HTTPException(409, "A conexão ainda não está pronta para envios autorizados") from None


@router.get("/execution")
async def get_execution(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        if not execution_enabled() and not reconciliation_enabled():
            return {"available": False, "enabled": False, "version": 0, "mode": "draft", "reason": "server_activation_pending", "features": feature_status()}
        settings = await settings_for(db, tenant_id)
        counts = (await db.execute(text("SELECT status,count(*) AS count FROM automation_jobs WHERE tenant_id=:t GROUP BY status"), {"t": tenant_id})).mappings().all()
        preferences_version = settings.get("preferences_version") if settings else (await db.execute(text("SELECT version FROM management_preferences WHERE tenant_id=:t"), {"t": tenant_id})).scalar_one_or_none()
        return {"available": True, "enabled": bool(execution_enabled() and execution_authorized(settings)),
                "requested_enabled": bool(settings and settings["enabled"]), "reconciliation_only": not execution_enabled(),
                "preferences_version": preferences_version, "reviewed_preferences_version": settings.get("reviewed_preferences_version") if settings else None,
                "reason": "waitlist_contract_missing" if settings and settings.get("vacancy_requested") else
                          "review_required" if settings and settings["enabled"] and not execution_authorized(settings) else None,
                "version": settings["version"] if settings else 0, "channel_id": settings["channel_id"] if settings else None,
                "daily_limit": settings["daily_limit"] if settings else 20, "mode": "reviewed_execution",
                "window": "09:00–18:00", "timezone": settings["timezone"] if settings else None,
                "counts": {r["status"]: r["count"] for r in counts}, "transport": "qr_managed",
                "features": feature_status(),
                "uncertain_policy": "manual_reconciliation_no_automatic_retry"}


@router.put("/execution")
async def save_execution(tenant_id: UUID, payload: ExecutionSave, actor: Actor = Depends(authenticate)):
    if not execution_enabled():
        raise HTTPException(423, "A execução de automações ainda não foi habilitada nesta instalação")
    auth_version = await owner(tenant_id, actor)
    if payload.enabled:
        if not payload.acknowledged:
            raise HTTPException(422, "Revise e autorize os envios, o limite diário e a janela de 9h às 18h")
        await checked_channel(tenant_id, payload.channel_id)
    async with owner_write_session(tenant_id, actor) as db:
        auth_version = db.info["automation_owner_auth_version"]
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": "automation-settings:" + str(tenant_id)})
        current = await settings_for(db, tenant_id, lock=True)
        _, replay = await request_identity(db, tenant_id, "automation_execution", payload.idempotency_key, payload.model_dump(mode="json"), entity_id=tenant_id)
        if replay:
            if current is None:
                raise HTTPException(409, "A configuração registrada não está disponível; revise antes de autorizar novamente")
            return {"recorded": True, "version": current["version"], "enabled": execution_authorized(current)}
        if payload.expected_version != (current["version"] if current else 0):
            raise HTTPException(409, "A autorização mudou. Consulte a configuração atual")
        preference_version = (await db.execute(text("SELECT version FROM management_preferences WHERE tenant_id=:t FOR SHARE"), {"t": tenant_id})).scalar_one_or_none()
        if payload.enabled and (preference_version is None or payload.expected_preferences_version != preference_version):
            raise HTTPException(409, "As rotinas mudaram ou ainda não foram configuradas. Revise a versão atual antes de autorizar")
        if payload.enabled:
            preferences = (await db.execute(text("SELECT preferences FROM management_preferences WHERE tenant_id=:t"), {"t": tenant_id})).scalar_one_or_none()
            if unavailable_requested(preferences):
                raise HTTPException(409, "A reposição de vagas está em rascunho e indisponível: ainda não existe lista de espera com preferências e consentimento próprios. Desmarque esse pedido e revise as demais rotinas antes de habilitar")
        await db.execute(text("""INSERT INTO automation_execution(tenant_id,enabled,channel_id,approved_by,approved_auth_version,approved_at,daily_limit,reviewed_preferences_version)
          VALUES(:t,:enabled,:channel,:account,:auth,now(),:limit,:prefs) ON CONFLICT(tenant_id) DO UPDATE
          SET enabled=EXCLUDED.enabled,channel_id=EXCLUDED.channel_id,approved_by=EXCLUDED.approved_by,
              approved_auth_version=EXCLUDED.approved_auth_version,approved_at=now(),daily_limit=EXCLUDED.daily_limit,
              reviewed_preferences_version=EXCLUDED.reviewed_preferences_version,
              version=automation_execution.version+1,updated_at=now()"""),
          {"t": tenant_id, "enabled": payload.enabled, "channel": payload.channel_id, "account": actor.account_id, "auth": auth_version, "limit": payload.daily_limit, "prefs": preference_version})
        await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='configuration_changed',updated_at=now() WHERE tenant_id=:t AND status='pending'"), {"t": tenant_id})
        await audit(db, tenant_id, "automation_execution_reviewed", "automation_execution", tenant_id)
        result = await settings_for(db, tenant_id)
        return {"recorded": True, "version": result["version"], "enabled": execution_authorized(result)}


@router.put("/consent")
async def consent(tenant_id: UUID, payload: ContactConsent, actor: Actor = Depends(authenticate)):
    if not execution_enabled():
        raise HTTPException(423, "Execução ainda não habilitada")
    await owner(tenant_id, actor)
    async with owner_write_session(tenant_id, actor) as db:
        customer = (await db.execute(text("SELECT id FROM customers WHERE tenant_id=:t AND whatsapp_e164=:phone AND active AND deleted_at IS NULL"), {"t": tenant_id, "phone": payload.contact_e164})).scalar_one_or_none()
        if customer is None:
            raise HTTPException(404, "Contato não encontrado neste negócio")
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": str(tenant_id) + ":automation-consent:" + payload.contact_e164})
        current = (await db.execute(text("SELECT updated_at FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone FOR UPDATE"), {"t": tenant_id, "phone": payload.contact_e164})).scalar_one_or_none()
        if current != payload.expected_updated_at:
            raise HTTPException(409, "O consentimento mudou. Consulte o registro atual")
        await db.execute(text("""INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in,operational_opt_in,source)
          VALUES(:t,:phone,:marketing,:operational,'owner_attestation') ON CONFLICT(tenant_id,contact_e164) DO UPDATE
          SET marketing_opt_in=EXCLUDED.marketing_opt_in,operational_opt_in=EXCLUDED.operational_opt_in,
              source=EXCLUDED.source,updated_at=GREATEST(clock_timestamp(),contact_consents.updated_at+interval '1 microsecond')"""),
          {"t": tenant_id, "phone": payload.contact_e164, "marketing": payload.marketing_opt_in, "operational": payload.operational_opt_in})
        # Evidence is retained only as a hash, never echoed into logs/queue bodies.
        await audit(db, tenant_id, "consent_reviewed:" + hashlib.sha256(payload.evidence.encode()).hexdigest()[:12], "contact_consents", customer)
        await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='consent_changed',updated_at=now() WHERE tenant_id=:t AND contact_e164=:phone AND status='pending'"), {"t": tenant_id, "phone": payload.contact_e164})
        updated = (await db.execute(text("SELECT updated_at FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone"), {"t": tenant_id, "phone": payload.contact_e164})).scalar_one()
        return {"recorded": True, "updated_at": updated}


@router.get("/consent")
async def get_consent(tenant_id: UUID, contact_e164: str = Query(pattern=r"^\+55[1-9][0-9]{9,10}$"), actor: Actor = Depends(authenticate)):
    await owner(tenant_id, actor)
    phone = ContactConsent(contact_e164=contact_e164, marketing_opt_in=False, operational_opt_in=False, evidence="read only").contact_e164
    async with management_session(tenant_id, actor) as db:
        if not execution_enabled() and not reconciliation_enabled():
            return {"available": False}
        row, state = await contact_facts(db, tenant_id, phone)
        values = {k: row[k] for k in ("marketing_opt_in", "operational_opt_in", "updated_at")} if row else {"marketing_opt_in": False, "operational_opt_in": False, "updated_at": None}
        return {"available": True, "human_paused": bool(state and state.get("human_paused")), **values}


@router.put("/contact-pause")
async def contact_pause(tenant_id: UUID, payload: ContactPause, actor: Actor = Depends(authenticate)):
    if not execution_enabled():
        raise HTTPException(423, "A execução está pausada nesta instalação")
    async with owner_write_session(tenant_id, actor) as db:
        customer = (await db.execute(text("SELECT id FROM customers WHERE tenant_id=:t AND whatsapp_e164=:phone AND active AND deleted_at IS NULL"), {"t": tenant_id, "phone": payload.contact_e164})).scalar_one_or_none()
        if customer is None:
            raise HTTPException(404, "Contato não encontrado neste negócio")
        changed = (await db.execute(text("""INSERT INTO automation_contact_state(tenant_id,contact_e164,human_paused)
            VALUES(:t,:phone,:paused) ON CONFLICT(tenant_id,contact_e164) DO UPDATE
            SET human_paused=EXCLUDED.human_paused,revision=automation_contact_state.revision+1,
                last_interaction_at=clock_timestamp() WHERE automation_contact_state.human_paused IS DISTINCT FROM EXCLUDED.human_paused RETURNING revision"""),
            {"t": tenant_id, "phone": payload.contact_e164, "paused": payload.paused})).scalar_one_or_none()
        if changed:
            await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='conversation_changed',updated_at=now() WHERE tenant_id=:t AND contact_e164=:phone AND status='pending'"), {"t": tenant_id, "phone": payload.contact_e164})
            await audit(db, tenant_id, "automation_contact_paused" if payload.paused else "automation_contact_resumed", "customers", customer)
        return {"recorded": True, "paused": payload.paused, "changed": changed is not None}


@router.get("/jobs")
async def jobs(tenant_id: UUID, limit: int = Query(default=50, ge=1, le=200), actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        if not execution_enabled() and not reconciliation_enabled():
            return {"items": [], "available": False}
        items = (await db.execute(text("""SELECT id,event,status,delivery_status,due_at,expires_at,last_error,attempted_at,updated_at,
          right(contact_e164,4) AS recipient_suffix FROM automation_jobs WHERE tenant_id=:t ORDER BY created_at DESC,id LIMIT :limit"""), {"t": tenant_id, "limit": limit})).mappings().all()
        return {"items": [dict(r) for r in items], "available": True}


@router.post("/jobs/{job_id}/reconcile")
async def reconcile(tenant_id: UUID, job_id: UUID, actor: Actor = Depends(authenticate)):
    await owner(tenant_id, actor)
    from app.automation_runtime import reconcile_automation_job
    return {"result": await reconcile_automation_job({}, str(tenant_id), str(job_id)), "resent": False}


async def campaign_review(db, tenant, identity):
    campaign = (await db.execute(text("SELECT * FROM campaigns WHERE tenant_id=:t AND id=:id FOR UPDATE"), {"t": tenant, "id": identity})).mappings().one_or_none()
    if not campaign:
        raise HTTPException(404, "Campanha não encontrada")
    audience = campaign["audience"]
    if audience not in AUDIENCE_SQL:
        raise HTTPException(409, "A audiência precisa de revisão")
    recipients = (await db.execute(text("""SELECT c.*,k.updated_at AS consent_version FROM customers c JOIN tenants t ON t.id=c.tenant_id
      JOIN contact_consents k ON k.tenant_id=c.tenant_id AND k.contact_e164=c.whatsapp_e164
      WHERE c.tenant_id=:t AND c.active AND c.deleted_at IS NULL AND k.marketing_opt_in AND (""" + AUDIENCE_SQL[audience] + ") ORDER BY c.id LIMIT 1001"), {"t": tenant})).mappings().all()
    if len(recipients) > 1000:
        raise HTTPException(409, "Divida a audiência em lotes de até mil contatos autorizados")
    snapshot = hashlib.sha256(json.dumps({"campaign": str(identity), "version": campaign["version"],
      "contacts": [{"id": str(c["id"]), "version": c["version"], "contact": c["whatsapp_e164"],
                    "consent": c["consent_version"].isoformat()} for c in recipients]}, sort_keys=True).encode()).hexdigest()
    return dict(campaign), recipients, snapshot


@router.get("/{campaign_id}/review")
async def review(tenant_id: UUID, campaign_id: UUID, actor: Actor = Depends(authenticate)):
    await owner(tenant_id, actor)
    async with management_session(tenant_id, actor) as db:
        campaign, recipients, snapshot = await campaign_review(db, tenant_id, campaign_id)
        return {"id": campaign_id, "version": campaign["version"], "message": campaign["message"],
                "eligible_count": len(recipients), "snapshot": snapshot, "status": campaign["status"],
                "execution_available": execution_enabled()}


@router.post("/{campaign_id}/approve")
async def approve(tenant_id: UUID, campaign_id: UUID, payload: CampaignApproval, actor: Actor = Depends(authenticate)):
    if not execution_enabled() or not payload.acknowledged:
        raise HTTPException(423, "Revise e autorize a campanha na execução habilitada")
    await owner(tenant_id, actor)
    async with owner_write_session(tenant_id, actor) as db:
        settings = await settings_for(db, tenant_id, lock=True)
        if not execution_authorized(settings):
            raise HTTPException(409, "Autorize primeiro o canal de envio deste negócio")
        _, replay = await request_identity(db, tenant_id, "automation_campaign", payload.idempotency_key, payload.model_dump(mode="json"), entity_id=campaign_id)
        if replay:
            return {"queued": True, "replayed": True}
        campaign, recipients, snapshot = await campaign_review(db, tenant_id, campaign_id)
        if campaign["status"] != "draft" or campaign["version"] != payload.expected_version or snapshot != payload.expected_snapshot:
            raise HTTPException(409, "A campanha ou a audiência mudou. Revise novamente")
        preference = (await db.execute(text("SELECT preferences,version FROM management_preferences WHERE tenant_id=:t"), {"t": tenant_id})).mappings().one_or_none()
        if not preference:
            raise HTTPException(409, "Configure as preferências antes de preparar o envio")
        count = 0
        for customer in recipients:
            await db.execute(text("""INSERT INTO campaign_recipients(tenant_id,campaign_id,contact_e164,status)
              VALUES(:t,:campaign,:phone,'approved') ON CONFLICT(tenant_id,campaign_id,contact_e164) DO NOTHING"""), {"t": tenant_id, "campaign": campaign_id, "phone": customer["whatsapp_e164"]})
            count += await queue_notification(db, tenant_id, settings, preference, customer, "campaign", "campaign:" + str(campaign_id),
                {"message": campaign["message"], "scheduled_at": campaign["scheduled_at"], "campaign_version": campaign["version"]+1}, base_labels(settings, customer, preference), campaign_id=campaign_id)
        if not count:
            raise HTTPException(409, "Nenhum envio elegível. Confira consentimentos, horário planejado e os dados usados na mensagem")
        await db.execute(text("UPDATE campaigns SET status='approved',version=version+1,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant_id, "id": campaign_id})
        await audit(db, tenant_id, "campaign_approved", "campaigns", campaign_id)
        return {"queued": True, "recipients": count, "replayed": False}


@router.post("/{campaign_id}/pause")
async def pause(tenant_id: UUID, campaign_id: UUID, actor: Actor = Depends(authenticate)):
    await owner(tenant_id, actor)
    async with owner_write_session(tenant_id, actor) as db:
        campaign, _, _ = await campaign_review(db, tenant_id, campaign_id)
        await db.execute(text("UPDATE campaigns SET status='paused',version=version+1,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant_id, "id": campaign_id})
        if execution_enabled():
            await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='campaign_paused',updated_at=now() WHERE tenant_id=:t AND campaign_id=:id AND status='pending'"), {"t": tenant_id, "id": campaign_id})
        return {"status": "paused"}
