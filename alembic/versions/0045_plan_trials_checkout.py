"""Scoped 3-day Orion trials and Cakto checkout intents; additive, no live charges.

Revision ID: 0045_plan_trials_checkout
Revises: 0044_single_whatsapp_connection
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision="0045_plan_trials_checkout"
down_revision="0044_single_whatsapp_connection"
branch_labels=None
depends_on=None


def _restrict(table):
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON {table} FROM app_runtime")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_support")
    op.execute(f"CREATE POLICY {table}_support ON {table} FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")


def upgrade():
    # Token only as SHA-256. Never store Cakto raw payload/CPF, e-mail or card.
    op.add_column("cakto_webhook_events", sa.Column("callback_hash", sa.String(64),nullable=True))
    op.add_column("cakto_webhook_events", sa.Column("amount_cents", sa.Integer(),nullable=True))
    op.add_column("cakto_webhook_events", sa.Column("currency", sa.String(3),nullable=True))
    op.create_index("ix_cakto_events_callback", "cakto_webhook_events", ["callback_hash"],
                    postgresql_where=sa.text("callback_hash IS NOT NULL"))

    op.create_table("orion_plan_trial_requests",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id",ondelete="RESTRICT"),nullable=False,unique=True),
        sa.Column("account_id",UUID(as_uuid=True),sa.ForeignKey("panel_accounts.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("full_name",sa.String(120),nullable=False),
        sa.Column("company_name",sa.String(160),nullable=False),
        sa.Column("whatsapp_e164",sa.String(20),nullable=False),
        sa.Column("status",sa.String(16),nullable=False,server_default="pending"),
        sa.Column("requested_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("reviewed_at",sa.DateTime(timezone=True)),
        sa.Column("reviewed_by",UUID(as_uuid=True)),
        sa.Column("started_at",sa.DateTime(timezone=True)),
        sa.Column("ends_at",sa.DateTime(timezone=True)),
        sa.Column("version",sa.Integer(),nullable=False,server_default="1"),
        sa.CheckConstraint("status IN ('pending','active','expired','rejected')",name="ck_orion_trial_status"),
        sa.CheckConstraint("(status='active') = (started_at IS NOT NULL AND ends_at IS NOT NULL)",
                           name="ck_orion_trial_window"),
        sa.CheckConstraint("version>=1",name="ck_orion_trial_version"),
    )
    op.create_index("ix_orion_trial_requested", "orion_plan_trial_requests", ["requested_at"])
    op.create_index("ix_orion_trial_status", "orion_plan_trial_requests", ["status","requested_at"])
    _restrict("orion_plan_trial_requests")

    op.create_table("orion_cakto_checkout_intents",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("account_id",UUID(as_uuid=True),sa.ForeignKey("panel_accounts.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("plan_code",sa.String(20),nullable=False),
        sa.Column("offer_id",sa.String(80),nullable=False),
        sa.Column("expected_cents",sa.Integer(),nullable=False),
        sa.Column("callback_hash",sa.String(64),nullable=False,unique=True),
        sa.Column("state",sa.String(16),nullable=False,server_default="pending"),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("verified_at",sa.DateTime(timezone=True)),
        sa.Column("external_order_id",sa.String(255),nullable=True,unique=True),
        sa.CheckConstraint("plan_code IN ('essencial','profissional','elite')",name="ck_orion_checkout_plan"),
        sa.CheckConstraint("state IN ('pending','verified','needs_review')",name="ck_orion_checkout_state"),
        sa.CheckConstraint("expected_cents>0",name="ck_orion_checkout_amount"),
    )
    op.create_index("ix_orion_cakto_intents_tenant", "orion_cakto_checkout_intents",
                    ["tenant_id","created_at"])
    _restrict("orion_cakto_checkout_intents")

    op.create_table("orion_cakto_bindings",
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("subscription_id",UUID(as_uuid=True),sa.ForeignKey("subscriptions.id",ondelete="RESTRICT"),nullable=False,unique=True),
        sa.Column("external_subscription_id",sa.String(255),nullable=False,unique=True),
        sa.Column("plan_code",sa.String(20),nullable=False),
        sa.Column("last_order_id",sa.String(255),nullable=False,unique=True),
        sa.Column("last_verified_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
    )
    _restrict("orion_cakto_bindings")

    # Fixed commercial prices, without inventing or publishing a WhatsApp message
    # quota. The legacy required column receives a non-marketed technical maximum;
    # the feature gate and contract for usage will be designed separately.
    for code,name,cents,staff in (
        ("orion_essencial","Orion Essencial",2990,2),
        ("orion_profissional","Orion Profissional",7990,6),
        ("orion_elite","Orion Elite",13990,10),
    ):
        op.get_bind().execute(sa.text("""
            INSERT INTO plans(code,name,monthly_cents,max_professionals,max_messages_month,active)
            VALUES (:code,:name,:cents,:staff,2147483647,true)
            ON CONFLICT (code) DO NOTHING
        """), {"code":code,"name":name,"cents":cents,"staff":staff})
        row=op.get_bind().execute(sa.text("""
            SELECT monthly_cents,max_professionals,active FROM plans WHERE code=:code
        """), {"code":code}).one()
        if tuple(row)!=(cents,staff,True):
            raise RuntimeError("orion_plan_catalog_collision")


def downgrade():
    # Financial/trial records must never be erased by an accidental rollback.
    raise RuntimeError("Irreversible financial and trial records; perform a reviewed forward migration")
