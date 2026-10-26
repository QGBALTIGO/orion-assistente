"""Propagate minimum ('a partir de') pricing into managed combos.

Revision ID: 0034_variable_combo_pricing
Revises: 0033_professional_commissions

A combo containing any variable-priced component is an estimate, NOT a
fixed-price guarantee. Its configured value remains the minimum; bookings
snapshot it and require manual confirmation of the agreed final price.
"""
from alembic import op

revision = "0034_variable_combo_pricing"
down_revision = "0033_professional_commissions"
branch_labels = None
depends_on = None


def upgrade():
    # CREATE OR REPLACE retains the existing function's ownership/EXECUTE grants.
    op.execute("""CREATE OR REPLACE FUNCTION public.refresh_service_package(scope uuid, package uuid) RETURNS void
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE p public.service_packages%ROWTYPE; n integer; dur integer; prep integer; cleanup integer;
                price bigint; valid boolean; variable_source boolean;
        BEGIN
          SELECT * INTO p FROM public.service_packages WHERE tenant_id=scope AND id=package FOR UPDATE;
          IF NOT FOUND THEN RETURN; END IF;
          SELECT count(*),COALESCE(sum(CASE WHEN i.add_duration THEN s.duration_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.prep_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.cleanup_min ELSE 0 END),0),
                 COALESCE(sum(s.price_cents),0),COALESCE(bool_and(s.active),false),
                 COALESCE(bool_or(s.price_mode='from'),false)
            INTO n,dur,prep,cleanup,price,valid,variable_source
            FROM public.service_package_items i JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE i.tenant_id=scope AND i.package_id=package;
          valid:=valid AND n BETWEEN 2 AND 6 AND dur BETWEEN 1 AND 1440 AND prep<=240 AND cleanup<=240
                 AND price<=100000000;
          IF p.kind='combo' THEN
            valid:=valid AND p.price_cents<=price;
            -- A combo price is its minimum whenever one of its sources varies.
            -- A legacy zero-priced combo that becomes variable inherits the
            -- source minimum rather than advertising an artificial R$ 0.00.
            price:=CASE WHEN variable_source AND p.price_cents=0 THEN price
                        ELSE p.price_cents END;
          ELSE
            valid:=valid AND EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope
                AND i.package_id=package AND i.service_id=p.base_service_id AND i.add_duration)
              AND NOT EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope AND i.package_id=package
                AND i.service_id<>p.base_service_id AND NOT EXISTS (SELECT 1 FROM public.service_addons a
                  WHERE a.tenant_id=scope AND a.base_service_id=p.base_service_id AND a.addon_service_id=i.service_id
                    AND a.add_duration=i.add_duration));
          END IF;
          UPDATE public.services SET name=p.name,description=p.description,price_cents=LEAST(100000000,price)::integer,
              price_mode=CASE WHEN variable_source THEN 'from' ELSE 'fixed' END,
              duration_min=GREATEST(1,LEAST(1440,dur)),prep_min=LEAST(240,prep),cleanup_min=LEAST(240,cleanup),
              active=p.active AND valid,category=CASE WHEN p.kind='combo' THEN 'Combos' ELSE NULL END,
              color=p.color,image_url=p.image_url,sort_order=p.sort_order
            WHERE tenant_id=scope AND id=p.synthetic_service_id;
          DELETE FROM public.professional_services WHERE tenant_id=scope AND service_id=p.synthetic_service_id;
          IF valid AND p.active THEN
            INSERT INTO public.professional_services (tenant_id,professional_id,service_id)
              SELECT scope,pr.id,p.synthetic_service_id FROM public.professionals pr
              WHERE pr.tenant_id=scope AND NOT EXISTS (
                SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope AND i.package_id=package
                  AND NOT EXISTS (SELECT 1 FROM public.professional_services ps WHERE ps.tenant_id=scope
                    AND ps.professional_id=pr.id AND ps.service_id=i.service_id));
          END IF;
        END $$""")

    # Re-enable/reclassify any existing derived combo that was disabled by
    # 0032 solely for including 'from'. Cascade to any dependent variants.
    op.execute("""
        DO $$
        DECLARE item record;
        BEGIN
          FOR item IN
            SELECT p.tenant_id,p.id
            FROM public.service_packages p
            WHERE p.kind='combo'
              AND EXISTS (
                SELECT 1 FROM public.service_package_items i
                JOIN public.services s
                  ON s.tenant_id=i.tenant_id AND s.id=i.service_id
                WHERE i.tenant_id=p.tenant_id AND i.package_id=p.id
                  AND s.price_mode='from'
              )
            ORDER BY p.tenant_id,p.id
          LOOP
            PERFORM public.refresh_service_package(item.tenant_id,item.id);
          END LOOP;
        END $$;
    """)


def downgrade():
    raise RuntimeError(
        "Variable-priced combo bookings may rely on existing snapshots. "
        "Use an explicitly reviewed forward migration."
    )
