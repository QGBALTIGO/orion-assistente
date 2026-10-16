"""Carga sintética idempotente, exclusiva de homologação.

Sem clientes, telefones reais, cobranças ou envios Meta. Só roda com
ORION_SEED_DEMO=1 e depois precisa ser removida do pre-deploy.
"""
import asyncio
import os
from datetime import time
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import text

from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction


def identity(label: str) -> UUID:
    return uuid5(NAMESPACE_DNS, "orion-piloto-sintetico-2026:" + label)


TENANT = identity("tenant")
PROFESSIONALS = [(identity("pro-alex"), "Alex — Demonstração"),
                 (identity("pro-bruno"), "Bruno — Demonstração")]
SERVICES = [(identity("corte"), "Corte", 40, 3500, 5, 5),
            (identity("barba"), "Barba", 20, 2000, 0, 5),
            (identity("acabamento"), "Acabamento", 15, 1500, 0, 0)]


async def seed() -> None:
    if os.getenv("ORION_SEED_DEMO") != "1":
        raise RuntimeError("Seeding bloqueado: ORION_SEED_DEMO não está habilitado")

    async with support_session_factory()() as session:
        async with session.begin():
            await session.execute(text("""
                INSERT INTO tenants (id, name, slug, timezone)
                VALUES (:id, :name, 'orion-demo', 'America/Campo_Grande')
                ON CONFLICT (id) DO NOTHING
            """), {"id": TENANT, "name": "Barbearia Orion — DEMONSTRAÇÃO FICTÍCIA"})

    async with get_session_factory()() as session:
        async with tenant_transaction(session, TENANT):
            for pid, name in PROFESSIONALS:
                await session.execute(text("""
                    INSERT INTO professionals (id, tenant_id, name)
                    VALUES (:id, :tenant, :name)
                    ON CONFLICT (id) DO NOTHING
                """), {"id": pid, "tenant": TENANT, "name": name})
            for sid, name, minutes, price, prep, cleanup in SERVICES:
                await session.execute(text("""
                    INSERT INTO services
                    (id,tenant_id,name,duration_min,price_cents,prep_min,cleanup_min)
                    VALUES (:id,:tenant,:name,:duration,:price,:prep,:cleanup)
                    ON CONFLICT (id) DO NOTHING
                """), {"id":sid, "tenant":TENANT, "name":name,
                       "duration":minutes, "price":price, "prep":prep,"cleanup":cleanup})
            for pid, _ in PROFESSIONALS:
                for sid, *_ in SERVICES:
                    await session.execute(text("""
                        INSERT INTO professional_services
                            (tenant_id,professional_id,service_id)
                        VALUES (:tenant,:pro,:service)
                        ON CONFLICT (tenant_id, professional_id, service_id) DO NOTHING
                    """), {"tenant":TENANT,"pro":pid,"service":sid})
                for weekday in range(6):
                    finish = time(13, 0) if weekday == 5 else time(18, 0)
                    await session.execute(text("""
                        INSERT INTO working_hours
                            (tenant_id,professional_id,weekday,start_time,end_time)
                        VALUES (:tenant,:pro,:weekday,'09:00',CAST(:finish AS time))
                        ON CONFLICT (tenant_id,professional_id,weekday) DO NOTHING
                    """), {"tenant":TENANT,"pro":pid,
                           "weekday":weekday,"finish":finish})
    print(f"Barbearia sintética de homologação pronta: tenant_id={TENANT}")


if __name__ == "__main__":
    asyncio.run(seed())
