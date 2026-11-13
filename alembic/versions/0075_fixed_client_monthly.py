"""0075: optional exact monthly cadence for fixed-client appointment series.

Additive column only: previous customer appointments and their financial history
are left untouched, with the same tenant RLS and booking overlap constraints.
"""
from alembic import op
import sqlalchemy as sa

revision = "0075_fixed_client_monthly"
down_revision = "0074_fixed_client_series"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("appointment_series", sa.Column("monthly_visits", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("ck_appointment_series_monthly", "appointment_series",
                               "monthly_visits IS NULL OR monthly_visits IN (2,4)")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM appointment_series WHERE monthly_visits IS NOT NULL) THEN
        RAISE EXCEPTION 'Monthly fixed series exist: use forward recovery, not destructive rollback';
      END IF;
    END $$""")
    op.drop_constraint("ck_appointment_series_monthly", "appointment_series", type_="check")
    op.drop_column("appointment_series", "monthly_visits")
