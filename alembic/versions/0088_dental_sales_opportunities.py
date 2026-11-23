"""Opportunities created by a dental clinic. Free text is encrypted by the API."""
from alembic import op

revision = "0088_dental_sales_opportunities"
down_revision = "0087_dental_patient_visits"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        CREATE FUNCTION public.dental_sales_owner() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
        AS $body$
            SELECT EXISTS (
                SELECT 1 FROM public.tenant_memberships m
                WHERE m.tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid
                  AND m.account_id = NULLIF(current_setting('app.dental_actor',true),'')::uuid
                  AND m.role = 'owner' AND m.active
                  AND NOT EXISTS (SELECT 1 FROM public.tenant_memberships other
                      WHERE other.account_id=m.account_id AND other.active AND other.tenant_id<>m.tenant_id)
            )
        $body$;
        REVOKE ALL ON FUNCTION public.dental_sales_owner() FROM PUBLIC,app_support;
        GRANT EXECUTE ON FUNCTION public.dental_sales_owner() TO app_runtime;
        CREATE TABLE public.dental_sales_opportunities (
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            id uuid NOT NULL,
            created_by uuid NOT NULL REFERENCES public.panel_accounts(id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            nonce bytea NOT NULL CHECK (octet_length(nonce)=12),
            encrypted_content bytea NOT NULL CHECK (octet_length(encrypted_content) BETWEEN 17 AND 2400),
            digest varchar(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (tenant_id,id)
        );
        CREATE INDEX ix_dental_sales_created ON public.dental_sales_opportunities
            (tenant_id,created_at DESC,id DESC);
        ALTER TABLE public.dental_sales_opportunities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_sales_opportunities FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.dental_sales_opportunities FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT ON public.dental_sales_opportunities TO app_runtime;
        CREATE POLICY sales_migration ON public.dental_sales_opportunities
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY sales_read ON public.dental_sales_opportunities FOR SELECT TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND public.dental_sales_owner());
        CREATE POLICY sales_append ON public.dental_sales_opportunities FOR INSERT TO app_runtime
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
                AND created_by=NULLIF(current_setting('app.dental_actor',true),'')::uuid
                AND public.dental_sales_owner());
    """)


def downgrade():
    op.execute("""
        LOCK TABLE public.dental_sales_opportunities IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_sales_opportunities) THEN
                RAISE EXCEPTION 'Preserve as oportunidades antes de reverter o esquema';
            END IF;
        END $$;
        DROP TABLE public.dental_sales_opportunities;
        DROP FUNCTION public.dental_sales_owner();
    """)
