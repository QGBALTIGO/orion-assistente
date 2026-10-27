"""Private, encrypted transactional email outbox; never backfill sends.

Revision ID: 0038_email_outbox
Revises: 0037_recurring_goals
"""
from alembic import op

revision = "0038_email_outbox"
down_revision = "0037_recurring_goals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE panel_email_outbox (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kind varchar(32) NOT NULL CHECK(kind IN ('verify','reset','password_changed','team_invite','team_access_approved','team_access_revoked')),
            recipient_digest char(64) NOT NULL,
            account_id uuid REFERENCES panel_accounts(id) ON DELETE SET NULL,
            tenant_id uuid REFERENCES tenants(id) ON DELETE RESTRICT,
            invitation_id uuid REFERENCES management_team_invitations(id) ON DELETE RESTRICT,
            token_id uuid REFERENCES panel_email_tokens(id) ON DELETE SET NULL,
            dedupe_key varchar(200) NOT NULL UNIQUE,
            payload_enc text,
            status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dead','uncertain','suppressed','expired')),
            attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8),
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            first_attempt_at timestamptz,
            expires_at timestamptz NOT NULL,
            provider_message_id varchar(200),
            last_error_code varchar(64),
            created_at timestamptz NOT NULL DEFAULT now(),
            accepted_at timestamptz,
            CHECK(length(dedupe_key) BETWEEN 1 AND 200),
            CHECK(expires_at > created_at),
            CHECK(status <> 'accepted' OR (provider_message_id IS NOT NULL AND accepted_at IS NOT NULL)),
            CHECK(status <> 'pending' OR payload_enc IS NOT NULL)
        );
        CREATE INDEX ix_panel_email_due ON panel_email_outbox(next_attempt_at,id) WHERE status='pending';
        CREATE INDEX ix_panel_email_recipient ON panel_email_outbox(recipient_digest,created_at);
        CREATE INDEX ix_panel_email_invitation ON panel_email_outbox(invitation_id,kind);
        CREATE UNIQUE INDEX uq_panel_email_invitation ON panel_email_outbox(invitation_id) WHERE kind='team_invite';
        ALTER TABLE panel_email_outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE panel_email_outbox FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON panel_email_outbox FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT,UPDATE ON panel_email_outbox TO app_support;
        CREATE POLICY panel_email_outbox_support ON panel_email_outbox FOR ALL TO app_support USING(true) WITH CHECK(true);
        CREATE POLICY panel_email_outbox_migrator ON panel_email_outbox FOR ALL TO app_migrator USING(true) WITH CHECK(true);
    """)


def downgrade():
    # Dropping queued secrets requires a separate operator recovery decision.
    raise RuntimeError("email_outbox_forward_only")
