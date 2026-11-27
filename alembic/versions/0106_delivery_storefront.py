"""Opt-in public Delivery storefronts and scoped checkout receipts."""

from alembic import op


revision = "0106_delivery_storefront"
down_revision = "0105_delivery_logistics"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_storefronts (
            tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
            slug varchar(80) NOT NULL UNIQUE CHECK (length(slug)>=3 AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            enabled boolean NOT NULL DEFAULT false,
            revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
            settings jsonb NOT NULL CHECK (jsonb_typeof(settings)='object'),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE public.delivery_storefront_items (
            tenant_id uuid NOT NULL REFERENCES public.delivery_storefronts(tenant_id) ON DELETE CASCADE,
            variant_id uuid NOT NULL,
            product_revision integer NOT NULL CHECK (product_revision>0),
            variant_revision integer NOT NULL CHECK (variant_revision>0),
            label varchar(160) NOT NULL CHECK (length(trim(label))>0),
            description varchar(600),
            kind varchar(20) NOT NULL CHECK (kind IN ('water','prepared_food','grocery','household')),
            PRIMARY KEY (tenant_id,variant_id),
            FOREIGN KEY (tenant_id,variant_id) REFERENCES public.commercial_variants(tenant_id,id) ON DELETE CASCADE
        );
        CREATE TABLE public.delivery_storefront_orders (
            tenant_id uuid NOT NULL,
            order_id uuid NOT NULL,
            tracking_digest varchar(64) NOT NULL CHECK (tracking_digest ~ '^[0-9a-f]{64}$'),
            payment_preference varchar(20) NOT NULL CHECK (payment_preference IN ('cash','debit_card','credit_card','pix')),
            PRIMARY KEY (tenant_id,order_id),
            UNIQUE (tenant_id,tracking_digest),
            FOREIGN KEY (tenant_id,order_id) REFERENCES public.delivery_order_details(tenant_id,order_id) ON DELETE CASCADE
        );
    """)
    for table, grants in (
        ("delivery_storefronts", "SELECT, INSERT, UPDATE"),
        ("delivery_storefront_items", "SELECT, INSERT, DELETE"),
        ("delivery_storefront_orders", "SELECT, INSERT"),
    ):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
        """)
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC, app_support, app_runtime")
        op.execute(f"GRANT {grants} ON public.{table} TO app_runtime")
    op.execute("""
        CREATE FUNCTION public.resolve_delivery_storefront(requested_slug text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $function$
            SELECT tenant_id FROM public.delivery_storefronts
            WHERE slug=requested_slug AND enabled
        $function$;
        REVOKE ALL ON FUNCTION public.resolve_delivery_storefront(text) FROM PUBLIC, app_support;
        GRANT EXECUTE ON FUNCTION public.resolve_delivery_storefront(text) TO app_runtime;
    """)


def downgrade():
    op.execute("""
        DO $block$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_storefront_orders) OR
               EXISTS (SELECT 1 FROM public.delivery_storefronts WHERE enabled) THEN
                RAISE EXCEPTION 'Despublique as lojas e preserve os comprovantes de Delivery antes de reverter';
            END IF;
        END $block$;
        DROP FUNCTION public.resolve_delivery_storefront(text);
        DROP TABLE public.delivery_storefront_orders;
        DROP TABLE public.delivery_storefront_items;
        DROP TABLE public.delivery_storefronts;
    """)
