"""Add the tenant-scoped empty packaging and customer loan ledger."""
from alembic import op


revision = "0102_delivery_containers"
down_revision = "0101_delivery_dispatch"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_containers (
            tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            id uuid NOT NULL,
            kind varchar(16) NOT NULL CHECK (kind IN ('cylinder','jug')),
            name varchar(120) NOT NULL,
            empty_quantity integer NOT NULL CHECK (empty_quantity BETWEEN 0 AND 10000000),
            revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
            PRIMARY KEY (tenant_id,id)
        );
        CREATE UNIQUE INDEX delivery_containers_name_idx ON public.delivery_containers (tenant_id,kind,lower(name));
        CREATE TABLE public.delivery_container_movements (
            tenant_id uuid NOT NULL,
            id uuid NOT NULL,
            container_id uuid NOT NULL,
            customer_id uuid,
            actor_id uuid NOT NULL,
            kind varchar(16) NOT NULL CHECK (kind IN ('initial','in','out','count','lend','return')),
            quantity integer NOT NULL CHECK (quantity BETWEEN 0 AND 10000000),
            balance_before integer NOT NULL CHECK (balance_before BETWEEN 0 AND 10000000),
            balance_after integer NOT NULL CHECK (balance_after BETWEEN 0 AND 10000000),
            note varchar(300),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id,id),
            FOREIGN KEY (tenant_id,container_id) REFERENCES public.delivery_containers(tenant_id,id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id,customer_id) REFERENCES public.customers(tenant_id,id),
            CHECK ((kind IN ('lend','return'))=(customer_id IS NOT NULL))
        );
        CREATE INDEX delivery_containers_history_idx
            ON public.delivery_container_movements(tenant_id,container_id,occurred_at DESC,id DESC);
        CREATE INDEX delivery_container_loans_idx
            ON public.delivery_container_movements(tenant_id,container_id,customer_id) WHERE customer_id IS NOT NULL;
    """)
    for table in ("delivery_containers", "delivery_container_movements"):
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
            CREATE POLICY {table}_migrator ON public.{table} TO app_migrator
                USING (true) WITH CHECK (true);
            REVOKE ALL ON public.{table} FROM PUBLIC,app_support,app_runtime;
            GRANT SELECT,INSERT ON public.{table} TO app_runtime;
        """)
    op.execute("GRANT UPDATE (empty_quantity,revision) ON public.delivery_containers TO app_runtime;")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_containers) THEN
                RAISE EXCEPTION 'Preserve o histórico de vasilhames ao desativar a homologação';
            END IF;
        END $$;
        DROP TABLE public.delivery_container_movements;
        DROP TABLE public.delivery_containers;
    """)
