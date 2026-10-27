"""Monthly company/professional goals with immutable monthly configuration.

Revision ID: 0037_recurring_goals
Revises: 0036_site_publication

Only active, complete calendar-month goals covering the tenant's current month
are adopted. Expired/custom intervals and appointment/financial facts are not
changed. With duplicate candidates, the most recently edited one recurs; the
others retain their exact legacy interval. Alembic's transactional revision
record and the revision uniqueness key make repeated upgrade-head a no-op.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0037_recurring_goals"
down_revision = "0036_site_publication"
branch_labels = None
depends_on = None

TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"


def upgrade():
    op.execute("LOCK TABLE professional_goals IN SHARE ROW EXCLUSIVE MODE")
    op.alter_column("professional_goals", "professional_id", nullable=True)
    op.add_column("professional_goals", sa.Column("recurrence", sa.String(12), nullable=False, server_default="none"))
    op.add_column("professional_goals", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_professional_goal_recurrence", "professional_goals", "recurrence IN ('none','monthly')")
    op.create_check_constraint("ck_professional_goal_deleted", "professional_goals", "deleted_at IS NULL OR NOT active")
    op.create_table("professional_goal_revisions",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("goal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_month", sa.Date(), nullable=False),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.String(24), nullable=False),
        sa.Column("target_value", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("tenant_id", "goal_id", "version", name="pk_professional_goal_revision"),
        sa.ForeignKeyConstraint(["tenant_id", "goal_id"], ["professional_goals.tenant_id", "professional_goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"], ["professionals.tenant_id", "professionals.id"]),
        sa.CheckConstraint("version>=1 AND target_value BETWEEN 1 AND 1000000000", name="ck_goal_revision_value"),
        sa.CheckConstraint("metric IN ('revenue_cents','appointments')", name="ck_goal_revision_metric"),
        sa.CheckConstraint("EXTRACT(DAY FROM effective_month)=1", name="ck_goal_revision_month"),
        sa.CheckConstraint("NOT deleted OR NOT active", name="ck_goal_revision_deleted"))
    op.execute("""CREATE INDEX ix_goal_revision_month
        ON professional_goal_revisions(tenant_id,goal_id,effective_month DESC,version DESC)""")
    op.execute("""WITH candidates AS (
        SELECT g.id,row_number() OVER (
            PARTITION BY g.tenant_id,g.professional_id,g.metric
            ORDER BY g.updated_at DESC,g.created_at DESC,g.id DESC) AS choice
        FROM professional_goals g JOIN tenants t ON t.id=g.tenant_id
        WHERE g.active AND g.recurrence='none'
          AND g.start_date=date_trunc('month',now() AT TIME ZONE t.timezone)::date
          AND g.end_date=(g.start_date+interval '1 month'-interval '1 day')::date
    ) UPDATE professional_goals g SET recurrence='monthly'
      FROM candidates c WHERE c.id=g.id AND c.choice=1""")
    op.execute("""INSERT INTO professional_goal_revisions
        (tenant_id,goal_id,version,effective_month,professional_id,metric,target_value,active,deleted,timezone,created_at)
        SELECT g.tenant_id,g.id,g.version,g.start_date,g.professional_id,g.metric,g.target_value,g.active,false,t.timezone,g.updated_at
        FROM professional_goals g JOIN tenants t ON t.id=g.tenant_id WHERE g.recurrence='monthly'
        ON CONFLICT (tenant_id,goal_id,version) DO NOTHING""")
    op.execute("""CREATE UNIQUE INDEX uq_professional_goal_active_monthly
        ON professional_goals(tenant_id,COALESCE(professional_id,'00000000-0000-0000-0000-000000000000'::uuid),metric)
        WHERE recurrence='monthly' AND active AND deleted_at IS NULL""")
    table = "professional_goal_revisions"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={TENANT}) WITH CHECK(tenant_id={TENANT})")
    op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
    op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
    op.execute(f"GRANT SELECT,INSERT ON {table} TO app_runtime")
    op.execute("""CREATE FUNCTION public.goal_history_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        IF current_user<>'app_migrator' THEN
            RAISE EXCEPTION 'Monthly goal history is immutable' USING ERRCODE='42501';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.goal_history_immutable() FROM PUBLIC")
    op.execute("""CREATE TRIGGER goal_history_immutable BEFORE UPDATE OR DELETE ON professional_goal_revisions
        FOR EACH ROW EXECUTE FUNCTION public.goal_history_immutable()""")
    # The prior API can coexist briefly during pre-deploy. Its generic update
    # must not mutate an adopted monthly goal without appending its revision.
    op.execute("""CREATE FUNCTION public.guard_monthly_goal_writer() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        IF current_user<>'app_migrator' AND current_setting('app.monthly_goal_writer',true) IS DISTINCT FROM 'on'
           AND (NEW.recurrence='monthly' OR (TG_OP='UPDATE' AND OLD.recurrence='monthly')) THEN
            RAISE EXCEPTION 'Monthly goals require the current revision writer' USING ERRCODE='55000';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.guard_monthly_goal_writer() FROM PUBLIC")
    op.execute("""CREATE TRIGGER guard_monthly_goal_writer BEFORE INSERT OR UPDATE ON professional_goals
        FOR EACH ROW EXECUTE FUNCTION public.guard_monthly_goal_writer()""")


def downgrade():
    raise RuntimeError("Recurring goal history requires reviewed forward recovery")
