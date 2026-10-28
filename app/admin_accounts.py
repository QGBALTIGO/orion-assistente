"""Platform-owner customer account management.

Only the revalidated legacy administrator may enumerate or change customer
accounts. Ordinary tenant owners and staff cannot use these endpoints.
"""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.account_auth import _storage
from app.panel_auth import panel_actor

router=APIRouter(prefix="/api/v1/admin/accounts",tags=["Administração de clientes"])


def require_platform_owner(claims:dict=Depends(panel_actor)):
    if claims.get("role")!="admin" or claims.get("auth_provider")!="legacy_admin" or not claims.get("account_id"):
        raise HTTPException(403,"Somente o administrador principal pode gerenciar contas")
    return UUID(str(claims["account_id"]))


class AccountEdit(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=2,max_length=120)
    expected_version:int=Field(ge=1)
    reason:str=Field(min_length=5,max_length=500)


class AccountAction(BaseModel):
    model_config=ConfigDict(extra="forbid")
    expected_version:int=Field(ge=1)
    reason:str=Field(min_length=5,max_length=500)
    confirmation:str|None=None


def _customer(row,now=None):
    return {key:row[key] for key in ("id","name","email","created_at","verified_at",
                                     "registration_pending","auth_version","state",
                                     "archived_at","purge_after","legal_hold",
                                     "company_name","company_id","company_status")}


@router.get("")
async def list_customer_accounts(q:str="",status:str="all",page:int=Query(1,ge=1),
                                 page_size:int=Query(20,ge=1,le=50),
                                 admin:UUID=Depends(require_platform_owner)):
    if status not in ("all","active","suspended","archived"):
        raise HTTPException(422,"Filtro inválido")
    q=q.strip()[:120]
    async with _storage()() as db:
        filters={"q":"%"+q.replace("%","\\%").replace("_","\\_")+"%",
                 "status":status,"size":page_size,"offset":(page-1)*page_size}
        base="""
            FROM panel_accounts a
            LEFT JOIN admin_account_controls c ON c.account_id=a.id
            LEFT JOIN LATERAL (
                SELECT t.id AS company_id,t.name AS company_name,t.status AS company_status
                FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id
                WHERE m.account_id=a.id AND m.active ORDER BY t.id LIMIT 1
            ) company ON true
            WHERE a.role='member'
              AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
              AND (:status='all' OR COALESCE(c.state,'active')=:status)
              AND (a.name ILIKE :q ESCAPE '\\' OR a.email ILIKE :q ESCAPE '\\'
                   OR COALESCE(company.company_name,'') ILIKE :q ESCAPE '\\')
        """
        total=(await db.execute(text("SELECT count(*) "+base),filters)).scalar_one()
        rows=(await db.execute(text("""
            SELECT a.id,a.name,a.email,a.created_at,a.verified_at,a.registration_pending,
                   a.auth_version,COALESCE(c.state,'active') AS state,c.archived_at,
                   c.purge_after,COALESCE(c.legal_hold,false) AS legal_hold,
                   company.company_id,company.company_name,company.company_status
        """+base+" ORDER BY a.created_at DESC,a.id DESC LIMIT :size OFFSET :offset"),filters)).mappings().all()
    return {"items":[_customer(r) for r in rows],"total":total,"page":page,"page_size":page_size}


@router.get("/{account_id}")
async def account_detail(account_id:UUID,admin:UUID=Depends(require_platform_owner)):
    async with _storage()() as db:
        row=(await db.execute(text("""
            SELECT a.id,a.name,a.email,a.created_at,a.verified_at,a.registration_pending,
                   a.auth_version,COALESCE(c.state,'active') AS state,c.archived_at,
                   c.purge_after,COALESCE(c.legal_hold,false) AS legal_hold,
                   t.id AS company_id,t.name AS company_name,t.status AS company_status
            FROM panel_accounts a
            LEFT JOIN admin_account_controls c ON c.account_id=a.id
            LEFT JOIN LATERAL (
                SELECT t.id,t.name,t.status FROM tenant_memberships m
                JOIN tenants t ON t.id=m.tenant_id WHERE m.account_id=a.id AND m.active
                ORDER BY t.id LIMIT 1
            ) t ON true
            WHERE a.id=:id AND a.role='member'
              AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
        """),{"id":account_id})).mappings().first()
        if not row:raise HTTPException(404,"Conta não encontrada")
        events=(await db.execute(text("""
            SELECT action,reason,occurred_at FROM admin_account_audit
            WHERE account_id=:id ORDER BY id DESC LIMIT 30
        """),{"id":account_id})).mappings().all()
    return {"account":_customer(row),"history":[dict(e) for e in events]}


