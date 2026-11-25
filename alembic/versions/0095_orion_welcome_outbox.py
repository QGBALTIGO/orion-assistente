"""Consent and durable delivery receipts for Orion's administrative welcome."""
from alembic import op

revision = "0095_orion_welcome_outbox"
down_revision = "0094_barbershop_trial_benefits"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.orion_welcome_outbox (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL UNIQUE REFERENCES public.tenants(id),
            account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
            recipient text NOT NULL CHECK (recipient ~ '^[+]55[1-9][0-9]{9,10}$'),
            consented_at timestamptz NOT NULL DEFAULT now(),
            channel_id uuid,
            channel_tenant_id uuid,
            body_digest text,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','sending','accepted','delivered','failed','uncertain','revoked','coalesced')),
            started_at timestamptz,
            checked_at timestamptz,
            revoked_at timestamptz
        );
        CREATE INDEX orion_welcome_queued ON public.orion_welcome_outbox (consented_at) WHERE status='queued';
        CREATE INDEX orion_welcome_recipient ON public.orion_welcome_outbox (recipient, started_at);
        ALTER TABLE public.orion_welcome_outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.orion_welcome_outbox FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.orion_welcome_outbox FROM PUBLIC, app_runtime;
        GRANT SELECT, INSERT, UPDATE ON public.orion_welcome_outbox TO app_support;
        CREATE POLICY orion_welcome_support ON public.orion_welcome_outbox
            FOR ALL TO app_support USING (true) WITH CHECK (true);
        CREATE POLICY orion_welcome_migrator ON public.orion_welcome_outbox
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
    """)


def downgrade():
    raise RuntimeError("Welcome consent and delivery receipts must survive application rollback")
