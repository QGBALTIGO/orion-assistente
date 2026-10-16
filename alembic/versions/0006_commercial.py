"""FATIA 6: assinatura SaaS, eventos Asaas verificados e contadores de uso.

Revision ID: 0006_commercial
Revises: 0005_google_csv
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision="0006_commercial"
down_revision="0005_google_csv"
branch_labels=None
depends_on=None
SETTING="NULLIF(current_setting('app.tenant_id',true),'')::uuid"


def upgrade():
    op.create_table(
        "plans",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("code",sa.String(50),nullable=False,unique=True),
        sa.Column("name",sa.String(120),nullable=False),
        sa.Column("monthly_cents",sa.Integer(),nullable=False),
        sa.Column("max_professionals",sa.Integer(),nullable=False),
        sa.Column("max_messages_month",sa.Integer(),nullable=False),
        sa.Column("active",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.CheckConstraint("monthly_cents>=0 AND max_professionals>0 AND max_messages_month>0",
                           name="ck_plan_limits"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id"),nullable=False,unique=True),
        sa.Column("plan_id",UUID(as_uuid=True),sa.ForeignKey("plans.id"),nullable=False),
        sa.Column("external_subscription_id",sa.String(120),nullable=True,unique=True),
        sa.Column("status",sa.String(20),nullable=False,server_default="trialing"),
        sa.Column("trial_ends_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("grace_until",sa.DateTime(timezone=True),nullable=True),
        sa.Column("last_verified_payment_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('trialing','active','past_due','suspended','canceled')",
                           name="ck_subscription_status"),
    )
    op.create_table(
        "billing_events",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id"),nullable=False),
        sa.Column("provider_event_id",sa.String(120),nullable=False,unique=True),
        sa.Column("external_payment_id",sa.String(120),nullable=True),
        sa.Column("event_type",sa.String(80),nullable=False),
        sa.Column("status",sa.String(30),nullable=False,server_default="pending_verification"),
        sa.Column("received_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("attempts",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("next_attempt_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("verified_at",sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint("status IN ('pending_verification','verified','ignored','failed')",
                           name="ck_billing_event_status"),
    )
    op.create_table(
        "usage_counters",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id"),nullable=False),
        sa.Column("period",sa.Date(),nullable=False),
        sa.Column("inbound_messages",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("outbound_messages",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("bookings",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("estimated_cost_cents",sa.Integer(),nullable=False,server_default="0"),
        sa.UniqueConstraint("tenant_id","period",name="uq_usage_period"),
        sa.CheckConstraint("inbound_messages>=0 AND outbound_messages>=0 AND bookings>=0",
                           name="ck_usage_nonnegative"),
    )
    for table in ("subscriptions","billing_events","usage_counters"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_runtime")
    # Suporte possui acesso somente aos metadados de assinatura, não aos dados
    # de cartão/cobrança nem a informações pessoais dos clientes da barbearia.
    op.execute("CREATE POLICY subscriptions_support ON subscriptions FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute("CREATE POLICY billing_support ON billing_events FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute("GRANT SELECT,INSERT,UPDATE ON subscriptions,billing_events TO app_support")
    op.execute("GRANT SELECT ON plans TO app_runtime,app_support")
    op.execute("REVOKE ALL ON plans FROM PUBLIC")
    op.execute("CREATE POLICY usage_support ON usage_counters FOR SELECT TO app_support USING (true)")
    op.execute("GRANT SELECT ON usage_counters TO app_support")


def downgrade():
    for table in ("usage_counters","billing_events","subscriptions","plans"):
        op.drop_table(table)
