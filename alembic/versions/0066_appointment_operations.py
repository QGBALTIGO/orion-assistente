"""Internal booking provenance and append-only extra-service metadata.

Existing bookings keep the standard mode and booked items. No appointment,
price, commission, availability rule or external notification is changed.
"""
from alembic import op

revision = "0066_appointment_operations"
down_revision = "0065_subscription_sent_messages"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
        ALTER TABLE public.appointments
          ADD COLUMN booking_mode varchar(16) NOT NULL DEFAULT 'standard',
          ADD COLUMN created_by_account_id uuid,
          ADD COLUMN actual_duration_min integer,
          ADD CONSTRAINT ck_appointment_booking_mode
            CHECK (booking_mode IN ('standard','exceptional','retroactive')),
          ADD CONSTRAINT ck_appointment_actual_duration
            CHECK (actual_duration_min IS NULL OR actual_duration_min BETWEEN 1 AND 1440);

        ALTER TABLE public.appointment_items
          ADD COLUMN item_origin varchar(8) NOT NULL DEFAULT 'booked',
          ADD COLUMN added_at timestamptz,
          ADD COLUMN added_by_account_id uuid,
          ADD COLUMN added_duration_min integer,
          ADD CONSTRAINT ck_appointment_item_origin
            CHECK (item_origin IN ('booked','extra')),
          ADD CONSTRAINT ck_appointment_item_added_duration
            CHECK (added_duration_min IS NULL OR added_duration_min BETWEEN 0 AND 1440),
          ADD CONSTRAINT ck_appointment_item_extra_metadata
            CHECK (item_origin <> 'extra' OR (added_at IS NOT NULL AND added_duration_min IS NOT NULL));

        ALTER TABLE public.appointment_history
          DROP CONSTRAINT ck_history_event,
          ADD CONSTRAINT ck_history_event CHECK (event IN (
            'hold_created','hold_expired','confirmed','cancelled','rescheduled',
            'completed','no_show','exceptional_created','retroactive_created','services_added'));
    """)
    # Actor UUIDs are audit snapshots, matching schedule_blocks. Token-based
    # support has no account UUID: the verified role stays in history.details.
    # Keep all existing tenant FKs, FORCE RLS policies and grants unchanged.
    # In particular, future exceptional bookings still use the original GiST
    # overlap constraint, and existing item duration snapshots remain > 0.
    # A preserved reservation can end before the recorded services do. Filter
    # at discovery time as well as under the motor lock so those not-yet-due
    # rows cannot occupy every slot in the bounded worker page.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.orion_due_auto_completions(batch_limit integer DEFAULT 100)
          RETURNS TABLE(tenant_id uuid,appointment_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path=pg_catalog,public
        AS $func$
          SELECT a.tenant_id,a.id
          FROM public.appointments a
          JOIN public.tenants t ON t.id=a.tenant_id
          CROSS JOIN LATERAL (
            SELECT GREATEST(a.end_at,a.start_at+make_interval(mins => COALESCE(
              a.actual_duration_min,
              (SELECT sum(COALESCE(i.added_duration_min,i.duration_snapshot_min))::integer
                 FROM public.appointment_items i
                 WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id),0))) AS completion_due_at
          ) timing
          WHERE a.status='confirmed' AND t.status='active'
            AND a.end_at <= now()
            AND timing.completion_due_at <= now()
            AND a.end_at >= t.auto_complete_after
            AND NOT EXISTS (
              SELECT 1 FROM public.appointment_items i
              WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id
                AND i.price_mode_snapshot='from')
            AND $1 BETWEEN 1 AND 200
          ORDER BY timing.completion_due_at,a.tenant_id,a.id
          LIMIT LEAST(GREATEST($1,1),200)
        $func$;
        REVOKE ALL ON FUNCTION public.orion_due_auto_completions(integer) FROM PUBLIC,app_runtime;
        GRANT EXECUTE ON FUNCTION public.orion_due_auto_completions(integer) TO app_support;
    """)


def downgrade():
    raise RuntimeError("Preserve internal booking and extra-service audit; use a forward migration")
