"""Tenant-scoped directory of insurance plans accepted by a dental clinic."""
from alembic import op

revision = "0091_dental_insurance_plans"
down_revision = "0089_dental_sales_patient_link"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.dental_insurance_plans (
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            id uuid NOT NULL,
            insurer varchar(120) NOT NULL CHECK (length(btrim(insurer)) >= 2),
            plan varchar(160) NOT NULL CHECK (length(btrim(plan)) >= 2),
            covered_procedures text CHECK (length(covered_procedures) <= 2000),
            active boolean NOT NULL DEFAULT true,
            version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            PRIMARY KEY (tenant_id, id)
        );
        CREATE UNIQUE INDEX uq_dental_insurance_plan
          ON public.dental_insurance_plans(tenant_id, lower(btrim(insurer)), lower(btrim(plan)));
        ALTER TABLE public.dental_insurance_plans ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_insurance_plans FORCE ROW LEVEL SECURITY;
        CREATE POLICY dental_insurance_plan_migrator ON public.dental_insurance_plans
          FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY dental_insurance_plan_owner ON public.dental_insurance_plans
          TO app_runtime USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND public.dental_sales_owner()
          ) WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND public.dental_sales_owner()
          );
        REVOKE ALL ON public.dental_insurance_plans FROM PUBLIC, app_support;
        GRANT SELECT, INSERT, UPDATE ON public.dental_insurance_plans TO app_runtime;
    """)


def downgrade():
    op.execute("""
        LOCK TABLE public.dental_insurance_plans IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_insurance_plans) THEN
                RAISE EXCEPTION 'Preserve os convênios cadastrados antes de reverter o esquema';
            END IF;
        END $$;
        DROP TABLE public.dental_insurance_plans;
    """)
