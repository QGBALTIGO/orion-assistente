"""Platform-owner customer account management.

Only the revalidated legacy administrator may enumerate or change customer
accounts. Ordinary tenant owners and staff cannot use these endpoints.
"""
import hmac
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.account_auth import _storage
from app.panel_auth import panel_actor
from app.plan_catalog import CODES
from app.plan_permissions import derive_plan, plan_state, lock_plan
from app.account_business import lock_account_business

router=APIRouter(prefix="/api/v1/admin/accounts",tags=["Administração de clientes"])


def require_platform_owner(claims:dict=Depends(panel_actor)):
    """Both administrator identities are revalidated by panel_actor on every call.

    The legacy platform operator is bound to the environment credential.
    A personal admin account additionally requires the verified owner email
    established by /auth/admin/claim. Support and ordinary members never pass.
    """
    if claims.get("role")!="admin" or not claims.get("account_id"):
        raise HTTPException(403,"Somente o administrador principal pode gerenciar contas")
    if claims.get("auth_provider")!="legacy_admin":
        owner=os.getenv("PANEL_OWNER_EMAIL","").strip().lower()
        email=str(claims.get("email") or "").strip().lower()
        if not (owner and claims.get("email_verified") is True and
                hmac.compare_digest(email,owner)):
            raise HTTPException(403,"Conta não autorizada para administração de clientes")
    try:
        return UUID(str(claims["account_id"]))
    except (ValueError,TypeError):
        raise HTTPException(403,"Identidade administrativa inválida") from None


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


class PlanOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    expected_revision: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    operation_id: UUID
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value):
        if value is None:
            return None
        value = " ".join(value.split())
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("Motivo inválido")
        return value or None


class ManualPlanAssignment(PlanOperation):
    confirmation: Literal["ALTERAR PLANO"]
    plan_code: Literal["essencial", "profissional", "elite"]
    access_type: Literal["indefinite", "temporary"]
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.access_type == "indefinite":
            if self.expires_at is not None:
                raise ValueError("A liberação sem prazo não possui data de expiração")
        elif self.expires_at is None or self.expires_at.tzinfo is None:
            raise ValueError("Informe uma data de expiração com fuso horário")
        else:
            self.expires_at = self.expires_at.astimezone(timezone.utc)
        return self


class ManualPlanRemoval(PlanOperation):
    confirmation: Literal["REMOVER ATRIBUIÇÃO"]


ACCOUNT_QUERY = """
    SELECT a.id,a.name,a.email,a.created_at,a.verified_at,a.registration_pending,
           a.auth_version,a.last_access_at,COALESCE(c.state,'active') AS state,c.archived_at,
           c.purge_after,c.purged_at,COALESCE(c.legal_hold,false) AS legal_hold,
           CASE WHEN company.company_count=1 THEN company.company_id END AS company_id,
           CASE WHEN company.company_count=1 THEN company.company_name END AS company_name,
           CASE WHEN company.company_count=1 THEN company.company_status END AS company_status,
           CASE WHEN company.company_count=1 THEN company.company_role END AS company_role,
           COALESCE(company.company_count,0) AS company_count,
           CASE WHEN company.company_count=1 THEN public.orion_plan_snapshot(company.company_id) END AS plan_snapshot
    FROM panel_accounts a LEFT JOIN admin_account_controls c ON c.account_id=a.id
    LEFT JOIN LATERAL (
        SELECT t.id AS company_id,t.name AS company_name,t.status AS company_status,
               m.role AS company_role,count(*) OVER() AS company_count
        FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id
        WHERE m.account_id=a.id AND m.active ORDER BY t.id LIMIT 1
    ) company ON true
    WHERE a.role='member' AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
"""


def _customer(row,now=None):
    result = {key:row[key] for key in ("id","name","email","created_at","verified_at",
                                     "registration_pending","auth_version","state",
                                     "archived_at","purge_after","purged_at","legal_hold",
                                     "company_name","company_id","company_status")}
    snapshot = row.get("plan_snapshot")
    state = derive_plan(snapshot, now=now) if snapshot else None
    phone = snapshot.get("contact_phone") if snapshot else None
    commercial = state.get("commercial_subscription") if state else None
    result.update(last_access_at=row.get("last_access_at"),
        phone=phone if isinstance(phone,str) and phone.strip() else None,
        company_membership_count=row.get("company_count", 0), effective_plan=state,
        commercial_subscription=commercial,
        plan_source=state["source"] if state else "none",
        subscription_status=commercial.get("status") if commercial else "none",
        can_manage_plan=bool(state and row.get("company_count")==1 and row.get("company_role")=="owner"
                             and not state["platform_workspace"]))
    return result


