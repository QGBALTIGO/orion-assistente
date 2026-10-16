"""Persistent website accounts, verification and password recovery.

Revision ID: 0008_panel_accounts
Revises: 0007_advanced
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_panel_accounts"
down_revision = "0007_advanced"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "panel_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('member', 'admin')", name="ck_panel_account_role"),
        sa.CheckConstraint("auth_version > 0", name="ck_panel_account_auth_version"),
        sa.CheckConstraint("email = lower(email)", name="ck_panel_account_lower_email"),
    )
    op.create_table(
        "panel_email_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", UUID(as_uuid=True),
                  sa.ForeignKey("panel_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("purpose IN ('verify', 'reset')", name="ck_panel_email_purpose"),
    )
    op.create_index("ix_panel_token_account", "panel_email_tokens", ["account_id"])
    for table in ("panel_accounts", "panel_email_tokens"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON {table} FROM app_runtime")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO app_support")
        op.execute(f"CREATE POLICY {table}_support ON {table} FOR ALL TO app_support "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")


def downgrade():
    op.drop_table("panel_email_tokens")
    op.drop_table("panel_accounts")
