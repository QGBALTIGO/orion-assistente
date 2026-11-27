"""Instructions for cash collection and provenance of manual orders."""
from alembic import op


revision = "0113_delivery_order_intake"
down_revision = "0112_delivery_area_terms"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_order_details
        ADD COLUMN intake_channel text CHECK (intake_channel IN ('pos','phone','attendant')),
        ADD COLUMN tender_preference text CHECK (tender_preference IN ('cash','pix','debit_card','credit_card')),
        ADD COLUMN change_for_cents bigint CHECK (change_for_cents IS NULL OR
            (tender_preference IS NOT NULL AND tender_preference='cash' AND change_for_cents BETWEEN 1 AND 1000000000)),
        ADD COLUMN operator_note varchar(300);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.delivery_order_details WHERE
            intake_channel IS NOT NULL OR tender_preference IS NOT NULL OR operator_note IS NOT NULL)
        THEN RAISE EXCEPTION 'Preserve as instruções de cobrança e a origem dos pedidos'; END IF;
        END $$;
        ALTER TABLE public.delivery_order_details DROP COLUMN intake_channel,DROP COLUMN tender_preference,
        DROP COLUMN change_for_cents,DROP COLUMN operator_note""")
