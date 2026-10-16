"""FATIA 3: canais Meta, eventos idempotentes, fila durável e RBAC do barbeiro.

Revision ID: 0004_whatsapp
Revises: 0003_booking_engine
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004_whatsapp"
down_revision = "0003_booking_engine"
branch_labels = None
depends_on = None
TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade():
    op.add_column("tenants", sa.Column("bot_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("authorized_staff", sa.Column("professional_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_staff_professional", "authorized_staff", "professionals",
                          ["tenant_id", "professional_id"], ["tenant_id", "id"])
    op.create_table(
        "whatsapp_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default="meta"),
        sa.Column("phone_number_id", sa.String(80), nullable=False, unique=True),
        sa.Column("business_number", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="testing"),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("bot_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_whatsapp_channel_scope"),
        sa.CheckConstraint("provider = 'meta'", name="ck_whatsapp_provider"),
        sa.CheckConstraint("status IN ('testing', 'connected', 'revoked', 'paused')", name="ck_whatsapp_status"),
        sa.CheckConstraint("business_number ~ '^\\+55[1-9][0-9]{9,10}$'", name="ck_whatsapp_number"),
    )
    op.create_table(
        "inbound_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("sender_e164", sa.String(16), nullable=False),
        sa.Column("message_text", sa.String(1500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "channel_id"],
                                ["whatsapp_channels.tenant_id", "whatsapp_channels.id"]),
        sa.UniqueConstraint("channel_id", "external_id", name="uq_inbound_dedup"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inbound_scope"),
        sa.CheckConstraint("sender_e164 ~ '^\\+55[1-9][0-9]{9,10}$'", name="ck_inbound_sender"),
        sa.CheckConstraint("status IN ('pending','processing','processed','failed')", name="ck_inbound_status"),
    )
    op.create_table(
        "message_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("to_e164", sa.String(16), nullable=False),
        sa.Column("body", sa.String(1500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("external_delivery_id", sa.String(150), nullable=True),
        sa.Column("last_error", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "channel_id"], ["whatsapp_channels.tenant_id", "whatsapp_channels.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "event_id"], ["inbound_events.tenant_id", "inbound_events.id"]),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_outbound_per_event"),
        sa.CheckConstraint("status IN ('pending','sent','dead')", name="ck_outbox_status"),
    )
    for table in ("whatsapp_channels", "inbound_events", "message_outbox"):
        op.create_index("ix_" + table + "_tenant", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO app_runtime")
    # app_support consulta apenas metadados dos canais para resolver uma
    # notificação assinada, sem acesso a eventos, textos ou dados de clientes.
    op.execute("CREATE POLICY whatsapp_channels_support ON whatsapp_channels "
               "FOR SELECT TO app_support USING (true)")
    op.execute("GRANT SELECT (id, tenant_id, phone_number_id, status, bot_enabled) "
               "ON whatsapp_channels TO app_support")
    op.execute("CREATE POLICY inbound_support_scan ON inbound_events FOR SELECT TO app_support USING (true)")
    op.execute("GRANT SELECT (id, tenant_id, status, processing_at) ON inbound_events TO app_support")


def downgrade():
    for table in ("message_outbox", "inbound_events", "whatsapp_channels"):
        op.drop_table(table)
    op.drop_constraint("fk_staff_professional", "authorized_staff", type_="foreignkey")
    op.drop_column("authorized_staff", "professional_id")
    op.drop_column("tenants", "bot_enabled")
