"""Link physical packaging movements to Delivery orders."""
from alembic import op


revision = "0107_delivery_order_containers"
down_revision = "0106_delivery_storefront"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_order_container_plans (
            tenant_id uuid NOT NULL,
            order_id uuid NOT NULL,
            variant_id uuid NOT NULL,
            container_id uuid NOT NULL,
            exchange_quantity integer NOT NULL CHECK (exchange_quantity BETWEEN 0 AND 1000),
            loan_quantity integer NOT NULL CHECK (loan_quantity BETWEEN 0 AND 1000),
            PRIMARY KEY (tenant_id,order_id,variant_id),
            FOREIGN KEY (tenant_id,order_id)
                REFERENCES public.delivery_order_details(tenant_id,order_id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id,variant_id) REFERENCES public.commercial_variants(tenant_id,id),
            FOREIGN KEY (tenant_id,container_id) REFERENCES public.delivery_containers(tenant_id,id),
            CHECK (exchange_quantity+loan_quantity BETWEEN 1 AND 1000)
        );
        ALTER TABLE public.delivery_order_container_plans ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.delivery_order_container_plans FORCE ROW LEVEL SECURITY;
        CREATE POLICY delivery_order_container_plans_runtime ON public.delivery_order_container_plans TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        CREATE POLICY delivery_order_container_plans_migrator ON public.delivery_order_container_plans TO app_migrator
            USING (true) WITH CHECK (true);
        REVOKE ALL ON public.delivery_order_container_plans FROM PUBLIC,app_support,app_runtime;
        GRANT SELECT,INSERT ON public.delivery_order_container_plans TO app_runtime;

        CREATE SEQUENCE public.delivery_container_movement_sequence;
        REVOKE ALL ON SEQUENCE public.delivery_container_movement_sequence FROM PUBLIC,app_support,app_runtime;
        GRANT USAGE ON SEQUENCE public.delivery_container_movement_sequence TO app_runtime;
        ALTER TABLE public.delivery_container_movements
            ADD COLUMN ledger_sequence bigint,
            ADD COLUMN order_id uuid,
            ADD COLUMN order_phase varchar(16),
            ADD CONSTRAINT delivery_container_movement_order_fk FOREIGN KEY (tenant_id,order_id)
                REFERENCES public.delivery_order_details(tenant_id,order_id) ON DELETE CASCADE,
            ADD CONSTRAINT delivery_container_movement_order_fields CHECK (
                (order_id IS NULL AND order_phase IS NULL)
                OR (order_id IS NOT NULL AND ledger_sequence IS NOT NULL AND order_phase IS NOT NULL
                    AND ((order_phase='fulfill' AND kind IN ('in','lend'))
                    OR (order_phase='return' AND kind IN ('out','return'))))
            );
        ALTER SEQUENCE public.delivery_container_movement_sequence
            OWNED BY public.delivery_container_movements.ledger_sequence;
        ALTER TABLE public.delivery_container_movements
            ALTER COLUMN ledger_sequence SET DEFAULT nextval('public.delivery_container_movement_sequence');
        CREATE UNIQUE INDEX delivery_container_order_phase_idx
            ON public.delivery_container_movements(tenant_id,order_id,container_id,order_phase,kind)
            WHERE order_id IS NOT NULL;
        CREATE INDEX delivery_container_loan_sequence_idx
            ON public.delivery_container_movements(tenant_id,container_id,customer_id,ledger_sequence)
            WHERE customer_id IS NOT NULL AND ledger_sequence IS NOT NULL;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_order_container_plans)
                OR EXISTS (SELECT 1 FROM public.delivery_container_movements WHERE order_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Preserve os vínculos de vasilhames com os pedidos';
            END IF;
        END $$;
        DROP TABLE public.delivery_order_container_plans;
        ALTER TABLE public.delivery_container_movements
            DROP CONSTRAINT delivery_container_movement_order_fields,
            DROP CONSTRAINT delivery_container_movement_order_fk,
            DROP COLUMN order_phase,
            DROP COLUMN order_id,
            DROP COLUMN ledger_sequence;
    """)
