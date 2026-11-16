"""Globally unique public appointment aliases; not authentication credentials.

Every appointment receives one stable alias in the same INSERT transaction.
Historic tenant-local protocol collisions get a distinct public alias without
changing an existing appointment protocol or breaking an old bearer link.
Only the authenticated internal WhatsApp bridge may resolve a short code
together with a VERIFIED customer/professional phone.
"""
from alembic import op

revision = "0054_appointment_short_links"
down_revision = "0053_central_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(r"""
      CREATE TABLE public.appointment_short_codes (
        code varchar(24) PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        appointment_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_appointment_short_identity UNIQUE(tenant_id,appointment_id),
        CONSTRAINT fk_appointment_short_booking FOREIGN KEY(tenant_id,appointment_id)
          REFERENCES public.appointments(tenant_id,id) ON DELETE CASCADE,
        CONSTRAINT ck_appointment_short_code CHECK
          (code ~ '^AG-[A-HJ-NP-Z2-9]{6}(-[A-F0-9]{8})?$')
      );
      CREATE INDEX ix_appointment_short_tenant ON public.appointment_short_codes(tenant_id,appointment_id);
      ALTER TABLE public.appointment_short_codes ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.appointment_short_codes FORCE ROW LEVEL SECURITY;
      CREATE POLICY appointment_short_runtime ON public.appointment_short_codes
        FOR SELECT TO app_runtime USING
        (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
      CREATE POLICY appointment_short_migrator ON public.appointment_short_codes
        FOR ALL TO app_migrator USING(true) WITH CHECK(true);
      REVOKE ALL ON public.appointment_short_codes FROM PUBLIC,app_runtime,app_support;
      GRANT SELECT ON public.appointment_short_codes TO app_runtime;
      GRANT SELECT,INSERT,UPDATE,DELETE ON public.appointment_short_codes TO app_migrator;
    """)
    op.execute(r"""
      CREATE FUNCTION public.orion_assign_appointment_short_code(
        incoming_protocol text, incoming_tenant uuid, incoming_appointment uuid)
      RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog AS $fn$
      DECLARE chosen text; attempt integer;
      BEGIN
        IF incoming_protocol !~ '^AG-[A-HJ-NP-Z2-9]{6}$'
          OR incoming_tenant IS NULL OR incoming_appointment IS NULL THEN
          RAISE EXCEPTION 'invalid_appointment_short_identity';
        END IF;
        SELECT code INTO chosen FROM public.appointment_short_codes
          WHERE tenant_id=incoming_tenant AND appointment_id=incoming_appointment;
        IF chosen IS NOT NULL THEN RETURN chosen; END IF;
        FOR attempt IN 1..12 LOOP
          chosen := CASE WHEN attempt=1 THEN incoming_protocol
            ELSE incoming_protocol || '-' ||
              upper(left(replace(gen_random_uuid()::text,'-',''),8)) END;
          INSERT INTO public.appointment_short_codes(code,tenant_id,appointment_id)
            VALUES(chosen,incoming_tenant,incoming_appointment)
            ON CONFLICT DO NOTHING;
          IF FOUND THEN RETURN chosen; END IF;
        END LOOP;
        RAISE EXCEPTION 'appointment_short_code_collision';
      END $fn$;
      REVOKE ALL ON FUNCTION public.orion_assign_appointment_short_code(text,uuid,uuid)
        FROM PUBLIC,app_runtime,app_support;
      CREATE FUNCTION public.orion_appointment_short_insert()
      RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
      BEGIN
        PERFORM public.orion_assign_appointment_short_code(NEW.protocol,NEW.tenant_id,NEW.id);
        RETURN NEW;
      END $fn$;
      REVOKE ALL ON FUNCTION public.orion_appointment_short_insert()
        FROM PUBLIC,app_runtime,app_support;
      CREATE TRIGGER trg_appointment_short_code AFTER INSERT ON public.appointments
        FOR EACH ROW EXECUTE FUNCTION public.orion_appointment_short_insert();
    """)
    # Older rows retain their actual appointment protocol. The globally unique
    # public alias gets a suffix if another tenant has claimed the same code.
    op.execute(r"""
      DO $fn$
      DECLARE item record;
      BEGIN
        FOR item IN
          SELECT a.tenant_id,a.id,a.protocol FROM public.appointments a
          ORDER BY a.created_at,a.tenant_id,a.id
        LOOP
          PERFORM public.orion_assign_appointment_short_code(
            item.protocol,item.tenant_id,item.id);
        END LOOP;
      END $fn$;
    """)
    op.execute(r"""
      CREATE FUNCTION public.orion_resolve_short_appointment(
        requested_code text, verified_phone text, requested_role text)
      RETURNS TABLE(tenant_id uuid,appointment_id uuid,
                    customer_id uuid,professional_id uuid)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
      BEGIN
        IF session_user <> 'app_support'
            OR requested_code !~ '^AG-[A-HJ-NP-Z2-9]{6}(-[A-F0-9]{8})?$'
            OR verified_phone !~ '^\+(55[1-9][0-9]{9,10}|5959[0-9]{8})$'
            OR requested_role NOT IN ('customer','professional') THEN
          RETURN;
        END IF;
        RETURN QUERY
          SELECT a.tenant_id,a.id,a.customer_id,a.professional_id
          FROM public.appointment_short_codes short
          JOIN public.appointments a ON a.tenant_id=short.tenant_id
            AND a.id=short.appointment_id
          JOIN public.tenants tenant ON tenant.id=a.tenant_id AND tenant.status='active'
          JOIN public.customers c ON c.tenant_id=a.tenant_id
            AND c.id=a.customer_id AND c.active AND c.deleted_at IS NULL
          JOIN public.professionals p ON p.tenant_id=a.tenant_id
            AND p.id=a.professional_id
          WHERE short.code=requested_code
            AND a.status<>'hold'
            AND ((requested_role='customer' AND c.whatsapp_e164=verified_phone)
              OR (requested_role='professional' AND p.active AND
                  p.phone_e164=verified_phone));
      END $fn$;
      REVOKE ALL ON FUNCTION public.orion_resolve_short_appointment(text,text,text)
        FROM PUBLIC,app_runtime;
      GRANT EXECUTE ON FUNCTION public.orion_resolve_short_appointment(text,text,text)
        TO app_support;
    """)


def downgrade():
    raise RuntimeError("Preserve short aliases, previously shared links and appointment histories")
