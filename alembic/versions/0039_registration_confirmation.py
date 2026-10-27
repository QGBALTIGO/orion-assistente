"""Require email proof only for new registrations; preserve existing accounts."""
from alembic import op

revision = '0039_registration_confirmation'
down_revision = '0038_email_outbox'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE panel_accounts ADD COLUMN registration_pending boolean NOT NULL DEFAULT false;
        ALTER TABLE panel_accounts ADD CONSTRAINT ck_panel_registration_pending CHECK (
            NOT registration_pending OR
            (auth_provider='password' AND role='member' AND verified_at IS NULL));
    """)


def downgrade():
    raise RuntimeError('registration_confirmation_forward_only')
