"""Commission bookkeeping, with immutable snapshots and FIFO advance allocation.

The professional advisory lock is shared with completion and relevant financial
edits. Nothing in this module contacts a bank, transfers money or sends a message.
"""
from datetime import datetime, timezone
import hashlib
import json
import secrets
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.booking_service import acquire_professional_lock
from app.catalog_api import audit
from app import management_models as data


async def lock_professionals(db, tenant_id, identities):
    # A financial edit can change its associated professional. Consistent UUID
    # ordering keeps two such edits from taking locks in the opposite order.
    for identity in sorted({identity for identity in identities if identity is not None}, key=str):
        await acquire_professional_lock(db, tenant_id, identity)


async def ensure_editable_entry(db, tenant_id, entry_id):
    locked = (await db.execute(text("""
        SELECT EXISTS(SELECT 1 FROM commission_settlements WHERE tenant_id=:tenant AND payment_entry_id=:id)
            OR EXISTS(SELECT 1 FROM commission_advance_allocations WHERE tenant_id=:tenant AND advance_entry_id=:id)
    """), {"tenant": tenant_id, "id": entry_id})).scalar_one()
    if locked:
        raise HTTPException(409, "Este lançamento participa de uma quitação e seu histórico deve ser preservado")


def allocate_advances(earned_cents, payments_cents, advances):
    previously_allocated = sum(int(item["allocated_cents"]) for item in advances)
    capacity = max(0, int(earned_cents) - int(payments_cents) - previously_allocated)
    plan = []
    for row in advances:
        item = dict(row)
        pending = int(item["amount_cents"]) - int(item["allocated_cents"])
        if pending < 0:
            raise HTTPException(409, "O histórico do vale precisa ser conciliado antes da quitação")
        allocation = min(pending, capacity)
        item.update(pending_cents=pending, allocation_cents=allocation)
        capacity -= allocation
        plan.append(item)
    return plan, capacity, previously_allocated


