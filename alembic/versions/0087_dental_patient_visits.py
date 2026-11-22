"""Append-only, explicit patient identity for each visit. Existing visits remain unlinked."""
from alembic import op

revision = "0087_dental_patient_visits"
down_revision = "0086_dental_laboratories"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        CREATE TABLE public.dental_visit_patient_events (
            tenant_id uuid NOT NULL,
            appointment_id uuid NOT NULL,
            version integer NOT NULL CHECK (version >= 1),
            patient_id uuid,
            actor_account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, appointment_id, version),
            FOREIGN KEY (tenant_id, appointment_id) REFERENCES public.appointments(tenant_id, id),
            FOREIGN KEY (tenant_id, patient_id) REFERENCES public.dental_patients(tenant_id, id),
            CHECK (version > 1 OR patient_id IS NOT NULL)
        );
        CREATE INDEX ix_dental_patient_visits ON public.dental_visit_patient_events
            (tenant_id, patient_id, appointment_id, version) WHERE patient_id IS NOT NULL;
        ALTER TABLE public.dental_visit_patient_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_visit_patient_events FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.dental_visit_patient_events FROM PUBLIC, app_runtime, app_support;
        GRANT SELECT, INSERT ON public.dental_visit_patient_events TO app_runtime;
        CREATE POLICY visit_patient_migration ON public.dental_visit_patient_events
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY visit_patient_read ON public.dental_visit_patient_events
            FOR SELECT TO app_runtime USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND EXISTS (SELECT 1 FROM public.dental_clinicians g
                    WHERE g.tenant_id = dental_visit_patient_events.tenant_id AND g.enabled
                      AND g.account_id = NULLIF(current_setting('app.dental_actor', true), '')::uuid)
            );
        CREATE POLICY visit_patient_append ON public.dental_visit_patient_events
            FOR INSERT TO app_runtime WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND actor_account_id = NULLIF(current_setting('app.dental_actor', true), '')::uuid
                AND EXISTS (SELECT 1 FROM public.dental_clinicians g
                    WHERE g.tenant_id = dental_visit_patient_events.tenant_id AND g.enabled
                      AND g.account_id = dental_visit_patient_events.actor_account_id)
            );
    """)


def downgrade():
    # A rollback of the application must retain links created since deployment.
    op.execute("""
        LOCK TABLE public.dental_visit_patient_events IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_visit_patient_events) THEN
                RAISE EXCEPTION 'Preserve o histórico de vínculos antes de reverter este esquema';
            END IF;
        END $$;
        DROP TABLE public.dental_visit_patient_events;
    """)
