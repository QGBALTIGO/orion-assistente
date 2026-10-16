"""FATIA 8: base de inbox humana, consentimento CRM, campanhas e workflows seguros.

Revision ID: 0007_advanced
Revises: 0006_commercial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID,JSONB

revision="0007_advanced"
down_revision="0006_commercial"
branch_labels=None
depends_on=None
SETTING="NULLIF(current_setting('app.tenant_id',true),'')::uuid"


def uid():
    return sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()"))


def tenant():
    return sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id"),nullable=False)


def upgrade():
    op.create_table(
        "human_handoffs",uid(),tenant(),
        sa.Column("channel_id",UUID(as_uuid=True),nullable=False),
        sa.Column("contact_e164",sa.String(16),nullable=False),
        sa.Column("status",sa.String(16),nullable=False,server_default="active"),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False,
                  server_default=sa.text("now() + interval '30 minutes'")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id","channel_id"],
                                ["whatsapp_channels.tenant_id","whatsapp_channels.id"]),
        sa.UniqueConstraint("tenant_id","channel_id","contact_e164",name="uq_handoff_contact"),
        sa.CheckConstraint("status IN ('active','resolved')",name="ck_handoff_status"),
    )
    op.create_table(
        "contact_consents",uid(),tenant(),
        sa.Column("contact_e164",sa.String(16),nullable=False),
        sa.Column("marketing_opt_in",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("source",sa.String(40),nullable=False,server_default="system"),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id","contact_e164",name="uq_contact_consent"),
        sa.CheckConstraint("contact_e164 ~ '^\\+55[1-9][0-9]{9,10}$'",name="ck_consent_phone"),
    )
    op.create_table(
        "campaigns",uid(),tenant(),
        sa.Column("name",sa.String(120),nullable=False),
        sa.Column("template_name",sa.String(120),nullable=False),
        sa.Column("status",sa.String(16),nullable=False,server_default="draft"),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id","id",name="uq_campaign_scope"),
        sa.CheckConstraint("status IN ('draft','approved','paused')",name="ck_campaign_status"),
    )
    op.create_table(
        "campaign_recipients",uid(),tenant(),
        sa.Column("campaign_id",UUID(as_uuid=True),nullable=False),
        sa.Column("contact_e164",sa.String(16),nullable=False),
        sa.Column("status",sa.String(16),nullable=False,server_default="draft"),
        sa.ForeignKeyConstraint(["tenant_id","campaign_id"],["campaigns.tenant_id","campaigns.id"]),
        sa.ForeignKeyConstraint(["tenant_id","contact_e164"],
                                ["contact_consents.tenant_id","contact_consents.contact_e164"]),
        sa.UniqueConstraint("tenant_id","campaign_id","contact_e164",name="uq_campaign_contact"),
        sa.CheckConstraint("status IN ('draft','blocked','approved')",name="ck_campaign_recipient_status"),
    )
    op.create_table(
        "workflow_definitions",uid(),tenant(),
        sa.Column("name",sa.String(120),nullable=False),
        sa.Column("steps",JSONB(),nullable=False),
        sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.CheckConstraint("jsonb_typeof(steps)='array'",name="ck_workflow_steps"),
    )
    for table in ("human_handoffs","contact_consents","campaigns",
                  "campaign_recipients","workflow_definitions"):
        op.create_index("ix_"+table+"_tenant",table,["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime "
                   f"USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator "
                   "USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_runtime")


def downgrade():
    for table in ("workflow_definitions","campaign_recipients","campaigns",
                  "contact_consents","human_handoffs"):
        op.drop_table(table)
