"""Add alternative commission rules without mutating historical payouts.

Revision ID: 0033_professional_service_commissions
Revises: 0032_variable_service_prices
"""
from alembic import op

revision = "0033_professional_service_commissions"
down_revision = "0032_variable_service_prices"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.professionals
            ADD COLUMN commission_mode varchar(16) NOT NULL DEFAULT 'percent',
            ADD COLUMN commission_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN commission_plan_version integer NOT NULL DEFAULT 1,
            ADD CONSTRAINT ck_professional_commission_mode
                CHECK (commission_mode IN ('percent','per_service')),
            ADD CONSTRAINT ck_professional_commission_rules
                CHECK (jsonb_typeof(commission_rules)='object'
                       AND commission_plan_version>=1);

        ALTER TABLE public.appointments
            ADD COLUMN commission_mode_snapshot varchar(16),
            ADD COLUMN commission_breakdown jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD CONSTRAINT ck_appointment_commission_breakdown
                CHECK (jsonb_typeof(commission_breakdown)='array');

        ALTER TABLE public.appointments
            DROP CONSTRAINT ck_appointment_commission;

        ALTER TABLE public.appointments
            ADD CONSTRAINT ck_appointment_commission CHECK (
                (commission_cents IS NULL
                 AND commission_percent_snapshot IS NULL
                 AND commission_mode_snapshot IS NULL)
                OR
                (commission_cents IS NOT NULL
                 AND commission_cents BETWEEN 0 AND total_cents
                 AND (
                    (commission_mode_snapshot IS NULL
                     AND commission_percent_snapshot BETWEEN 0 AND 100)
                    OR (commission_mode_snapshot='percent'
                        AND commission_percent_snapshot BETWEEN 0 AND 100)
                    OR (commission_mode_snapshot='per_service'
                        AND commission_percent_snapshot IS NULL)
                 ))
            );
    """)


def downgrade():
    raise RuntimeError(
        "Commission history is immutable. A reviewed forward migration is required."
    )
