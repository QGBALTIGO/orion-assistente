"""Scoped professional commission configuration and immutable completion calculations.

Money is always integer BRL cents. Catalog editing and completion both acquire
the booking advisory lock for the professional to serialize rule changes.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import text

from app.catalog_api import audit
from app.catalog_schemas import Professional, StrictModel
from app.booking_service import acquire_professional_lock
from app.brazilian_fields import reject_boolean_money


class CommissionRuleInput(StrictModel):
    service_id: UUID
    commission_kind: Literal["percent", "fixed"]
    commission_percent: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    commission_fixed_cents: int | None = Field(default=None, ge=0, le=100_000_000)

    _fixed = field_validator("commission_fixed_cents", mode="before")(reject_boolean_money)

    @model_validator(mode="after")
    def valid_rule(self):
        if self.commission_kind == "percent":
            if self.commission_percent is None or self.commission_fixed_cents is not None:
                raise ValueError("Informe apenas a porcentagem desta comissão")
        elif self.commission_fixed_cents is None or self.commission_percent is not None:
            raise ValueError("Informe apenas o valor fixo desta comissão")
        return self


class ProfessionalStatusChange(StrictModel):
    """Soft archive/reactivation: financial and appointment links are retained."""
    active: bool
    expected_version: int = Field(ge=1)


class ProfessionalConfiguration(Professional):
    service_ids: list[UUID] = Field(default_factory=list, max_length=200)
    commission_rules: list[CommissionRuleInput] = Field(default_factory=list, max_length=200)
    expected_version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$"
    )

    @model_validator(mode="after")
    def consistent_rules(self):
        selected = self.service_ids
        rules = [rule.service_id for rule in self.commission_rules]
        if len(set(selected)) != len(selected) or len(set(rules)) != len(rules):
            raise ValueError("Cada serviço deve ser informado uma única vez")
        if not set(rules).issubset(selected):
            raise ValueError("Uma comissão foi informada para um serviço não selecionado")
        if self.commission_mode == "per_service" and set(rules) != set(selected):
            raise ValueError("Configure a comissão de todos os serviços selecionados")
        return self


def currency_round(amount):
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def proportional_split(total_cents, weights):
    """Split cents exactly, by largest remainder, with stable input-order ties."""
    if not isinstance(total_cents, int) or total_cents < 0:
        raise ValueError("Montante inválido")
    values = [int(weight) for weight in weights]
    if any(weight < 0 for weight in values):
        raise ValueError("Valores dos itens inválidos")
    if not values:
        return []
    weight_total = sum(values)
    if not weight_total:
        return [0] * (len(values) - 1) + [total_cents]
    shares = [divmod(total_cents * weight, weight_total) for weight in values]
    amounts = [q for q, _ in shares]
    missing = total_cents - sum(amounts)
    order = sorted(range(len(values)), key=lambda n: (-shares[n][1], n))
    for index in order[:missing]:
        amounts[index] += 1
    return amounts


def calculate_commission(net_cents, items, mode, default_percent, *, gross_cents=None):
    """Pure calculator used by completions and finance forecasts.

    The percentage model preserves the historic whole-appointment rounding.
    Per-service rates apply to apportioned net item values (after discounts).
    Fixed commissions are earned per selected item independent of discounts.
    """
    if mode not in {"percent", "per_service"} or not 0 <= int(net_cents) <= 1_000_000_000:
        raise ValueError("Configuração de comissão inválida")
    if not items:
        raise ValueError("O agendamento não contém serviços")
    weights = [int(item["price_snapshot_cents"]) for item in items]
    gross = int(net_cents if gross_cents is None else gross_cents)
    if gross < int(net_cents) or gross > 1_000_000_000:
        raise ValueError("Valor bruto incompatível com o valor líquido")
    quoted = sum(weights)
    variable = [index for index, item in enumerate(items)
                if item.get("price_mode_snapshot") == "from"]
    if variable and gross >= quoted:
        # A variable service absorbs the extra charged at completion.
        # Fixed-price services retain their original quoted value.
        extra = proportional_split(gross-quoted, [weights[index] for index in variable])
        gross_bases = list(weights)
        for index, value in zip(variable, extra):
            gross_bases[index] += value
    else:
        gross_bases = proportional_split(gross, weights)
    bases = proportional_split(int(net_cents), gross_bases)
    default_rate = Decimal(str(default_percent))
    if not Decimal("0") <= default_rate <= Decimal("100"):
        raise ValueError("Taxa padrão inválida")
    result = []
    if mode == "percent":
        due = currency_round(Decimal(net_cents) * default_rate / 100)
        pays = proportional_split(due, bases)
        for item, base, value in zip(items, bases, pays):
            result.append({
                "id": item["id"], "base": base, "value": value,
                "kind": "percent", "source": "default",
                "percent": default_rate, "fixed": None,
            })
    else:
        for item, base in zip(items, bases):
            kind = item.get("commission_kind") or "percent"
            source = "rule" if item.get("commission_kind") else "default"
            rate = Decimal(str(item["rate_percent"])) if source == "rule" and kind == "percent" else default_rate
            fixed = int(item["commission_fixed_cents"]) if source == "rule" and kind == "fixed" else None
            value = fixed if kind == "fixed" else currency_round(Decimal(base) * rate / 100)
            result.append({
                "id": item["id"], "base": base, "value": value,
                "kind": kind, "source": source,
                "percent": rate if kind == "percent" else None, "fixed": fixed,
            })
    due = sum(int(item["value"]) for item in result)
    if due > 1_000_000_000:
        raise HTTPException(422, "Comissões deste atendimento excedem o limite permitido")
    return {"commission_cents": due, "items": result,
            "mode": mode, "percent": default_rate if mode == "percent" else None}


async def commission_on_completion(db, tenant_id, professional_id, appointment_id, net_cents, gross_cents=None):
    """Call only with the professional booking advisory lock already held."""
    profile = (await db.execute(text("""
        SELECT commission_mode,commission_percent FROM professionals
        WHERE tenant_id=:tenant AND id=:professional FOR SHARE
    """), {"tenant": tenant_id, "professional": professional_id})).mappings().one_or_none()
    if profile is None:
        raise HTTPException(404, "Profissional não encontrado")
    items = (await db.execute(text("""
        SELECT i.id,i.item_order,i.price_snapshot_cents,i.price_mode_snapshot,
            r.commission_kind,r.commission_percent AS rate_percent,r.commission_fixed_cents
        FROM appointment_items i
        LEFT JOIN professional_commission_rules r
          ON r.tenant_id=i.tenant_id AND r.professional_id=:professional
          AND r.service_id=i.service_id
        WHERE i.tenant_id=:tenant AND i.appointment_id=:appointment
        ORDER BY i.item_order,i.id
    """), {"tenant": tenant_id, "appointment": appointment_id,
           "professional": professional_id})).mappings().all()
    try:
        calculation = calculate_commission(
            int(net_cents), items, profile["commission_mode"], profile["commission_percent"],
            gross_cents=gross_cents,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for item in calculation["items"]:
        await db.execute(text("""
            UPDATE appointment_items SET
                commission_kind_snapshot=:kind,commission_source_snapshot=:source,
                commission_percent_snapshot=:percent,
                commission_fixed_cents_snapshot=:fixed,
                commission_base_cents=:base,commission_cents=:value
            WHERE tenant_id=:tenant AND appointment_id=:appointment AND id=:id
        """), {"tenant": tenant_id, "appointment": appointment_id, **item})
    return calculation


async def list_commission_rules(db, tenant_id):
    records = (await db.execute(text("""
        SELECT professional_id,service_id,commission_kind,commission_percent,
               commission_fixed_cents,updated_at
        FROM professional_commission_rules
        WHERE tenant_id=:tenant ORDER BY professional_id,service_id
    """), {"tenant": tenant_id})).mappings().all()
    return {"items": [dict(item) for item in records]}


async def save_professional_configuration(db, tenant_id, payload, professional_id=None):
    """Create or replace base-service assignments and rates in one transaction."""
    from app.plan_limits import lock_professionals, ensure_professional_activation
    await lock_professionals(db, tenant_id)
    selected = payload.service_ids
    fields = payload.model_dump(exclude={
        "service_ids", "commission_rules", "expected_version", "idempotency_key"
    })
    from app.plan_permissions import require_feature
    commission_fields = {"commission_mode", "commission_percent", "commission_rules"}
    explicit_commissions = bool(commission_fields & payload.model_fields_set)
    if explicit_commissions:
        await require_feature(db, tenant_id, "commissions.advanced", lock="exclusive")
    if professional_id is None:
        if not payload.idempotency_key:
            raise HTTPException(422, "Informe uma chave de operação para o novo profissional")
        from app.management_models import request_identity
        identity_payload = payload.model_dump(exclude={"idempotency_key"})
        # Keep receipts from before the phone field compatible with old callers.
        if "phone_e164" not in payload.model_fields_set:
            identity_payload.pop("phone_e164", None)
        identity, replay = await request_identity(
            db, tenant_id, "professional_configuration", payload.idempotency_key,
            identity_payload,
        )
        if replay:
            previous = (await db.execute(text("""
                SELECT * FROM professionals WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": identity})).mappings().one_or_none()
            if previous is None:
                raise HTTPException(409, "Cadastro anterior não encontrado")
            return dict(previous)
        professional_id = identity
        await ensure_professional_activation(db, tenant_id, fields["active"])
        await acquire_professional_lock(db, tenant_id, professional_id)
        attrs = list(fields)
        query = """INSERT INTO professionals (id,tenant_id,""" + ",".join(attrs) + """)
          VALUES (:id,:tenant,""" + ",".join(":" + attr for attr in attrs) + """)
          RETURNING *"""
        saved = (await db.execute(text(query), {
            "tenant": tenant_id, "id": professional_id, **fields
        })).mappings().one()
    else:
        if payload.expected_version is None:
            raise HTTPException(422, "Atualize os dados antes de editar a comissão")
        # An older/cached form must not erase a phone it never displayed.
        if "phone_e164" not in payload.model_fields_set:
            fields.pop("phone_e164", None)
        # Forms without the advanced section must not replace persisted rates
        # with Pydantic defaults after a downgrade.
        for name in ("commission_mode", "commission_percent"):
            if name not in payload.model_fields_set:
                fields.pop(name, None)
        await ensure_professional_activation(db, tenant_id, fields["active"], professional_id)
        await acquire_professional_lock(db, tenant_id, professional_id)
        assigns = ",".join(key + "=:" + key for key in fields)
        saved = (await db.execute(text("""
            UPDATE professionals SET """ + assigns + """,
                commission_config_version=commission_config_version+1
            WHERE tenant_id=:tenant AND id=:id
              AND commission_config_version=:version RETURNING *
        """), {"tenant": tenant_id, "id": professional_id,
               "version": payload.expected_version, **fields})).mappings().one_or_none()
        if saved is None:
            exists = (await db.execute(text("""
                SELECT 1 FROM professionals WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": professional_id})).scalar_one_or_none()
            raise HTTPException(409 if exists else 404,
                "O profissional foi alterado. Atualize os dados e tente novamente"
                if exists else "Profissional não encontrado")

    # Obtain the professional advisory lock before row-level service locks,
    # matching the order used for ordinary catalog-link mutations.
    params = {"tenant": tenant_id, "ids": selected}
    verified = (await db.execute(text("""
        SELECT id FROM services WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[]))
          AND catalog_kind='base' FOR SHARE
    """), params)).scalars().all()
    if set(verified) != set(selected):
        raise HTTPException(422, "Selecione somente serviços válidos desta empresa")

    old_links = (await db.execute(text("""
        SELECT ps.id,ps.service_id FROM professional_services ps
        JOIN services s ON s.tenant_id=ps.tenant_id AND s.id=ps.service_id
        WHERE ps.tenant_id=:tenant AND ps.professional_id=:professional
          AND s.catalog_kind='base'
    """), {"tenant": tenant_id, "professional": professional_id})).mappings().all()
    old_ids = {link["service_id"] for link in old_links}
    for service_id in selected:
        if service_id not in old_ids:
            await db.execute(text("""
                INSERT INTO professional_services(tenant_id,professional_id,service_id)
                VALUES (:tenant,:professional,:service)
                ON CONFLICT(tenant_id,professional_id,service_id) DO NOTHING
            """), {"tenant": tenant_id, "professional": professional_id, "service": service_id})
    for link in old_links:
        if link["service_id"] not in selected:
            await db.execute(text("""
                DELETE FROM professional_services WHERE tenant_id=:tenant AND id=:id
            """), {"tenant": tenant_id, "id": link["id"]})
    # Even while the default-percentage mode is active, keep configured rates
    # for still-selected services so toggling between modes does not erase work.
    if "commission_rules" in payload.model_fields_set:
        await db.execute(text("""
            DELETE FROM professional_commission_rules
            WHERE tenant_id=:tenant AND professional_id=:professional
              AND service_id<>ALL(CAST(:ids AS uuid[]))
        """), {"tenant": tenant_id, "professional": professional_id, "ids": selected})
    for rule in payload.commission_rules if "commission_rules" in payload.model_fields_set else ():
        await db.execute(text("""
            INSERT INTO professional_commission_rules
              (tenant_id,professional_id,service_id,commission_kind,
               commission_percent,commission_fixed_cents)
            VALUES (:tenant,:professional,:service,:kind,:percent,:fixed)
            ON CONFLICT (tenant_id,professional_id,service_id)
            DO UPDATE SET commission_kind=EXCLUDED.commission_kind,
                commission_percent=EXCLUDED.commission_percent,
                commission_fixed_cents=EXCLUDED.commission_fixed_cents,
                updated_at=now()
        """), {"tenant": tenant_id, "professional": professional_id,
               "service": rule.service_id, "kind": rule.commission_kind,
               "percent": rule.commission_percent, "fixed": rule.commission_fixed_cents})
    await audit(db, tenant_id, "updated" if payload.expected_version else "created",
                "professionals", professional_id)
    return dict(saved)



async def change_professional_status(db, tenant_id, professional_id, payload: ProfessionalStatusChange):
    """Hide or restore a professional without deleting history or commission rules."""
    from app.plan_limits import ensure_professional_activation
    await ensure_professional_activation(db, tenant_id, payload.active, professional_id)
    await acquire_professional_lock(db, tenant_id, professional_id)
    params = {"tenant": tenant_id, "id": professional_id, "version": payload.expected_version}
    current = (await db.execute(text("""
        SELECT id, active, commission_config_version
        FROM professionals
        WHERE tenant_id=:tenant AND id=:id FOR UPDATE
    """), params)).mappings().one_or_none()
    if current is None:
        raise HTTPException(404, "Profissional não encontrado nesta empresa")
    if current["commission_config_version"] != payload.expected_version:
        raise HTTPException(409, "Os dados deste profissional mudaram. Atualize a lista e tente novamente")
    if bool(current["active"]) == payload.active:
        return dict(current)

    if not payload.active:
        # Confirmed bookings and unexpired holds must be reassigned/cancelled
        # before archiving; historical bookings and financials remain untouched.
        pending = (await db.execute(text("""
            SELECT count(*) FROM appointments
            WHERE tenant_id=:tenant AND professional_id=:id
              AND end_at>now()
              AND (status='confirmed'
                   OR (status='hold' AND hold_expires_at>now()))
        """), params)).scalar_one()
        if pending:
            raise HTTPException(
                409,
                f"Este profissional tem {pending} agendamento(s) em aberto. "
                "Reagende ou cancele esses atendimentos antes de removê-lo.",
            )

    saved = (await db.execute(text("""
        UPDATE professionals
        SET active=:active,
            commission_config_version=commission_config_version+1
        WHERE tenant_id=:tenant AND id=:id
          AND commission_config_version=:version
        RETURNING *
    """), {**params, "active": payload.active})).mappings().one()
    await audit(db, tenant_id, "updated", "professionals", professional_id)
    return dict(saved)
