"""FATIA 2: clientes e reservas reais com exclusão GiST e RLS.

Revision ID: 0003_booking_engine
Revises: 0002_catalog_operations
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_booking_engine"
down_revision = "0002_catalog_operations"
branch_labels = None
depends_on = None
TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def uid():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def tenant():
    return sa.Column("tenant_id", UUID(as_uuid=True), nullable=False)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column("tenants", sa.Column("slot_step_minutes", sa.Integer(), nullable=False, server_default="15"))
    op.add_column("tenants", sa.Column("min_notice_minutes", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("tenants", sa.Column("booking_horizon_days", sa.Integer(), nullable=False, server_default="60"))
    op.create_check_constraint("ck_tenant_slot_step", "tenants", "slot_step_minutes IN (5, 10, 15, 20, 30, 60)")
    op.create_check_constraint("ck_tenant_min_notice", "tenants", "min_notice_minutes BETWEEN 0 AND 10080")
    op.create_check_constraint("ck_tenant_horizon", "tenants", "booking_horizon_days BETWEEN 1 AND 365")
    op.create_table(
        "customers", uid(), tenant(),
        sa.Column("whatsapp_e164", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "id", name="uq_customer_scope"),
        sa.UniqueConstraint("tenant_id", "whatsapp_e164", name="uq_customer_phone"),
        sa.CheckConstraint("whatsapp_e164 ~ '^\\+55[1-9][0-9]{9,10}$'", name="ck_customer_phone"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_customer_name"),
    )
    op.create_table(
        "appointments", uid(), tenant(),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("protocol", sa.String(9), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupied_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupied_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="hold"),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"],
                                ["customers.tenant_id", "customers.id"], name="fk_appointment_customer"),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"],
                                ["professionals.tenant_id", "professionals.id"], name="fk_appointment_professional"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_appointment_scope"),
        sa.UniqueConstraint("tenant_id", "protocol", name="uq_appointment_protocol"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointment_idempotency"),
        sa.CheckConstraint("end_at > start_at", name="ck_appointment_positive_duration"),
        sa.CheckConstraint("occupied_from <= start_at AND occupied_until >= end_at AND occupied_until > occupied_from",
                           name="ck_appointment_occupied_range"),
        sa.CheckConstraint("total_cents >= 0", name="ck_appointment_total"),
        sa.CheckConstraint("status IN ('hold','confirmed','cancelled','completed','no_show')",
                           name="ck_appointment_status"),
        sa.CheckConstraint("(status <> 'hold') OR (hold_expires_at IS NOT NULL)",
                           name="ck_hold_expiry"),
    )
    op.execute("""
        ALTER TABLE appointments ADD CONSTRAINT appointments_no_overlap
        EXCLUDE USING gist (
          tenant_id WITH =,
          professional_id WITH =,
          tstzrange(occupied_from, occupied_until, '[)') WITH &&
        ) WHERE (status IN ('hold', 'confirmed'))
    """)
    op.create_index("ix_appointments_professional_time", "appointments",
                    ["tenant_id", "professional_id", "start_at"])
    op.create_table(
        "appointment_items", uid(), tenant(),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name_snapshot", sa.String(120), nullable=False),
        sa.Column("duration_snapshot_min", sa.Integer(), nullable=False),
        sa.Column("price_snapshot_cents", sa.Integer(), nullable=False),
        sa.Column("prep_snapshot_min", sa.Integer(), nullable=False),
        sa.Column("cleanup_snapshot_min", sa.Integer(), nullable=False),
        sa.Column("item_order", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "appointment_id"],
                                ["appointments.tenant_id", "appointments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "service_id"],
                                ["services.tenant_id", "services.id"]),
        sa.UniqueConstraint("tenant_id", "appointment_id", "item_order", name="uq_appointment_item_order"),
        sa.CheckConstraint("duration_snapshot_min > 0 AND price_snapshot_cents >= 0",
                           name="ck_item_snapshot"),
    )
    op.create_table(
        "appointment_history", uid(), tenant(),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("operator", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "appointment_id"],
                                ["appointments.tenant_id", "appointments.id"], ondelete="CASCADE"),
        sa.CheckConstraint("event IN ('hold_created','hold_expired','confirmed','cancelled','rescheduled')",
                           name="ck_history_event"),
    )
    for table in ("customers", "appointments", "appointment_items", "appointment_history"):
        op.create_index("ix_" + table + "_tenant_id", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO app_runtime")
    # Apenas o serviço de domínio altera horários/estados; banco bloqueia
    # sobreposição independentemente da quantidade de instâncias da API.


def downgrade():
    for table in ("appointment_history", "appointment_items", "appointments", "customers"):
        op.drop_table(table)
    for name in ("ck_tenant_slot_step", "ck_tenant_min_notice", "ck_tenant_horizon"):
        op.drop_constraint(name, "tenants", type_="check")
    for name in ("slot_step_minutes", "min_notice_minutes", "booking_horizon_days"):
        op.drop_column("tenants", name)
