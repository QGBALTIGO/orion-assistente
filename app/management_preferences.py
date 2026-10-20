"""Operational settings, automation drafts and an observed delivery queue."""
from datetime import datetime
import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit
from app.management_api import management_session
from app.management_schemas import normalize_phone
from app.tenant_access import resolve_tenant_access

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management", tags=["Preferências e notificações"])
EVENTS = ("booking_confirmed", "reminder_24h", "reminder_2h", "reminder_1h", "rescheduled", "cancelled", "inactive_60d",
          "cancelled_7d", "birthday_7d", "birthday_2d", "loyalty_reward", "after_service")


async def automation_active(db, tenant):
    from app.automation_runtime import execution_enabled, execution_approved, settings_for
    return execution_enabled() and execution_approved(await settings_for(db, tenant))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BusinessPreferences(StrictModel):
    contact_phone: str | None = Field(default=None, max_length=40)
    monthly_goal_cents: int = Field(default=0, ge=0, le=1_000_000_000)
    tax_document_kind: Literal["none", "cpf", "cnpj"] = "none"
    tax_document: str | None = Field(default=None, max_length=18)
    legal_name: str = Field(default="", max_length=160)
    trade_name: str = Field(default="", max_length=160)
    google_review_url: str | None = Field(default=None, max_length=2048)
    google_review_message: str = Field(default="Como foi sua experiência? Conte para a gente e deixe sua avaliação.", max_length=1000)
    auto_review_requested: bool = False
    weekly_summary_requested: bool = False
    approved_link_hosts: list[str] = Field(default_factory=list,max_length=20)

    @field_validator('approved_link_hosts')
    @classmethod
    def approved_hosts(cls,value):
        from app.communication_links import approved_host
        return sorted(set(approved_host(x) for x in value))

    _phone = field_validator("contact_phone")(normalize_phone)

    @field_validator("google_review_url")
    @classmethod
    def url(cls, value):
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use um link HTTPS de avaliação")
        return value

    @model_validator(mode="after")
    def document(self):
        if self.tax_document_kind == "none":
            if self.tax_document:
                raise ValueError("Selecione o tipo do documento")
        elif self.tax_document:
            digits = "".join(x for x in self.tax_document if x.isdigit())
            if any(x not in "0123456789./- " for x in self.tax_document) or len(digits) != (11 if self.tax_document_kind == "cpf" else 14):
                raise ValueError("O documento não corresponde ao tipo selecionado")
            self.tax_document = digits
        return self


class AutomationRule(StrictModel):
    event: Literal["booking_confirmed", "reminder_24h", "reminder_2h", "reminder_1h", "rescheduled", "cancelled", "inactive_60d", "cancelled_7d", "birthday_7d", "birthday_2d", "loyalty_reward", "after_service"]
    requested_enabled: bool = False
    template: str = Field(min_length=1, max_length=1500)
    template_source: Literal['custom','library'] = 'custom'
    template_revision: int = Field(default=1,ge=1,le=100000)


class DeliveryPolicy(StrictModel):
    operational_start_hour: StrictInt = Field(default=9,ge=0,le=23)
    operational_end_hour: StrictInt = Field(default=18,ge=1,le=24)
    marketing_start_hour: StrictInt = Field(default=9,ge=0,le=23)
    marketing_end_hour: StrictInt = Field(default=18,ge=1,le=24)
    weekdays: list[StrictInt] = Field(default_factory=lambda:list(range(7)),min_length=1,max_length=7)
    holidays: list[str] = Field(default_factory=list,max_length=100)
    birthday_leap_day: Literal['skip','feb28','mar01'] = 'skip'

    @model_validator(mode='after')
    def consistent_policy(self):
        from datetime import date
        if self.operational_start_hour>=self.operational_end_hour or self.marketing_start_hour>=self.marketing_end_hour:
            raise ValueError('O início da janela precisa ser anterior ao fim')
        if any(type(x) is not int or not 0<=x<=6 for x in self.weekdays) or len(set(self.weekdays))!=len(self.weekdays):
            raise ValueError('Dias de envio inválidos ou repetidos')
        for x in self.holidays:date.fromisoformat(x)
        return self