@router.get("")
async def list_customer_accounts(q:str="",status:str="all",page:int=Query(1,ge=1),
                                 page_size:int=Query(20,ge=1,le=50),
                                 admin:UUID=Depends(require_platform_owner),plan:str="all",source:str="all",
                                 subscription_status:str="all",response:Response=None):
    if response is not None:
        response.headers["Cache-Control"]="no-store"
    if status not in ("all","active","suspended","archived","erased"):
        raise HTTPException(422,"Filtro inválido")
    if plan not in {"all","none",*CODES} or source not in {"all","manual","cakto","trial","none","platform_admin"}:
        raise HTTPException(422,"Filtro de plano inválido")
    if subscription_status not in {"all","none","active","trialing","past_due","grace","suspended","canceled","expired"}:
        raise HTTPException(422,"Filtro de assinatura inválido")
    q=q.strip()[:120]
    async with _storage()() as db:
        filters={"q":"%"+q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%", "status":status}
        rows=(await db.execute(text(ACCOUNT_QUERY+"""
              AND (CAST(:status AS text)='all' OR COALESCE(c.state,'active')=CAST(:status AS text))
              AND (a.name ILIKE :q ESCAPE '\\' OR a.email ILIKE :q ESCAPE '\\'
                   OR COALESCE(company.company_name,'') ILIKE :q ESCAPE '\\')
            ORDER BY a.created_at DESC,a.id DESC"""),filters)).mappings().all()
    now=datetime.now(timezone.utc)
    items=[_customer(r,now=now) for r in rows]
    def matches(item):
        effective=item["effective_plan"] or {}
        commercial=item["commercial_subscription"] or {}
        if plan!="all" and (effective.get("code") or "none")!=plan:
            return False
        if source!="all" and effective.get("source","none")!=source:
            return False
        if subscription_status=="grace":
            grace=commercial.get("grace_until")
            if isinstance(grace,str):
                try: grace=datetime.fromisoformat(grace.replace("Z","+00:00"))
                except ValueError: grace=None
            return bool(commercial.get("status")=="past_due" and grace and grace.tzinfo and grace>now)
        if subscription_status=="expired":
            return effective.get("status")=="expired"
        return subscription_status=="all" or item["subscription_status"]==subscription_status
    items=[item for item in items if matches(item)]
    offset=(page-1)*page_size
    return {"items":items[offset:offset+page_size],"total":len(items),"page":page,"page_size":page_size}


@router.get("/stats")
async def account_stats(admin:UUID=Depends(require_platform_owner)):
    """Read-only platform totals, excluding legacy operator identities."""
    async with _storage()() as db:
        records=(await db.execute(text("""
            SELECT COALESCE(c.state,'active') AS state,count(*) AS total
            FROM panel_accounts a
            LEFT JOIN admin_account_controls c ON c.account_id=a.id
            WHERE a.role='member'
              AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
            GROUP BY COALESCE(c.state,'active')
        """))).mappings().all()
    stats={"active":0,"suspended":0,"archived":0,"erased":0}
    for item in records:
        if item["state"] in stats:
            stats[item["state"]]=item["total"]
    return {"total":sum(stats.values()),**stats}


@router.get("/{account_id}")
async def account_detail(account_id:UUID,admin:UUID=Depends(require_platform_owner),response:Response=None):
    if response is not None:
        response.headers["Cache-Control"]="no-store"
    async with _storage()() as db:
        row=(await db.execute(text(ACCOUNT_QUERY+" AND a.id=:id"),{"id":account_id})).mappings().first()
        if not row:raise HTTPException(404,"Conta não encontrada")
        events=(await db.execute(text("""
            SELECT action,reason,occurred_at FROM admin_account_audit
            WHERE account_id=:id ORDER BY id DESC LIMIT 30
        """),{"id":account_id})).mappings().all()
    return {"account":_customer(row),"history":[dict(e) for e in events]}


async def _plan_company(db, account_id, tenant_id, *, lock=False):
    row = (await db.execute(text("""SELECT t.id,t.name,t.status,a.id AS account_id,
            m.role AS membership_role,COALESCE(c.state,'active') AS account_state,
            (SELECT count(*) FROM tenant_memberships active_memberships
             WHERE active_memberships.account_id=a.id AND active_memberships.active) AS company_count
        FROM panel_accounts a JOIN tenant_memberships m ON m.account_id=a.id
        JOIN tenants t ON t.id=m.tenant_id
        LEFT JOIN admin_account_controls c ON c.account_id=a.id
        WHERE a.id=:account AND t.id=:tenant AND m.active AND a.role='member'
          AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'"""
        + (" FOR SHARE OF a,m" if lock else "")), {"account": account_id, "tenant": tenant_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Empresa não vinculada à conta selecionada")
    if row["company_count"] != 1:
        raise HTTPException(409, "O vínculo da empresa precisa ser regularizado antes de alterar o plano")
    if row["membership_role"] != "owner":
        raise HTTPException(403, "Selecione a conta do proprietário desta empresa")
    if row["account_state"] == "erased":
        raise HTTPException(409, "A conta selecionada foi excluída")
    return dict(row)


async def _plan_history(db, tenant_id):
    rows = (await db.execute(text("""SELECT id,actor_id,operation_id,action,previous_plan,new_plan,
            reason,access_type,expires_at,occurred_at FROM tenant_plan_audit
        WHERE tenant_id=:tenant ORDER BY occurred_at DESC,id DESC LIMIT 50"""), {"tenant": tenant_id})).mappings().all()
    return [dict(row) for row in rows]


async def _plan_envelope(db, company, state, *, replayed=False):
    return {"company": {"id": company["id"], "name": company["name"], "status": company["status"],
                        "account_id": company["account_id"],
                        "active_professionals": state["limits"]["active_professionals"]},
            "effective_plan": state, "history": await _plan_history(db, company["id"]), "replayed": replayed}


@router.get("/{account_id}/companies/{tenant_id}/plan")
async def account_company_plan(account_id:UUID,tenant_id:UUID,response:Response,
                               admin:UUID=Depends(require_platform_owner)):
    response.headers["Cache-Control"]="no-store"
    async with _storage()() as db:
        async with db.begin():
            company = await _plan_company(db, account_id, tenant_id)
            state = await plan_state(db, tenant_id)
            result = await _plan_envelope(db, company, state)
    return jsonable_encoder(result)


def _plan_fingerprint(account_id, tenant_id, action, payload):
    body = {"account_id":str(account_id),"tenant_id":str(tenant_id),"action":action,
            "payload":payload.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",", ":")).encode()).hexdigest()


async def _change_company_plan(account_id, tenant_id, payload, admin, action):
    fingerprint = _plan_fingerprint(account_id, tenant_id, action, payload)
    try:
        async with _storage()() as db:
            async with db.begin():
                # Membership writers use this account lock too. The plan lock
                # then serializes grants, removals and protected domain writes.
                await lock_account_business(db, account_id)
                await lock_plan(db, tenant_id, exclusive=True)
                company = await _plan_company(db, account_id, tenant_id, lock=True)
                previous = await plan_state(db, tenant_id, lock=None)
                existing = (await db.execute(text("""SELECT tenant_id,actor_id,request_fingerprint
                    FROM tenant_plan_audit WHERE tenant_id=:tenant AND operation_id=:operation"""),
                    {"tenant":tenant_id,"operation":payload.operation_id})).mappings().one_or_none()
                if existing:
                    if (existing["tenant_id"] != tenant_id or existing["actor_id"] != admin
                            or not hmac.compare_digest(existing["request_fingerprint"],fingerprint)):
                        raise HTTPException(409,"Esta operação já foi usada para outra alteração")
                    return await _plan_envelope(db,company,previous,replayed=True)
                if (previous["version"] != payload.expected_version
                        or not hmac.compare_digest(previous["revision"],payload.expected_revision)):
                    raise HTTPException(409,"O plano mudou. Atualize a ficha antes de confirmar novamente")
                if previous["platform_workspace"]:
                    raise HTTPException(409,"O acesso administrativo integral é preservado nesta empresa")
                if action == "manual_assign":
                    if payload.expires_at is not None and payload.expires_at <= datetime.now(timezone.utc):
                        raise HTTPException(422,"A expiração precisa estar no futuro")
                    changed = (await db.execute(text("""INSERT INTO tenant_plan_controls
                        (tenant_id,version,manual_plan_code,manual_started_at,manual_expires_at,manual_actor_id,manual_reason)
                        VALUES (:tenant,1,:code,clock_timestamp(),:expires,:admin,:reason)
                        ON CONFLICT(tenant_id) DO UPDATE SET
                            version=tenant_plan_controls.version+1,manual_plan_code=EXCLUDED.manual_plan_code,
                            manual_started_at=EXCLUDED.manual_started_at,manual_expires_at=EXCLUDED.manual_expires_at,
                            manual_actor_id=EXCLUDED.manual_actor_id,manual_reason=EXCLUDED.manual_reason,updated_at=clock_timestamp()
                        WHERE tenant_plan_controls.version=:version RETURNING version"""),
                        {"tenant":tenant_id,"code":payload.plan_code,"expires":payload.expires_at,
                         "admin":admin,"reason":payload.reason,"version":payload.expected_version})).scalar_one_or_none()
                    access_type, expires = payload.access_type, payload.expires_at
                else:
                    if previous["manual_assignment"] is None:
                        raise HTTPException(409,"Não existe atribuição manual para remover")
                    changed = (await db.execute(text("""UPDATE tenant_plan_controls SET version=version+1,
                        manual_plan_code=NULL,manual_started_at=NULL,manual_expires_at=NULL,manual_actor_id=NULL,
                        manual_reason=NULL,updated_at=clock_timestamp()
                        WHERE tenant_id=:tenant AND version=:version AND manual_plan_code IS NOT NULL RETURNING version"""),
                        {"tenant":tenant_id,"version":payload.expected_version})).scalar_one_or_none()
                    access_type, expires = None, None
                if changed is None:
                    raise HTTPException(409,"O plano mudou. Atualize a ficha")
                current = await plan_state(db,tenant_id,lock=None)
                await db.execute(text("""INSERT INTO tenant_plan_audit
                    (id,tenant_id,actor_id,operation_id,action,previous_plan,new_plan,reason,access_type,expires_at,request_fingerprint)
                    VALUES (:id,:tenant,:actor,:operation,:action,CAST(:previous AS jsonb),CAST(:new AS jsonb),
                            :reason,:access_type,:expires,:fingerprint)"""),
                    {"id":uuid4(),"tenant":tenant_id,"actor":admin,"operation":payload.operation_id,"action":action,
                     "previous":json.dumps(jsonable_encoder(previous)),"new":json.dumps(jsonable_encoder(current)),
                     "reason":payload.reason,"access_type":access_type,"expires":expires,"fingerprint":fingerprint})
                return await _plan_envelope(db,company,current)
    except IntegrityError:
        raise HTTPException(409,"Operação concorrente ou já registrada. Atualize a ficha") from None


@router.put("/{account_id}/companies/{tenant_id}/plan")
async def assign_company_plan(account_id:UUID,tenant_id:UUID,payload:ManualPlanAssignment,response:Response,
                              admin:UUID=Depends(require_platform_owner)):
    response.headers["Cache-Control"]="no-store"
    return jsonable_encoder(await _change_company_plan(account_id,tenant_id,payload,admin,"manual_assign"))


@router.delete("/{account_id}/companies/{tenant_id}/plan")
async def remove_company_plan(account_id:UUID,tenant_id:UUID,payload:ManualPlanRemoval,response:Response,
                              admin:UUID=Depends(require_platform_owner)):
    response.headers["Cache-Control"]="no-store"
    return jsonable_encoder(await _change_company_plan(account_id,tenant_id,payload,admin,"manual_remove"))


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
            if row["state"] in {"archived","erased"}:raise HTTPException(409,"Esta conta não pode ser editada")
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
    if action not in ("suspend","reactivate","archive","restore","hold","release_hold","erase_now"):
        raise HTTPException(404,"Ação não encontrada")
    if action=="archive" and payload.confirmation!="EXCLUIR":
        raise HTTPException(422,"Digite EXCLUIR para confirmar")
    if action=="erase_now":
        if payload.confirmation!="EXCLUIR PERMANENTEMENTE":
            raise HTTPException(422,"Digite EXCLUIR PERMANENTEMENTE para confirmar")
        from app.account_erasure import erase_customer_login_now
        done=await erase_customer_login_now(account_id,payload.expected_version,admin,payload.reason)
        if not done:
            raise HTTPException(409,"Conta alterada, não arquivada ou com retenção legal. Atualize a ficha.")
        return {"ok":True,"state":"erased","email_reusable":True,"reversible":False}
    async with _storage()() as db:
        async with db.begin():
            row=(await db.execute(text("""
                SELECT a.id,a.auth_version,COALESCE(c.state,'active') AS state,
                       COALESCE(c.legal_hold,false) AS legal_hold,
                       COALESCE(c.purge_after<=now(),false) AS recovery_expired
                FROM panel_accounts a LEFT JOIN admin_account_controls c ON c.account_id=a.id
                WHERE a.id=:id AND a.role='member'
                  AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
                FOR UPDATE OF a
            """),{"id":account_id})).mappings().first()
            if not row:raise HTTPException(404,"Conta não encontrada")
            if row["auth_version"]!=payload.expected_version:raise HTTPException(409,"Conta alterada. Atualize a ficha.")
            allowed={"active":{"suspend","archive"},"suspended":{"reactivate","archive"},
                     "archived":{"restore","hold","release_hold"},"erased":set()}
            if action not in allowed[row["state"]]:raise HTTPException(409,"Transição não permitida")
            if action=="restore" and row["recovery_expired"]:
                raise HTTPException(409,"O prazo de recuperação de 30 dias terminou")
            if action=="hold" and row["legal_hold"]:
                raise HTTPException(409,"A conta já possui retenção legal ativa")
            if action=="release_hold" and not row["legal_hold"]:
                raise HTTPException(409,"Não existe retenção legal ativa")
            if action in {"hold","release_hold"}:
                await db.execute(text("""
                    UPDATE admin_account_controls SET legal_hold=:hold,updated_at=now(),reason=:reason
                    WHERE account_id=:id AND state='archived'
                """),{"id":account_id,"hold":action=="hold","reason":payload.reason})
                # Legal hold never activates or authenticates the archived account.
                await db.execute(text("UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:id"),
                                 {"id":account_id})
                await _audit(db,admin,account_id,action,payload.reason)
                return {"ok":True,"state":"archived","legal_hold":action=="hold"}
            newstate={"suspend":"suspended","reactivate":"active",
                      "archive":"archived","restore":"active"}[action]
            await db.execute(text("""
                INSERT INTO admin_account_controls(account_id,state,reason,archived_at,purge_after)
                VALUES(:id,CAST(:state AS varchar(16)),:reason,
                       CASE WHEN CAST(:state AS varchar(16))='archived' THEN now() ELSE NULL END,
                       CASE WHEN CAST(:state AS varchar(16))='archived' THEN now()+interval '30 days' ELSE NULL END)
                ON CONFLICT(account_id) DO UPDATE SET state=EXCLUDED.state,
                    reason=EXCLUDED.reason,archived_at=EXCLUDED.archived_at,
                    purge_after=EXCLUDED.purge_after,updated_at=now()
            """),{"id":account_id,"state":newstate,"reason":payload.reason})
            await db.execute(text("UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:id"),
                             {"id":account_id})
            if action in {"suspend","archive"}:
                # No pending verification or reset code survives an access block.
                # The email worker checks used_at before delivering OTPs.
                await db.execute(text("""
                    UPDATE panel_email_tokens SET used_at=now()
                    WHERE account_id=:id AND used_at IS NULL
                """),{"id":account_id})
            await _audit(db,admin,account_id,action,payload.reason)
    return {"ok":True,"state":newstate,"reversible_days":30 if action=="archive" else None}
