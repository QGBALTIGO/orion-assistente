"""Authenticated management endpoints backed by the tenant's existing database.

Financial entries are bookkeeping, campaigns remain drafts, and site preferences
do not publish a public page or start an external integration.
"""
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, ensure_tenant
from app.csv_export import csv_document
from app.database import get_session_factory, tenant_transaction
from app import management_models as data
from app.management_schemas import (
    AppointmentTransition, CouponIssue, CustomerCreate, CustomerFields, CustomerPatch, FinanceCreate, FinanceFields,
    FinancePatch, FinanceStatus, GoalCreate, GoalFields, GoalPatch,
    LoyaltyRuleCreate, LoyaltyRuleFields, LoyaltyRulePatch,
    LoyaltyTransactionCreate, MarketingCreate, MarketingFields, MarketingPatch,
    PaymentMethod, SettlementCreate, SiteSave, VersionedOperation, CommissionPlanUpdate,
)
from app.tenant_access import resolve_tenant_access
from app import loyalty_rewards as rewards
from app import financial_settlements as settlements
from app import commission_policies
from app.qr_api import customer_display_names, whatsapp_name_matches

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management", tags=["Gestão"])
CustomerStatus = Literal["all", "active", "inactive", "at_risk", "birthday"]
MAX_EXPORT_ROWS = 20_000


@asynccontextmanager
async def management_session(tenant_id, actor, *, write=False):
    # Verify current membership before opening a runtime connection or choosing
    # its RLS setting. Do not trust a tenant or role supplied in the JSON body.
    access = await resolve_tenant_access(actor, tenant_id, "draft" if write else "read")
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant_id):
                await ensure_tenant(db, tenant_id)
                db.info["management_actor_role"] = access.role
                db.info["management_actor_account_id"] = access.account_id
                yield db
    except IntegrityError as exc:
        raise HTTPException(409, "Conflito de cadastro ou vínculo. Atualize os dados e tente novamente") from exc


def values_from(payload):
    return payload.model_dump(exclude={"idempotency_key", "expected_version"})


def merged_changes(current, payload, model):
    changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Informe pelo menos um campo para alterar")
    complete = {key: value for key, value in current.items() if key in model.model_fields}
    try:
        validated = model.model_validate({**complete, **changes}).model_dump()
    except ValidationError as exc:
        # Omit the input value from responses so validation cannot echo notes or
        # accidentally serialize an exception held in Pydantic's error context.
        raise HTTPException(422, [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                                  for error in exc.errors()]) from exc
    return {key: validated[key] for key in changes}


def customer_values(values):
    result = dict(values)
    if "phone" in result:
        result["whatsapp_e164"] = result.pop("phone")
    return result


def require_current_customer(row):
    if row.get("deleted_at") is not None:
        raise HTTPException(404, "Cliente não encontrado no diretório")


def csv_response(filename, headers, rows):
    return Response(content=csv_document(headers, rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "no-store"})