class AutomationPreferences(StrictModel):
    rules: list[AutomationRule] = Field(default_factory=list, max_length=12)
    recovery_days: Literal[30, 45, 60, 90] = 60
    vacancy_requested: bool = False
    delivery_policy: DeliveryPolicy = Field(default_factory=DeliveryPolicy)

    @field_validator("rules")
    @classmethod
    def distinct(cls, rules):
        if len({x.event for x in rules}) != len(rules):
            raise ValueError("Cada rotina só pode aparecer uma vez")
        return rules


class BusinessSave(StrictModel):
    expected_version: int = Field(ge=0)
    business: BusinessPreferences


class AutomationsSave(StrictModel):
    expected_version: int = Field(ge=0)
    configuration: AutomationPreferences


async def preferences(db, tenant_id):
    row = (await db.execute(text("SELECT preferences,version,updated_at FROM management_preferences WHERE tenant_id=:tenant"),
                           {"tenant": tenant_id})).mappings().one_or_none()
    stored = row["preferences"] if row else {}
    return {"business": BusinessPreferences.model_validate(stored.get("business", {})).model_dump(),
            "automations": AutomationPreferences.model_validate(stored.get("automations", {})).model_dump(),
            "version": row["version"] if row else 0, "updated_at": row["updated_at"] if row else None}


async def save_section(db, tenant_id, section, value, expected):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": "management-preferences:" + str(tenant_id)})
    current = await preferences(db, tenant_id)
    if expected != current["version"]:
        raise HTTPException(409, "As preferências mudaram. Atualize antes de salvar")
    content = {"business": current["business"], "automations": current["automations"], section: value}
    await db.execute(text("""INSERT INTO management_preferences(tenant_id,preferences) VALUES(:tenant,CAST(:content AS jsonb))
        ON CONFLICT(tenant_id) DO UPDATE SET preferences=EXCLUDED.preferences,version=management_preferences.version+1,updated_at=now()"""),
        {"tenant": tenant_id, "content": json.dumps(content)})
    await audit(db, tenant_id, "preferences_saved", "management_preferences", tenant_id)
    return await preferences(db, tenant_id)


