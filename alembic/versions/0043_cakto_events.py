"""Additive Cakto webhook receipt inbox. No customer data or automatic entitlement changes."""
from alembic import op
import sqlalchemy as sa

revision = "0043_cakto_events"
down_revision = "0042_admin_account_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cakto_webhook_events",
        sa.Column("delivery_digest", sa.String(64), primary_key=True),
        sa.Column("external_order_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("reported_status", sa.String(40), nullable=False),
        sa.Column("external_offer_id", sa.String(80), nullable=True),
        sa.Column("external_subscription_id", sa.String(255), nullable=True),
        sa.Column("verification_state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("verification_state IN ('pending','verified','mismatch','superseded','manual_review','failed')",
                           name="ck_cakto_verification_state"),
        sa.CheckConstraint("attempts >= 0", name="ck_cakto_attempts"),
    )
    op.create_index("ix_cakto_events_retry", "cakto_webhook_events",
                    ["next_attempt_at", "received_at"],
                    postgresql_where=sa.text("verification_state='pending'"))
    op.create_index("ix_cakto_events_order", "cakto_webhook_events", ["external_order_id"])
    op.execute("ALTER TABLE cakto_webhook_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cakto_webhook_events FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON cakto_webhook_events FROM PUBLIC")
    op.execute("REVOKE ALL ON cakto_webhook_events FROM app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON cakto_webhook_events TO app_support")
    op.execute("CREATE POLICY cakto_events_support ON cakto_webhook_events FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute("CREATE POLICY cakto_events_migrator ON cakto_webhook_events FOR ALL TO app_migrator USING (true) WITH CHECK (true)")


def downgrade():
    op.drop_table("cakto_webhook_events")
