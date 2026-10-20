"""CRM toolbox integrated with the current customers and personal accounts.

Quick replies only prepare text. The existing explicit send flow owns delivery.
Labels never constitute consent and this module never contacts a provider.
"""
from datetime import datetime, timezone
import os
from uuid import UUID
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictInt
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit
from app.catalog_schemas import StrictModel
from app.inbox_templates import LabelSave, QuickReplySave, VARIABLES, render_reply
from app.management_api import management_session

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management/inbox", tags=["Atendimento: CRM e respostas"])


def enabled():
    if os.getenv("INBOX_TOOLS_ENABLED", "0") != "1":
        raise HTTPException(423, "As ferramentas de atendimento ainda não estão habilitadas")


class Version(StrictModel):
    expected_version: StrictInt = Field(ge=1)


class Preview(StrictModel):
    customer_id: UUID
    expected_version: StrictInt = Field(ge=1)


def row_version(row, expected):
    if row is None:
        raise HTTPException(404, "Registro não encontrado neste estabelecimento")
    if row["version"] != expected:
        raise HTTPException(409, "Este registro mudou. Atualize antes de continuar")


async def customer(db, tenant, identity):
    row = (await db.execute(text("""SELECT id,name,whatsapp_e164,notes FROM customers
        WHERE tenant_id=:t AND id=:id AND active AND deleted_at IS NULL"""),
        {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Cliente não encontrado neste estabelecimento")
    return dict(row)


async def reply_row(db, tenant, identity, actor, *, lock=False):
    return (await db.execute(text("""SELECT * FROM inbox_quick_replies WHERE tenant_id=:t AND id=:id
        AND (owner_account_id IS NULL OR owner_account_id=CAST(:account AS uuid))""" + (" FOR UPDATE" if lock else "")),
        {"t": tenant, "id": identity, "account": actor.account_id})).mappings().one_or_none()


@router.get("/capabilities")
async def capabilities(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    from app.tenant_access import resolve_tenant_access
    await resolve_tenant_access(actor, tenant_id)
    return {"enabled": os.getenv("INBOX_TOOLS_ENABLED", "0") == "1", "variables": sorted(VARIABLES),
            "actions": ["prepare_text"], "sends_messages": False, "labels_prove_consent": False}


@router.get("/labels")
async def labels(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor) as db:
        rows = (await db.execute(text("SELECT id,name,color,version FROM inbox_labels WHERE tenant_id=:t ORDER BY lower(name),id LIMIT 200"), {"t": tenant_id})).mappings().all()
        return jsonable_encoder({"items": rows})


@router.post("/labels", status_code=201)
async def create_label(tenant_id: UUID, payload: LabelSave, actor: Actor = Depends(authenticate)):
    enabled()
    if payload.expected_version:
        raise HTTPException(422, "Uma etiqueta nova deve usar a versão zero")
    async with management_session(tenant_id, actor, write=True) as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": f"inbox-labels:{tenant_id}"})
        count = (await db.execute(text("SELECT count(*) FROM inbox_labels WHERE tenant_id=:t"), {"t": tenant_id})).scalar_one()
        if count >= 200:
            raise HTTPException(409, "Limite de 200 etiquetas por estabelecimento")
        row = (await db.execute(text("""INSERT INTO inbox_labels(tenant_id,name,color) VALUES(:t,:name,:color)
            RETURNING id,name,color,version"""), {"t": tenant_id, "name": payload.name, "color": payload.color})).mappings().one()
        await audit(db, tenant_id, "inbox_label_created", "inbox_labels", row["id"])
        return jsonable_encoder(row)


@router.put("/labels/{label_id}")
async def update_label(tenant_id: UUID, label_id: UUID, payload: LabelSave, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor, write=True) as db:
        row = (await db.execute(text("SELECT * FROM inbox_labels WHERE tenant_id=:t AND id=:id FOR UPDATE"), {"t": tenant_id, "id": label_id})).mappings().one_or_none()
        row_version(row, payload.expected_version)
        updated = (await db.execute(text("""UPDATE inbox_labels SET name=:name,color=:color,version=version+1
            WHERE tenant_id=:t AND id=:id RETURNING id,name,color,version"""),
            {"t": tenant_id, "id": label_id, "name": payload.name, "color": payload.color})).mappings().one()
        await audit(db, tenant_id, "inbox_label_updated", "inbox_labels", label_id)
        return jsonable_encoder(updated)


@router.delete("/labels/{label_id}")
async def delete_label(tenant_id: UUID, label_id: UUID, payload: Version, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor, write=True) as db:
        row = (await db.execute(text("SELECT version FROM inbox_labels WHERE tenant_id=:t AND id=:id FOR UPDATE"), {"t": tenant_id, "id": label_id})).mappings().one_or_none()
        row_version(row, payload.expected_version)
        await db.execute(text("DELETE FROM inbox_labels WHERE tenant_id=:t AND id=:id"), {"t": tenant_id, "id": label_id})
        await audit(db, tenant_id, "inbox_label_deleted", "inbox_labels", label_id)
        return {"deleted": True}


@router.get("/customers")
async def customers(tenant_id: UUID, q: str = Query(default="", max_length=100), label_id: UUID | None = None,
                    offset: int = Query(default=0, ge=0, le=20000), actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT c.id,c.name,c.whatsapp_e164 AS phone FROM customers c
          WHERE c.tenant_id=:t AND c.active AND c.deleted_at IS NULL
          AND (:q='' OR position(lower(:q) in lower(c.name))>0 OR position(:q in c.whatsapp_e164)>0)
          AND (CAST(:label AS uuid) IS NULL OR EXISTS(SELECT 1 FROM inbox_customer_labels x
            WHERE x.tenant_id=c.tenant_id AND x.customer_id=c.id AND x.label_id=CAST(:label AS uuid)))
          ORDER BY lower(c.name),c.id LIMIT 51 OFFSET :offset"""),
          {"t": tenant_id, "q": q.strip(), "label": label_id, "offset": offset})).mappings().all()
        return jsonable_encoder({"items": rows[:50], "has_more": len(rows)>50})


@router.put("/customers/{customer_id}/labels/{label_id}")
async def assign_label(tenant_id: UUID, customer_id: UUID, label_id: UUID, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor, write=True) as db:
        await customer(db, tenant_id, customer_id)
        found = (await db.execute(text("SELECT id FROM inbox_labels WHERE tenant_id=:t AND id=:id FOR SHARE"), {"t": tenant_id, "id": label_id})).scalar_one_or_none()
        if found is None:
            raise HTTPException(404, "Etiqueta não encontrada")
        changed = (await db.execute(text("""INSERT INTO inbox_customer_labels(tenant_id,customer_id,label_id)
            VALUES(:t,:customer,:label) ON CONFLICT DO NOTHING RETURNING label_id"""),
            {"t": tenant_id, "customer": customer_id, "label": label_id})).scalar_one_or_none()
        if changed:
            await audit(db, tenant_id, "inbox_label_assigned", "customers", customer_id)
        return {"assigned": True}


@router.delete("/customers/{customer_id}/labels/{label_id}")
async def unassign_label(tenant_id: UUID, customer_id: UUID, label_id: UUID, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor, write=True) as db:
        await customer(db, tenant_id, customer_id)
        changed = (await db.execute(text("""DELETE FROM inbox_customer_labels WHERE tenant_id=:t AND customer_id=:customer
            AND label_id=:label RETURNING label_id"""), {"t": tenant_id, "customer": customer_id, "label": label_id})).scalar_one_or_none()
        if changed:
            await audit(db, tenant_id, "inbox_label_removed", "customers", customer_id)
        return {"removed": True}


async def context(db, tenant, identity):
    person = await customer(db, tenant, identity)
    business = (await db.execute(text("SELECT name,timezone FROM tenants WHERE id=:t"), {"t": tenant})).mappings().one()
    appointments = (await db.execute(text("""SELECT a.id,a.status,a.start_at,a.protocol,a.total_cents,p.name AS professional,
      (SELECT string_agg(i.name_snapshot,', ' ORDER BY i.item_order) FROM appointment_items i
       WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id) AS services
      FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
      WHERE a.tenant_id=:t AND a.customer_id=:customer
      ORDER BY a.start_at DESC,a.id LIMIT 20"""), {"t": tenant, "customer": identity})).mappings().all()
    # Find the actual nearest future appointment independently of the history page.
    upcoming = (await db.execute(text("""SELECT a.id,a.status,a.start_at,a.protocol,a.total_cents,p.name AS professional,
      (SELECT string_agg(i.name_snapshot,', ' ORDER BY i.item_order) FROM appointment_items i
       WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id) AS services
      FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
      WHERE a.tenant_id=:t AND a.customer_id=:customer AND a.status='confirmed' AND a.start_at>now()
      ORDER BY a.start_at,a.id LIMIT 1"""), {"t": tenant, "customer": identity})).mappings().one_or_none()
    tags = (await db.execute(text("""SELECT l.id,l.name,l.color,l.version FROM inbox_labels l JOIN inbox_customer_labels x
      ON x.tenant_id=l.tenant_id AND x.label_id=l.id WHERE l.tenant_id=:t AND x.customer_id=:customer
      ORDER BY lower(l.name),l.id"""), {"t": tenant, "customer": identity})).mappings().all()
    facts = {"cliente.nome": person["name"], "cliente.primeiro_nome": person["name"].split()[0] if person["name"].split() else "",
             "empresa.nome": business["name"]}
    address = (await db.execute(text("""SELECT v.profile->>'address' FROM business_profile_state s
      JOIN business_profile_versions v ON v.tenant_id=s.tenant_id AND v.id=s.published_version_id
      WHERE s.tenant_id=:t"""), {"t": tenant})).scalar_one_or_none()
    if isinstance(address, str) and address.strip():
        facts["empresa.endereco"] = address
    public_slug = (await db.execute(text("SELECT public_slug FROM management_site_preferences WHERE tenant_id=:t AND public_enabled"), {"t": tenant})).scalar_one_or_none()
    origin = os.getenv("PANEL_PUBLIC_URL", "").rstrip("/")
    parsed = urlsplit(origin)
    if (public_slug and parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment):
        facts["empresa.link_agendar"] = origin+"/s/"+public_slug
    if upcoming:
        local = upcoming["start_at"].astimezone(ZoneInfo(business["timezone"]))
        facts.update({"agendamento.data": local.strftime("%d/%m/%Y"), "agendamento.horario": local.strftime("%H:%M"),
            "agendamento.protocolo": upcoming["protocol"], "profissional.nome": upcoming["professional"],
            "servico.nome": upcoming["services"], "servico.valor": f"R$ {upcoming['total_cents']//100},{upcoming['total_cents']%100:02d}"})
    return {"customer": person, "timezone": business["timezone"], "labels": tags, "upcoming": upcoming, "history": appointments,
            "history_limit": 20, "facts": facts}


@router.get("/customers/{customer_id}")
async def customer_context(tenant_id: UUID, customer_id: UUID, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor) as db:
        return jsonable_encoder(await context(db, tenant_id, customer_id))


@router.get("/quick-replies")
async def replies(tenant_id: UUID, q: str = Query(default="", max_length=100), actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT id,title,category,body AS text,version,
          CASE WHEN owner_account_id IS NULL THEN 'company' ELSE 'personal' END AS visibility
          FROM inbox_quick_replies WHERE tenant_id=:t AND (owner_account_id IS NULL OR owner_account_id=CAST(:account AS uuid))
          AND (:q='' OR position(lower(:q) in lower(title||' '||category||' '||body))>0)
          ORDER BY category,title,id LIMIT 200"""), {"t": tenant_id, "account": actor.account_id, "q": q.strip()})).mappings().all()
        return jsonable_encoder({"items": rows})


@router.post("/quick-replies", status_code=201)
async def create_reply(tenant_id: UUID, payload: QuickReplySave, actor: Actor = Depends(authenticate)):
    enabled()
    if payload.expected_version or payload.visibility == "personal" and actor.account_id is None:
        raise HTTPException(422, "Verifique a versão e entre com sua conta pessoal")
    async with management_session(tenant_id, actor, write=True) as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": f"inbox-replies:{tenant_id}"})
        count = (await db.execute(text("SELECT count(*) FROM inbox_quick_replies WHERE tenant_id=:t"), {"t": tenant_id})).scalar_one()
        if count >= 200:
            raise HTTPException(409, "Limite de 200 respostas por estabelecimento")
        row = (await db.execute(text("""INSERT INTO inbox_quick_replies(tenant_id,owner_account_id,title,category,body)
          VALUES(:t,:account,:title,:category,:body) RETURNING id,version"""),
          {"t": tenant_id, "account": actor.account_id if payload.visibility == "personal" else None,
           "title": payload.title, "category": payload.category, "body": payload.text})).mappings().one()
        await audit(db, tenant_id, "inbox_reply_created", "inbox_quick_replies", row["id"])
        return jsonable_encoder(row)


@router.put("/quick-replies/{reply_id}")
async def update_reply(tenant_id: UUID, reply_id: UUID, payload: QuickReplySave, actor: Actor = Depends(authenticate)):
    enabled()
    if payload.visibility == "personal" and actor.account_id is None:
        raise HTTPException(422, "Entre com sua conta pessoal")
    async with management_session(tenant_id, actor, write=True) as db:
        row = await reply_row(db, tenant_id, reply_id, actor, lock=True)
        row_version(row, payload.expected_version)
        updated = (await db.execute(text("""UPDATE inbox_quick_replies SET title=:title,category=:category,body=:body,
          owner_account_id=:account,version=version+1,updated_at=now() WHERE tenant_id=:t AND id=:id RETURNING id,version"""),
          {"t": tenant_id, "id": reply_id, "title": payload.title, "category": payload.category, "body": payload.text,
           "account": actor.account_id if payload.visibility == "personal" else None})).mappings().one()
        await audit(db, tenant_id, "inbox_reply_updated", "inbox_quick_replies", reply_id)
        return jsonable_encoder(updated)


@router.delete("/quick-replies/{reply_id}")
async def delete_reply(tenant_id: UUID, reply_id: UUID, payload: Version, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor, write=True) as db:
        row_version(await reply_row(db, tenant_id, reply_id, actor, lock=True), payload.expected_version)
        await db.execute(text("DELETE FROM inbox_quick_replies WHERE tenant_id=:t AND id=:id"), {"t": tenant_id, "id": reply_id})
        await audit(db, tenant_id, "inbox_reply_deleted", "inbox_quick_replies", reply_id)
        return {"deleted": True}


@router.post("/quick-replies/{reply_id}/preview")
async def preview_reply(tenant_id: UUID, reply_id: UUID, payload: Preview, actor: Actor = Depends(authenticate)):
    enabled()
    async with management_session(tenant_id, actor) as db:
        row = await reply_row(db, tenant_id, reply_id, actor)
        row_version(row, payload.expected_version)
        crm = await context(db, tenant_id, payload.customer_id)
        try:
            body = render_reply(row["body"], crm["facts"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"text": body, "phone": crm["customer"]["whatsapp_e164"], "sent": False,
                "mode": "prepare_text", "prepared_at": datetime.now(timezone.utc)}
