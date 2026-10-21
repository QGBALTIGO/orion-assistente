import asyncio
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from app import workflow_drafts as api
from tests.integration.test_inbox_tools_postgres import inbox_db
from tests.integration.test_automation_postgres import automation_db

pytestmark=pytest.mark.integration


@pytest_asyncio.fixture
async def flows(inbox_db):
    try:yield inbox_db
    finally:
        async with inbox_db.migration.begin() as db:
            await db.execute(text('DELETE FROM workflow_definitions WHERE tenant_id=:t'),{'t':inbox_db.tenant})


async def test_draft_cas_simulation_and_persistent_disabled_state(flows):
    c=flows
    payload=api.Draft(name='Fluxo sintético',steps=[api.Step(kind='handoff',label='Equipe')])
    saved=await api.create(c.tenant,payload,c.actor);identity=UUID(saved['id'])
    edits=[api.Draft(name=name,steps=payload.steps,expected_version=1) for name in ['Primeiro','Segundo']]
    results=await asyncio.gather(*(api.update(c.tenant,identity,p,c.actor) for p in edits),return_exceptions=True)
    assert sum(isinstance(r,dict) and r['version']==2 for r in results)==1
    assert sum(isinstance(r,HTTPException) and r.status_code==409 for r in results)==1
    with pytest.raises(HTTPException):
        await api.simulate(c.tenant,identity,api.Revision(expected_version=1),c.actor)
    result=await api.simulate(c.tenant,identity,api.Revision(expected_version=2),c.actor)
    assert not result['executed'] and not result['sends_messages'] and not result['writes_business_data']
    async with c.migration.connect() as db:
        assert (await db.execute(text('SELECT enabled FROM workflow_definitions WHERE id=:id'),{'id':identity})).scalar_one() is False
        assert not (await db.execute(text('SELECT id FROM inbox_messages WHERE tenant_id=:t'),{'t':c.tenant})).all()
