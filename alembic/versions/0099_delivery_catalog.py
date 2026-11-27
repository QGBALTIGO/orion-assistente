"""Add tenant-scoped delivery catalog details and request receipts."""
from alembic import op


revision = "0099_delivery_catalog"
down_revision = "0098_manual_pix_successor"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.delivery_variant_details (
            tenant_id uuid NOT NULL,
            variant_id uuid NOT NULL,
            category varchar(80) NOT NULL,
            unit varchar(24) NOT NULL,
            purchase_cost_cents bigint CHECK (purchase_cost_cents BETWEEN 0 AND 100000000),
            minimum_quantity integer NOT NULL CHECK (minimum_quantity BETWEEN 0 AND 10000000),
            barcode varchar(32) CHECK (barcode ~ '^[0-9]{1,32}$'),
            PRIMARY KEY (tenant_id, variant_id),
            FOREIGN KEY (tenant_id, variant_id) REFERENCES public.commercial_variants (tenant_id, id)
                ON DELETE CASCADE,
            CHECK (length(trim(category)) > 0 AND length(trim(unit)) > 0),
            UNIQUE (tenant_id, barcode)
        );
        CREATE TABLE public.delivery_catalog_operations (
            tenant_id uuid NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
            operation_key varchar(120) NOT NULL,
            fingerprint varchar(64) NOT NULL,
            result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, operation_key)
        );
    """)
    for table in ("delivery_variant_details", "delivery_catalog_operations"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """)
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC, app_support, app_runtime")
        privileges = "SELECT, INSERT, UPDATE" if table == "delivery_variant_details" else "SELECT, INSERT"
        op.execute(f"GRANT {privileges} ON public.{table} TO app_runtime")


def downgrade():
    op.drop_table("delivery_catalog_operations", schema="public")
    op.drop_table("delivery_variant_details", schema="public")
