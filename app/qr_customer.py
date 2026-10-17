"""Cadastro e histórico de agendamento estritamente escopados ao tenant/PN autenticado.

Somente nome fornecido voluntariamente e reservas são persistidos. Mensagens livres
não são armazenadas neste módulo; o contexto da conversa expira no Redis.
"""
import re
from uuid import UUID

from sqlalchemy import text

from app.database import get_session_factory, tenant_transaction


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
    return name if 2 <= len(name) <= 60 else None


async def customer_name(tenant: UUID, phone: str) -> str | None:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            value = (await s.execute(text("""
                SELECT name FROM customers
                WHERE tenant_id=:tenant AND whatsapp_e164=:phone
            """), {"tenant": tenant, "phone": "+" + phone})).scalar_one_or_none()
    return value if value and value != "Cliente WhatsApp" else None


async def remember_name(tenant: UUID, phone: str, name: str) -> None:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            await s.execute(text("""
                INSERT INTO customers (tenant_id,whatsapp_e164,name)
                VALUES (:tenant,:phone,:name)
                ON CONFLICT (tenant_id,whatsapp_e164)
                DO UPDATE SET name=EXCLUDED.name
            """), {"tenant": tenant, "phone": "+" + phone, "name": name})


async def customer_appointments(tenant: UUID, phone: str, limit: int = 8) -> list[dict]:
    async with get_session_factory()() as s:
        async with tenant_transaction(s, tenant):
            rows = (await s.execute(text("""
                SELECT a.id, a.protocol, a.professional_id, a.start_at,
                       a.total_cents, a.status
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:phone
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
                  AND c.whatsapp_e164=:phone
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
