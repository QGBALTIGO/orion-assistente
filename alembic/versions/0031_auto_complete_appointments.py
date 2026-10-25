"""Turn on prospective, tenant-isolated automatic appointment completion.

Activation is prospective: already-finished bookings are never silently
backfilled. The trusted support scanner can discover only tenant/appointment IDs,
not appointment details or customer information. A tenant-scoped app_runtime
transaction is required for every actual state change.

Revision ID: 0031_auto_complete_appointments
Revises: 0030_legacy_admin_identity
"""
from alembic import op

revision = "0031_auto_complete_appointments"
down_revision = "0030_legacy_admin_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.tenants
          ADD COLUMN auto_complete_after timestamptz NOT NULL DEFAULT clock_timestamp();
        CREATE INDEX ix_auto_complete_due
          ON public.appointments(end_at,tenant_id,id)
          WHERE status='confirmed';

        CREATE FUNCTION public.orion_due_auto_completions(batch_limit integer DEFAULT 100)
          RETURNS TABLE(tenant_id uuid,appointment_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path=pg_catalog,public
        AS $func$
          SELECT a.tenant_id,a.id
          FROM public.appointments a
          JOIN public.tenants t ON t.id=a.tenant_id
          WHERE a.status='confirmed' AND t.status='active'
            AND a.end_at <= now()
            AND a.end_at >= t.auto_complete_after
            AND $1 BETWEEN 1 AND 200
          ORDER BY a.end_at,a.tenant_id,a.id
          LIMIT LEAST(GREATEST($1,1),200)
        $func$;

        REVOKE ALL ON FUNCTION public.orion_due_auto_completions(integer)
          FROM PUBLIC,app_runtime;
        GRANT EXECUTE ON FUNCTION public.orion_due_auto_completions(integer)
          TO app_support;
    """)


def downgrade():
    raise RuntimeError(
        "Automatic completion has a durable activation boundary. "
        "Disable the worker flag and use a reviewed forward migration instead."
    )
