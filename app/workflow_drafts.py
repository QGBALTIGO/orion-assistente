"""Revisioned editor/simulator over the existing disabled workflow table."""
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter,Depends,HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field,StrictInt,field_validator
from sqlalchemy import text

from app.catalog_api import Actor,authenticate,audit
from app.catalog_schemas import StrictModel
from app.management_api import management_session
from app.inbox_api import enabled

router=APIRouter(prefix='/api/v1/tenants/{tenant_id}/management/workflows',tags=['Fluxos em rascunho'])


class Step(StrictModel):
    kind:Literal['tag','handoff','reminder_draft']
    label:str=Field(min_length=1,max_length=120)

    @field_validator('label')
    @classmethod
    def label_text(cls,value):
        value=' '.join(value.split())
        if not value or any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in value):
            raise ValueError('Informe uma descrição válida')
        return value


class Draft(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    steps:list[Step]=Field(min_length=1,max_length=10)
    expected_version:StrictInt=Field(default=0,ge=0)

    @field_validator('name')
    @classmethod
    def name_text(cls,value):
        return Step.label_text(value)


class Revision(StrictModel):
    expected_version:StrictInt=Field(ge=1)


def simulate_steps(steps):
    """An action trace only. It neither reads customer facts nor executes tools."""
    labels={'tag':'Prepararia uma etiqueta','handoff':'Solicitaria atendimento humano','reminder_draft':'Prepararia um lembrete para revisão'}
    trace=[]
    for i,step in enumerate(steps):
        parsed=Step.model_validate(step)
        trace.append({'position':i+1,'kind':parsed.kind,'description':labels[parsed.kind]+': '+parsed.label,'executed':False})
    return trace


@router.get('')
async def drafts(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    enabled()
    async with management_session(tenant_id,actor) as db:
        rows=(await db.execute(text('SELECT id,name,steps,version,updated_at FROM workflow_definitions WHERE tenant_id=:t AND NOT enabled ORDER BY updated_at DESC,id LIMIT 50'),{'t':tenant_id})).mappings().all()
        return jsonable_encoder({'items':rows,'limit':50,'execution_enabled':False})


@router.post('',status_code=201)
async def create(tenant_id:UUID,payload:Draft,actor:Actor=Depends(authenticate)):
    enabled()
    if payload.expected_version:
        raise HTTPException(422,'Um novo fluxo deve usar a versão zero')
    async with management_session(tenant_id,actor,write=True) as db:
        await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'workflow-drafts:'+str(tenant_id)})
        count=(await db.execute(text('SELECT count(*) FROM workflow_definitions WHERE tenant_id=:t'),{'t':tenant_id})).scalar_one()
        if count>=50:
            raise HTTPException(409,'Limite de 50 rascunhos por estabelecimento')
        row=(await db.execute(text('''INSERT INTO workflow_definitions(tenant_id,name,steps,enabled)
          VALUES(:t,:name,CAST(:steps AS jsonb),false) RETURNING id,name,steps,version,updated_at'''),
          {'t':tenant_id,'name':payload.name,'steps':json.dumps([s.model_dump() for s in payload.steps])})).mappings().one()
        await audit(db,tenant_id,'workflow_draft_created','workflow_definitions',row['id'])
        return jsonable_encoder(row)


@router.put('/{workflow_id}')
async def update(tenant_id:UUID,workflow_id:UUID,payload:Draft,actor:Actor=Depends(authenticate)):
    enabled()
    async with management_session(tenant_id,actor,write=True) as db:
        row=(await db.execute(text('''UPDATE workflow_definitions SET name=:name,steps=CAST(:steps AS jsonb),version=version+1,updated_at=clock_timestamp()
          WHERE tenant_id=:t AND id=:id AND NOT enabled AND version=:version RETURNING id,name,steps,version,updated_at'''),
          {'t':tenant_id,'id':workflow_id,'version':payload.expected_version,'name':payload.name,'steps':json.dumps([s.model_dump() for s in payload.steps])})).mappings().one_or_none()
        if not row:raise HTTPException(409,'O rascunho mudou ou não está disponível para edição. Atualize a lista')
        await audit(db,tenant_id,'workflow_draft_updated','workflow_definitions',workflow_id)
        return jsonable_encoder(row)


@router.post('/{workflow_id}/simulate')
async def simulate(tenant_id:UUID,workflow_id:UUID,payload:Revision,actor:Actor=Depends(authenticate)):
    enabled()
    async with management_session(tenant_id,actor) as db:
        row=(await db.execute(text('SELECT steps,version FROM workflow_definitions WHERE tenant_id=:t AND id=:id AND NOT enabled'),{'t':tenant_id,'id':workflow_id})).mappings().one_or_none()
        if not row or row['version']!=payload.expected_version:
            raise HTTPException(409,'O fluxo mudou. Atualize antes de simular')
        return {'version':row['version'],'trace':simulate_steps(row['steps']),'executed':False,'sends_messages':False,'writes_business_data':False}
