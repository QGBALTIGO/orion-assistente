"""Record delivery addresses, fees and dispatch in the existing order ledger."""
from alembic import op


revision = "0101_delivery_dispatch"
down_revision = "0100_delivery_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_order_details
            ADD COLUMN address jsonb,
            ADD COLUMN delivery_fee_cents integer NOT NULL DEFAULT 0,
            ADD COLUMN courier_name varchar(120),
            ADD COLUMN dispatched_at timestamptz,
            DROP CONSTRAINT delivery_order_details_mode_check,
            ADD CONSTRAINT delivery_order_details_mode_check CHECK (
                (mode IN ('counter','pickup') AND address IS NULL AND delivery_fee_cents=0 AND courier_name IS NULL)
                OR (mode='delivery' AND address IS NOT NULL AND jsonb_typeof(address)='object'
                    AND delivery_fee_cents BETWEEN 0 AND 1000000000)),
            ADD CONSTRAINT delivery_order_details_dispatch_check CHECK ((courier_name IS NULL)=(dispatched_at IS NULL));
        ALTER TABLE public.commercial_orders DROP CONSTRAINT ck_commercial_order_state,
            ADD CONSTRAINT ck_commercial_order_state CHECK (
                origin IN ('commerce','delivery') AND payment_status='not_verified' AND revision>0
                AND (status IN ('draft','confirmed','cancelled')
                    OR (origin='delivery' AND status IN ('dispatched','fulfilled'))));
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.delivery_order_details WHERE mode='delivery') THEN
                RAISE EXCEPTION 'Preserve os endereços e os despachos ao desativar a homologação';
            END IF;
        END $$;
        ALTER TABLE public.delivery_order_details
            DROP CONSTRAINT delivery_order_details_dispatch_check,
            DROP CONSTRAINT delivery_order_details_mode_check,
            DROP COLUMN address, DROP COLUMN delivery_fee_cents, DROP COLUMN courier_name, DROP COLUMN dispatched_at,
            ADD CONSTRAINT delivery_order_details_mode_check CHECK (mode IN ('counter','pickup'));
        ALTER TABLE public.commercial_orders DROP CONSTRAINT ck_commercial_order_state,
            ADD CONSTRAINT ck_commercial_order_state CHECK (
                origin IN ('commerce','delivery') AND payment_status='not_verified' AND revision>0
                AND (status IN ('draft','confirmed','cancelled') OR (origin='delivery' AND status='fulfilled')));
    """)
