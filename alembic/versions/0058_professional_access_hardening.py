"""Protect professional calendar blocks and unique professional email identity."""
from alembic import op

revision = "0058_professional_scope"
down_revision = "0057_professional_portal"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
        -- Existing management and WhatsApp blocks are the company's by default.
        -- A provider's own blocks carry its verified Orion account identity.
        ALTER TABLE schedule_blocks
            ADD COLUMN origin varchar(16) NOT NULL DEFAULT 'company',
            ADD COLUMN created_by_account_id uuid NULL;
        ALTER TABLE schedule_blocks ADD CONSTRAINT ck_schedule_block_origin
            CHECK (
                (origin='company' AND created_by_account_id IS NULL) OR
                (origin='professional' AND created_by_account_id IS NOT NULL)
            );
        CREATE INDEX ix_schedule_blocks_origin
            ON schedule_blocks(tenant_id,professional_id,origin,created_by_account_id)
            WHERE origin='professional';

        -- One verified email can bind at most one professional per business.
        -- Refuse a migration if existing conflicting data would be silently lost.
        ALTER TABLE professional_portal_links
            ADD COLUMN can_manage_owner_blocks boolean NOT NULL DEFAULT false;
        ALTER TABLE professional_portal_links
            ADD CONSTRAINT uq_professional_portal_email UNIQUE(tenant_id,email);
    """)


def downgrade():
    raise RuntimeError("Professional authorization hardening requires forward-only audited recovery")
