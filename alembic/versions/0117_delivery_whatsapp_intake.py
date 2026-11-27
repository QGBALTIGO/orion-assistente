"""Identify orders confirmed by the Delivery WhatsApp assistant."""
from alembic import op

revision = "0117_delivery_whatsapp_intake"
down_revision = "0116_delivery_manual_pix"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_order_details
          DROP CONSTRAINT delivery_order_details_intake_channel_check,
          ADD CONSTRAINT delivery_order_details_intake_channel_check
          CHECK (intake_channel IN ('pos','phone','attendant','whatsapp'));
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.delivery_order_details WHERE intake_channel='whatsapp')
          THEN RAISE EXCEPTION 'Preserve a origem dos pedidos pelo WhatsApp'; END IF;
        END $$;
        ALTER TABLE public.delivery_order_details
          DROP CONSTRAINT delivery_order_details_intake_channel_check,
          ADD CONSTRAINT delivery_order_details_intake_channel_check
          CHECK (intake_channel IN ('pos','phone','attendant'));
    """)