@router.get("/preferences")
async def get_preferences(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    access = await resolve_tenant_access(actor, tenant_id, "read")
    async with management_session(tenant_id, actor) as db:
        result = await preferences(db, tenant_id)
        if access.role == "viewer":
            result["business"]["tax_document"] = None
        return {**result, "can_save": access.role in {"owner", "support", "admin"},
                "automation_execution_enabled": await automation_active(db, tenant_id)}


@router.put("/preferences")
async def put_preferences(tenant_id: UUID, payload: BusinessSave, actor: Actor = Depends(authenticate)):
    await resolve_tenant_access(actor, tenant_id, "publish")
    async with management_session(tenant_id, actor, write=True) as db:
        result = await save_section(db, tenant_id, "business", payload.business.model_dump(), payload.expected_version)
        return {**result, "automation_execution_enabled": await automation_active(db, tenant_id)}


@router.get("/marketing/automations")
async def get_automations(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        result = await preferences(db, tenant_id)
        channels = (await db.execute(text("SELECT status,bot_enabled FROM whatsapp_channels WHERE tenant_id=:tenant"), {"tenant": tenant_id})).mappings().all()
        active = await automation_active(db, tenant_id)
        from app.automation_templates import catalog
        return {"configuration": result["automations"], "version": result["version"], "events": list(EVENTS),'templates':catalog(),
                "channel_connected": any(x["status"] == "connected" for x in channels),
                "execution_enabled": active, "mode": "reviewed_execution" if active else "draft",
                "reason": "Regras autorizadas pelo proprietário, sujeitas a consentimento e revisão antes do envio." if active else "Configuração salva. Revise e autorize os envios separadamente."}


@router.put("/marketing/automations")
async def put_automations(tenant_id: UUID, payload: AutomationsSave, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        result = await save_section(db, tenant_id, "automations", payload.configuration.model_dump(), payload.expected_version)
        active = await automation_active(db, tenant_id)
        return {"configuration": result["automations"], "version": result["version"], "execution_enabled": active, "mode": "reviewed_execution" if active else "draft"}


@router.get("/marketing/queue")
async def queue(tenant_id: UUID, status: Literal["all", "pending", "sent", "dead"] = "all", limit: int = Query(default=50, ge=1, le=200),
                actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT o.id,o.status,o.attempts,o.next_attempt_at,e.received_at AS created_at,
            right(o.to_e164,4) AS recipient_suffix,
            CASE WHEN o.status='dead' AND o.last_error IN ('delivery_unverified','prior_attempt_unverified') THEN 'Entrega em verificação; não reenviar automaticamente'
              WHEN o.status='dead' THEN 'Envio interrompido; consulte a operação do canal' ELSE NULL END AS error,
            CASE WHEN o.status='dead' AND o.last_error IN ('delivery_unverified','prior_attempt_unverified') THEN 'verification_required'
              ELSE o.status END AS delivery_state
            FROM message_outbox o JOIN inbound_events e ON e.tenant_id=o.tenant_id AND e.id=o.event_id
            WHERE o.tenant_id=:tenant AND (:status='all' OR o.status=:status)
            ORDER BY e.received_at DESC,o.id DESC LIMIT :limit"""),
            {"tenant": tenant_id, "status": status, "limit": limit})).mappings().all()
        summary = (await db.execute(text("SELECT status,count(*) AS count FROM message_outbox WHERE tenant_id=:tenant GROUP BY status"),
                                   {"tenant": tenant_id})).mappings().all()
        return {"items": [dict(row) for row in rows], "summary": {x["status"]: x["count"] for x in summary},
                "source": "whatsapp_reply_outbox", "description": "Fila de respostas do canal Meta. Autorizações e recibos das campanhas ficam em Envios revisados."}


class ReadNotifications(StrictModel):
    ids: list[UUID] = Field(min_length=1, max_length=100)


@router.get("/notifications")
async def notifications(tenant_id: UUID, unread_only: bool = False, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT h.id,h.event,h.occurred_at AS happened_at,a.id AS appointment_id,
            a.start_at,c.name AS customer_name,p.name AS professional_name,r.read_at
            FROM appointment_history h JOIN appointments a ON a.tenant_id=h.tenant_id AND a.id=h.appointment_id
            JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
            JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
            LEFT JOIN management_notification_reads r ON r.tenant_id=h.tenant_id AND r.event_id=h.id AND r.account_id=:account
            WHERE h.tenant_id=:tenant AND h.event IN ('confirmed','rescheduled','cancelled','completed','no_show')
            AND (NOT :unread OR r.read_at IS NULL) ORDER BY h.occurred_at DESC,h.id DESC LIMIT 100"""),
            {"tenant": tenant_id, "account": actor.account_id, "unread": unread_only})).mappings().all()
        return {"items": [dict(row) for row in rows], "can_mark_read": actor.account_id is not None,
                "source": "appointment_history", "push_configured": False}


@router.post("/notifications/read")
async def read_notifications(tenant_id: UUID, payload: ReadNotifications, actor: Actor = Depends(authenticate)):
    if actor.account_id is None:
        raise HTTPException(403, "Entre com sua conta pessoal para guardar as notificações lidas")
    async with management_session(tenant_id, actor) as db:
        for identity in set(payload.ids):
            await db.execute(text("""INSERT INTO management_notification_reads(tenant_id,account_id,event_id)
                SELECT :tenant,:account,h.id FROM appointment_history h WHERE h.tenant_id=:tenant AND h.id=:id
                ON CONFLICT DO NOTHING"""), {"tenant": tenant_id, "account": actor.account_id, "id": identity})
        return {"ok": True}
