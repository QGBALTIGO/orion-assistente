"""Keep optional per-neighborhood terms outside the legacy settings document."""
from alembic import op


revision = "0112_delivery_area_terms"
down_revision = "0111_delivery_customer_addresses"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_logistics_policies ADD COLUMN area_terms jsonb NOT NULL
        DEFAULT '[]'::jsonb CHECK (jsonb_typeof(area_terms)='array')""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.delivery_logistics_policies WHERE area_terms<>'[]'::jsonb)
        THEN RAISE EXCEPTION 'Preserve as condições de entrega por bairro'; END IF;
        END $$;
        ALTER TABLE public.delivery_logistics_policies DROP COLUMN area_terms""")
