"""FATIA 8 — recursos avançados desabilitados para envio externo por padrão."""
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter,Depends,HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,authorize
from app.catalog_schemas import StrictModel
from app.database import get_session_factory,tenant_transaction

router=APIRouter(prefix="/api/v1/tenants/{tenant_id}",tags=["Inbox e CRM internos"])


class Consent(StrictModel):
    contact_e164:str=Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    marketing_opt_in:bool=False
    source:Literal["support","whatsapp","customer_consent"]="support"


class CampaignDraft(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    template_name:str=Field(pattern=r"^[a-z0-9_]{1,120}$")


class WorkflowStep(StrictModel):
    kind:Literal["tag","handoff","reminder_draft"]
    label:str=Field(min_length=1,max_length=120)


class WorkflowDraft(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    steps:list[WorkflowStep]=Field(min_length=1,max_length=10)


@router.post("/crm/consent")
async def set_consent(tenant_id:UUID,payload:Consent,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            await s.execute(text("""
                INSERT INTO contact_consents
                (tenant_id,contact_e164,marketing_opt_in,source)
                VALUES (:tenant,:phone,:allowed,:source)
                ON CONFLICT (tenant_id,contact_e164) DO UPDATE
                SET marketing_opt_in=EXCLUDED.marketing_opt_in,
                    source=EXCLUDED.source,updated_at=now()
            """),{"tenant":tenant_id,"phone":payload.contact_e164,
                  "allowed":payload.marketing_opt_in,"source":payload.source})
    return {"recorded":True}


@router.get("/inbox/handoffs")
async def handoffs(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            rows=(await s.execute(text("""
                SELECT id,contact_e164,status,expires_at
                FROM human_handoffs WHERE tenant_id=:tenant
                ORDER BY created_at DESC LIMIT 100
            """),{"tenant":tenant_id})).mappings().all()
            return jsonable_encoder(rows)


@router.post("/inbox/handoffs/{handoff_id}/resolve")
async def resolve(tenant_id:UUID,handoff_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            result=await s.execute(text("""
                UPDATE human_handoffs SET status='resolved',expires_at=now()
                WHERE tenant_id=:tenant AND id=:id RETURNING id
            """),{"tenant":tenant_id,"id":handoff_id})
            if result.scalar_one_or_none() is None:
                raise HTTPException(404,"Conversa não encontrada")
    return {"status":"resolved"}


@router.post("/campaigns/draft",status_code=201)
async def draft_campaign(tenant_id:UUID,payload:CampaignDraft,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    from uuid import uuid4
    campaign_id=uuid4()
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            await s.execute(text("""
                INSERT INTO campaigns (id,tenant_id,name,template_name,status)
                VALUES (:id,:tenant,:name,:template,'draft')
            """),{"id":campaign_id,"tenant":tenant_id,
                  "name":payload.name,"template":payload.template_name})
    return {"id":str(campaign_id),"status":"draft"}


@router.get("/campaigns/{campaign_id}/eligible-count")
async def count_eligible(tenant_id:UUID,campaign_id:UUID,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id)
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            exists=(await s.execute(text("""
                SELECT id FROM campaigns WHERE tenant_id=:tenant AND id=:id
            """),{"tenant":tenant_id,"id":campaign_id})).scalar_one_or_none()
            if not exists:raise HTTPException(404,"Campanha não encontrada")
            count=(await s.execute(text("""
                SELECT count(*) FROM contact_consents WHERE tenant_id=:tenant
                  AND marketing_opt_in=true
            """),{"tenant":tenant_id})).scalar_one()
            return {"eligible_contacts":count}


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign_locked(tenant_id:UUID,campaign_id:UUID,
                               actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    # Proibido envio não solicitado sem aprovação Meta/consentimento/validadores.
    raise HTTPException(423,"Campanhas externas indisponíveis até aprovação e revisão")


@router.post("/workflows/draft",status_code=201)
async def draft_workflow(tenant_id:UUID,payload:WorkflowDraft,actor:Actor=Depends(authenticate)):
    authorize(actor,tenant_id,write=True)
    from uuid import uuid4
    flow_id=uuid4()
    async with get_session_factory()() as s:
        async with tenant_transaction(s,tenant_id):
            await s.execute(text("""
                INSERT INTO workflow_definitions (id,tenant_id,name,steps,enabled)
                VALUES (:id,:tenant,:name,CAST(:steps AS jsonb),false)
            """),{"id":flow_id,"tenant":tenant_id,"name":payload.name,
                  "steps":json.dumps([step.model_dump() for step in payload.steps])})
    return {"id":str(flow_id),"enabled":False}