async def settlement_preview(db, tenant_id, professional_id):
    await acquire_professional_lock(db, tenant_id, professional_id)
    professional = (await db.execute(text("""
        SELECT id,name,commission_percent,commission_mode FROM professionals WHERE tenant_id=:tenant AND id=:id FOR SHARE
    """), {"tenant": tenant_id, "id": professional_id})).mappings().one_or_none()
    if professional is None:
        raise HTTPException(404, "Profissional não encontrado nesta empresa")
    params = {"tenant": tenant_id, "professional": professional_id}
    earned = (await db.execute(text("""
        SELECT a.id,a.total_cents,a.commission_percent_snapshot,a.commission_cents,a.updated_at,
            """ + data.COMMISSION_VALUE + """ AS earned_cents
        FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        WHERE a.tenant_id=:tenant AND a.professional_id=:professional AND a.status='completed' ORDER BY a.id
    """), params)).mappings().all()
    payments = (await db.execute(text("""
        SELECT id,amount_cents,version FROM financial_entries WHERE tenant_id=:tenant AND professional_id=:professional
            AND kind='expense' AND status='paid' AND entry_type='commission_payment' ORDER BY id
    """), params)).mappings().all()
    advances = (await db.execute(text("""
        SELECT e.id,e.description,e.amount_cents,e.occurred_at,e.version,
            COALESCE(sum(a.amount_cents),0)::bigint AS allocated_cents
        FROM financial_entries e LEFT JOIN commission_advance_allocations a ON a.tenant_id=e.tenant_id AND a.advance_entry_id=e.id
        WHERE e.tenant_id=:tenant AND e.professional_id=:professional AND e.kind='expense' AND e.status='paid' AND e.entry_type='advance'
        GROUP BY e.id,e.description,e.amount_cents,e.occurred_at,e.created_at,e.version ORDER BY e.occurred_at,e.created_at,e.id
    """), params)).mappings().all()
    earned_total = sum(int(row["earned_cents"]) for row in earned)
    paid_total = sum(row["amount_cents"] for row in payments)
    advances_total = sum(row["amount_cents"] for row in advances)
    plan, payable, allocated = allocate_advances(earned_total, paid_total, advances)
    summary = {"earned_cents": earned_total, "payments_cents": paid_total, "advances_cents": advances_total,
               "allocated_advances_cents": allocated, "pending_advances_cents": advances_total-allocated,
               "advance_allocation_cents": sum(row["allocation_cents"] for row in plan), "payable_cents": payable,
               "balance_cents": earned_total-paid_total-advances_total,
               "legacy_rate_estimates": sum(row["commission_cents"] is None for row in earned)}
    snapshot = {"professional": dict(professional), "earnings": [dict(row) for row in earned],
                "payments": [dict(row) for row in payments], "advances": [dict(row) for row in advances], "summary": summary}
    encoded = json.dumps(jsonable_encoder(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"professional": dict(professional), "summary": summary, "advances": plan,
            "snapshot_hash": hashlib.sha256(encoded.encode()).hexdigest(), "_snapshot": snapshot,
            "basis": "all_recorded_completed_appointments", "transfers_enabled": False}


async def get_settlement(db, tenant_id, identity):
    row = (await db.execute(text("SELECT * FROM commission_settlements WHERE tenant_id=:tenant AND id=:id"),
                           {"tenant": tenant_id, "id": identity})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Quitação não encontrada nesta empresa")
    allocations = (await db.execute(text("""
        SELECT a.advance_entry_id,a.amount_cents,e.description,e.occurred_at
        FROM commission_advance_allocations a JOIN financial_entries e ON e.tenant_id=a.tenant_id AND e.id=a.advance_entry_id
        WHERE a.tenant_id=:tenant AND a.settlement_id=:id ORDER BY e.occurred_at,e.id
    """), {"tenant": tenant_id, "id": identity})).mappings().all()
    return {**data.clean_row(row), "allocations": [dict(item) for item in allocations], "transfers_enabled": False}


async def create_settlement(db, tenant_id, payload, operator):
    await acquire_professional_lock(db, tenant_id, payload.professional_id)
    values = payload.model_dump(exclude={"idempotency_key"})
    identity, replay = await data.request_identity(db, tenant_id, "commission_settlement", payload.idempotency_key, values)
    if replay:
        return await get_settlement(db, tenant_id, identity)
    preview = await settlement_preview(db, tenant_id, payload.professional_id)
    if not secrets.compare_digest(preview["snapshot_hash"], payload.expected_snapshot):
        raise HTTPException(409, "O saldo mudou desde a conferência. Atualize a prévia antes de registrar a quitação")
    summary = preview["summary"]
    if summary["advance_allocation_cents"] == 0 and summary["payable_cents"] == 0:
        raise HTTPException(409, "Não há comissão disponível para pagar ou descontar de vales")
    payment_id = None
    now = datetime.now(timezone.utc)
    if summary["payable_cents"] > 0:
        # Keep each regular finance entry within its existing safe money bound.
        # Large cumulative settlements must be reconciled by an explicit process.
        if summary["payable_cents"] > 1_000_000_000:
            raise HTTPException(422, "O saldo acumulado excede o limite de um lançamento financeiro")
        payment_id = uuid4()
        await db.execute(text("""
            INSERT INTO financial_entries (id,tenant_id,kind,description,amount_cents,category,payment_method,status,entry_type,occurred_at,professional_id,notes)
            VALUES (:id,:tenant,'expense','Quitação de comissão',:amount,'Comissões',:method,'paid','commission_payment',:occurred,:professional,:notes)
        """), {"id": payment_id, "tenant": tenant_id, "amount": summary["payable_cents"], "method": payload.payment_method,
               "occurred": payload.occurred_at or now, "professional": payload.professional_id, "notes": payload.notes})
        await audit(db, tenant_id, "management_created", "financial_entries", payment_id)
    await db.execute(text("""
        INSERT INTO commission_settlements (id,tenant_id,professional_id,professional_name,earned_cents,payments_before_cents,advances_cents,
            allocated_before_cents,allocated_cents,payment_cents,payment_entry_id,snapshot_hash,snapshot,notes,operator,settled_at)
        VALUES (:id,:tenant,:professional,:name,:earned,:paid,:advances,:allocated_before,:allocated,:payment,:payment_id,:hash,CAST(:snapshot AS jsonb),:notes,:operator,:now)
    """), {"id": identity, "tenant": tenant_id, "professional": payload.professional_id, "name": preview["professional"]["name"],
           "earned": summary["earned_cents"], "paid": summary["payments_cents"], "advances": summary["advances_cents"],
           "allocated_before": summary["allocated_advances_cents"], "allocated": summary["advance_allocation_cents"],
           "payment": summary["payable_cents"], "payment_id": payment_id, "hash": preview["snapshot_hash"],
           "snapshot": json.dumps(jsonable_encoder(preview["_snapshot"]), sort_keys=True), "notes": payload.notes, "operator": operator, "now": now})
    for advance in preview["advances"]:
        if advance["allocation_cents"] > 0:
            await db.execute(text("""
                INSERT INTO commission_advance_allocations (tenant_id,settlement_id,advance_entry_id,amount_cents)
                VALUES (:tenant,:settlement,:entry,:amount)
            """), {"tenant": tenant_id, "settlement": identity, "entry": advance["id"], "amount": advance["allocation_cents"]})
    await audit(db, tenant_id, "commission_settled", "commission_settlements", identity)
    return await get_settlement(db, tenant_id, identity)


async def list_settlements(db, tenant_id, professional_id=None, limit=50, offset=0):
    where = "tenant_id=:tenant" + (" AND professional_id=:professional" if professional_id else "")
    params = {"tenant": tenant_id, "professional": professional_id, "limit": limit, "offset": offset}
    total = (await db.execute(text("SELECT count(*) FROM commission_settlements WHERE " + where), params)).scalar_one()
    rows = (await db.execute(text("""
        SELECT id,professional_id,professional_name,earned_cents,payments_before_cents,advances_cents,allocated_before_cents,
            allocated_cents,payment_cents,payment_entry_id,notes,operator,settled_at FROM commission_settlements WHERE
    """ + where + " ORDER BY settled_at DESC,id LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


async def list_advances(db, tenant_id, professional_id=None, status="all", start_date=None, end_date=None, limit=50, offset=0):
    period, lower, upper = await data.period_for(db, tenant_id, start_date, end_date)
    scope = " AND e.professional_id=:professional" if professional_id else ""
    query = """
        WITH advance_values AS (
            SELECT e.*,p.name AS professional_name,COALESCE(sum(a.amount_cents),0)::bigint AS allocated_cents,
                (e.amount_cents-COALESCE(sum(a.amount_cents),0))::bigint AS pending_cents
            FROM financial_entries e JOIN professionals p ON p.tenant_id=e.tenant_id AND p.id=e.professional_id
            LEFT JOIN commission_advance_allocations a ON a.tenant_id=e.tenant_id AND a.advance_entry_id=e.id
            WHERE e.tenant_id=:tenant AND e.kind='expense' AND e.status='paid' AND e.entry_type='advance'
                AND e.occurred_at>=:lower AND e.occurred_at<:upper
    """ + scope + """ GROUP BY e.id,p.name
        ), classified AS (SELECT *,CASE WHEN pending_cents=0 THEN 'settled' WHEN allocated_cents>0 THEN 'partial' ELSE 'open' END AS settlement_status
            FROM advance_values)
    """
    where = " WHERE settlement_status=:status" if status != "all" else ""
    params = {"tenant": tenant_id, "professional": professional_id, "lower": lower, "upper": upper, "status": status, "limit": limit, "offset": offset}
    total = (await db.execute(text(query + " SELECT count(*) FROM classified" + where), params)).scalar_one()
    rows = (await db.execute(text(query + " SELECT * FROM classified" + where + " ORDER BY occurred_at DESC,id LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return {"items": [data.clean_row(row) for row in rows], "total": total, "limit": limit, "offset": offset, "period": period}
