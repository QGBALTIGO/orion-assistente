"""Add explicit account memberships and immutable business-profile revisions.

Revision ID: 0010_business_profiles
Revises: 0009_email_otp
No legacy rows, accounts, appointments or WhatsApp credentials are changed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010_business_profiles"
down_revision = "0009_email_otp"
branch_labels = None
depends_on = None
SETTING = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"


def uid():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def tenant():
    return sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)


def stamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade():
    op.create_table("tenant_memberships", uid(), tenant(),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        stamp(), stamp("updated_at"),
        sa.UniqueConstraint("tenant_id", "account_id", name="uq_tenant_account_membership"),
        sa.CheckConstraint("role IN ('owner','editor','viewer')", name="ck_membership_role"))
    op.create_index("ix_membership_account", "tenant_memberships", ["account_id"])
    op.execute("ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON tenant_memberships FROM PUBLIC, app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON tenant_memberships TO app_support")
    for role in ("app_support", "app_migrator"):
        op.execute(f"CREATE POLICY membership_{role} ON tenant_memberships FOR ALL TO {role} USING (true) WITH CHECK (true)")

    op.create_table("tenant_membership_events", uid(), tenant(),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False), stamp(),
        sa.CheckConstraint("role IN ('owner','editor','viewer')", name="ck_membership_event_role"))
    op.create_index("ix_membership_events_tenant", "tenant_membership_events", ["tenant_id", "created_at"])
    op.execute("ALTER TABLE tenant_membership_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_membership_events FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON tenant_membership_events FROM PUBLIC, app_runtime")
    op.execute("GRANT SELECT,INSERT ON tenant_membership_events TO app_support")
    for role in ("app_support", "app_migrator"):
        op.execute(f"CREATE POLICY membership_events_{role} ON tenant_membership_events FOR ALL TO {role} USING (true) WITH CHECK (true)")

    op.create_table("business_profile_versions", uid(), tenant(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("actor_role", sa.String(16), nullable=False), stamp(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_profile_version_scope"),
        sa.UniqueConstraint("tenant_id", "revision", name="uq_profile_revision"),
        sa.CheckConstraint("revision > 0 AND jsonb_typeof(profile)='object'", name="ck_profile_content"),
        sa.CheckConstraint("actor_role IN ('owner','editor','support','admin')", name="ck_profile_actor"))
    op.create_table("business_profile_state",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("draft_version_id", UUID(as_uuid=True)),
        sa.Column("published_version_id", UUID(as_uuid=True)),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"), stamp("updated_at"),
        sa.ForeignKeyConstraint(["tenant_id", "draft_version_id"], ["business_profile_versions.tenant_id", "business_profile_versions.id"], name="fk_profile_draft_scope"),
        sa.ForeignKeyConstraint(["tenant_id", "published_version_id"], ["business_profile_versions.tenant_id", "business_profile_versions.id"], name="fk_profile_published_scope"),
        sa.CheckConstraint("generation >= 0", name="ck_profile_generation"))
    op.create_table("business_profile_events", uid(), tenant(),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor_account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("actor_role", sa.String(16), nullable=False), stamp(),
        sa.ForeignKeyConstraint(["tenant_id", "version_id"], ["business_profile_versions.tenant_id", "business_profile_versions.id"], name="fk_profile_event_scope"),
        sa.CheckConstraint("action IN ('draft_saved','published','rollback')", name="ck_profile_action"),
        sa.CheckConstraint("actor_role IN ('owner','editor','support','admin')", name="ck_profile_event_actor"))
    op.create_index("ix_profile_events_tenant", "business_profile_events", ["tenant_id", "created_at"])
    for table in ("business_profile_versions", "business_profile_state", "business_profile_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, app_support, app_runtime")
        # No runtime UPDATE/DELETE for revision or audit rows: append-only.
        privileges = "SELECT,INSERT,UPDATE" if table == "business_profile_state" else "SELECT,INSERT"
        op.execute(f"GRANT {privileges} ON {table} TO app_runtime")


def downgrade():
    # Operational rollback selects an old publication / old application image.
    # DDL downgrade is only for disposable databases, after intentional review.
    for table in ("business_profile_events", "business_profile_state", "business_profile_versions", "tenant_membership_events", "tenant_memberships"):
        op.drop_table(table)
