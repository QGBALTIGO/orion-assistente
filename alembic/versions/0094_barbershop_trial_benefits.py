"""Durable single-use Orion trial benefits."""
from alembic import op

revision = "0094_barbershop_trial_benefits"
down_revision = "0093_dental_reception"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE public.orion_plan_trial_requests ALTER COLUMN whatsapp_e164 DROP NOT NULL;
        CREATE TABLE public.orion_trial_benefits (
            identity_digest text PRIMARY KEY CHECK (identity_digest ~ '^[0-9a-f]{64}$'),
            recorded_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE public.orion_trial_benefits ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.orion_trial_benefits FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.orion_trial_benefits FROM PUBLIC, app_runtime;
        GRANT SELECT, INSERT ON public.orion_trial_benefits TO app_support;
        CREATE POLICY orion_trial_benefits_support ON public.orion_trial_benefits
            FOR ALL TO app_support USING (true) WITH CHECK (true);
        CREATE POLICY orion_trial_benefits_migrator ON public.orion_trial_benefits
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
    """)


def downgrade():
    raise RuntimeError("Trial benefit history must survive application rollback")
