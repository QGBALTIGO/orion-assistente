"""Record supplier receipts with their physical stock and payable in one transaction."""
from alembic import op


revision = "0114_delivery_purchases"
down_revision = "0113_delivery_order_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_suppliers (
            tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            id uuid NOT NULL, name varchar(140) NOT NULL CHECK (length(btrim(name))>0), cnpj varchar(14),
            phone varchar(16), brands varchar(240), active boolean NOT NULL DEFAULT true,
            updated_by uuid NOT NULL REFERENCES public.panel_accounts(id),
            revision integer NOT NULL DEFAULT 1 CHECK (revision>0), PRIMARY KEY(tenant_id,id),
            UNIQUE(tenant_id,cnpj)
        );
        CREATE INDEX delivery_suppliers_names ON public.delivery_suppliers(tenant_id,lower(name),id);
        CREATE TABLE public.delivery_purchases (
            tenant_id uuid NOT NULL, id uuid NOT NULL, supplier_id uuid NOT NULL,
            reference varchar(80) NOT NULL CHECK (length(btrim(reference))>0),
            amount_cents bigint NOT NULL CHECK (amount_cents BETWEEN 1 AND 1000000000),
            due_on date, financial_entry_id uuid NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
            received_by uuid NOT NULL REFERENCES public.panel_accounts(id),
            PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,financial_entry_id),
            FOREIGN KEY(tenant_id,supplier_id) REFERENCES public.delivery_suppliers(tenant_id,id),
            FOREIGN KEY(tenant_id,financial_entry_id) REFERENCES public.financial_entries(tenant_id,id)
        );
        CREATE UNIQUE INDEX delivery_supplier_document ON public.delivery_purchases(tenant_id,supplier_id,lower(reference));
        CREATE INDEX delivery_purchases_recent ON public.delivery_purchases(tenant_id,received_at DESC,id);
        CREATE TABLE public.delivery_purchase_items (
            tenant_id uuid NOT NULL, purchase_id uuid NOT NULL, variant_id uuid NOT NULL,
            label varchar(350) NOT NULL, quantity integer NOT NULL CHECK(quantity>0),
            unit_cost_cents integer NOT NULL CHECK(unit_cost_cents BETWEEN 0 AND 100000000),
            PRIMARY KEY(tenant_id,purchase_id,variant_id),
            FOREIGN KEY(tenant_id,purchase_id) REFERENCES public.delivery_purchases(tenant_id,id) ON DELETE CASCADE,
            FOREIGN KEY(tenant_id,variant_id) REFERENCES public.commercial_variants(tenant_id,id)
        )
    """)
    for table in ("delivery_suppliers", "delivery_purchases", "delivery_purchase_items"):
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
            CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING (true) WITH CHECK (true);
            REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support;
            GRANT SELECT,INSERT ON public.{table} TO app_runtime;
        """)
    op.execute("GRANT UPDATE ON public.delivery_suppliers TO app_runtime")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.delivery_suppliers) THEN RAISE EXCEPTION 'Preserve fornecedores e compras'; END IF;
        END $$;
        DROP TABLE public.delivery_purchase_items,public.delivery_purchases,public.delivery_suppliers""")
