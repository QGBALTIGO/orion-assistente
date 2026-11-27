"""Keep delivery coverage, scheduling and couriers scoped to the merchant."""
from alembic import op


revision = "0105_delivery_logistics"
down_revision = "0104_delivery_returns"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_logistics_policies (
            tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
            configuration jsonb NOT NULL CHECK (jsonb_typeof(configuration)='object'),
            revision integer NOT NULL CHECK (revision>0),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE public.delivery_couriers (
            tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            id uuid NOT NULL,
            name varchar(120) NOT NULL CHECK (length(btrim(name))>0),
            phone varchar(16),
            active boolean NOT NULL DEFAULT true,
            revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id,id)
        );
        CREATE INDEX ix_delivery_courier_name ON public.delivery_couriers (tenant_id,lower(name),id);
        ALTER TABLE public.delivery_order_details
            ADD COLUMN courier_id uuid,
            ADD COLUMN scheduled_at timestamptz,
            ADD COLUMN logistics_snapshot jsonb,
            ADD CONSTRAINT delivery_order_courier_fk FOREIGN KEY (tenant_id,courier_id)
                REFERENCES public.delivery_couriers (tenant_id,id),
            ADD CONSTRAINT delivery_order_logistics_check CHECK (
                (mode='delivery' OR (courier_id IS NULL AND scheduled_at IS NULL AND logistics_snapshot IS NULL))
                AND (courier_id IS NULL OR courier_name IS NOT NULL)
                AND (logistics_snapshot IS NULL OR jsonb_typeof(logistics_snapshot)='object')
            );
        CREATE INDEX ix_delivery_schedule ON public.delivery_order_details (tenant_id,scheduled_at,order_id)
            WHERE scheduled_at IS NOT NULL;
    """)
    for table in ("delivery_logistics_policies", "delivery_couriers"):
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
            CREATE POLICY {table}_migrator ON public.{table} TO app_migrator
                USING (true) WITH CHECK (true);
            REVOKE ALL ON public.{table} FROM PUBLIC,app_support,app_runtime;
            GRANT SELECT,INSERT,UPDATE ON public.{table} TO app_runtime;
        """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_logistics_policies)
                OR EXISTS (SELECT 1 FROM public.delivery_couriers)
                OR EXISTS (SELECT 1 FROM public.delivery_order_details
                    WHERE courier_id IS NOT NULL OR scheduled_at IS NOT NULL OR logistics_snapshot IS NOT NULL)
            THEN
                RAISE EXCEPTION 'Preserve as configurações e os registros de logística';
            END IF;
        END $$;
        DROP INDEX public.ix_delivery_schedule;
        ALTER TABLE public.delivery_order_details
            DROP CONSTRAINT delivery_order_logistics_check,
            DROP CONSTRAINT delivery_order_courier_fk,
            DROP COLUMN logistics_snapshot,
            DROP COLUMN scheduled_at,
            DROP COLUMN courier_id;
        DROP TABLE public.delivery_couriers;
        DROP TABLE public.delivery_logistics_policies;
    """)
