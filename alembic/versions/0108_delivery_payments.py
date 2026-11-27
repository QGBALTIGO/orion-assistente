"""Link each partial Delivery receipt to its order and financial ledger row."""
from alembic import op


revision = "0108_delivery_payments"
down_revision = "0107_delivery_order_containers"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_credit_collections (
            tenant_id uuid NOT NULL,
            order_id uuid NOT NULL,
            financial_entry_id uuid NOT NULL,
            PRIMARY KEY (tenant_id, financial_entry_id),
            FOREIGN KEY (tenant_id, order_id)
                REFERENCES public.delivery_order_details(tenant_id, order_id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, financial_entry_id)
                REFERENCES public.financial_entries(tenant_id, id)
        );
        CREATE INDEX ix_delivery_credit_collections_order
            ON public.delivery_credit_collections(tenant_id, order_id);
        ALTER TABLE public.delivery_credit_collections ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.delivery_credit_collections FORCE ROW LEVEL SECURITY;
        CREATE POLICY delivery_credit_collections_runtime ON public.delivery_credit_collections TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        CREATE POLICY delivery_credit_collections_migrator ON public.delivery_credit_collections TO app_migrator
            USING (true) WITH CHECK (true);
        REVOKE ALL ON public.delivery_credit_collections FROM PUBLIC,app_support,app_runtime;
        GRANT SELECT,INSERT ON public.delivery_credit_collections TO app_runtime;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_credit_collections) THEN
                RAISE EXCEPTION 'Preserve as parcelas de Delivery ao desativar a funcionalidade';
            END IF;
        END $$;
        DROP TABLE public.delivery_credit_collections;
    """)
