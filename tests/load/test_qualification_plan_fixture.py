"""Fixture safety only; these doubles are never capacity or PostgreSQL evidence."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from scripts import qualification_load as load


class Result:
    def __init__(self, value): self.value=value
    def scalar_one(self): return self.value
    def mappings(self): return self
    def one(self): return self.value


def context():
    sandbox=object.__new__(load.DisposableDatabase)
    sandbox.name="orion_qualification_"+uuid4().hex
    sandbox.oid=12345
    sandbox.engine=SimpleNamespace(url=make_url("postgresql+psycopg://postgres:synthetic@127.0.0.1/"+sandbox.name))
    class DB:
        engine=sandbox.engine
        present=True
        revision="0050_plan_permissions"
        identity={"database_name":sandbox.name,"oid":sandbox.oid,"owner_name":"postgres","role_name":"postgres"}
        def __init__(self): self.calls=[]
        def execute(self, query, params=None):
            sql=str(query);self.calls.append((sql,params))
            if "to_regclass" in sql: return Result(self.present)
            if "FROM pg_database" in sql: return Result(self.identity)
            if "SELECT version_num" in sql: return Result(self.revision)
            return Result(None)
    return sandbox,DB()


def test_legacy_schema_seed_does_not_add_any_account_or_grant():
    sandbox,db=context();db.present=False
    assert load.seed_fixture_plans(sandbox,db,[uuid4()])==0
    assert len(db.calls)==1 and "to_regclass" in db.calls[0][0]


def test_new_disposable_fixture_owns_explicit_elite_grants_without_sessions_or_billing():
    sandbox,db=context();tenants=[uuid4(),uuid4()]
    assert load.seed_fixture_plans(sandbox,db,tenants)==2
    writes=[(sql,params) for sql,params in db.calls if sql.lstrip().startswith("INSERT")]
    assert len(writes)==3
    assert writes[0][1]["email"].endswith("@example.test")
    assert "fixture-only-no-login" in writes[0][0] and "'admin'" in writes[0][0]
    assert [params["tenant"] for _,params in writes[1:]]==tenants
    assert all("'elite'" in sql and params["actor"]==writes[0][1]["id"] for sql,params in writes[1:])
    assert not any(word in " ".join(sql for sql,_ in writes) for word in
                   ("subscriptions","auth_version","tenant_memberships","plan_permissions_release","panel_sessions"))


@pytest.mark.parametrize("damage",["not_created","name","remote","role","engine_database","connection_database","oid","owner","connection_role","revision","connection_engine"])
def test_synthetic_grants_refuse_unowned_context_before_any_insert(damage):
    sandbox,db=context()
    if damage=="not_created": sandbox.oid=None
    elif damage=="name": sandbox.name="existing_database"
    elif damage in {"remote","role","engine_database"}:
        sandbox.engine.url=sandbox.engine.url.set(**{
            "remote":{"host":"production.example"},"role":{"username":"app_support"},
            "engine_database":{"database":"existing_database"}}[damage])
    elif damage=="connection_database": db.identity={**db.identity,"database_name":"existing_database"}
    elif damage=="oid": db.identity={**db.identity,"oid":999}
    elif damage=="owner": db.identity={**db.identity,"owner_name":"app_migrator"}
    elif damage=="connection_role": db.identity={**db.identity,"role_name":"app_support"}
    elif damage=="revision": db.revision="0051_unreviewed"
    elif damage=="connection_engine": db.engine=object()
    with pytest.raises(ValueError): load.seed_fixture_plans(sandbox,db,[uuid4()])
    assert not any(sql.lstrip().startswith("INSERT") for sql,_ in db.calls)
