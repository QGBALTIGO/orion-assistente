"""Cliente fixo: séries finitas de agendamentos reais, com RLS por empresa.

A migração é aditiva: nenhum agendamento existente é alterado.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0074_fixed_client_series"
down_revision = "0073_cakto_recovery"
branch_labels = None
depends_on = None

TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_table(
        "appointment_series",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("service_ids", sa.ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("services_label", sa.String(750), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("interval_weeks", sa.SmallInteger(), nullable=False),
        sa.Column("occurrence_count", sa.SmallInteger(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"], ["professionals.tenant_id", "professionals.id"]),
        sa.UniqueConstraint("tenant_id", "id", name="uq_appointment_series_scope"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointment_series_request"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_appointment_series_weekday"),
        sa.CheckConstraint("interval_weeks IN (1,2,4)", name="ck_appointment_series_interval"),
        sa.CheckConstraint("occurrence_count BETWEEN 2 AND 52", name="ck_appointment_series_count"),
        sa.CheckConstraint("array_length(service_ids,1) BETWEEN 1 AND 6", name="ck_appointment_series_services"),
    )
    op.create_index("ix_appointment_series_customer", "appointment_series", ["tenant_id", "customer_id", "created_at"])
    op.add_column("appointments", sa.Column("series_id", UUID(as_uuid=True), nullable=True))
    op.add_column("appointments", sa.Column("series_index", sa.SmallInteger(), nullable=True))
    op.create_foreign_key("fk_appointment_series", "appointments", "appointment_series",
                          ["tenant_id", "series_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_appointment_series_pair", "appointments",
        "(series_id IS NULL AND series_index IS NULL) OR (series_id IS NOT NULL AND series_index BETWEEN 1 AND 52)")
    op.create_unique_constraint("uq_appointment_series_index", "appointments",
                                ["tenant_id", "series_id", "series_index"])
    op.create_index("ix_appointment_series_instances", "appointments",
                    ["tenant_id", "series_id", "start_at"])
    op.execute("ALTER TABLE appointment_series ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE appointment_series FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY appointment_series_runtime ON appointment_series FOR ALL TO app_runtime
        USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})""")
    op.execute("""CREATE POLICY appointment_series_migrator ON appointment_series FOR ALL TO app_migrator
        USING (true) WITH CHECK (true)""")
    op.execute("REVOKE ALL ON appointment_series FROM PUBLIC, app_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE ON appointment_series TO app_runtime")


def downgrade():
    # Never destroy clients' reserved sessions during an unsafe rollback.
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM appointment_series LIMIT 1) THEN
        RAISE EXCEPTION 'A recurring series exists: use a forward recovery instead of dropping client bookings';
      END IF;
    END $$""")
    op.drop_index("ix_appointment_series_instances", table_name="appointments")
    op.drop_constraint("uq_appointment_series_index", "appointments", type_="unique")
    op.drop_constraint("ck_appointment_series_pair", "appointments", type_="check")
    op.drop_constraint("fk_appointment_series", "appointments", type_="foreignkey")
    op.drop_column("appointments", "series_index")
    op.drop_column("appointments", "series_id")
    op.drop_table("appointment_series")
