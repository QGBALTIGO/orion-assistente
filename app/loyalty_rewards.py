"""Loyalty credits and coupons share the completion transaction and customer lock."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import secrets
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import audit
from app import management_models as data


def points_for_amount(rule, amount_cents):
    """Whole steps earn whole points; cash and visit components are cumulative."""
    points = rule["points_per_visit"] if rule["earning_mode"] in ("visit", "both") else 0
    if rule["earning_mode"] in ("spend", "both"):
        points += amount_cents // rule["spend_step_cents"] * rule["points_per_step"]
    return int(points)


async def configuration_lock(db, tenant_id):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                     {"scope": "loyalty-config:" + str(tenant_id)})


async def validate_reward_service(db, tenant_id, values):
    identity = values.get("reward_service_id")
    if identity is not None:
        found = (await db.execute(text("SELECT id FROM services WHERE tenant_id=:tenant AND id=:id AND catalog_kind<>'variant'"),
                                  {"tenant": tenant_id, "id": identity})).scalar_one_or_none()
        if found is None:
            raise HTTPException(404, "O serviço da recompensa não pertence ao catálogo desta empresa")


async def deactivate_other_rules(db, tenant_id, identity):
    rows = (await db.execute(text("""
        UPDATE loyalty_rules SET active=false,version=version+1,updated_at=now()
        WHERE tenant_id=:tenant AND id<>:id AND active RETURNING id
    """), {"tenant": tenant_id, "id": identity})).scalars().all()
    for rule_id in rows:
        await audit(db, tenant_id, "loyalty_rule_deactivated", "loyalty_rules", rule_id)


async def create_rule(db, tenant_id, values, key):
    data.checked_fields("loyalty_rules", values)
    await validate_reward_service(db, tenant_id, values)
    await configuration_lock(db, tenant_id)
    identity, replay = await data.request_identity(db, tenant_id, "loyalty_rules", key, values)
    if replay:
        return await data.get_row(db, tenant_id, "loyalty_rules", identity)
    if values["active"]:
        await deactivate_other_rules(db, tenant_id, identity)
    fields = list(values)
    row = (await db.execute(text("INSERT INTO loyalty_rules (tenant_id,id," + ",".join(fields) + ") VALUES (:tenant,:id," +
        ",".join(":" + name for name in fields) + ") RETURNING *"), {"tenant": tenant_id, "id": identity, **values})).mappings().one()
    await audit(db, tenant_id, "management_created", "loyalty_rules", identity)
    return dict(row)


async def update_rule(db, tenant_id, identity, expected_version, changes, current):
    await validate_reward_service(db, tenant_id, {**current, **changes})
    await configuration_lock(db, tenant_id)
    if changes.get("active") is True:
        await deactivate_other_rules(db, tenant_id, identity)
    # A stale expected_version raises inside this same transaction, rolling back
    # other deactivations as well; activation is never partially applied.
    return await data.update_row(db, tenant_id, "loyalty_rules", identity, expected_version, changes)


async def lock_customer(db, tenant_id, customer_id):
    return await data.get_row(db, tenant_id, "customers", customer_id, lock=True)


async def active_rule(db, tenant_id):
    row = (await db.execute(text("SELECT * FROM loyalty_rules WHERE tenant_id=:tenant AND active FOR SHARE"),
                           {"tenant": tenant_id})).mappings().one_or_none()
    return dict(row) if row else None


async def balance_for(db, tenant_id, customer_id):
    return int((await db.execute(text("SELECT COALESCE(sum(points_delta),0) FROM loyalty_transactions WHERE tenant_id=:tenant AND customer_id=:customer"),
        {"tenant": tenant_id, "customer": customer_id})).scalar_one())


async def issue_reward(db, tenant_id, customer_id, rule, now, *, coupon_id=None):
    """Caller already holds the customer row lock. Issue exactly one reward."""
    if rule["reward_type"] == "manual":
        raise HTTPException(409, "Configure um cupom percentual, de valor fixo ou de serviço")
    balance = await balance_for(db, tenant_id, customer_id)
    if balance < rule["reward_points"]:
        raise HTTPException(409, "O cliente ainda não atingiu os pontos para esta recompensa")
    identity = coupon_id or uuid4()
    values = {"id": identity, "tenant": tenant_id, "customer": customer_id, "rule": rule["id"],
              "rule_version": rule["version"], "code": "OR-" + secrets.token_hex(8).upper(),
              "kind": rule["reward_type"], "percent": rule["reward_percent"] if rule["reward_type"] == "percent" else None,
              "amount": rule["reward_value_cents"] if rule["reward_type"] == "fixed" else None,
              "service": rule["reward_service_id"] if rule["reward_type"] == "free_service" else None,
              "description": rule["reward_description"], "cost": rule["reward_points"],
              "now": now, "expires": now + timedelta(days=rule["reward_valid_days"])}
    coupon = (await db.execute(text("""
        INSERT INTO loyalty_coupons (id,tenant_id,customer_id,rule_id,rule_version,code,reward_type,reward_percent,
            reward_value_cents,reward_service_id,description,points_spent,issued_at,expires_at)
        VALUES (:id,:tenant,:customer,:rule,:rule_version,:code,:kind,:percent,:amount,:service,:description,:cost,:now,:expires)
        RETURNING *
    """), values)).mappings().one()
    await db.execute(text("""
        INSERT INTO loyalty_transactions (tenant_id,customer_id,rule_id,points_delta,reason,kind,coupon_id,rule_snapshot)
        VALUES (:tenant,:customer,:rule,:delta,:reason,'reward',:coupon,CAST(:snapshot AS jsonb))
    """), {"tenant": tenant_id, "customer": customer_id, "rule": rule["id"], "delta": -rule["reward_points"],
           "reason": "Emissão do cupom " + values["code"], "coupon": identity,
           "snapshot": json.dumps(jsonable_encoder(rule), sort_keys=True)})
    await audit(db, tenant_id, "loyalty_coupon_issued", "loyalty_coupons", identity)
    return data.clean_row(coupon)


async def award_completion(db, tenant_id, appointment, now):
    customer = await lock_customer(db, tenant_id, appointment["customer_id"])
    if customer.get("deleted_at") is not None or not customer["active"]:
        return {"points_earned": 0, "coupon": None}
    rule = await active_rule(db, tenant_id)
    if rule is None:
        return {"points_earned": 0, "coupon": None}
    points = points_for_amount(rule, appointment["total_cents"])
    if points == 0:
        return {"points_earned": 0, "coupon": None}
    row = (await db.execute(text("""
        INSERT INTO loyalty_transactions (tenant_id,customer_id,rule_id,appointment_id,points_delta,reason,kind,rule_snapshot,basis_amount_cents)
        VALUES (:tenant,:customer,:rule,:appointment,:points,'Atendimento concluído','earned',CAST(:snapshot AS jsonb),:amount)
        ON CONFLICT (tenant_id,appointment_id) WHERE kind='earned' DO NOTHING RETURNING id
    """), {"tenant": tenant_id, "customer": appointment["customer_id"], "rule": rule["id"], "appointment": appointment["id"],
           "points": points, "snapshot": json.dumps(jsonable_encoder(rule), sort_keys=True), "amount": appointment["total_cents"]})).scalar_one_or_none()
    if row is None:
        return {"points_earned": 0, "coupon": None}
    await audit(db, tenant_id, "loyalty_points_earned", "loyalty_transactions", row)
    balance = await balance_for(db, tenant_id, appointment["customer_id"])
    coupon = None
    if rule["reward_type"] != "manual" and balance >= rule["reward_points"]:
        coupon = await issue_reward(db, tenant_id, appointment["customer_id"], rule, now)
        balance -= rule["reward_points"]
    return {"points_earned": points, "points_balance": balance, "coupon": coupon}


async def completion_discount(db, tenant_id, appointment, coupon_id, now, *, gross_cents=None):
    if coupon_id is None:
        return 0
    customer = await lock_customer(db, tenant_id, appointment["customer_id"])
    if customer.get("deleted_at") is not None:
        raise HTTPException(409, "Este cliente foi removido do diretório")
    coupon = (await db.execute(text("SELECT * FROM loyalty_coupons WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
                              {"tenant": tenant_id, "id": coupon_id})).mappings().one_or_none()
    if coupon is None:
        raise HTTPException(404, "Cupom não encontrado nesta empresa")
    if coupon["customer_id"] != appointment["customer_id"]:
        raise HTTPException(422, "O cupom pertence a outro cliente")
    if coupon["status"] != "available" or coupon["expires_at"] <= now:
        raise HTTPException(409, "Este cupom já foi utilizado ou expirou")
    if appointment["discount_cents"] != 0:
        raise HTTPException(409, "O atendimento já possui um desconto")
    gross = appointment["total_cents"] if gross_cents is None else gross_cents
    if coupon["reward_type"] == "percent":
        discount = int((Decimal(gross) * coupon["reward_percent"] / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    elif coupon["reward_type"] == "fixed":
        discount = min(gross, coupon["reward_value_cents"])
    else:
        value = (await db.execute(text("""
            SELECT sum(price_snapshot_cents) FROM appointment_items
            WHERE tenant_id=:tenant AND appointment_id=:appointment AND service_id=:service
        """), {"tenant": tenant_id, "appointment": appointment["id"], "service": coupon["reward_service_id"]})).scalar_one()
        if value is None:
            raise HTTPException(422, "O atendimento não inclui o serviço gratuito deste cupom")
        discount = min(gross, int(value))
    await db.execute(text("""
        UPDATE loyalty_coupons SET status='redeemed',redeemed_at=:now,redeemed_appointment_id=:appointment,
            redemption_discount_cents=:discount,version=version+1 WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": tenant_id, "id": coupon_id, "appointment": appointment["id"], "discount": discount, "now": now})
    await audit(db, tenant_id, "loyalty_coupon_redeemed", "loyalty_coupons", coupon_id)
    return discount


