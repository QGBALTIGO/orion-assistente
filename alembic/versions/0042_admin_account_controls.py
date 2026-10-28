"""Scoped customer-account lifecycle: suspension, reversible 30-day deletion and audit.

Physical erasure requires a separate retention-aware purge workflow; this
migration does not cascade-delete any customer, tenant or financial record.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision="0042_admin_account_controls"
down_revision="0041_quick_onboarding_activation"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("admin_account_controls",
        sa.Column("account_id",UUID(as_uuid=True),sa.ForeignKey("panel_accounts.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("state",sa.String(16),nullable=False,server_default="active"),
        sa.Column("reason",sa.String(500),nullable=True),
        sa.Column("archived_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("purge_after",sa.DateTime(timezone=True),nullable=True),
        sa.Column("purged_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("legal_hold",sa.Boolean(),nullable=False,server_default=sa.text("false")),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.CheckConstraint("state IN ('active','suspended','archived','erased')",name="ck_admin_account_state"),
        sa.CheckConstraint("(state='archived') = (archived_at IS NOT NULL AND purge_after IS NOT NULL)",name="ck_admin_account_archive_dates"),
        sa.CheckConstraint("(state='erased') = (purged_at IS NOT NULL)",name="ck_admin_account_erasure"),
    )
    op.create_table("admin_account_audit",
        sa.Column("id",sa.BigInteger(),sa.Identity(),primary_key=True),
        sa.Column("actor_id",UUID(as_uuid=True),nullable=True),
        sa.Column("account_id",UUID(as_uuid=True),nullable=False),
        sa.Column("action",sa.String(32),nullable=False),
        sa.Column("reason",sa.String(500),nullable=True),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("metadata",JSONB(),nullable=False,server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_admin_controls_purge", "admin_account_controls",["purge_after"],
                    postgresql_where=sa.text("state='archived' AND NOT legal_hold"))
    op.create_index("ix_admin_audit_account", "admin_account_audit",["account_id","occurred_at"])
    for table in ("admin_account_controls","admin_account_audit"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON {table} FROM app_runtime")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_support")
        op.execute(f"CREATE POLICY {table}_support ON {table} FOR ALL TO app_support USING (true) WITH CHECK (true)")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE admin_account_audit_id_seq TO app_support")

def downgrade():
    op.drop_table("admin_account_audit")
    op.drop_table("admin_account_controls")
