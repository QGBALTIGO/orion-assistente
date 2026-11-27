"""Link manual counter and pickup orders to the commercial ledger."""
from alembic import op


revision = "0100_delivery_orders"
down_revision = "0099_delivery_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.commercial_orders
            ADD COLUMN origin varchar(16) NOT NULL DEFAULT 'commerce',
            DROP CONSTRAINT ck_commercial_order_state,
            ADD CONSTRAINT ck_commercial_order_state CHECK (
                origin IN ('commerce','delivery') AND payment_status='not_verified' AND revision>0
                AND (status IN ('draft','confirmed','cancelled') OR (origin='delivery' AND status='fulfilled')));
        CREATE TABLE public.delivery_order_details (
            tenant_id uuid NOT NULL,
            order_id uuid NOT NULL,
            mode varchar(16) NOT NULL CHECK (mode IN ('counter','pickup')),
            customer_id uuid,
            financial_entry_id uuid,
            PRIMARY KEY (tenant_id,order_id),
            FOREIGN KEY (tenant_id,order_id) REFERENCES public.commercial_orders (tenant_id,id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id,customer_id) REFERENCES public.customers (tenant_id,id) ON DELETE SET NULL (customer_id),
            FOREIGN KEY (tenant_id,financial_entry_id) REFERENCES public.financial_entries (tenant_id,id),
            UNIQUE (tenant_id,financial_entry_id)
        );
        CREATE INDEX ix_delivery_order_period ON public.commercial_orders (tenant_id,created_at DESC,id DESC)
            WHERE origin='delivery';
        ALTER TABLE public.delivery_order_details ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.delivery_order_details FORCE ROW LEVEL SECURITY;
        CREATE POLICY delivery_order_details_runtime ON public.delivery_order_details TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        CREATE POLICY delivery_order_details_migrator ON public.delivery_order_details TO app_migrator
            USING (true) WITH CHECK (true);
        REVOKE ALL ON public.delivery_order_details FROM PUBLIC,app_support,app_runtime;
        GRANT SELECT,INSERT,UPDATE ON public.delivery_order_details TO app_runtime;
    """)


def downgrade():
    # Remove this revision only on a disposable database with no delivery records.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.commercial_orders WHERE origin='delivery') THEN
                RAISE EXCEPTION 'Desative a homologação preservando os pedidos de Delivery';
            END IF;
        END $$;
        DROP TABLE public.delivery_order_details;
        DROP INDEX public.ix_delivery_order_period;
        ALTER TABLE public.commercial_orders DROP CONSTRAINT ck_commercial_order_state, DROP COLUMN origin;
        ALTER TABLE public.commercial_orders ADD CONSTRAINT ck_commercial_order_state
            CHECK (status IN ('draft','confirmed','cancelled') AND payment_status='not_verified' AND revision>0);
    """)
