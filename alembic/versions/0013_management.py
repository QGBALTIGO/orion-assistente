"""Persistent management modules, reusing booking customers and draft campaigns.

Revision ID: 0013_management
Revises: 0012_commercial_fulfillment
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0013_management"
down_revision = "0012_commercial_fulfillment"
branch_labels = None
depends_on = None
TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
TABLES = (
    "management_requests", "financial_entries", "loyalty_rules",
    "loyalty_transactions", "professional_goals", "management_site_preferences",
)


def uid():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def tenant(primary=False):
    return sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
                     nullable=False, primary_key=primary)


def versioned():
    return (
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def upgrade():
    # Manual customers may omit a phone; booking requests still validate one.
    op.alter_column("customers", "whatsapp_e164", existing_type=sa.String(16), nullable=True)
    for column in (
        sa.Column("instagram", sa.String(120), nullable=True),
        sa.Column("neighborhood", sa.String(120), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ):
        op.add_column("customers", column)
    op.create_check_constraint("ck_customer_management_version", "customers", "version >= 1")
    op.create_check_constraint("ck_customer_management_notes", "customers", "notes IS NULL OR length(notes) <= 5000")
    op.create_check_constraint("ck_customer_management_birth", "customers", "birth_date IS NULL OR birth_date >= DATE '1900-01-01'")
    op.create_index("ix_customers_management_name", "customers", ["tenant_id", "active", "name", "id"])
    op.create_index("ix_appointments_management_period", "appointments", ["tenant_id", "start_at", "status"])
    op.create_index("ix_appointments_management_customer", "appointments", ["tenant_id", "customer_id", "status", "start_at"])
    op.add_column("appointments", sa.Column("commission_percent_snapshot", sa.Numeric(5, 2), nullable=True))
    op.add_column("appointments", sa.Column("commission_cents", sa.BigInteger(), nullable=True))
    op.create_check_constraint("ck_appointment_commission", "appointments", """
        (commission_percent_snapshot IS NULL AND commission_cents IS NULL) OR
        (commission_percent_snapshot IS NOT NULL AND commission_cents IS NOT NULL AND
         commission_percent_snapshot BETWEEN 0 AND 100 AND commission_cents BETWEEN 0 AND total_cents)
    """)
    op.drop_constraint("ck_history_event", "appointment_history", type_="check")
    op.create_check_constraint("ck_history_event", "appointment_history",
        "event IN ('hold_created','hold_expired','confirmed','cancelled','rescheduled','completed','no_show')")
    for column in (
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("color", sa.String(7), nullable=False, server_default="#8b5cf6"),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("services", column)
    op.create_check_constraint("ck_service_color", "services", "color ~ '^#[0-9a-fA-F]{6}$'")
    op.create_check_constraint("ck_service_sort_order", "services", "sort_order BETWEEN 0 AND 1000000")
    op.create_index("ix_services_management_order", "services", ["tenant_id", "sort_order", "name"])
    for column in (
        sa.Column("specialty", sa.String(120), nullable=True),
        sa.Column("color", sa.String(7), nullable=False, server_default="#31ad52"),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("accepts_appointments", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_url", sa.String(2048), nullable=True),
    ):
        op.add_column("professionals", column)
    op.create_check_constraint("ck_professional_color", "professionals", "color ~ '^#[0-9a-fA-F]{6}$'")
    op.create_check_constraint("ck_professional_commission", "professionals", "commission_percent BETWEEN 0 AND 100")

    op.create_table(
        "management_requests", tenant(),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("tenant_id", "operation", "idempotency_key"),
        sa.CheckConstraint("length(idempotency_key) BETWEEN 8 AND 120", name="ck_management_request_key"),
    )
    op.create_table(
        "financial_entries", uid(), tenant(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("category", sa.String(64), nullable=False, server_default="Geral"),
        sa.Column("payment_method", sa.String(24), nullable=False, server_default="other"),
        sa.Column("status", sa.String(16), nullable=False, server_default="paid"),
        sa.Column("entry_type", sa.String(24), nullable=False, server_default="general"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=True),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True), *versioned(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_financial_entry_scope"),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"], ["professionals.tenant_id", "professionals.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "appointment_id"], ["appointments.tenant_id", "appointments.id"]),
        sa.CheckConstraint("kind IN ('income','expense')", name="ck_financial_kind"),
        sa.CheckConstraint("amount_cents BETWEEN 1 AND 1000000000 AND currency='BRL'", name="ck_financial_money"),
        sa.CheckConstraint("status IN ('pending','paid','cancelled')", name="ck_financial_status"),
        sa.CheckConstraint("entry_type IN ('general','advance','commission_payment')", name="ck_financial_entry_type"),
        sa.CheckConstraint("entry_type='general' OR (kind='expense' AND professional_id IS NOT NULL)", name="ck_financial_staff_expense"),
        sa.CheckConstraint("payment_method IN ('pix','cash','credit_card','debit_card','bank_transfer','other')", name="ck_financial_method"),
        sa.CheckConstraint("length(trim(description)) > 0 AND length(trim(category)) > 0 AND version >= 1", name="ck_financial_values"),
        sa.CheckConstraint("notes IS NULL OR length(notes) <= 5000", name="ck_financial_notes"),
    )
    op.create_index("ix_financial_period", "financial_entries", ["tenant_id", "occurred_at", "id"])
    op.create_index("ix_financial_status", "financial_entries", ["tenant_id", "status", "occurred_at"])
    op.create_table(
        "loyalty_rules", uid(), tenant(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("earning_mode", sa.String(16), nullable=False, server_default="visit"),
        sa.Column("points_per_visit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("spend_step_cents", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("points_per_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reward_points", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("reward_description", sa.String(1000), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()), *versioned(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_loyalty_rule_scope"),
        sa.CheckConstraint("earning_mode IN ('visit','spend')", name="ck_loyalty_mode"),
        sa.CheckConstraint("points_per_visit BETWEEN 1 AND 10000 AND points_per_step BETWEEN 1 AND 10000", name="ck_loyalty_points"),
        sa.CheckConstraint("spend_step_cents BETWEEN 1 AND 1000000000 AND reward_points BETWEEN 1 AND 1000000", name="ck_loyalty_thresholds"),
        sa.CheckConstraint("length(trim(name)) > 0 AND length(trim(reward_description)) > 0 AND version >= 1", name="ck_loyalty_rule_text"),
    )
    op.create_index("ix_loyalty_rules_active", "loyalty_rules", ["tenant_id", "active"])
    op.create_table(
        "loyalty_transactions", uid(), tenant(),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", UUID(as_uuid=True), nullable=True),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("points_delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_loyalty_transaction_scope"),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "rule_id"], ["loyalty_rules.tenant_id", "loyalty_rules.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "appointment_id"], ["appointments.tenant_id", "appointments.id"]),
        sa.CheckConstraint("points_delta BETWEEN -1000000 AND 1000000 AND points_delta <> 0", name="ck_loyalty_delta"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_loyalty_reason"),
    )
    op.create_index("ix_loyalty_customer_history", "loyalty_transactions", ["tenant_id", "customer_id", "created_at"])
    op.create_table(
        "professional_goals", uid(), tenant(),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(24), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("target_value", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), *versioned(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_professional_goal_scope"),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"], ["professionals.tenant_id", "professionals.id"]),
        sa.CheckConstraint("metric IN ('revenue_cents','appointments')", name="ck_professional_goal_metric"),
        sa.CheckConstraint("end_date >= start_date AND end_date - start_date < 366", name="ck_professional_goal_period"),
        sa.CheckConstraint("target_value BETWEEN 1 AND 1000000000 AND version >= 1", name="ck_professional_goal_value"),
    )
    op.create_index("ix_professional_goal_period", "professional_goals", ["tenant_id", "professional_id", "start_date", "end_date"])
    op.create_table(
        "management_site_preferences", tenant(primary=True),
        sa.Column("title", sa.String(160), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("slug", sa.String(80), nullable=True, unique=True),
        sa.Column("theme_color", sa.String(7), nullable=False, server_default="#31ad52"),
        sa.Column("logo_url", sa.String(2048), nullable=True),
        sa.Column("cover_url", sa.String(2048), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("contact_phone", sa.String(16), nullable=True),
        sa.Column("instagram_url", sa.String(2048), nullable=True),
        sa.Column("show_prices", sa.Boolean(), nullable=False, server_default=sa.true()), *versioned(),
        sa.CheckConstraint("theme_color ~ '^#[0-9a-fA-F]{6}$'", name="ck_site_theme_color"),
        sa.CheckConstraint("length(description) <= 5000 AND version >= 1", name="ck_site_values"),
        sa.CheckConstraint("slug IS NULL OR (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) >= 3)", name="ck_site_slug"),
    )
    for column in (
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("audience", sa.String(24), nullable=False, server_default="all_active"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ):
        op.add_column("campaigns", column)
    op.create_check_constraint("ck_campaign_management_values", "campaigns",
        "version >= 1 AND (message IS NULL OR length(message) BETWEEN 1 AND 5000)")
    op.create_check_constraint("ck_campaign_management_audience", "campaigns",
        "audience IN ('all_active','inactive_customers','birthday_month','new_customers')")
    op.create_index("ix_campaigns_management", "campaigns", ["tenant_id", "status", "created_at"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, app_support, app_runtime")
        permissions = "SELECT,INSERT" if table in ("management_requests", "loyalty_transactions") else "SELECT,INSERT,UPDATE"
        op.execute(f"GRANT {permissions} ON {table} TO app_runtime")


def downgrade():
    # Never invent phone numbers or delete customers to make a rollback succeed.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM customers WHERE whatsapp_e164 IS NULL) OR
           EXISTS (SELECT 1 FROM appointment_history WHERE event IN ('completed','no_show')) THEN
            RAISE EXCEPTION 'Cannot downgrade management while phone-free customers or completion history exist';
        END IF;
    END $$""")
    op.drop_constraint("ck_history_event", "appointment_history", type_="check")
    op.create_check_constraint("ck_history_event", "appointment_history",
        "event IN ('hold_created','hold_expired','confirmed','cancelled','rescheduled')")
    op.drop_constraint("ck_appointment_commission", "appointments", type_="check")
    op.drop_column("appointments", "commission_cents")
    op.drop_column("appointments", "commission_percent_snapshot")
    for name in ("ck_professional_color", "ck_professional_commission"):
        op.drop_constraint(name, "professionals", type_="check")
    for name in ("specialty", "color", "commission_percent", "accepts_appointments", "image_url"):
        op.drop_column("professionals", name)
    op.drop_index("ix_services_management_order", table_name="services")
    for name in ("ck_service_color", "ck_service_sort_order"):
        op.drop_constraint(name, "services", type_="check")
    for name in ("category", "color", "image_url", "sort_order"):
        op.drop_column("services", name)
    op.drop_index("ix_campaigns_management", table_name="campaigns")
    op.drop_constraint("ck_campaign_management_audience", "campaigns", type_="check")
    op.drop_constraint("ck_campaign_management_values", "campaigns", type_="check")
    for name in ("message", "audience", "scheduled_at", "version", "updated_at"):
        op.drop_column("campaigns", name)
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_index("ix_appointments_management_period", table_name="appointments")
    op.drop_index("ix_appointments_management_customer", table_name="appointments")
    op.drop_index("ix_customers_management_name", table_name="customers")
    for name in ("ck_customer_management_version", "ck_customer_management_notes", "ck_customer_management_birth"):
        op.drop_constraint(name, "customers", type_="check")
    for name in ("email", "birth_date", "notes", "active", "version", "updated_at", "instagram", "neighborhood", "city", "deleted_at"):
        op.drop_column("customers", name)
    op.alter_column("customers", "whatsapp_e164", existing_type=sa.String(16), nullable=False)
