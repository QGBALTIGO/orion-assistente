"""Associate on-account due dates with Delivery's financial entries."""
from alembic import op


revision = "0103_delivery_credit"
down_revision = "0102_delivery_containers"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_order_details ADD COLUMN payment_due_date date,
            ADD CONSTRAINT delivery_order_credit_receipt CHECK (payment_due_date IS NULL OR financial_entry_id IS NOT NULL);
        CREATE INDEX ix_delivery_credit_due ON public.delivery_order_details (tenant_id,payment_due_date,order_id)
            WHERE payment_due_date IS NOT NULL;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_order_details WHERE payment_due_date IS NOT NULL) THEN
                RAISE EXCEPTION 'Preserve os vencimentos a prazo ao desativar a homologação';
            END IF;
        END $$;
        DROP INDEX public.ix_delivery_credit_due;
        ALTER TABLE public.delivery_order_details DROP CONSTRAINT delivery_order_credit_receipt,
            DROP COLUMN payment_due_date;
    """)
