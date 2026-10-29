"""0046: isolated direct payment attempts; no real charge, PII or card data in migration.

Revision ID: 0046_orion_direct_checkout
Revises: 0045_plan_trials_checkout
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0046_orion_direct_checkout"
down_revision = "0045_plan_trials_checkout"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "orion_direct_payment_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("intent_id", UUID(as_uuid=True),
                  sa.ForeignKey("orion_cakto_checkout_intents.id", ondelete="RESTRICT"),
                  nullable=False, unique=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True),
                  sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("method", sa.String(12), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="reserved"),
        sa.Column("external_order_id", sa.String(255), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("method IN ('pix','card')",
                           name="ck_orion_direct_method"),
        sa.CheckConstraint(
            "state IN ('reserved','created','declined','unknown','needs_review')",
            name="ck_orion_direct_state"),
    )
    op.create_index("ix_orion_direct_tenant", "orion_direct_payment_attempts",
                    ["tenant_id", "created_at"])
    op.execute("ALTER TABLE orion_direct_payment_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orion_direct_payment_attempts FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON orion_direct_payment_attempts FROM PUBLIC")
    op.execute("REVOKE ALL ON orion_direct_payment_attempts FROM app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON orion_direct_payment_attempts TO app_support")
    op.execute("""CREATE POLICY orion_direct_support ON orion_direct_payment_attempts
                  FOR ALL TO app_support USING (true) WITH CHECK (true)""")
    op.execute("""CREATE POLICY orion_direct_migrator ON orion_direct_payment_attempts
                  FOR ALL TO app_migrator USING (true) WITH CHECK (true)""")


def downgrade():
    raise RuntimeError("Irreversible payment attempt ledger: forward-only migration")
