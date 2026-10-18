"""First-party commercial contact requests, isolated from tenant operations.

Revision ID: 0018_sales_requests
Revises: 0017_financial_settlements
"""
from alembic import op

revision = "0018_sales_requests"
down_revision = "0017_financial_settlements"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.sales_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key uuid NOT NULL UNIQUE,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            name varchar(120) NOT NULL CHECK (length(name) BETWEEN 2 AND 120 AND name=btrim(name)),
            email varchar(254) NOT NULL CHECK (length(email) BETWEEN 5 AND 254 AND email=lower(email)),
            phone varchar(16) CHECK (phone ~ '^\\+55[1-9][0-9]{9,10}$'),
            business_name varchar(160) CHECK (length(business_name) BETWEEN 1 AND 160),
            segment varchar(20) NOT NULL CHECK (segment IN ('barbearia','estetica','odontologia','restaurante','fitness','outro')),
            intent varchar(20) NOT NULL CHECK (intent IN ('trial','essencial','profissional','escala','contact')),
            consent boolean NOT NULL DEFAULT true CHECK (consent),
            consent_version varchar(32) NOT NULL CHECK (consent_version='sales-contact-v1'),
            consented_at timestamptz NOT NULL DEFAULT now(),
            status varchar(12) NOT NULL DEFAULT 'new' CHECK (status IN ('new','contacted','closed')),
            version integer NOT NULL DEFAULT 1 CHECK (version>=1),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            contacted_at timestamptz,
            updated_by_role varchar(16) CHECK (updated_by_role IN ('support','admin')),
            updated_by_account_id uuid REFERENCES public.panel_accounts(id) ON DELETE SET NULL,
            CHECK (status<>'contacted' OR contacted_at IS NOT NULL),
            CHECK (substring(idempotency_key::text,15,1)='4')
        );
        CREATE INDEX ix_sales_requests_created ON public.sales_requests(created_at DESC,id DESC);
        CREATE INDEX ix_sales_requests_status_created ON public.sales_requests(status,created_at DESC,id DESC);
        CREATE INDEX ix_sales_requests_intent_created ON public.sales_requests(intent,created_at DESC,id DESC);
        ALTER TABLE public.sales_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.sales_requests FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.sales_requests FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT ON public.sales_requests TO app_support;
        GRANT UPDATE(status,version,updated_at,contacted_at,updated_by_role,updated_by_account_id)
            ON public.sales_requests TO app_support;
        CREATE POLICY sales_requests_support ON public.sales_requests FOR ALL TO app_support
            USING(true) WITH CHECK(true);
        CREATE POLICY sales_requests_migrator ON public.sales_requests FOR ALL TO app_migrator
            USING(true) WITH CHECK(true);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.sales_requests) THEN
        RAISE EXCEPTION 'Cannot downgrade while commercial contact requests exist';
        END IF; END $$""")
    op.drop_table("sales_requests", schema="public")
