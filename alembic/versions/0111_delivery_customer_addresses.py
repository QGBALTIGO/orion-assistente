"""Save named delivery addresses without changing historical order snapshots."""
from alembic import op


revision = "0111_delivery_customer_addresses"
down_revision = "0110_delivery_checkout_options"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_customer_addresses (
            tenant_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            id uuid NOT NULL,
            label varchar(60) CHECK (length(btrim(label)) > 0),
            address jsonb CHECK (jsonb_typeof(address)='object'),
            CHECK ((label IS NULL) = (address IS NULL)),
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, customer_id, id),
            FOREIGN KEY (tenant_id, customer_id) REFERENCES public.customers(tenant_id,id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX delivery_customer_address_label ON public.delivery_customer_addresses
            (tenant_id,customer_id,lower(label));
        ALTER TABLE public.delivery_customer_addresses ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.delivery_customer_addresses FORCE ROW LEVEL SECURITY;
        CREATE POLICY delivery_customer_addresses_runtime ON public.delivery_customer_addresses TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        CREATE POLICY delivery_customer_addresses_migrator ON public.delivery_customer_addresses TO app_migrator
            USING (true) WITH CHECK (true);
        REVOKE ALL ON public.delivery_customer_addresses FROM PUBLIC,app_support,app_runtime;
        GRANT SELECT,INSERT,UPDATE ON public.delivery_customer_addresses TO app_runtime;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_customer_addresses) THEN
                RAISE EXCEPTION 'Preserve os endereços dos clientes antes de reverter';
            END IF;
        END $$;
        DROP TABLE public.delivery_customer_addresses;
    """)
