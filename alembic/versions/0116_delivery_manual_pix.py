"""Optional merchant Pix keys and immutable instructions per order."""
from alembic import op

revision = "0116_delivery_manual_pix"
down_revision = "0115_delivery_driver_portal"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_pix_settings (
            tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
            revision integer NOT NULL CHECK(revision>0), key_type text CHECK(key_type IN ('cpf','cnpj','phone','email','random')),
            encrypted_key bytea, holder text, suffix text,
            updated_by uuid NOT NULL REFERENCES public.panel_accounts(id), updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK ((encrypted_key IS NULL AND key_type IS NULL AND holder IS NULL AND suffix IS NULL)
                OR (encrypted_key IS NOT NULL AND key_type IS NOT NULL AND holder IS NOT NULL AND suffix IS NOT NULL))
        );
        CREATE TABLE public.delivery_pix_orders (
            tenant_id uuid NOT NULL, order_id uuid NOT NULL, encrypted_key bytea, holder text,
            confirmed_by uuid REFERENCES public.panel_accounts(id), confirmed_at timestamptz,
            PRIMARY KEY(tenant_id,order_id),
            FOREIGN KEY(tenant_id,order_id) REFERENCES public.delivery_order_details(tenant_id,order_id) ON DELETE CASCADE,
            CHECK ((confirmed_at IS NULL)=(confirmed_by IS NULL)), CHECK ((encrypted_key IS NULL)=(holder IS NULL))
        );
    """)
    for table in ("delivery_pix_settings", "delivery_pix_orders"):
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
                USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
            CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING(true) WITH CHECK(true);
            REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support;
            GRANT SELECT,INSERT,UPDATE ON public.{table} TO app_runtime;
        """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.delivery_pix_settings) OR EXISTS (SELECT 1 FROM public.delivery_pix_orders)
        THEN RAISE EXCEPTION 'Preserve a configuração e o histórico de Pix'; END IF;
        END $$;
        DROP TABLE public.delivery_pix_orders,public.delivery_pix_settings;""")
