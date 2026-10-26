"""CI-only real PostgreSQL fixture proving upgrade 0033 -> 0034 re-enables combos.

Called once to seed before upgrade, once to verify and clean up after upgrade.
Never connect to a production URL; requires explicit CI sentinel.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text

TENANT = "71000000-0000-4000-8000-000000000001"
PRO = "71000000-0000-4000-8000-000000000002"
CUT = "71000000-0000-4000-8000-000000000003"
BEARD = "71000000-0000-4000-8000-000000000004"
PACKAGE = "71000000-0000-4000-8000-000000000005"
SYNTH = "71000000-0000-4000-8000-000000000006"


def scoped(conn):
    conn.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": TENANT})


def seed(conn):
    conn.execute(text("""
      INSERT INTO tenants(id,name,slug,timezone)
      VALUES (:tenant,'Migracao sintética CI','migration-combo-ci','America/Sao_Paulo')
    """), {"tenant": TENANT})
    scoped(conn)
    conn.execute(text("""
      INSERT INTO professionals(id,tenant_id,name) VALUES (:id,:tenant,'Barbeiro de teste')
    """), {"id": PRO, "tenant": TENANT})
    for identity, name, price in ((CUT, "Corte", 4000), (BEARD, "Barba", 2000)):
        conn.execute(text("""
          INSERT INTO services (id,tenant_id,name,duration_min,price_cents)
          VALUES (:id,:tenant,:name,40,:price)
        """), {"id": identity, "tenant": TENANT, "name": name, "price": price})
        conn.execute(text("""
          INSERT INTO professional_services(tenant_id,professional_id,service_id)
          VALUES (:tenant,:pro,:service)
        """), {"tenant": TENANT, "pro": PRO, "service": identity})
    conn.execute(text("""
      INSERT INTO services(id,tenant_id,name,duration_min,price_cents,active,catalog_kind)
      VALUES (:id,:tenant,'Combo com valor mínimo',1,0,false,'combo')
    """), {"id": SYNTH, "tenant": TENANT})
    conn.execute(text("""
      INSERT INTO service_packages(id,tenant_id,synthetic_service_id,name,price_cents,active)
      VALUES (:package,:tenant,:synthetic,'Combo com valor mínimo',5200,true)
    """), {"tenant": TENANT, "package": PACKAGE, "synthetic": SYNTH})
    for order, identity in enumerate((CUT, BEARD)):
        conn.execute(text("""
          INSERT INTO service_package_items
            (tenant_id,package_id,service_id,item_order,add_duration)
          VALUES (:tenant,:package,:service,:order,true)
        """), {"tenant": TENANT, "package": PACKAGE, "service": identity, "order": order})
    before = conn.execute(text("""
      SELECT active,price_mode,price_cents FROM services WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": TENANT, "id": SYNTH}).one()
    assert tuple(before) == (True, "fixed", 5200), before
    conn.execute(text("""
      UPDATE services SET price_mode='from' WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": TENANT, "id": CUT})
    disabled = conn.execute(text("""
      SELECT active,price_mode,price_cents FROM services WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": TENANT, "id": SYNTH}).one()
    assert tuple(disabled) == (False, "fixed", 5200), disabled
    print("combo_backfill_fixture_0033_seeded")


def check(conn):
    scoped(conn)
    after = conn.execute(text("""
      SELECT active,price_mode,price_cents FROM services WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": TENANT, "id": SYNTH}).one()
    assert tuple(after) == (True, "from", 5200), after
    service_link = conn.execute(text("""
      SELECT count(*) FROM professional_services
      WHERE tenant_id=:tenant AND professional_id=:pro AND service_id=:service
    """), {"tenant": TENANT, "pro": PRO, "service": SYNTH}).scalar_one()
    assert service_link == 1, service_link
    stored = conn.execute(text("""
      SELECT price_cents,active FROM service_packages WHERE tenant_id=:tenant AND id=:id
    """), {"tenant": TENANT, "id": PACKAGE}).one()
    assert tuple(stored) == (5200, True), stored
    print("combo_backfill_reenabled_and_preserved_minimum")
    conn.execute(text("DELETE FROM service_package_items WHERE tenant_id=:tenant"), {"tenant": TENANT})
    conn.execute(text("DELETE FROM service_packages WHERE tenant_id=:tenant"), {"tenant": TENANT})
    conn.execute(text("DELETE FROM professional_services WHERE tenant_id=:tenant"), {"tenant": TENANT})
    conn.execute(text("DELETE FROM services WHERE tenant_id=:tenant"), {"tenant": TENANT})
    conn.execute(text("DELETE FROM professionals WHERE tenant_id=:tenant"), {"tenant": TENANT})
    conn.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": TENANT})


def main():
    if os.environ.get("ORION_DISPOSABLE_COMBO_CI") != "1":
        raise RuntimeError("Only an explicit disposable CI database is allowed")
    if os.environ.get("ORION_EXPECTED_DATABASE_NAME") != "barber_saas":
        raise RuntimeError("Only the reviewed disposable CI database is allowed")
    url = os.environ["MIGRATION_DATABASE_URL"]
    if "localhost:5432/barber_saas" not in url:
        raise RuntimeError("CI database is not localhost")
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "check"}:
        raise RuntimeError("Expected seed or check")
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            if sys.argv[1] == "seed":
                seed(db)
            else:
                check(db)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
