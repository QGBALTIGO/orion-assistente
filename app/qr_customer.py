"""Cadastro e histórico de agendamento estritamente escopados ao tenant/PN autenticado.

Somente nome fornecido voluntariamente e reservas são persistidos. Mensagens livres
não são armazenadas neste módulo; o contexto da conversa expira no Redis.
"""
import re
from uuid import UUID

from sqlalchemy import text

from app.database import get_session_factory, tenant_transaction
from app.customer_names import useful_name
from app.customer_identity import resolve_customer
from app.qr_behaviors import norm


def third_party_subject(value: str) -> bool:
    """A relationship/beneficiary is not the verified contact's identity."""
    if not isinstance(value, str):
        return False
    text = norm(value)
    relative = r"(?:filh[oa]|espos[oa]|marido|mulher|mae|pai|irma[oa]|net[oa]|sobrinh[oa]|amig[oa]|cliente|paciente)"
    return bool(re.search(r"\b(?:para|pra|pro)\s+(?:(?:o|a)\s+)?(?:meu|minha|nosso|nossa)\s+" + relative + r"\b", text)
        or re.search(r"\b(?:meu|minha|nosso|nossa)\s+" + relative + r"\s+(?:quer|precisa|vai|gostaria|se chama)\b", text)
        or re.search(r"\b(?:sou|sou o|sou a|eu sou o|eu sou a)\s+" + relative + r"\s+(?:de|do|da)\b", text)
        or re.search(r"\b(?:para|pra|pro)\s+(?:(?:o|a)\s+)?(?:ele|ela|outra pessoa|outro cliente|outra cliente)\b", text))


def supplied_name(value: str) -> str | None:
    # O cliente deve declarar explicitamente o nome, nunca inferido pela IA.
    match = re.search(
        r"(?:^|[,!.;\n]\s*)(?:(?:oi+|ol[áa]+|bom dia|boa tarde|boa noite)\s+)?"
        r"(?:meu nome [ée]|me chamo|pode me chamar de|(?:eu\s+)?sou [oa])\s+"
        r"([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ '.-]{1,120})",
        value.strip(), re.IGNORECASE
    )
    if not match:
        return None
    name = re.split(r"[,!?;\n]|(?<=[A-Za-zÀ-ÖØ-öø-ÿ]{2})\.\s+|"
                    r"\s+(?:(?:e\s+)?(?:quero|queria|gostaria|preciso|desejo|vou)|para|porque|que)\b",
                    match.group(1), maxsplit=1, flags=re.IGNORECASE)[0].strip(" .-'")
    name = " ".join(name.split())
    if re.match(r"(?:cliente|barbeiro|dono|responsável|atendente|profissional|administrador)\b", name, re.IGNORECASE):
        return None
    if re.match(r"(?:pai|mãe|mae|filh[oa]|espos[oa]|marido|irm[ãa]o|irm[ãa]|responsável)\s+(?:de|do|da)\b", name, re.IGNORECASE):
        return None
    return name if 2 <= len(name) <= 60 else None


async def customer_name(tenant: UUID, phone: str) -> str | None:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            value = (await s.execute(text("""
                SELECT name FROM customers
                WHERE tenant_id=:tenant AND whatsapp_e164=:phone AND deleted_at IS NULL
            """), {"tenant": tenant, "phone": "+" + phone})).scalar_one_or_none()
    return useful_name(value)


async def remember_name(tenant: UUID, phone: str, name: str) -> None:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            current, created = await resolve_customer(s, tenant, "+" + phone, name, lock=True)
            if created or current["deleted_at"] is not None or current["name"] == name:
                return
            await s.execute(text("""
                UPDATE customers SET name=:name,version=version+1,updated_at=now()
                WHERE tenant_id=:tenant AND id=:id AND deleted_at IS NULL
            """), {"tenant": tenant, "id": current["id"], "name": name})


async def customer_appointments(tenant: UUID, phone: str, limit: int = 8) -> list[dict]:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            rows = (await s.execute(text("""
                SELECT a.id, a.protocol, a.professional_id, a.start_at,
                       a.total_cents, a.status
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:phone AND c.deleted_at IS NULL
                  AND a.status <> 'hold'
                ORDER BY a.start_at DESC LIMIT :limit
            """), {"tenant": tenant, "phone": "+" + phone, "limit": min(limit, 15)})).mappings().all()
    return [dict(r) for r in rows]


async def appointment_services(tenant: UUID, appointment_id: UUID, phone: str):
    """Não permite acessar/reservar um compromisso apenas conhecendo seu UUID."""
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            row = (await s.execute(text("""
                SELECT a.id,a.professional_id,a.start_at,a.status
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND a.id=:id
                  AND c.whatsapp_e164=:phone AND c.deleted_at IS NULL
            """), {"tenant": tenant, "id": appointment_id,
                   "phone": "+" + phone})).mappings().first()
            if not row:
                return None
            services = (await s.execute(text("""
                SELECT service_id FROM appointment_items
                WHERE tenant_id=:tenant AND appointment_id=:id
                ORDER BY item_order
            """), {"tenant": tenant, "id": appointment_id})).scalars().all()
    return {**dict(row), "services": list(services)}


async def customer_last_completed(tenant: UUID, phone: str) -> dict | None:
    """Read the latest recorded completion, never infer completion from a clock.

    Only booking facts are read. Clinical notes, birth dates, documents and free
    conversation text never enter this result or a model prompt.
    """
    if not isinstance(tenant, UUID) or not isinstance(phone, str) or not re.fullmatch(r"[1-9][0-9]{6,14}", phone):
        raise ValueError("Authenticated tenant and phone required")
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = (await db.execute(text("""
                WITH latest AS (
                    SELECT a.id,a.professional_id,a.start_at,a.end_at,a.status,a.updated_at
                    FROM appointments a
                    JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                    WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:phone
                      AND c.deleted_at IS NULL AND a.status='completed'
                    ORDER BY a.end_at DESC,a.start_at DESC,a.id DESC LIMIT 1
                )
                SELECT a.*,p.name AS professional_name,
                       COALESCE(items.services,'[]'::jsonb) AS services
                FROM latest a
                LEFT JOIN professionals p ON p.tenant_id=:tenant AND p.id=a.professional_id
                LEFT JOIN LATERAL (
                    SELECT jsonb_agg(jsonb_build_object(
                        'service_id',i.service_id,'name',i.name_snapshot,
                        'duration_min',i.duration_snapshot_min)
                        ORDER BY i.item_order) AS services
                    FROM appointment_items i
                    WHERE i.tenant_id=:tenant AND i.appointment_id=a.id
                ) items ON true
            """), {"tenant": tenant,"phone": "+" + phone})).mappings().first()
    return dict(row) if row else None
