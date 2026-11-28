"""Separate courier reservation from physical dispatch, preserving existing assignments."""
from alembic import op

revision = "0118_delivery_route_batches"
down_revision = "0117_delivery_whatsapp_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.delivery_driver_assignments
          DROP CONSTRAINT delivery_driver_assignments_status_check,
          ADD CONSTRAINT delivery_driver_assignments_status_check
            CHECK(status IN ('offered','reserved','accepted','arrived','refused','fulfilled','released')),
          ADD COLUMN route_batch_id uuid,
          ADD COLUMN route_position integer,
          ADD CONSTRAINT ck_delivery_route_position CHECK (
            (route_batch_id IS NULL AND route_position IS NULL) OR
            (route_batch_id IS NOT NULL AND route_position IS NOT NULL AND route_position BETWEEN 1 AND 5));
        CREATE UNIQUE INDEX delivery_driver_batch_position
          ON public.delivery_driver_assignments(tenant_id,route_batch_id,route_position)
          WHERE route_batch_id IS NOT NULL;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.delivery_driver_assignments
            WHERE status='reserved' OR route_batch_id IS NOT NULL)
          THEN RAISE EXCEPTION 'Preserve as reservas e os registros de rotas'; END IF;
        END $$;
        DROP INDEX public.delivery_driver_batch_position;
        ALTER TABLE public.delivery_driver_assignments
          DROP CONSTRAINT ck_delivery_route_position,
          DROP COLUMN route_batch_id, DROP COLUMN route_position,
          DROP CONSTRAINT delivery_driver_assignments_status_check,
          ADD CONSTRAINT delivery_driver_assignments_status_check
            CHECK(status IN ('offered','accepted','arrived','refused','fulfilled','released'));
    """)
