"""Explicit, account-owned setup. Metadata records progress, never a second catalog.

All operational writes use runtime RLS. The support transaction holds the live
owner membership until the runtime transaction commits, including final activation.
"""
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
import hashlib
import json
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import support_session_factory, audit
from app.database import get_session_factory, tenant_transaction
from app.tenant_access import TenantAccess, check_access
from app import onboarding_schemas as schemas
from app import business_profile_service as profiles
from app.business_profile_schemas import BusinessProfile
from app.commercial_service import audit as commercial_audit

LABELS = {"business": "Empresa", "location": "Atendimento e endereço", "professionals": "Profissionais",
          "catalog": "Serviços e produtos", "hours": "Expediente", "channels": "Canais independentes", "review": "Revisão"}


def enabled():
    return os.getenv("ONBOARDING_ENABLED", "0") == "1"


def require_enabled():
    if not enabled():
        raise HTTPException(423, "A configuração inicial ainda não está habilitada")


def digest(value):
    return hashlib.sha256(json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def conflict(message="A configuração mudou. Atualize antes de salvar"):
    raise HTTPException(409, message)


@asynccontextmanager
async def access_session(actor, tenant, *, write=False):
    """Do not trust the membership cached in a panel cookie or a prior GET."""
    async with support_session_factory()() as db:
        async with db.begin():
            if actor.role in {"support", "admin"}:
                role = actor.role
            elif actor.role == "member" and isinstance(actor.account_id, UUID):
                if os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
                    raise HTTPException(423, "O acesso por empresa ainda não está habilitado")
                role = (await db.execute(text("""SELECT role FROM tenant_memberships
                    WHERE tenant_id=:t AND account_id=:a AND active FOR SHARE"""),
                    {"t": tenant, "a": actor.account_id})).scalar_one_or_none()
            elif actor.role == "viewer" and actor.tenant_id == tenant and not write:
                role = "viewer"
            else:
                role = None
            check_access(role, "publish" if write else "read")
            yield TenantAccess(role, actor.account_id)


async def _rows(db, table, tenant):
    # Only fixed, server-owned table names call this helper.
    return [dict(r) for r in (await db.execute(text(f"SELECT * FROM {table} WHERE tenant_id=:t ORDER BY id"),
                                             {"t": tenant})).mappings().all()]


async def _facts(db, tenant):
    business = (await db.execute(text("SELECT id,name,timezone,status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).mappings().first()
    if business is None:
        raise HTTPException(404, "Empresa não encontrada")
    result = {"business": dict(business)}
    for key, table in (("professionals", "professionals"), ("services", "services"),
                       ("links", "professional_services"), ("hours", "working_hours"),
                       ("breaks", "working_breaks"), ("products", "commercial_products"),
                       ("variants", "commercial_variants")):
        result[key] = await _rows(db, table, tenant)
    return result


def _minutes(value):
    if isinstance(value, str):
        value = time.fromisoformat(value)
    return value.hour * 60 + value.minute + value.second / 60


def _usable(row, table, managed, *, staged):
    if staged and str(row["id"]) in managed.get(table, {}):
        return bool(managed[table][str(row["id"])])
    return bool(row.get("active"))


def readiness(configuration, facts, managed=None, *, staged=True):
    """Structural readiness uses real bindings/intervals, not today's occupancy."""
    managed = managed or {}
    pending = []
    def add(step, code, message):
        pending.append({"step": step, "code": code, "message": message})
    if not all(configuration.get(key) for key in ("niche", "activity")):
        add("business", "identity", "Informe o ramo e a atividade da empresa")
    mode = configuration.get("modality")
    if mode not in {"onsite", "online", "client_address", "hybrid"}:
        add("location", "modality", "Escolha como a empresa atende")
    elif mode in {"onsite", "hybrid"}:
        try:
            schemas.Address.model_validate(configuration.get("private_address"))
        except (ValueError, TypeError):
            add("location", "private_address", "Complete o endereço operacional privado")
    activity = configuration.get("activity")
    pros = [p for p in facts.get("professionals", []) if _usable(p, "professionals", managed, staged=staged)
            and p.get("accepts_appointments")]
    services = [s for s in facts.get("services", []) if _usable(s, "services", managed, staged=staged)]
    if activity in {"services", "both"}:
        if not pros:
            add("professionals", "professional", "Cadastre um profissional que aceite agendamentos")
        if not services:
            add("catalog", "service", "Cadastre um serviço com valor e duração")
        fitting_services, fitting_professionals = set(), set()
        for s in services:
            duration = s.get("duration_min", 0) + s.get("prep_min", 0) + s.get("cleanup_min", 0)
            if duration <= 0 or type(s.get("price_cents")) is not int:
                continue
            for p in pros:
                if not any(str(link["service_id"]) == str(s["id"]) and str(link["professional_id"]) == str(p["id"])
                           for link in facts.get("links", [])):
                    continue
                for w in facts.get("hours", []):
                    if str(w["professional_id"]) != str(p["id"]):
                        continue
                    blocks = sorted((_minutes(b["start_time"]), _minutes(b["end_time"]))
                                    for b in facts.get("breaks", []) if str(b["working_hour_id"]) == str(w["id"]))
                    cursor, end = _minutes(w["start_time"]), _minutes(w["end_time"])
                    for start, stop in blocks + [(end, end)]:
                        if start - cursor >= duration:
                            fitting_services.add(str(s["id"]))
                            fitting_professionals.add(str(p["id"]))
                        cursor = max(cursor, stop)
        if (not fitting_services or any(str(s["id"]) not in fitting_services for s in services)
                or any(str(p["id"]) not in fitting_professionals for p in pros)):
            add("hours", "scheduling_bindings", "Vincule um serviço ao profissional e a um expediente que comporte sua duração e pausas")
    if activity in {"products", "both"}:
        products = {str(p["id"]) for p in facts.get("products", []) if _usable(p, "commercial_products", managed, staged=staged)}
        variants = [v for v in facts.get("variants", []) if str(v["product_id"]) in products
                    and _usable(v, "commercial_variants", managed, staged=staged)]
        if not variants:
            add("catalog", "product", "Cadastre um produto e sua apresentação; preço sob consulta é permitido")
        if os.getenv("COMMERCIAL_CATALOG_ENABLED", "0") != "1":
            add("catalog", "products_unavailable", "O módulo de produtos precisa estar disponível antes da conclusão")
    return pending


def _entity_fingerprint(facts, table, item):
    related = {}
    if table == "services":
        related["professional_ids"] = sorted(str(x["professional_id"]) for x in facts["links"] if x["service_id"] == item["id"])
    elif table == "commercial_products":
        related["variants"] = [v for v in facts["variants"] if v["product_id"] == item["id"]]
    elif table == "working_hours":
        related["breaks"] = [b for b in facts["breaks"] if b["working_hour_id"] == item["id"]]
    return digest({"row": item, **related})


def _snapshot(configuration, managed, facts):
    return digest({"configuration": {"private_address": None, **configuration}, "managed_ids": managed, "facts": facts})


async def _profile(db, tenant):
    row = (await db.execute(text("""SELECT s.generation,s.draft_version_id,s.published_version_id,
          d.profile AS draft,p.profile AS published FROM business_profile_state s
          LEFT JOIN business_profile_versions d ON d.tenant_id=s.tenant_id AND d.id=s.draft_version_id
          LEFT JOIN business_profile_versions p ON p.tenant_id=s.tenant_id AND p.id=s.published_version_id
          WHERE s.tenant_id=:t"""), {"t": tenant})).mappings().first()
    return dict(row) if row else {"generation": 0, "draft": None, "published": None}


async def _state(db, tenant, access):
    facts = await _facts(db, tenant)
    meta = (await db.execute(text("SELECT * FROM business_onboarding WHERE tenant_id=:t"), {"t": tenant})).mappings().first()
    configuration = dict(meta["configuration"]) if meta else {}
    managed = meta["managed_ids"] if meta else {}
    channels = await _profile(db, tenant)
    if not meta:
        published = channels.get("published") or {}
        categories = published.get("categories") or []
        configuration = {"niche": categories[0] if len(categories) == 1 else None,
                         "activity": "both" if facts["products"] and facts["services"] else "products" if facts["products"] else "services" if facts["services"] else None,
                         "contact_phone": published.get("contact_phone"), "modality": None, "private_address": None}
    pending = readiness(configuration, facts, managed)
    configuration.setdefault("private_address", None)
    site = (await db.execute(text("SELECT public_enabled FROM management_site_preferences WHERE tenant_id=:t"), {"t": tenant})).scalar_one_or_none()
    google = (await db.execute(text("SELECT status FROM google_connections WHERE tenant_id=:t"), {"t": tenant})).scalar_one_or_none()
    whatsapp = (await db.execute(text("SELECT bool_or(status='connected') FROM whatsapp_channels WHERE tenant_id=:t"), {"t": tenant})).scalar_one_or_none()
    catalog = {k: [] for k in ("professionals", "services", "products", "hours")}
    for key, table in (("professionals", "professionals"), ("services", "services"), ("products", "commercial_products"), ("hours", "working_hours")):
        for raw in facts[key]:
            row = dict(raw)
            row.pop("tenant_id", None)
            row["fingerprint"] = _entity_fingerprint(facts, table, raw)
            if str(raw["id"]) in managed.get(table, {}):
                row.update(draft=not bool(meta["completed_at"]), requested_active=managed[table][str(raw["id"])])
            if key == "services":
                row["professional_ids"] = [x["professional_id"] for x in facts["links"] if x["service_id"] == raw["id"]]
            if key == "products":
                row["variants"] = [{k: v for k, v in variant.items() if k != "tenant_id"}
                                   for variant in facts["variants"] if variant["product_id"] == raw["id"]]
                for variant in row["variants"]:
                    if str(variant["id"]) in managed.get("commercial_variants", {}):
                        variant.update(draft=not bool(meta["completed_at"]), requested_active=managed["commercial_variants"][str(variant["id"])])
            if key == "hours":
                row["breaks"] = [{k: v for k, v in b.items() if k != "tenant_id"} for b in facts["breaks"] if b["working_hour_id"] == raw["id"]]
            catalog[key].append(row)
    operations = [dict(r) for r in (await db.execute(text("""SELECT idempotency_key AS key,step,version,result
        FROM business_onboarding_operations WHERE tenant_id=:t ORDER BY created_at DESC,idempotency_key DESC LIMIT 20"""),
        {"t": tenant})).mappings().all()]
    missing = {p["step"] for p in pending}
    result = {"enabled": enabled(), "tenant_id": tenant, "name": facts["business"]["name"], "timezone": facts["business"]["timezone"],
              "version": meta["version"] if meta else 0, "can_edit": access.role in {"owner", "support", "admin"},
              "status": "legacy" if not meta else "complete" if meta["completed_at"] else "incomplete",
              "required": bool(meta and meta["is_new"] and not meta["completed_at"]),
              "current_step": meta["current_step"] if meta else "business", "pending": pending,
              "steps": [{"id": k, "label": label, "required": k not in {"channels", "review"}
                         and (k not in {"hours", "professionals"} or configuration.get("activity") != "products"),
                         "complete": k not in missing and (k != "review" or bool(configuration.get("reviewed")))} for k, label in LABELS.items()],
              "configuration": configuration, "catalog": catalog,
              "snapshot": _snapshot(configuration, managed, facts),
              "channels": {"assistant": {"status": "draft" if channels.get("draft") else "published" if channels.get("published") else "not_configured",
                           "generation": channels["generation"], "profile": channels.get("draft") or channels.get("published") or BusinessProfile().model_dump(mode="json")},
                           "site": {"status": "published" if site else "not_published"},
                           "google": {"status": google or "not_configured"},
                           "whatsapp": {"status": "connected" if whatsapp else "not_configured"}},
              "operations": operations, "last_operation_key": operations[0]["key"] if operations else None}
    return jsonable_encoder(result)


async def get_state(actor, tenant):
    require_enabled()
    async with access_session(actor, tenant) as access:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                return await _state(db, tenant, access)


async def _invitations(db, account):
    return [dict(r) for r in (await db.execute(text("""SELECT i.tenant_id,t.name AS business_name,i.status,i.role,i.expires_at
        FROM management_team_invitations i JOIN tenants t ON t.id=i.tenant_id
        WHERE i.expires_at>now() AND ((i.claimed_by=:a AND i.status='claimed')
          OR (:verified AND i.email=:email AND i.status='pending')) ORDER BY i.created_at DESC LIMIT 50"""),
        {"a": account["id"], "verified": bool(account["verified_at"]), "email": account["email"]})).mappings().all()]


async def entry(actor, tenant_id=None):
    if not enabled():
        return {"enabled": False, "kind": "dashboard", "create_allowed": False, "businesses": [], "invitations": []}
    if actor.role in {"support", "admin"} or not isinstance(actor.account_id, UUID):
        return {"enabled": True, "kind": "dashboard", "create_allowed": False, "businesses": [], "invitations": [], "reason": "legacy_operator"}
    async with support_session_factory()() as db:
        account = (await db.execute(text("SELECT id,name,email,verified_at FROM panel_accounts WHERE id=:a"), {"a": actor.account_id})).mappings().first()
        if account is None:
            raise HTTPException(401, "Conta indisponível")
        invitations = await _invitations(db, account)
        rows = [dict(r) for r in (await db.execute(text("""SELECT t.id,t.name,m.role,
            coalesce(o.is_new AND o.completed_at IS NULL,false) AS required,coalesce(o.version,0) AS version,o.current_step
            FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id
            LEFT JOIN business_onboarding o ON o.tenant_id=t.id WHERE m.account_id=:a AND m.active ORDER BY t.name,t.id"""),
            {"a": actor.account_id})).mappings().all()]
    selected = next((r for r in rows if str(r["id"]) == str(tenant_id)), None) if tenant_id else rows[0] if len(rows) == 1 else None
    if tenant_id and selected is None:
        raise HTTPException(403, "Empresa não vinculada à conta")
    kind = "choose_business" if len(rows) > 1 and selected is None else "invitation" if not rows and invitations else "onboarding" if not rows else "dashboard"
    if selected and selected["role"] == "owner" and selected["required"]:
        kind = "onboarding"
    elif selected and selected["required"]:
        kind = "blocked"
    if selected and selected["role"] == "owner" and not selected["required"] and selected["version"] == 0:
        # A read-only suggestion is not a retroactive lifecycle block for legacy.
        async with get_session_factory()() as db:
            async with tenant_transaction(db, UUID(str(selected["id"]))):
                current = await _facts(db, UUID(str(selected["id"])))
        activity = "both" if current["services"] and current["products"] else "products" if current["products"] else "services"
        structural = readiness({"niche": "legacy", "activity": activity, "modality": "online"}, current, staged=False)
        if structural:
            kind = "onboarding"
    return jsonable_encoder({"enabled": True, "kind": kind, "create_allowed": not bool(invitations),
        "account": {"verified": bool(account["verified_at"]), "name": account["name"], "email": account["email"]},
        "businesses": rows, "invitations": invitations, "tenant_id": selected["id"] if selected else None,
        "current_step": selected["current_step"] if selected else "business", "reason": "owner_setup_pending" if kind == "blocked" else None})


async def create_business(actor, payload):
    require_enabled()
    if actor.role not in {"member", "admin"} or not isinstance(actor.account_id, UUID):
        raise HTTPException(403, "Entre com sua conta pessoal para criar a empresa")
    if os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        raise HTTPException(423, "O acesso por empresa ainda não está habilitado")
    values = payload.model_dump(mode="json", exclude={"idempotency_key"})
    fingerprint = digest({"account": actor.account_id, "data": values})
    async with support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"), {"scope": "onboarding-create:" + str(actor.account_id)})
            account = (await db.execute(text("SELECT id,email,verified_at FROM panel_accounts WHERE id=:a FOR SHARE"), {"a": actor.account_id})).mappings().first()
            if account is None:
                raise HTTPException(401, "Conta indisponível")
            previous = (await db.execute(text("SELECT tenant_id,fingerprint FROM business_onboarding_creations WHERE account_id=:a AND idempotency_key=:key"), {"a": actor.account_id, "key": payload.idempotency_key})).mappings().first()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    conflict("A chave já foi usada para outro pedido")
                tenant = previous["tenant_id"]
            else:
                if await _invitations(db, account):
                    conflict("Conclua ou revise o convite pendente antes de criar outra empresa")
                tenant = uuid4()
                await db.execute(text("INSERT INTO tenants(id,name,slug,timezone,status) VALUES(:t,:name,:slug,:timezone,'paused')"),
                                 {"t": tenant, "name": payload.name, "slug": "negocio-" + tenant.hex, "timezone": payload.timezone})
                await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"), {"t": tenant, "a": actor.account_id})
                await db.execute(text("INSERT INTO tenant_membership_events(tenant_id,account_id,actor_account_id,role,active) VALUES(:t,:a,:a,'owner',true)"), {"t": tenant, "a": actor.account_id})
                configuration = {k: v for k, v in values.items() if k not in {"name", "timezone"}}
                await db.execute(text("""INSERT INTO business_onboarding(tenant_id,created_by,is_new,configuration,current_step,version)
                    VALUES(:t,:a,true,CAST(:c AS jsonb),'location',1)"""), {"t": tenant, "a": actor.account_id, "c": json.dumps(configuration)})
                await db.execute(text("INSERT INTO business_onboarding_creations(account_id,idempotency_key,fingerprint,tenant_id) VALUES(:a,:key,:fp,:t)"),
                                 {"t": tenant, "a": actor.account_id, "key": payload.idempotency_key, "fp": fingerprint})
                await db.execute(text("""INSERT INTO business_onboarding_operations(tenant_id,idempotency_key,
                    actor_account_id,fingerprint,step,result,version) VALUES(:t,:key,:a,:fp,'business',CAST(:r AS jsonb),1)"""),
                    {"t": tenant, "key": payload.idempotency_key, "a": actor.account_id,
                     "fp": fingerprint, "r": json.dumps({"tenant_id": str(tenant)})})
                await db.execute(text("INSERT INTO platform_audit(tenant_id,action) VALUES(:t,'onboarding_created')"), {"t": tenant})
    return await get_state(actor, tenant)


async def _lock_meta(db, tenant, actor):
    await db.execute(text("""INSERT INTO business_onboarding(tenant_id,created_by)
        VALUES(:t,:a) ON CONFLICT(tenant_id) DO NOTHING"""), {"t": tenant, "a": actor.account_id})
    return dict((await db.execute(text("SELECT * FROM business_onboarding WHERE tenant_id=:t FOR UPDATE"), {"t": tenant})).mappings().one())


async def _replay(db, tenant, actor, key, fingerprint):
    row = (await db.execute(text("SELECT * FROM business_onboarding_operations WHERE tenant_id=:t AND idempotency_key=:k"), {"t": tenant, "k": key})).mappings().first()
    if row and (row["fingerprint"] != fingerprint or row["actor_account_id"] != actor.account_id):
        conflict("A chave já identifica outra operação")
    return row


async def _persist(db, tenant, actor, meta, key, fingerprint, step, result):
    version = meta["version"] + 1
    await db.execute(text("""UPDATE business_onboarding SET configuration=CAST(:c AS jsonb),managed_ids=CAST(:m AS jsonb),
        current_step=:step,version=:v,completed_at=:done,updated_at=now() WHERE tenant_id=:t"""),
        {"t": tenant, "c": json.dumps(meta["configuration"]), "m": json.dumps(meta["managed_ids"]),
         "step": meta["current_step"], "v": version, "done": meta["completed_at"]})
    await db.execute(text("""INSERT INTO business_onboarding_operations
        (tenant_id,idempotency_key,actor_account_id,fingerprint,step,result,version)
        VALUES(:t,:k,:a,:f,:s,CAST(:r AS jsonb),:v)"""),
        {"t": tenant, "k": key, "a": actor.account_id, "f": fingerprint, "s": step,
         "r": json.dumps(jsonable_encoder(result)), "v": version})


async def _edit_guard(db, tenant, table, item_id, expected):
    item = (await db.execute(text(f"SELECT * FROM {table} WHERE tenant_id=:t AND id=:id FOR UPDATE"), {"t": tenant, "id": item_id})).mappings().first()
    if item is None:
        raise HTTPException(404, "Item não encontrado nesta empresa")
    related = {"services": ("professional_services", "service_id"),
               "commercial_products": ("commercial_variants", "product_id"),
               "working_hours": ("working_breaks", "working_hour_id")}.get(table)
    if related:
        await db.execute(text(f"SELECT id FROM {related[0]} WHERE tenant_id=:t AND {related[1]}=:id ORDER BY id FOR UPDATE"), {"t": tenant, "id": item_id})
    facts = await _facts(db, tenant)
    if not expected or expected != _entity_fingerprint(facts, table, dict(item)):
        conflict("Este item foi alterado. Confira os dados atuais")
    return item


async def _save_simple(db, tenant, meta, table, data, expected=None):
    values = dict(data)
    item_id = values.pop("id", None)
    values.pop("expected_fingerprint", None)
    if item_id:
        item_id = UUID(str(item_id))
        current = await _edit_guard(db, tenant, table, item_id, expected)
        requested_active = values.get("active", meta["managed_ids"].get(table, {}).get(str(item_id), current["active"]))
    else:
        item_id = uuid4()
        requested_active = values["active"]
        meta["managed_ids"].setdefault(table, {})[str(item_id)] = requested_active
    if str(item_id) in meta["managed_ids"].get(table, {}) and not meta["completed_at"]:
        meta["managed_ids"][table][str(item_id)] = requested_active
        values["active"] = False
    params = {**values, "t": tenant, "id": item_id}
    fields = list(values)
    if isinstance(params.get("commission_percent"), str):
        from decimal import Decimal
        params["commission_percent"] = Decimal(params["commission_percent"])
    for key in ("synonyms", "faqs", "official_benefits", "attributes"):
        if key in params:
            params[key] = json.dumps(params[key])
    def value(key):
        return f"CAST(:{key} AS jsonb)" if key in {"synonyms", "faqs", "official_benefits", "attributes"} else ":" + key
    if data.get("id"):
        revision = ",revision=revision+1,updated_at=now()" if table.startswith("commercial_") else ""
        await db.execute(text(f"UPDATE {table} SET " + ",".join(f"{k}={value(k)}" for k in fields) + revision + " WHERE tenant_id=:t AND id=:id"), params)
    else:
        await db.execute(text(f"INSERT INTO {table}(id,tenant_id," + ",".join(fields) + ") VALUES(:id,:t," + ",".join(value(k) for k in fields) + ")"), params)
    await audit(db, tenant, "onboarding_saved", table, item_id)
    return item_id


async def _save_hours(db, tenant, data):
    pro = (await db.execute(text("SELECT id FROM professionals WHERE tenant_id=:t AND id=:p FOR UPDATE"), {"t": tenant, "p": data.professional_id})).scalar_one_or_none()
    if pro is None:
        raise HTTPException(422, "Profissional não pertence à empresa")
    result = []
    for day in data.weekdays:
        row = (await db.execute(text("SELECT * FROM working_hours WHERE tenant_id=:t AND professional_id=:p AND weekday=:d FOR UPDATE"), {"t": tenant, "p": pro, "d": day})).mappings().first()
        if row:
            await db.execute(text("SELECT id FROM working_breaks WHERE tenant_id=:t AND working_hour_id=:id ORDER BY id FOR UPDATE"), {"t": tenant, "id": row["id"]})
        facts = await _facts(db, tenant)
        if row and (not data.replace_existing or data.expected_fingerprints.get(str(day)) != _entity_fingerprint(facts, "working_hours", dict(row))):
            conflict("Já existe expediente neste dia. Revise-o antes de substituir")
        item_id = row["id"] if row else uuid4()
        if row:
            await db.execute(text("DELETE FROM working_breaks WHERE tenant_id=:t AND working_hour_id=:id"), {"t": tenant, "id": item_id})
            await db.execute(text("UPDATE working_hours SET start_time=:s,end_time=:e WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": item_id, "s": data.start_time, "e": data.end_time})
        else:
            await db.execute(text("INSERT INTO working_hours(id,tenant_id,professional_id,weekday,start_time,end_time) VALUES(:id,:t,:p,:d,:s,:e)"), {"id": item_id, "t": tenant, "p": pro, "d": day, "s": data.start_time, "e": data.end_time})
        if data.break_start:
            await db.execute(text("INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time) VALUES(:t,:id,:s,:e)"), {"t": tenant, "id": item_id, "s": data.break_start, "e": data.break_end})
        await audit(db, tenant, "onboarding_saved", "working_hours", item_id)
        result.append(str(item_id))
    return {"ids": result}


async def _save_channels(db, tenant, data, access):
    if os.getenv("BUSINESS_PROFILES_ENABLED", "0") != "1":
        raise HTTPException(423, "O perfil da assistente ainda não está disponível")
    state = await profiles._state(db, tenant, data.expected_profile_generation)
    profile = None
    version = state["draft_version_id"] or state["published_version_id"]
    if version:
        profile = (await db.execute(text("SELECT profile FROM business_profile_versions WHERE tenant_id=:t AND id=:v"), {"t": tenant, "v": version})).scalar_one()
    values = dict(profile or {})
    style = dict(values.get("style_rules") or {})
    style.update(data.style_rules.model_dump(mode="json", exclude_unset=True))
    values.update(style_rules=style)
    if "assistant_name" in data.model_fields_set:
        values["assistant_name"] = data.assistant_name
    if "capabilities" in data.model_fields_set:
        values["capabilities"] = data.capabilities
    validated = BusinessProfile.model_validate(values)
    revision = (await db.execute(text("SELECT coalesce(max(revision),0)+1 FROM business_profile_versions WHERE tenant_id=:t"), {"t": tenant})).scalar_one()
    version_id = uuid4()
    await db.execute(text("""INSERT INTO business_profile_versions(id,tenant_id,revision,profile,created_by,actor_role)
        VALUES(:id,:t,:r,CAST(:p AS jsonb),:a,:role)"""), {"id": version_id, "t": tenant, "r": revision, "p": validated.model_dump_json(), "a": access.account_id, "role": access.role})
    await db.execute(text("UPDATE business_profile_state SET draft_version_id=:v,generation=generation+1,updated_at=now() WHERE tenant_id=:t"), {"t": tenant, "v": version_id})
    await profiles._event(db, tenant, version_id, "draft_saved", access)
    return {"version_id": str(version_id), "generation": data.expected_profile_generation + 1}


async def _apply_step(db, tenant, meta, step, data, access):
    config = meta["configuration"]
    if step == "business":
        await db.execute(text("SELECT public.onboarding_update_identity(:t,:name,:tz)"), {"t": tenant, "name": data.name, "tz": data.timezone})
        config.update(data.model_dump(mode="json", exclude={"name", "timezone"}))
    elif step == "location":
        config.update(modality=data.modality, private_address=data.address.model_dump(mode="json") if data.address else None)
    elif step == "professionals":
        item = data.item.model_dump(mode="json", exclude_unset=bool(data.item.id))
        item_id = await _save_simple(db, tenant, meta, "professionals", item, data.item.expected_fingerprint)
        # This is an explicit setup suggestion, not panel/staff authorization.
        if data.self_provider:
            if access.account_id is None:
                raise HTTPException(422, "A associação exige uma conta pessoal")
            config["self_provider"] = {"account_id": str(access.account_id), "professional_id": str(item_id)}
        return {"id": str(item_id)}
    elif step == "catalog":
        if data.kind == "product" and os.getenv("COMMERCIAL_CATALOG_ENABLED", "0") != "1":
            raise HTTPException(423, "O catálogo de produtos ainda não está disponível")
        if data.kind == "service":
            for pro in data.professional_ids:
                row = (await db.execute(text("SELECT id FROM professionals WHERE tenant_id=:t AND id=:id FOR UPDATE"), {"t": tenant, "id": pro})).scalar_one_or_none()
                if row is None:
                    raise HTTPException(422, "Vínculo a profissional de outra empresa ou inexistente")
            if data.item.get("id"):
                kind = (await db.execute(text("SELECT catalog_kind FROM services WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": UUID(data.item["id"])})).scalar_one_or_none()
                if kind and kind != "base":
                    conflict("Edite pacotes e complementos no cadastro específico")
            item = {k: v for k, v in data.item.items() if not data.item.get("id") or k in data._item_fields}
            item_id = await _save_simple(db, tenant, meta, "services", item, data.item.get("expected_fingerprint"))
            await db.execute(text("DELETE FROM professional_services WHERE tenant_id=:t AND service_id=:id"), {"t": tenant, "id": item_id})
            for pro in data.professional_ids:
                await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)"), {"t": tenant, "p": pro, "s": item_id})
        else:
            item = {k: v for k, v in data.item.items() if not data.item.get("id") or k in data._item_fields}
            item_id = await _save_simple(db, tenant, meta, "commercial_products", item, data.item.get("expected_fingerprint"))
            variant = data.variant.model_dump(mode="json")
            variant_id = variant.pop("id") or uuid4()
            if not isinstance(variant_id, UUID):
                variant_id = UUID(variant_id)
            existing = (await db.execute(text("SELECT * FROM commercial_variants WHERE tenant_id=:t AND product_id=:p AND id=:id FOR UPDATE"), {"t": tenant, "p": item_id, "id": variant_id})).mappings().first()
            if data.variant.id and not existing:
                raise HTTPException(422, "A variante não pertence a este produto")
            if not existing:
                meta["managed_ids"].setdefault("commercial_variants", {})[str(variant_id)] = variant["active"]
            if str(variant_id) in meta["managed_ids"].get("commercial_variants", {}) and not meta["completed_at"]:
                if not existing or "active" in data.variant.model_fields_set:
                    meta["managed_ids"]["commercial_variants"][str(variant_id)] = variant["active"]
                variant["active"] = False
            params = {**variant, "attributes": json.dumps(variant["attributes"]), "id": variant_id, "t": tenant, "p": item_id}
            if existing:
                fields = set(data.variant.model_fields_set) - {"id"}
                if str(variant_id) in meta["managed_ids"].get("commercial_variants", {}) and not meta["completed_at"]:
                    fields.add("active")
                assignments = [f"{k}=" + (f"CAST(:{k} AS jsonb)" if k == "attributes" else ":"+k) for k in sorted(fields)]
                await db.execute(text("UPDATE commercial_variants SET " + ",".join(assignments)
                    + ",revision=revision+1,updated_at=now() WHERE tenant_id=:t AND product_id=:p AND id=:id"), params)
            else:
                await db.execute(text("""INSERT INTO commercial_variants(id,tenant_id,product_id,name,sku,attributes,price_kind,unit_price_cents,active)
                  VALUES(:id,:t,:p,:name,:sku,CAST(:attributes AS jsonb),:price_kind,:unit_price_cents,:active)"""), params)
                await db.execute(text("INSERT INTO commercial_inventory(tenant_id,variant_id) VALUES(:t,:id)"), {"t": tenant, "id": variant_id})
            await commercial_audit(db, tenant, access, "product", item_id, "onboarding_saved", {"variant_id": str(variant_id)})
            return {"id": str(item_id), "variant_id": str(variant_id)}
        return {"id": str(item_id)}
    elif step == "hours":
        return await _save_hours(db, tenant, data)
    elif step == "channels":
        return await _save_channels(db, tenant, data, access)
    elif step == "review":
        config["reviewed"] = True
    return {}


async def save_step(actor, tenant, step, payload):
    require_enabled()
    model = schemas.STEPS.get(step)
    if model is None:
        raise HTTPException(404, "Etapa desconhecida")
    data = model.model_validate(payload.data)
    # Omission matters on edits: an omitted default preserves the current value.
    fingerprint = digest({"actor": actor.account_id, "step": step,
                          "data": data.model_dump(mode="json"), "supplied": payload.data})
    try:
        async with access_session(actor, tenant, write=True) as access:
            async with get_session_factory()() as db:
                async with tenant_transaction(db, tenant):
                    meta = await _lock_meta(db, tenant, actor)
                    replay = await _replay(db, tenant, actor, payload.idempotency_key, fingerprint)
                    if not replay:
                        if meta["version"] != payload.expected_version:
                            conflict()
                        if step != "review":
                            meta["configuration"]["reviewed"] = False
                        result = await _apply_step(db, tenant, meta, step, data, access)
                        pending = readiness(meta["configuration"], await _facts(db, tenant), meta["managed_ids"])
                        meta["current_step"] = pending[0]["step"] if pending else "review"
                        await _persist(db, tenant, actor, meta, payload.idempotency_key, fingerprint, step, result)
                    response = await _state(db, tenant, access)
                    response["result"] = replay["result"] if replay else result
                    return response
    except IntegrityError:
        conflict("O cadastro já existe ou foi alterado. Atualize antes de repetir o pedido")


async def complete(actor, tenant, payload):
    require_enabled()
    fingerprint = digest({"actor": actor.account_id, "step": "complete", "data": payload.model_dump(exclude={"expected_version", "idempotency_key"})})
    async with access_session(actor, tenant, write=True) as access:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                meta = await _lock_meta(db, tenant, actor)
                if await _replay(db, tenant, actor, payload.idempotency_key, fingerprint):
                    return await _state(db, tenant, access)
                if meta["version"] != payload.expected_version:
                    conflict()
                if not payload.activate_catalog:
                    raise HTTPException(422, "Confirme explicitamente a disponibilização do cadastro operacional")
                # Serialize final readiness against normal catalog edits/deletions.
                for table in ("professionals", "services", "commercial_products", "commercial_variants", "working_hours", "working_breaks", "professional_services"):
                    await db.execute(text(f"SELECT id FROM {table} WHERE tenant_id=:t ORDER BY id FOR UPDATE"), {"t": tenant})
                current_facts = await _facts(db, tenant)
                if payload.expected_snapshot != _snapshot(meta["configuration"], meta["managed_ids"], current_facts):
                    conflict("O catálogo ou expediente mudou. Revise os dados atuais antes de concluir")
                pending = readiness(meta["configuration"], current_facts, meta["managed_ids"])
                if pending:
                    raise HTTPException(409, {"message": "Complete os fatos pendentes antes de concluir", "pending": pending})
                for table in ("professionals", "services", "commercial_products", "commercial_variants"):
                    for item_id, requested in meta["managed_ids"].get(table, {}).items():
                        await db.execute(text(f"UPDATE {table} SET active=:active WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": UUID(item_id), "active": bool(requested)})
                meta["configuration"].update(reviewed=True, activation_requested=True)
                meta["managed_ids"] = {}
                meta["completed_at"] = datetime.now(timezone.utc)
                meta["current_step"] = "review"
                await _persist(db, tenant, actor, meta, payload.idempotency_key, fingerprint, "complete", {})
                if meta["is_new"]:
                    await db.execute(text("SELECT public.onboarding_activate_tenant(:t)"), {"t": tenant})
                return await _state(db, tenant, access)


async def operation_status(db, tenant):
    """Parent's capability gates may call this INSIDE their tenant transaction.

    Metadata absent is legacy, never a retroactive block. This does not authorize
    an operation and deliberately does not inspect customer/appointment counts.
    """
    row = (await db.execute(text("SELECT is_new,completed_at FROM business_onboarding WHERE tenant_id=:t"), {"t": tenant})).mappings().first()
    return {"managed": bool(row and row["is_new"]), "complete": not row or not row["is_new"] or row["completed_at"] is not None}


async def require_operational_ready(db, tenant, capability):
    """Use in the caller's existing RLS transaction, before an operational write.

    Setup edits, reads and cancellation of existing appointments do not call it.
    Legacy companies and a disabled rollout retain their previous engine gates.
    """
    if not enabled():
        return
    row = (await db.execute(text("SELECT is_new,completed_at,configuration FROM business_onboarding WHERE tenant_id=:t FOR SHARE"), {"t": tenant})).mappings().first()
    if not row or not row["is_new"]:
        return
    if row["completed_at"] is None:
        raise HTTPException(409, "Conclua a configuração da empresa antes desta operação")
    configuration = dict(row["configuration"])
    if capability in {"appointments", "rescheduling", "public_site", "google"}:
        if configuration.get("activity") not in {"services", "both"}:
            raise HTTPException(409, "Esta empresa não tem agenda configurada")
        configuration["activity"] = "services"
    elif capability in {"product_orders", "products"}:
        if configuration.get("activity") not in {"products", "both"}:
            raise HTTPException(409, "Esta empresa não tem produtos configurados")
        configuration["activity"] = "products"
    elif capability not in {"catalog_answers", "assistant", "whatsapp", "google"}:
        raise ValueError("Unknown onboarding capability")
    pending = readiness(configuration, await _facts(db, tenant), staged=False)
    if pending:
        raise HTTPException(409, {"message": "O cadastro operacional precisa de revisão", "pending": pending})
