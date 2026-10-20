"""Extend the existing reviewed outbox with factual cancellation notices."""
from alembic import op

revision = "0026_cancelled_notification"
down_revision = "0025_inbox_control"
branch_labels = None
depends_on = None
EVENTS = "'booking_confirmed','reminder_24h','reminder_2h','reminder_1h','rescheduled','inactive_60d','cancelled_7d','birthday_7d','birthday_2d','loyalty_reward','after_service','campaign'"


def upgrade():
    op.execute("ALTER TABLE public.automation_jobs DROP CONSTRAINT automation_jobs_event_check")
    op.execute(f"ALTER TABLE public.automation_jobs ADD CONSTRAINT automation_jobs_event_check CHECK(event IN ({EVENTS},'cancelled'))")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.automation_jobs WHERE event='cancelled')
      OR EXISTS(SELECT 1 FROM public.management_preferences WHERE preferences->'automations'->'rules' @> '[{"event":"cancelled"}]'::jsonb) THEN
      RAISE EXCEPTION 'Preserve cancellation notifications before downgrade'; END IF; END $$""")
    op.execute("ALTER TABLE public.automation_jobs DROP CONSTRAINT automation_jobs_event_check")
    op.execute(f"ALTER TABLE public.automation_jobs ADD CONSTRAINT automation_jobs_event_check CHECK(event IN ({EVENTS}))")
