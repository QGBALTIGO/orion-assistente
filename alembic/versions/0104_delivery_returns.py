"""Link a Delivery return to its separate financial reversal."""
from alembic import op


revision = "0104_delivery_returns"
down_revision = "0103_delivery_credit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_order_details ADD COLUMN return_financial_entry_id uuid,
            ADD CONSTRAINT delivery_return_receipt_fk FOREIGN KEY (tenant_id,return_financial_entry_id)
                REFERENCES public.financial_entries(tenant_id,id),
            ADD CONSTRAINT delivery_return_receipt_unique UNIQUE (tenant_id,return_financial_entry_id),
            ADD CONSTRAINT delivery_return_receipt_check CHECK (return_financial_entry_id IS NULL
                OR (financial_entry_id IS NOT NULL AND return_financial_entry_id<>financial_entry_id));
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_order_details WHERE return_financial_entry_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Preserve os estornos ao desativar a homologação';
            END IF;
        END $$;
        ALTER TABLE public.delivery_order_details DROP COLUMN return_financial_entry_id;
    """)
