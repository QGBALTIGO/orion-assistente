"""A private directory for prosthetic laboratories used by a clinic."""
from alembic import op

revision = "0086_dental_laboratories"
down_revision = "0085_dental_prosthesis_tracking"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.dental_laboratories (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            name varchar(120) NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
            phone varchar(30),
            active boolean NOT NULL DEFAULT true,
            version integer NOT NULL DEFAULT 1 CHECK (version > 0)
        );
        CREATE UNIQUE INDEX uq_dental_laboratory_name
            ON public.dental_laboratories (tenant_id,lower(name));
        ALTER TABLE public.dental_laboratories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_laboratories FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.dental_laboratories FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT,UPDATE ON public.dental_laboratories TO app_runtime;
        CREATE POLICY dental_laboratories_migrator ON public.dental_laboratories
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY dental_laboratories_runtime ON public.dental_laboratories
            FOR ALL TO app_runtime
            USING (
                tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid
                AND EXISTS (
                    SELECT 1 FROM public.dental_clinicians g
                    WHERE g.tenant_id=dental_laboratories.tenant_id
                    AND g.account_id=nullif(current_setting('app.dental_actor',true),'')::uuid
                    AND g.enabled AND g.level='dentist'
                )
            );
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_laboratories) THEN
                RAISE EXCEPTION 'Reconcile saved laboratories before downgrading the schema';
            END IF;
        END $$;
        DROP TABLE public.dental_laboratories;
    """)
