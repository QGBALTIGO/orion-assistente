"""Tenant-scoped intake templates. Patient answers belong to clinical records."""
from alembic import op

revision = "0083_dental_intake_templates"
down_revision = "0082_dental_chair_availability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.dental_intake_templates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            name varchar(80) NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 80),
            questions jsonb NOT NULL CHECK (
                jsonb_typeof(questions)='array' AND jsonb_array_length(questions) BETWEEN 1 AND 50
            ),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            UNIQUE (tenant_id,name)
        );
        ALTER TABLE public.dental_intake_templates ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_intake_templates FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.dental_intake_templates FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT,UPDATE ON public.dental_intake_templates TO app_runtime;
        CREATE POLICY dental_intake_templates_migrator ON public.dental_intake_templates
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY dental_intake_templates_runtime ON public.dental_intake_templates
            FOR ALL TO app_runtime
            USING (
                tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid
                AND nullif(current_setting('app.dental_actor',true),'') IS NOT NULL
            )
            WITH CHECK (
                tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid
                AND nullif(current_setting('app.dental_actor',true),'') IS NOT NULL
            );
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.dental_intake_templates) THEN
            RAISE EXCEPTION 'Reconcile saved intake templates before rollback';
          END IF;
        END $$;
        DROP TABLE public.dental_intake_templates;
    """)
