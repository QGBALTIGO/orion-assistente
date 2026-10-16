"""FATIA 1: catálogo, equipe, expedientes, pausas, folgas, bloqueios e auditoria.

Revision ID: 0002_catalog_operations
Revises: 0001_foundation_rls
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_catalog_operations"
down_revision = "0001_foundation_rls"
branch_labels = None
depends_on = None

TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
TABLES = (
    "authorized_staff", "professionals", "services", "professional_services",
    "working_hours", "working_breaks", "time_off", "schedule_blocks",
    "tenant_audit",
)


def uid():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def tenant():
    return sa.Column("tenant_id", UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)


def profile_fk(col="professional_id"):
    return sa.ForeignKeyConstraint(
        ["tenant_id", col], ["professionals.tenant_id", "professionals.id"],
        name="fk_" + col + "_tenant_professional",
    )


def upgrade():
    # Papéis sem capacidade de contornar RLS; acesso global SOMENTE à tabela tenants.
    op.execute("CREATE POLICY tenants_support ON tenants FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenants TO app_support")
    op.create_table(
        "platform_audit",
        uid(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute("ALTER TABLE platform_audit ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform_audit FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY platform_audit_support ON platform_audit FOR ALL TO app_support USING (true) WITH CHECK (true)")
    op.execute("CREATE POLICY platform_audit_migrator ON platform_audit FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
    op.execute("REVOKE ALL ON platform_audit FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON platform_audit TO app_support")

    op.create_table(
        "authorized_staff", uid(), tenant(),
        sa.Column("phone_e164", sa.String(16), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_staff_phone"),
        sa.CheckConstraint("phone_e164 ~ '^\\+55[1-9][0-9]{9,10}$'", name="ck_staff_phone_e164"),
        sa.CheckConstraint("role IN ('owner','barber','assistant')", name="ck_staff_role"),
    )
    op.create_table(
        "professionals", uid(), tenant(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_professional_scope"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_professional_name"),
    )
    op.create_table(
        "services", uid(), tenant(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("prep_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cleanup_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_service_scope"),
        sa.CheckConstraint("duration_min BETWEEN 1 AND 1440", name="ck_service_duration"),
        sa.CheckConstraint("price_cents >= 0", name="ck_service_price"),
        sa.CheckConstraint("prep_min BETWEEN 0 AND 240 AND cleanup_min BETWEEN 0 AND 240", name="ck_service_buffers"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_service_name"),
    )
    op.create_table(
        "professional_services", uid(), tenant(),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"],
            ["professionals.tenant_id", "professionals.id"], name="fk_assignment_professional"),
        sa.ForeignKeyConstraint(["tenant_id", "service_id"],
            ["services.tenant_id", "services.id"], name="fk_assignment_service"),
        sa.UniqueConstraint("tenant_id", "professional_id", "service_id", name="uq_assignment"),
    )
    op.create_table(
        "working_hours", uid(), tenant(),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(timezone=False), nullable=False),
        sa.Column("end_time", sa.Time(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"],
            ["professionals.tenant_id", "professionals.id"], name="fk_hours_professional"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_hours_scope"),
        sa.UniqueConstraint("tenant_id", "professional_id", "weekday", name="uq_hours_weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_hours_weekday"),
        sa.CheckConstraint("start_time < end_time", name="ck_hours_interval"),
    )
    op.create_table(
        "working_breaks", uid(), tenant(),
        sa.Column("working_hour_id", UUID(as_uuid=True), nullable=False),
        sa.Column("start_time", sa.Time(timezone=False), nullable=False),
        sa.Column("end_time", sa.Time(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "working_hour_id"],
            ["working_hours.tenant_id", "working_hours.id"], name="fk_break_hours"),
        sa.CheckConstraint("start_time < end_time", name="ck_break_interval"),
    )
    for table in ("time_off", "schedule_blocks"):
        op.create_table(
            table, uid(), tenant(),
            sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reason", sa.String(200), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id", "professional_id"],
                ["professionals.tenant_id", "professionals.id"],
                name="fk_" + table + "_professional"),
            sa.CheckConstraint("starts_at < ends_at", name="ck_" + table + "_interval"),
        )
    op.create_table(
        "tenant_audit", uid(), tenant(),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    for table in TABLES:
        op.create_index("ix_" + table + "_tenant", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_table("platform_audit")
    op.execute("DROP POLICY tenants_support ON tenants")