@router.get("/dashboard")
async def get_dashboard(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                        actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.dashboard(db, tenant_id, start_date, end_date)


@router.get("/customers")
async def list_customers(tenant_id: UUID, q: str = Query(default="", max_length=120),
                         status: CustomerStatus = "all", birthday_month: int | None = Query(default=None, ge=1, le=12),
                         risk_days: int = Query(default=30, ge=30, le=90),
                         limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                         actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        phones, account = await whatsapp_name_matches(tenant_id, q)
        result = await data.customers(db, tenant_id, q, status, birthday_month, limit, offset, risk_days, name_phones=phones)
    result["items"] = await customer_display_names(tenant_id, result["items"], expected_account=account)
    return result


@router.get("/customers/export.csv")
async def export_customers(tenant_id: UUID, q: str = Query(default="", max_length=120),
                           status: CustomerStatus = "all", birthday_month: int | None = Query(default=None, ge=1, le=12),
                           risk_days: int = Query(default=30, ge=30, le=90),
                           actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        phones, account = await whatsapp_name_matches(tenant_id, q)
        result = await data.customers(db, tenant_id, q, status, birthday_month, MAX_EXPORT_ROWS, 0, risk_days, name_phones=phones)
        if result["total"] > MAX_EXPORT_ROWS:
            raise HTTPException(413, "Refine os filtros para exportar até 20 mil clientes por arquivo")
    result["items"] = await customer_display_names(tenant_id, result["items"], max_phones=MAX_EXPORT_ROWS, expected_account=account)
    return csv_response("clientes-orion.csv",
        ["Nome", "WhatsApp", "Instagram", "E-mail", "Aniversário", "Bairro", "Cidade", "Observações", "Ativo", "Cadastrado em (UTC)", "Última visita", "Agendamentos", "Atendimentos concluídos", "Total em centavos", "Pontos"],
        [[item.get(key) for key in ("display_name", "phone", "instagram", "email", "birth_date", "neighborhood", "city", "notes", "active", "created_at", "last_visit_at", "total_appointments", "completed_appointments", "total_spent_cents", "loyalty_points")]
         for item in result["items"]])


@router.get("/customers/{customer_id}/services")
async def get_customer_services(tenant_id: UUID, customer_id: UUID,
                                limit: int = Query(default=6, ge=1, le=20),
                                offset: int = Query(default=0, ge=0),
                                actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.customer_service_history(db, tenant_id, customer_id, limit, offset)


@router.get("/customers/{customer_id}")
async def get_customer(tenant_id: UUID, customer_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        result = await data.customer_detail(db, tenant_id, customer_id)
    return (await customer_display_names(tenant_id, [result]))[0]


@router.post("/customers", status_code=201)
async def create_customer(tenant_id: UUID, payload: CustomerCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        row = await data.insert_row(db, tenant_id, "customers", customer_values(values_from(payload)), payload.idempotency_key)
        return data.clean_row(row)


@router.patch("/customers/{customer_id}")
async def update_customer(tenant_id: UUID, customer_id: UUID, payload: CustomerPatch, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "customers", customer_id)
        require_current_customer(current)
        values = customer_values(merged_changes(data.clean_row(current), payload, CustomerFields))
        return data.clean_row(await data.update_row(db, tenant_id, "customers", customer_id, payload.expected_version, values))


@router.delete("/customers/{customer_id}")
async def delete_customer(tenant_id: UUID, customer_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "customers", customer_id)
        require_current_customer(current)
        row = await data.update_row(db, tenant_id, "customers", customer_id, payload.expected_version,
                                    {"active": False, "deleted_at": datetime.now(timezone.utc)})
        return data.clean_row(row)


@router.get("/finance")
async def list_finance(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                       kind: Literal["income", "expense"] | None = None, status: FinanceStatus | None = None,
                       category: str | None = Query(default=None, max_length=64), payment_method: PaymentMethod | None = None,
                       entry_type: Literal["general", "advance", "commission_payment"] | None = None, professional_id: UUID | None = None,
                       limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                       actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.finance(db, tenant_id, start_date, end_date, kind, status, category, payment_method, limit, offset, entry_type, professional_id)


@router.get("/finance/summary")
async def get_finance_summary(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                              actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        period, lower, upper = await data.period_for(db, tenant_id, start_date, end_date)
        where, parameters = data.finance_filter(tenant_id, lower, upper)
        return {"period": period, "summary": await data.finance_summary(db, where, parameters, period["timezone"])}


@router.put("/professionals/{professional_id}/commission-plan")
async def save_professional_commission_plan(tenant_id: UUID, professional_id: UUID,
                                           payload: CommissionPlanUpdate,
                                           actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await commission_policies.save_plan(db, tenant_id, professional_id, payload)


@router.get("/finance/commissions")
async def get_commissions(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                          actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.commission_report(db, tenant_id, start_date, end_date)


@router.get("/finance/settlements/preview")
async def preview_settlement(tenant_id: UUID, professional_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        result = await settlements.settlement_preview(db, tenant_id, professional_id)
        return {key: value for key, value in result.items() if key != "_snapshot"}


@router.get("/finance/settlements")
async def list_commission_settlements(tenant_id: UUID, professional_id: UUID | None = None,
                                      limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                                      actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await settlements.list_settlements(db, tenant_id, professional_id, limit, offset)


@router.get("/finance/settlements/{settlement_id}")
async def get_commission_settlement(tenant_id: UUID, settlement_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await settlements.get_settlement(db, tenant_id, settlement_id)


@router.post("/finance/settlements", status_code=201)
async def create_commission_settlement(tenant_id: UUID, payload: SettlementCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await settlements.create_settlement(db, tenant_id, payload, db.info["management_actor_role"])


@router.get("/finance/advances")
async def list_staff_advances(tenant_id: UUID, professional_id: UUID | None = None,
                              status: Literal["all", "open", "partial", "settled"] = "all",
                              start_date: date | None = None, end_date: date | None = None,
                              limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                              actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await settlements.list_advances(db, tenant_id, professional_id, status, start_date, end_date, limit, offset)


@router.get("/finance/dre")
async def get_dre(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                   actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.income_statement(db, tenant_id, start_date, end_date)


@router.get("/finance/export.csv")
async def export_finance(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                         kind: Literal["income", "expense"] | None = None, status: FinanceStatus | None = None,
                         category: str | None = Query(default=None, max_length=64), payment_method: PaymentMethod | None = None,
                         entry_type: Literal["general", "advance", "commission_payment"] | None = None, professional_id: UUID | None = None,
                         actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        result = await data.finance(db, tenant_id, start_date, end_date, kind, status, category, payment_method, MAX_EXPORT_ROWS, 0, entry_type, professional_id)
        if result["total"] > MAX_EXPORT_ROWS:
            raise HTTPException(413, "Refine o período para exportar até 20 mil lançamentos por arquivo")
    return csv_response("financeiro-orion.csv",
        ["Data", "Tipo", "Descrição", "Valor em centavos", "Categoria", "Pagamento", "Status", "Cliente", "Profissional", "Observações"],
        [[item.get(key) for key in ("occurred_at", "kind", "description", "amount_cents", "category", "payment_method", "status", "customer_name", "professional_name", "notes")]
         for item in result["items"]])


@router.post("/finance", status_code=201)
async def create_finance(tenant_id: UUID, payload: FinanceCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        values = values_from(payload)
        await data.validate_links(db, tenant_id, values)
        return data.clean_row(await data.insert_row(db, tenant_id, "financial_entries", values, payload.idempotency_key))


@router.patch("/finance/{entry_id}")
async def update_finance(tenant_id: UUID, entry_id: UUID, payload: FinancePatch, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "financial_entries", entry_id)
        changes = merged_changes(current, payload, FinanceFields)
        await data.validate_links(db, tenant_id, {**current, **changes})
        return data.clean_row(await data.update_row(db, tenant_id, "financial_entries", entry_id, payload.expected_version, changes))


@router.delete("/finance/{entry_id}")
async def cancel_finance(tenant_id: UUID, entry_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return data.clean_row(await data.update_row(db, tenant_id, "financial_entries", entry_id, payload.expected_version, {"status": "cancelled"}))


@router.get("/loyalty")
async def get_loyalty(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.loyalty(db, tenant_id)


@router.post("/loyalty/rules", status_code=201)
async def create_loyalty_rule(tenant_id: UUID, payload: LoyaltyRuleCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return data.clean_row(await rewards.create_rule(db, tenant_id, values_from(payload), payload.idempotency_key))


@router.patch("/loyalty/rules/{rule_id}")
async def update_loyalty_rule(tenant_id: UUID, rule_id: UUID, payload: LoyaltyRulePatch, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "loyalty_rules", rule_id)
        changes = merged_changes(current, payload, LoyaltyRuleFields)
        return data.clean_row(await rewards.update_rule(db, tenant_id, rule_id, payload.expected_version, changes, current))


@router.delete("/loyalty/rules/{rule_id}")
async def disable_loyalty_rule(tenant_id: UUID, rule_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        await rewards.configuration_lock(db, tenant_id)
        return data.clean_row(await data.update_row(db, tenant_id, "loyalty_rules", rule_id, payload.expected_version, {"active": False}))


@router.get("/loyalty/ranking")
async def get_loyalty_ranking(tenant_id: UUID, near_reward: bool = False,
                              limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                              actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await rewards.ranking(db, tenant_id, near_reward, limit, offset)


@router.get("/loyalty/coupons")
async def get_loyalty_coupons(tenant_id: UUID, customer_id: UUID | None = None,
                              status: Literal["all", "available", "redeemed", "expired"] = "all",
                              limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                              actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await rewards.list_coupons(db, tenant_id, customer_id, status, limit, offset)


@router.post("/loyalty/coupons", status_code=201)
async def issue_loyalty_coupon(tenant_id: UUID, payload: CouponIssue, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await rewards.issue_manually(db, tenant_id, payload.customer_id, payload.idempotency_key)


@router.get("/loyalty/transactions")
async def list_points(tenant_id: UUID, customer_id: UUID | None = None,
                      limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                      actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.point_history(db, tenant_id, customer_id, limit, offset)


@router.post("/loyalty/transactions", status_code=201)
async def create_points(tenant_id: UUID, payload: LoyaltyTransactionCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return data.clean_row(await data.record_points(db, tenant_id, values_from(payload), payload.idempotency_key))


@router.get("/goals")
async def list_goals(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                     actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.goals(db, tenant_id, start_date, end_date)


@router.post("/goals", status_code=201)
async def create_goal(tenant_id: UUID, payload: GoalCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        values = values_from(payload)
        await data.validate_links(db, tenant_id, values)
        return data.clean_row(await data.insert_row(db, tenant_id, "professional_goals", values, payload.idempotency_key))


@router.patch("/goals/{goal_id}")
async def update_goal(tenant_id: UUID, goal_id: UUID, payload: GoalPatch, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "professional_goals", goal_id)
        changes = merged_changes(current, payload, GoalFields)
        await data.validate_links(db, tenant_id, {**current, **changes})
        return data.clean_row(await data.update_row(db, tenant_id, "professional_goals", goal_id, payload.expected_version, changes))


@router.delete("/goals/{goal_id}")
async def disable_goal(tenant_id: UUID, goal_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return data.clean_row(await data.update_row(db, tenant_id, "professional_goals", goal_id, payload.expected_version, {"active": False}))


@router.get("/performance")
async def get_performance(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                          actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.performance(db, tenant_id, start_date, end_date)


@router.get("/performance/daily")
async def get_daily_performance(tenant_id: UUID, start_date: date | None = None, end_date: date | None = None,
                                professional_id: UUID | None = None, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.daily_performance(db, tenant_id, start_date, end_date, professional_id)


@router.get("/site")
async def get_site(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.site(db, tenant_id)


@router.put("/site")
async def put_site(tenant_id: UUID, payload: SiteSave, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await data.save_site(db, tenant_id, values_from(payload), payload.expected_version)


@router.get("/marketing")
async def list_marketing(tenant_id: UUID, limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
                         actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await data.marketing(db, tenant_id, limit, offset)


@router.post("/marketing", status_code=201)
async def create_marketing(tenant_id: UUID, payload: MarketingCreate, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        values = {**values_from(payload), "template_name": "management_draft", "status": "draft"}
        return data.clean_row(await data.insert_row(db, tenant_id, "campaigns", values, payload.idempotency_key, operation="marketing"))


@router.patch("/marketing/{campaign_id}")
async def update_marketing(tenant_id: UUID, campaign_id: UUID, payload: MarketingPatch, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "campaigns", campaign_id, lock=True)
        if current["status"] != "draft":
            raise HTTPException(409, "Somente campanhas em rascunho podem ser editadas")
        changes = merged_changes(current, payload, MarketingFields)
        return data.clean_row(await data.update_row(db, tenant_id, "campaigns", campaign_id, payload.expected_version, changes))


@router.delete("/marketing/{campaign_id}")
async def archive_marketing(tenant_id: UUID, campaign_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        current = await data.get_row(db, tenant_id, "campaigns", campaign_id, lock=True)
        if current["status"] != "draft":
            raise HTTPException(409, "Somente rascunhos podem ser arquivados por este módulo")
        return data.clean_row(await data.update_row(db, tenant_id, "campaigns", campaign_id, payload.expected_version, {"status": "paused"}))


@router.get("/subscription")
async def get_subscription(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        subscription = (await db.execute(text("""
            SELECT s.id,s.status,s.trial_ends_at,s.grace_until,s.last_verified_payment_at,s.updated_at,
                   p.id AS plan_id,p.code,p.name,p.monthly_cents,p.max_professionals,p.max_messages_month
            FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.tenant_id=:tenant
        """), {"tenant": tenant_id})).mappings().one_or_none()
        plans = (await db.execute(text("""
            SELECT id,code,name,monthly_cents,max_professionals,max_messages_month FROM plans
            WHERE active ORDER BY monthly_cents,name,id
        """))).mappings().all()
        return {"subscription": dict(subscription) if subscription else None, "plans": [dict(row) for row in plans],
                "checkout_enabled": False}


@router.post("/appointments/{appointment_id}/complete")
async def complete_appointment(tenant_id: UUID, appointment_id: UUID, payload: AppointmentTransition,
                               actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await data.transition_appointment(db, tenant_id, appointment_id, "completed", payload, db.info["management_actor_role"])


@router.post("/appointments/{appointment_id}/no-show")
async def appointment_no_show(tenant_id: UUID, appointment_id: UUID, payload: AppointmentTransition,
                              actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        return await data.transition_appointment(db, tenant_id, appointment_id, "no_show", payload, db.info["management_actor_role"])
