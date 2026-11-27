"""Record Delivery checkout instructions independently of payment receipts."""
from alembic import op


revision = "0110_delivery_checkout_options"
down_revision = "0109_delivery_catalog_editor"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.delivery_storefront_items
            ADD COLUMN sort_order smallint NOT NULL DEFAULT 0 CHECK (sort_order BETWEEN 0 AND 199);
        ALTER TABLE public.delivery_storefront_orders
            ADD COLUMN buyer_note varchar(300),
            ADD COLUMN change_for_cents bigint,
            ADD CONSTRAINT delivery_storefront_change_for_cash
                CHECK (change_for_cents IS NULL OR (
                    payment_preference = 'cash' AND change_for_cents BETWEEN 1 AND 1000000000));
    """)


def downgrade():
    op.execute("""
        ALTER TABLE public.delivery_storefront_orders
            DROP COLUMN change_for_cents, DROP COLUMN buyer_note;
        ALTER TABLE public.delivery_storefront_items DROP COLUMN sort_order;
    """)
