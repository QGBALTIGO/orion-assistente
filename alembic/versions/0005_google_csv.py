"""FATIA 4: OAuth Google por tenant e outbox transacional.

Revision ID: 0005_google_csv
Revises: 0004_whatsapp
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision="0005_google_csv"
down_revision="0004_whatsapp"
branch_labels=None
depends_on=None
SETTING="NULLIF(current_setting('app.tenant_id',true),'')::uuid"


def upgrade():
    op.create_table(
        "google_connections",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id"),nullable=False),
        sa.Column("calendar_id",sa.String(250),nullable=False),
        sa.Column("refresh_token_enc",sa.LargeBinary(),nullable=True),
        sa.Column("status",sa.String(20),nullable=False,server_default="pending"),
        sa.Column("last_error",sa.String(100),nullable=True),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id",name="uq_google_tenant"),
        sa.CheckConstraint("status IN ('pending','active','revoked','error')",name="ck_google_status"),
    )
    op.create_table(
        "calendar_outbox",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),nullable=False),
        sa.Column("appointment_id",UUID(as_uuid=True),nullable=False),
        sa.Column("action",sa.String(15),nullable=False),
        sa.Column("status",sa.String(20),nullable=False,server_default="pending"),
        sa.Column("attempts",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("next_attempt_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("external_event_id",sa.String(100),nullable=True),
        sa.Column("last_error",sa.String(100),nullable=True),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id","appointment_id"],["appointments.tenant_id","appointments.id"]),
        sa.UniqueConstraint("tenant_id","appointment_id",name="uq_calendar_pending_per_booking"),
        sa.CheckConstraint("action IN ('create','update','delete')",name="ck_calendar_action"),
        sa.CheckConstraint("status IN ('pending','sent','dead')",name="ck_calendar_status"),
    )
    for table in ("google_connections","calendar_outbox"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_runtime")
    # IDs sem informação pessoal para worker varrer pendências multiempresa.
    op.execute("CREATE POLICY calendar_outbox_scan ON calendar_outbox FOR SELECT TO app_support USING (true)")
    op.execute("GRANT SELECT (id,tenant_id,status,next_attempt_at) ON calendar_outbox TO app_support")


def downgrade():
    op.drop_table("calendar_outbox")
    op.drop_table("google_connections")
