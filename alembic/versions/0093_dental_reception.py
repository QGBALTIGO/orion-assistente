"""Private patient registration and reception scheduling for dentistry."""
from alembic import op

revision = "0093_dental_reception"
down_revision = "0092_subscription_audience"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE public.dental_patients ADD COLUMN administrative_profile jsonb NOT NULL DEFAULT '{}'
            CHECK (jsonb_typeof(administrative_profile) = 'object');
        ALTER TABLE public.appointments ADD COLUMN preserve_catalog_snapshot boolean NOT NULL DEFAULT false;

        CREATE FUNCTION public.dental_reception_member() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
        AS $body$
            SELECT EXISTS (
                SELECT 1 FROM public.tenant_memberships m
                WHERE m.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                  AND m.account_id = NULLIF(current_setting('app.dental_reception_actor', true), '')::uuid
                  AND m.active AND m.role IN ('owner','editor')
                  AND NOT EXISTS (SELECT 1 FROM public.tenant_memberships other
                      WHERE other.account_id=m.account_id AND other.active AND other.tenant_id<>m.tenant_id)
            )
        $body$;
        REVOKE ALL ON FUNCTION public.dental_reception_member() FROM PUBLIC, app_support;
        GRANT EXECUTE ON FUNCTION public.dental_reception_member() TO app_runtime;

        CREATE TABLE public.dental_professional_profiles (
            tenant_id uuid NOT NULL,
            professional_id uuid NOT NULL,
            specialties text[] NOT NULL DEFAULT '{}' CHECK (cardinality(specialties) <= 12),
            cro_number varchar(14) CHECK (cro_number ~ '^[0-9]{3,14}$'),
            cro_state char(2),
            default_room_id uuid,
            PRIMARY KEY (tenant_id, professional_id),
            FOREIGN KEY (tenant_id, professional_id) REFERENCES public.professionals(tenant_id, id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, default_room_id) REFERENCES public.dental_rooms(tenant_id, id),
            CHECK ((cro_number IS NULL) = (cro_state IS NULL))
        );

        CREATE TABLE public.dental_reception_appointments (
            tenant_id uuid NOT NULL,
            appointment_id uuid NOT NULL,
            visit_type text NOT NULL CHECK (visit_type IN ('evaluation','first_visit','return','session','emergency')),
            administrative_note text CHECK (length(administrative_note) <= 500),
            progress_for_start_at timestamptz,
            checked_in_at timestamptz,
            started_at timestamptz,
            PRIMARY KEY (tenant_id, appointment_id),
            FOREIGN KEY (tenant_id, appointment_id) REFERENCES public.appointments(tenant_id, id),
            CHECK ((checked_in_at IS NULL) = (progress_for_start_at IS NULL)),
            CHECK (started_at IS NULL OR (checked_in_at IS NOT NULL AND checked_in_at <= started_at))
        );
    """)
    tenant = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    actor = "NULLIF(current_setting('app.dental_reception_actor', true), '')::uuid"
    scope = f"{tenant} AND public.dental_reception_member()"
    for table, privileges in (
        ("dental_professional_profiles", "SELECT, INSERT, UPDATE"),
        ("dental_reception_appointments", "SELECT, INSERT, UPDATE"),
    ):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC, app_support")
        op.execute(f"GRANT {privileges} ON public.{table} TO app_runtime")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"CREATE POLICY {table}_reception ON public.{table} TO app_runtime "
                   f"USING ({scope}) WITH CHECK ({scope})")
    op.execute(f"CREATE POLICY patients_reception ON public.dental_patients TO app_runtime "
               f"USING ({scope}) WITH CHECK ({scope})")
    op.execute(f"CREATE POLICY visits_reception_read ON public.dental_visit_patient_events FOR SELECT TO app_runtime "
               f"USING ({scope})")
    op.execute(f"CREATE POLICY visits_reception_append ON public.dental_visit_patient_events FOR INSERT TO app_runtime "
               f"WITH CHECK ({scope} AND actor_account_id = {actor} AND version = 1)")
    op.execute(f"CREATE POLICY audit_reception ON public.dental_access_audit FOR INSERT TO app_runtime "
               f"WITH CHECK ({scope} AND actor_account_id = {actor})")
    op.execute(f"CREATE POLICY insurance_reception_read ON public.dental_insurance_plans FOR SELECT TO app_runtime "
               f"USING ({scope})")


def downgrade():
    op.execute("""
        LOCK TABLE public.dental_professional_profiles, public.dental_reception_appointments,
            public.dental_patients IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_professional_profiles)
              OR EXISTS (SELECT 1 FROM public.dental_reception_appointments)
              OR EXISTS (SELECT 1 FROM public.dental_patients WHERE administrative_profile <> '{}')
            THEN RAISE EXCEPTION 'Preserve os dados administrativos antes de reverter este esquema';
            END IF;
        END $$;
        DROP POLICY insurance_reception_read ON public.dental_insurance_plans;
        DROP POLICY audit_reception ON public.dental_access_audit;
        DROP POLICY visits_reception_append ON public.dental_visit_patient_events;
        DROP POLICY visits_reception_read ON public.dental_visit_patient_events;
        DROP POLICY patients_reception ON public.dental_patients;
        DROP TABLE public.dental_reception_appointments, public.dental_professional_profiles;
        DROP FUNCTION public.dental_reception_member();
        ALTER TABLE public.appointments DROP COLUMN preserve_catalog_snapshot;
        ALTER TABLE public.dental_patients DROP COLUMN administrative_profile;
    """)
