"""Variable service pricing, booking snapshots and safer completion.

Revision ID: 0032_variable_service_prices
Revises: 0031_auto_complete_appointments

Existing offers and appointments remain fixed unless explicitly changed.
Variable prices are minimum estimates; only a staff-confirmed actual price is
counted as earned revenue. No automatic revenue recognition for unset values.
"""
from alembic import op

revision = "0032_variable_service_prices"
down_revision = "0031_auto_complete_appointments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.services
          ADD COLUMN price_mode varchar(8) NOT NULL DEFAULT 'fixed',
          ADD CONSTRAINT ck_services_price_mode
            CHECK (price_mode IN ('fixed','from') AND
              (price_mode='fixed' OR price_cents>0));

        ALTER TABLE public.appointment_items
          ADD COLUMN price_mode_snapshot varchar(8) NOT NULL DEFAULT 'fixed',
          ADD CONSTRAINT ck_appointment_item_price_mode
            CHECK (price_mode_snapshot IN ('fixed','from'));

        CREATE INDEX ix_appointment_items_variable_price ON public.appointment_items(tenant_id,appointment_id)
          WHERE price_mode_snapshot='from';
    """)
    # The existing package function is retained, with the variable flag
    # propagated only for variants; a fixed-price combo containing a variable
    # source is disabled rather than advertised with an invented fixed total.
    op.execute("""CREATE OR REPLACE FUNCTION public.refresh_service_package(scope uuid, package uuid) RETURNS void
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE p public.service_packages%ROWTYPE; n integer; dur integer; prep integer; cleanup integer;
                price bigint; valid boolean;
        BEGIN
          SELECT * INTO p FROM public.service_packages WHERE tenant_id=scope AND id=package FOR UPDATE;
          IF NOT FOUND THEN RETURN; END IF;
          SELECT count(*),COALESCE(sum(CASE WHEN i.add_duration THEN s.duration_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.prep_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.cleanup_min ELSE 0 END),0),
                 COALESCE(sum(s.price_cents),0),COALESCE(bool_and(s.active),false)
            INTO n,dur,prep,cleanup,price,valid
            FROM public.service_package_items i JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE i.tenant_id=scope AND i.package_id=package;
          valid:=valid AND n BETWEEN 2 AND 6 AND dur BETWEEN 1 AND 1440 AND prep<=240 AND cleanup<=240
                 AND price<=100000000;
          IF p.kind='combo' THEN
            valid:=valid AND p.price_cents<=price
              AND NOT EXISTS (
                SELECT 1 FROM public.service_package_items i
                JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
                WHERE i.tenant_id=scope AND i.package_id=package AND s.price_mode='from'
              );
            price:=p.price_cents;
          ELSE
            valid:=valid AND EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope
                AND i.package_id=package AND i.service_id=p.base_service_id AND i.add_duration)
              AND NOT EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope AND i.package_id=package
                AND i.service_id<>p.base_service_id AND NOT EXISTS (SELECT 1 FROM public.service_addons a
                  WHERE a.tenant_id=scope AND a.base_service_id=p.base_service_id AND a.addon_service_id=i.service_id
                    AND a.add_duration=i.add_duration));
          END IF;
          UPDATE public.services SET name=p.name,description=p.description,price_cents=LEAST(100000000,price)::integer,
              price_mode=CASE WHEN p.kind='variant' AND EXISTS (
                SELECT 1 FROM public.service_package_items i
                JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
                WHERE i.tenant_id=scope AND i.package_id=package AND s.price_mode='from'
              ) THEN 'from' ELSE 'fixed' END,
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
    op.execute("""
        DROP TRIGGER package_service_changed ON public.services;
        CREATE TRIGGER package_service_changed
          AFTER UPDATE OF name,description,duration_min,price_cents,price_mode,
                   prep_min,cleanup_min,active
          ON public.services FOR EACH ROW EXECUTE FUNCTION public.package_source_changed();
    """)
    # Retain the original SECURITY DEFINER owner/permissions and tenant boundary.
    # In-progress variable-priced bookings never automatically finalise; the
    # operator must enter the agreed actual charge first.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.orion_due_auto_completions(batch_limit integer DEFAULT 100)
          RETURNS TABLE(tenant_id uuid,appointment_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path=pg_catalog,public
        AS $func$
          SELECT a.tenant_id,a.id
          FROM public.appointments a
          JOIN public.tenants t ON t.id=a.tenant_id
          WHERE a.status='confirmed' AND t.status='active'
            AND a.end_at <= now()
            AND a.end_at >= t.auto_complete_after
            AND NOT EXISTS (
              SELECT 1 FROM public.appointment_items i
              WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id
                AND i.price_mode_snapshot='from')
            AND $1 BETWEEN 1 AND 200
          ORDER BY a.end_at,a.tenant_id,a.id
          LIMIT LEAST(GREATEST($1,1),200)
        $func$;
        REVOKE ALL ON FUNCTION public.orion_due_auto_completions(integer) FROM PUBLIC,app_runtime;
        GRANT EXECUTE ON FUNCTION public.orion_due_auto_completions(integer) TO app_support;
    """)


def downgrade():
    raise RuntimeError(
        "Historical minimum-price snapshots and pricing flags must not be dropped. "
        "Use a reviewed forward migration."
    )
