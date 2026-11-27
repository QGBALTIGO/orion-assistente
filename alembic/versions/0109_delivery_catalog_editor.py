"""Add catalog presentation and channel controls for Delivery."""
from alembic import op


revision = "0109_delivery_catalog_editor"
down_revision = "0108_delivery_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.delivery_product_profiles (
            tenant_id uuid NOT NULL,
            product_id uuid NOT NULL,
            brand varchar(100) CHECK (brand IS NULL OR length(btrim(brand)) > 0),
            preset_image varchar(32) CHECK (preset_image IS NULL OR preset_image ~ '^[a-z0-9-]+$'),
            photo_url varchar(140),
            PRIMARY KEY (tenant_id, product_id),
            FOREIGN KEY (tenant_id, product_id)
                REFERENCES public.commercial_products (tenant_id, id) ON DELETE CASCADE,
            CHECK (preset_image IS NULL OR photo_url IS NULL),
            CHECK (photo_url IS NULL OR photo_url ~ (
                '^/media/' || tenant_id::text ||
                '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[.]webp$'))
        );
        ALTER TABLE public.delivery_product_profiles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.delivery_product_profiles FORCE ROW LEVEL SECURITY;
        CREATE POLICY delivery_product_profiles_tenant ON public.delivery_product_profiles TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY delivery_product_profiles_migrator ON public.delivery_product_profiles
            TO app_migrator USING (true) WITH CHECK (true);
        REVOKE ALL ON public.delivery_product_profiles FROM PUBLIC, app_support;
        GRANT SELECT, INSERT, UPDATE ON public.delivery_product_profiles TO app_runtime;

        ALTER TABLE public.delivery_variant_details
            ADD COLUMN sell_internally boolean NOT NULL DEFAULT true,
            ADD COLUMN allow_delivery boolean NOT NULL DEFAULT true,
            ADD COLUMN allow_pickup boolean NOT NULL DEFAULT true,
            ADD COLUMN allow_storefront boolean NOT NULL DEFAULT true,
            ADD COLUMN exchange_container_id uuid,
            ADD CONSTRAINT delivery_variant_exchange_container_fk
                FOREIGN KEY (tenant_id, exchange_container_id)
                REFERENCES public.delivery_containers (tenant_id, id);
    """)


def downgrade():
    op.execute("""
        ALTER TABLE public.delivery_variant_details
            DROP COLUMN exchange_container_id, DROP COLUMN allow_storefront,
            DROP COLUMN allow_pickup, DROP COLUMN allow_delivery, DROP COLUMN sell_internally;
        DROP TABLE public.delivery_product_profiles;
    """)
