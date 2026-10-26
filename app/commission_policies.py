"""Per-professional, per-service compensation with immutable completion snapshots.

The catalogue price (including an "a partir de" minimum) is not a commission
rate. Only confirmed completed net revenue earns commission. Service policies
are read in the same tenant transaction and professional advisory lock used
by booking completion and settlement; changes never rewrite completed work.
"""
import json
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.booking_service import acquire_professional_lock
from app.catalog_api import audit


def money_parts(components, total):
    """Deterministic largest-remainder split of actual net cents, not quotes.

    Free services still receive a deterministic zero allocation. The resulting
    nonnegative shares always sum to the exact net amount, including discounts.
    """
    if type(total) is not int or total < 0:
        raise ValueError("Nonnegative integer net amount required")
    weights = [int(x["weight_cents"]) for x in components]
    if not weights or any(x < 0 for x in weights):
        raise ValueError("Verified nonnegative component weights required")
    if not any(weights):
        weights = [1 for _ in components]
    denominator = sum(weights)
    result = [total * weight // denominator for weight in weights]
    remaining = total - sum(result)
    order = sorted(range(len(weights)), key=lambda index:
                   (-(total * weights[index] % denominator), index))
    for index in order[:remaining]:
        result[index] += 1
    return result


def _part(service, *, order):
    try:
        identity = str(UUID(str(service["service_id"])))
        amount = service["price_cents"]
        name = str(service["name"])[:120]
        if type(amount) is not int or not 0 <= amount <= 100_000_000:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(409, "O histórico do serviço não permite calcular a comissão. Revise o agendamento.") from exc
    return {"service_id": identity, "name": name,
            "weight_cents": amount, "order": order}


def split_appointment_services(items):
    """Flatten immutable combo/variant snapshots before allocating real income.

    A synthetic service without its historic item composition is never priced as
    an arbitrary stand-alone product. That would silently pay the wrong worker.
    """
    components = []
    for row in items:
        snapshot = row.get("catalog_snapshot")
        kind = row.get("catalog_kind")
        if kind in {"combo", "variant"}:
            if not isinstance(snapshot, dict) or snapshot.get("kind") != kind:
                raise HTTPException(409, "O pacote não possui a composição histórica necessária para calcular a comissão.")
            members = snapshot.get("items")
            if not isinstance(members, list) or not members:
                raise HTTPException(409, "A composição do pacote não está disponível para este atendimento.")
            for member in members:
                if not isinstance(member, dict):
                    raise HTTPException(409, "A composição histórica do pacote é inválida.")
                nested = member.get("components")
                if isinstance(nested, list) and nested:
                    for component in nested:
                        components.append(_part(component, order=len(components)))
                else:
                    components.append(_part(member, order=len(components)))
        else:
            components.append(_part({
                "service_id": row["service_id"],
                "name": row["name_snapshot"],
                "price_cents": row["price_snapshot_cents"],
            }, order=len(components)))
        if len(components) > 100:
            raise HTTPException(409, "O atendimento possui mais serviços do que o limite auditável.")
    if not components:
        raise HTTPException(409, "O atendimento não possui serviços para calcular a comissão.")
    return components


def calculate_service_commissions(items, policies, net_cents):
    """Pure calculation; every component must have an explicit verified policy."""
    components = split_appointment_services(items)
    shares = money_parts(components, net_cents)
    breakdown = []
    for component, net in zip(components, shares):
        policy = policies.get(component["service_id"])
        if not isinstance(policy, dict):
            raise HTTPException(409, "Falta configurar a comissão de um serviço deste profissional.")
        kind = policy.get("kind")
        if kind == "percent":
            try:
                rate = Decimal(str(policy["percent"]))
            except (ValueError, TypeError, KeyError) as exc:
                raise HTTPException(409, "A porcentagem da comissão cadastrada é inválida.") from exc
            if not rate.is_finite() or not Decimal("0") <= rate <= Decimal("100") or rate.as_tuple().exponent < -2:
                raise HTTPException(409, "A porcentagem da comissão cadastrada está fora do limite.")
            earned = int((Decimal(net) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            rate_value = str(rate)
        elif kind == "fixed":
            amount = policy.get("fixed_cents")
            if type(amount) is not int or amount < 0 or amount > 100_000_000:
                raise HTTPException(409, "O valor da comissão cadastrada é inválido.")
            # A fixed commission can never pay more than this service earned
            # after discounts; the UI explains this cap.
            earned = min(amount, net)
            rate_value = amount
        else:
            raise HTTPException(409, "Configure o tipo da comissão deste serviço.")
        breakdown.append({
            "service_id": component["service_id"],
            "service_name": component["name"],
            "service_order": component["order"],
            "kind": kind,
            "rate": rate_value,
            "service_net_cents": net,
            "commission_cents": earned,
        })
    return sum(item["commission_cents"] for item in breakdown), breakdown


async def compute_completion(db, tenant_id, professional_id, appointment_id, net_cents):
    professional = (await db.execute(text("""
        SELECT commission_mode,commission_percent,commission_rules
        FROM professionals WHERE tenant_id=:tenant AND id=:pro FOR SHARE
    """), {"tenant": tenant_id, "pro": professional_id})).mappings().one_or_none()
    if professional is None:
        raise HTTPException(404, "Profissional não encontrado nesta empresa")
    mode = professional["commission_mode"]
    if mode == "percent":
        rate = Decimal(str(professional["commission_percent"]))
        value = int((Decimal(net_cents) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return {"mode": "percent", "percent": rate, "amount": value,
                "breakdown": [{"kind": "percent", "rate": str(rate),
                               "service_net_cents": net_cents, "commission_cents": value}]}
    if mode != "per_service":
        raise HTTPException(409, "O modo de comissão do profissional precisa ser revisado")
    rows = (await db.execute(text("""
        SELECT i.item_order,i.service_id,i.name_snapshot,i.price_snapshot_cents,
               i.catalog_snapshot,s.catalog_kind
        FROM appointment_items i JOIN services s
          ON s.tenant_id=i.tenant_id AND s.id=i.service_id
        WHERE i.tenant_id=:tenant AND i.appointment_id=:appointment
        ORDER BY i.item_order FOR SHARE OF i
    """), {"tenant": tenant_id, "appointment": appointment_id})).mappings().all()
    earned, breakdown = calculate_service_commissions(
        [dict(row) for row in rows], professional["commission_rules"], int(net_cents))
    return {"mode": "per_service", "percent": None, "amount": earned, "breakdown": breakdown}


async def save_plan(db, tenant_id, professional_id, payload):
    """Atomically replace current assignments and update the per-service rules.

    Inactive historic services retain their stored policies so previously
    booked packages can still be closed accurately. Only selected active
    assignments may be used by future bookings.
    """
    await acquire_professional_lock(db, tenant_id, professional_id)
    professional = (await db.execute(text("""
        SELECT id,commission_mode,commission_percent,commission_plan_version,
               commission_rules
        FROM professionals WHERE tenant_id=:tenant AND id=:professional FOR UPDATE
    """), {"tenant": tenant_id, "professional": professional_id})).mappings().one_or_none()
    if professional is None:
        raise HTTPException(404, "Profissional não encontrado nesta empresa")
    if professional["commission_plan_version"] != payload.expected_version:
        raise HTTPException(409, "As regras de comissão foram alteradas. Atualize a ficha do profissional.")
    if payload.mode == "per_service" and not payload.services:
        raise HTTPException(422, "Selecione serviços e configure suas comissões.")
    ids = [rule.service_id for rule in payload.services]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Um mesmo serviço foi incluído mais de uma vez")
    if ids:
        valid = (await db.execute(text("""
            SELECT id FROM services
            WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[]))
              AND catalog_kind='base'
            ORDER BY id FOR SHARE
        """), {"tenant": tenant_id, "ids": ids})).scalars().all()
        if len(valid) != len(ids):
            raise HTTPException(422, "Selecione apenas serviços próprios e válidos deste estabelecimento")
    kept = dict(professional["commission_rules"])
    if payload.mode == "per_service":
        for rule in payload.services:
            kept[str(rule.service_id)] = (
                {"kind": "percent", "percent": str(rule.percent)}
                if rule.kind == "percent"
                else {"kind": "fixed", "fixed_cents": rule.fixed_cents})
    # Generic catalogue APIs do not update this version or mode. One controlled
    # endpoint owns the complete plan, and always uses tenant RLS + advisory lock.
    linked = (await db.execute(text("""
        SELECT ps.id,ps.service_id
        FROM professional_services ps JOIN services s
          ON s.tenant_id=ps.tenant_id AND s.id=ps.service_id
        WHERE ps.tenant_id=:tenant AND ps.professional_id=:professional
          AND s.catalog_kind='base'
        ORDER BY ps.service_id FOR UPDATE OF ps
    """), {"tenant": tenant_id, "professional": professional_id})).mappings().all()
    wanted = set(ids)
    for row in linked:
        if row["service_id"] not in wanted:
            await db.execute(text("""
                DELETE FROM professional_services
                WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": row["id"]})
    current = {row["service_id"] for row in linked}
    for service_id in sorted(wanted-current):
        await db.execute(text("""
            INSERT INTO professional_services(tenant_id,professional_id,service_id)
            VALUES (:tenant,:professional,:service)
            ON CONFLICT(tenant_id,professional_id,service_id) DO NOTHING
        """), {"tenant": tenant_id, "professional": professional_id, "service": service_id})
    saved = (await db.execute(text("""
        UPDATE professionals SET
            commission_mode=:mode,
            commission_rules=CAST(:rules AS jsonb),
            commission_plan_version=commission_plan_version+1
        WHERE tenant_id=:tenant AND id=:professional
          AND commission_plan_version=:expected
        RETURNING commission_mode,commission_rules,commission_plan_version,commission_percent
    """), {"tenant": tenant_id, "professional": professional_id,
           "mode": payload.mode, "rules": json.dumps(kept, ensure_ascii=False),
           "expected": payload.expected_version})).mappings().one_or_none()
    if saved is None:
        raise HTTPException(409, "A configuração de comissão mudou. Recarregue antes de salvar.")
    await audit(db, tenant_id, "professional_commission_updated", "professionals", professional_id)
    return {"professional_id": professional_id, **dict(saved),
            "service_ids": ids}