@router.put("/{account_id}")
async def edit_account(account_id:UUID,payload:AccountEdit,
                       admin:UUID=Depends(require_platform_owner)):
    name=" ".join(payload.name.split())
    if len(name)<2:raise HTTPException(422,"Nome inválido")
    async with _storage()() as db:
        async with db.begin():
            row=(await db.execute(text("""
                SELECT a.id,a.auth_version,COALESCE(c.state,'active') AS state
                FROM panel_accounts a LEFT JOIN admin_account_controls c ON c.account_id=a.id
                WHERE a.id=:id AND a.role='member' FOR UPDATE OF a
            """),{"id":account_id})).mappings().first()
            if not row:raise HTTPException(404,"Conta não encontrada")
            if row["state"]=="archived":raise HTTPException(409,"Restaure a conta antes de editar")
            if row["auth_version"]!=payload.expected_version:raise HTTPException(409,"Dados desatualizados")
            await db.execute(text("UPDATE panel_accounts SET name=:name,auth_version=auth_version+1 WHERE id=:id"),
                             {"name":name,"id":account_id})
            await _audit(db,admin,account_id,"edit",payload.reason)
    return {"ok":True}


async def _audit(db,admin,account_id,action,reason):
    await db.execute(text("""
        INSERT INTO admin_account_audit(actor_id,account_id,action,reason)
        VALUES(:actor,:account,:action,:reason)
    """),{"actor":admin,"account":account_id,"action":action,"reason":reason})


@router.post("/{account_id}/{action}")
async def manage_account(account_id:UUID,action:str,payload:AccountAction,
                         admin:UUID=Depends(require_platform_owner)):
    if action not in ("suspend","reactivate","archive","restore"):
        raise HTTPException(404,"Ação não encontrada")
    if action=="archive" and payload.confirmation!="EXCLUIR":
        raise HTTPException(422,"Digite EXCLUIR para confirmar")
    async with _storage()() as db:
        async with db.begin():
            row=(await db.execute(text("""
                SELECT a.id,a.auth_version,COALESCE(c.state,'active') AS state,
                       COALESCE(c.legal_hold,false) AS legal_hold
                FROM panel_accounts a LEFT JOIN admin_account_controls c ON c.account_id=a.id
                WHERE a.id=:id AND a.role='member'
                  AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
                FOR UPDATE OF a
            """),{"id":account_id})).mappings().first()
            if not row:raise HTTPException(404,"Conta não encontrada")
            if row["auth_version"]!=payload.expected_version:raise HTTPException(409,"Conta alterada. Atualize a ficha.")
            allowed={"active":{"suspend","archive"},"suspended":{"reactivate","archive"},
                     "archived":{"restore"}}
            if action not in allowed[row["state"]]:raise HTTPException(409,"Transição não permitida")
            newstate={"suspend":"suspended","reactivate":"active",
                      "archive":"archived","restore":"active"}[action]
            await db.execute(text("""
                INSERT INTO admin_account_controls(account_id,state,reason,archived_at,purge_after)
                VALUES(:id,:state,:reason,
                       CASE WHEN :state='archived' THEN now() ELSE NULL END,
                       CASE WHEN :state='archived' THEN now()+interval '30 days' ELSE NULL END)
                ON CONFLICT(account_id) DO UPDATE SET state=EXCLUDED.state,
                    reason=EXCLUDED.reason,archived_at=EXCLUDED.archived_at,
                    purge_after=EXCLUDED.purge_after,updated_at=now()
            """),{"id":account_id,"state":newstate,"reason":payload.reason})
            await db.execute(text("UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:id"),
                             {"id":account_id})
            await _audit(db,admin,account_id,action,payload.reason)
    return {"ok":True,"state":newstate,"reversible_days":30 if action=="archive" else None}