async def issue_manually(db, tenant_id, customer_id, key):
    customer = await lock_customer(db, tenant_id, customer_id)
    if customer.get("deleted_at") is not None:
        raise HTTPException(404, "Cliente não encontrado no diretório")
    identity, replay = await data.request_identity(db, tenant_id, "loyalty_coupon_issue", key, {"customer_id": customer_id})
    if replay:
        row = (await db.execute(text("SELECT * FROM loyalty_coupons WHERE tenant_id=:tenant AND id=:id"),
                               {"tenant": tenant_id, "id": identity})).mappings().one()
        return data.clean_row(row)
    rule = await active_rule(db, tenant_id)
    if rule is None:
        raise HTTPException(409, "Ative e configure um programa de fidelidade")
    return await issue_reward(db, tenant_id, customer_id, rule, datetime.now(timezone.utc), coupon_id=identity)


COUPON_STATUS = "CASE WHEN l.status='available' AND l.expires_at<=now() THEN 'expired' ELSE l.status END"


async def list_coupons(db, tenant_id, customer_id=None, status="all", limit=50, offset=0):
    clauses = ["l.tenant_id=:tenant"]
    params = {"tenant": tenant_id, "limit": limit, "offset": offset}
    if customer_id:
        clauses.append("l.customer_id=:customer")
        params["customer"] = customer_id
    if status != "all":
        clauses.append("(" + COUPON_STATUS + ")=:status")
        params["status"] = status
    where = " AND ".join(clauses)
    total = (await db.execute(text("SELECT count(*) FROM loyalty_coupons l WHERE " + where), params)).scalar_one()
    rows = (await db.execute(text("""
        SELECT l.*,c.name AS customer_name,r.name AS rule_name,""" + COUPON_STATUS + """ AS effective_status
        FROM loyalty_coupons l JOIN customers c ON c.tenant_id=l.tenant_id AND c.id=l.customer_id
        JOIN loyalty_rules r ON r.tenant_id=l.tenant_id AND r.id=l.rule_id
        WHERE """ + where + " ORDER BY l.issued_at DESC,l.id LIMIT :limit OFFSET :offset"), params)).mappings().all()
    items = []
    for row in rows:
        item = data.clean_row(row)
        item["status"] = item.pop("effective_status")
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def ranking(db, tenant_id, near_reward=False, limit=50, offset=0):
    rule = (await db.execute(text("SELECT id,reward_points,reward_description FROM loyalty_rules WHERE tenant_id=:tenant AND active"),
                            {"tenant": tenant_id})).mappings().one_or_none()
    if near_reward and rule is None:
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "rule": None}
    threshold = rule["reward_points"] if rule else None
    query = """
        WITH balances AS (SELECT c.id,c.name,c.whatsapp_e164 AS phone,
            COALESCE(sum(l.points_delta),0)::bigint AS points,
            COALESCE(sum(l.points_delta) FILTER (WHERE l.points_delta>0),0)::bigint AS points_earned
            FROM customers c JOIN loyalty_transactions l ON l.tenant_id=c.tenant_id AND l.customer_id=c.id
            WHERE c.tenant_id=:tenant AND c.active AND c.deleted_at IS NULL
            GROUP BY c.id,c.name,c.whatsapp_e164)
    """
    where = " WHERE points*10>=CAST(:threshold AS bigint)*7" if near_reward else ""
    params = {"tenant": tenant_id, "threshold": threshold, "limit": limit, "offset": offset}
    total = (await db.execute(text(query + " SELECT count(*) FROM balances" + where), params)).scalar_one()
    rows = (await db.execute(text(query + " SELECT * FROM balances" + where + " ORDER BY points DESC,points_earned DESC,name,id LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return {"items": [{**dict(row), "progress_percent": round(int(row["points"]) * 100 / threshold, 2) if threshold else None,
                       "points_to_reward": max(0, threshold - int(row["points"])) if threshold else None} for row in rows],
            "total": total, "limit": limit, "offset": offset, "rule": dict(rule) if rule else None}
