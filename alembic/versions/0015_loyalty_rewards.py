"""Transactional loyalty earning, reward coupons and completion discounts.

Revision ID: 0015_loyalty_rewards
Revises: 0014_public_catalog
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0015_loyalty_rewards"
down_revision = "0014_public_catalog"
branch_labels = None
depends_on = None
TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"


def upgrade():
    op.add_column("appointments", sa.Column("original_total_cents", sa.Integer(), nullable=True))
    op.add_column("appointments", sa.Column("discount_cents", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_appointment_discount", "appointments", """
        discount_cents>=0 AND ((original_total_cents IS NULL AND discount_cents=0) OR
        (original_total_cents IS NOT NULL AND original_total_cents=total_cents+discount_cents))
    """)
    for column in (
        sa.Column("reward_type", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("reward_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("reward_value_cents", sa.Integer(), nullable=True),
        sa.Column("reward_service_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reward_valid_days", sa.Integer(), nullable=False, server_default="30"),
    ):
        op.add_column("loyalty_rules", column)
    op.create_foreign_key("fk_loyalty_reward_service", "loyalty_rules", "services", ["tenant_id", "reward_service_id"], ["tenant_id", "id"])
    op.drop_constraint("ck_loyalty_mode", "loyalty_rules", type_="check")
    op.create_check_constraint("ck_loyalty_mode", "loyalty_rules", "earning_mode IN ('visit','spend','both')")
    op.drop_constraint("ck_loyalty_points", "loyalty_rules", type_="check")
    op.create_check_constraint("ck_loyalty_points", "loyalty_rules", """
        points_per_visit BETWEEN 0 AND 10000 AND points_per_step BETWEEN 0 AND 10000 AND
        (NOT active OR (earning_mode IN ('visit','both') AND points_per_visit>0) OR
         (earning_mode IN ('spend','both') AND points_per_step>0))
    """)
    op.create_check_constraint("ck_loyalty_reward", "loyalty_rules", """
        reward_valid_days BETWEEN 1 AND 3650 AND reward_type IN ('manual','percent','fixed','free_service') AND
        ((reward_type='manual') OR
         (reward_type='percent' AND reward_percent IS NOT NULL AND reward_percent>0 AND reward_percent<=100) OR
         (reward_type='fixed' AND reward_value_cents IS NOT NULL AND reward_value_cents BETWEEN 1 AND 1000000000) OR
         (reward_type='free_service' AND reward_service_id IS NOT NULL))
    """)
    op.create_index("uq_loyalty_active_program", "loyalty_rules", ["tenant_id"], unique=True, postgresql_where=sa.text("active"))
    op.create_table("loyalty_coupons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("reward_type", sa.String(16), nullable=False),
        sa.Column("reward_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("reward_value_cents", sa.Integer(), nullable=True),
        sa.Column("reward_service_id", UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("points_spent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_appointment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("redemption_discount_cents", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_loyalty_coupon_scope"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_loyalty_coupon_code"),
        sa.UniqueConstraint("tenant_id", "redeemed_appointment_id", name="uq_loyalty_coupon_redemption"),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "rule_id"], ["loyalty_rules.tenant_id", "loyalty_rules.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "reward_service_id"], ["services.tenant_id", "services.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "redeemed_appointment_id"], ["appointments.tenant_id", "appointments.id"]),
        sa.CheckConstraint("rule_version>=1 AND version>=1 AND points_spent BETWEEN 1 AND 1000000 AND expires_at>issued_at", name="ck_coupon_values"),
        sa.CheckConstraint("""(reward_type='percent' AND reward_percent IS NOT NULL AND reward_percent>0 AND reward_percent<=100) OR
            (reward_type='fixed' AND reward_value_cents IS NOT NULL AND reward_value_cents BETWEEN 1 AND 1000000000) OR
            (reward_type='free_service' AND reward_service_id IS NOT NULL)""", name="ck_coupon_reward"),
        sa.CheckConstraint("""(status='available' AND redeemed_at IS NULL AND redeemed_appointment_id IS NULL AND redemption_discount_cents IS NULL) OR
            (status='redeemed' AND redeemed_at IS NOT NULL AND redeemed_appointment_id IS NOT NULL AND redemption_discount_cents IS NOT NULL AND redemption_discount_cents>=0)
        """, name="ck_coupon_status"),
    )
    op.create_index("ix_loyalty_coupon_customer", "loyalty_coupons", ["tenant_id", "customer_id", "status", "expires_at"])
    op.execute("ALTER TABLE loyalty_coupons ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE loyalty_coupons FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY loyalty_coupons_runtime ON loyalty_coupons FOR ALL TO app_runtime USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})")
    op.execute("CREATE POLICY loyalty_coupons_migrator ON loyalty_coupons FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
    op.execute("REVOKE ALL ON loyalty_coupons FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT ON loyalty_coupons TO app_runtime")
    op.execute("GRANT UPDATE (status,redeemed_at,redeemed_appointment_id,redemption_discount_cents,version) ON loyalty_coupons TO app_runtime")

    op.drop_constraint("ck_loyalty_delta", "loyalty_transactions", type_="check")
    op.alter_column("loyalty_transactions", "points_delta", existing_type=sa.Integer(), type_=sa.BigInteger())
    op.create_check_constraint("ck_loyalty_delta", "loyalty_transactions", "points_delta BETWEEN -10000000000000 AND 10000000000000 AND points_delta<>0")
    for column in (
        sa.Column("kind", sa.String(16), nullable=False, server_default="adjustment"),
        sa.Column("coupon_id", UUID(as_uuid=True), nullable=True),
        sa.Column("rule_snapshot", JSONB(), nullable=True),
        sa.Column("basis_amount_cents", sa.Integer(), nullable=True),
    ):
        op.add_column("loyalty_transactions", column)
    op.create_foreign_key("fk_loyalty_transaction_coupon", "loyalty_transactions", "loyalty_coupons", ["tenant_id", "coupon_id"], ["tenant_id", "id"])
    op.create_check_constraint("ck_loyalty_transaction_kind", "loyalty_transactions", """
        (kind='adjustment' AND coupon_id IS NULL) OR
        (kind='earned' AND points_delta>0 AND appointment_id IS NOT NULL AND rule_id IS NOT NULL AND rule_snapshot IS NOT NULL AND basis_amount_cents IS NOT NULL AND basis_amount_cents>=0 AND coupon_id IS NULL) OR
        (kind='reward' AND points_delta<0 AND coupon_id IS NOT NULL AND rule_id IS NOT NULL)
    """)
    op.create_index("uq_loyalty_appointment_earned", "loyalty_transactions", ["tenant_id", "appointment_id"], unique=True,
                    postgresql_where=sa.text("kind='earned'"))
    op.create_index("uq_loyalty_coupon_spent", "loyalty_transactions", ["tenant_id", "coupon_id"], unique=True,
                    postgresql_where=sa.text("kind='reward'"))


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM loyalty_coupons) OR EXISTS (
        SELECT 1 FROM appointments WHERE discount_cents>0) OR EXISTS (
        SELECT 1 FROM loyalty_transactions WHERE abs(points_delta)>1000000) THEN
        RAISE EXCEPTION 'Cannot downgrade loyalty rewards while coupon/discount history exists'; END IF; END $$""")
    op.drop_index("uq_loyalty_coupon_spent", table_name="loyalty_transactions")
    op.drop_index("uq_loyalty_appointment_earned", table_name="loyalty_transactions")
    op.drop_constraint("ck_loyalty_transaction_kind", "loyalty_transactions", type_="check")
    op.drop_constraint("fk_loyalty_transaction_coupon", "loyalty_transactions", type_="foreignkey")
    for name in ("kind", "coupon_id", "rule_snapshot", "basis_amount_cents"):
        op.drop_column("loyalty_transactions", name)
    op.drop_constraint("ck_loyalty_delta", "loyalty_transactions", type_="check")
    op.alter_column("loyalty_transactions", "points_delta", existing_type=sa.BigInteger(), type_=sa.Integer())
    op.create_check_constraint("ck_loyalty_delta", "loyalty_transactions", "points_delta BETWEEN -1000000 AND 1000000 AND points_delta<>0")
    op.drop_table("loyalty_coupons")
    op.drop_index("uq_loyalty_active_program", table_name="loyalty_rules")
    op.drop_constraint("ck_loyalty_reward", "loyalty_rules", type_="check")
    op.drop_constraint("fk_loyalty_reward_service", "loyalty_rules", type_="foreignkey")
    for name in ("reward_type", "reward_percent", "reward_value_cents", "reward_service_id", "reward_valid_days"):
        op.drop_column("loyalty_rules", name)
    # Existing combined/zero-point rules cannot be silently translated to the
    # old visit-or-spend model. PostgreSQL rejects this rollback if they exist.
    op.drop_constraint("ck_loyalty_points", "loyalty_rules", type_="check")
    op.create_check_constraint("ck_loyalty_points", "loyalty_rules", "points_per_visit BETWEEN 1 AND 10000 AND points_per_step BETWEEN 1 AND 10000")
    op.drop_constraint("ck_loyalty_mode", "loyalty_rules", type_="check")
    op.create_check_constraint("ck_loyalty_mode", "loyalty_rules", "earning_mode IN ('visit','spend')")
    op.drop_constraint("ck_appointment_discount", "appointments", type_="check")
    op.drop_column("appointments", "discount_cents")
    op.drop_column("appointments", "original_total_cents")
