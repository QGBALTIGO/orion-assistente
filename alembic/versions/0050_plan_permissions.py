"""Scoped manual plan control, immutable audit and safe runtime projection.

No customer or financial record is changed by this schema migration. The
reviewed release entrypoint applies the separately authorized initial grants.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0050_plan_permissions"
down_revision = "0049_preserved_phone_guard"
branch_labels = None
depends_on = None


def _private_table(name):
    op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON public.{name} FROM PUBLIC,app_runtime")
    op.execute(f"CREATE POLICY {name}_migrator ON public.{name} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
    op.execute(f"CREATE POLICY {name}_support ON public.{name} FOR ALL TO app_support USING(true) WITH CHECK(true)")


def upgrade():
    op.add_column("panel_accounts", sa.Column("last_access_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("GRANT SELECT(last_access_at),UPDATE(last_access_at) ON public.panel_accounts TO app_support")
    op.create_table("tenant_plan_controls",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("manual_plan_code", sa.String(20)),
        sa.Column("manual_started_at", sa.DateTime(timezone=True)),
        sa.Column("manual_expires_at", sa.DateTime(timezone=True)),
        sa.Column("manual_actor_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("manual_reason", sa.String(500)),
        sa.Column("platform_account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("version>0", name="ck_tenant_plan_version"),
        sa.CheckConstraint("manual_plan_code IS NULL OR manual_plan_code IN ('essencial','profissional','elite')", name="ck_tenant_manual_code"),
        sa.CheckConstraint("(manual_plan_code IS NULL AND manual_started_at IS NULL AND manual_expires_at IS NULL AND manual_actor_id IS NULL AND manual_reason IS NULL) OR (manual_plan_code IS NOT NULL AND manual_started_at IS NOT NULL AND manual_actor_id IS NOT NULL AND (manual_expires_at IS NULL OR manual_expires_at>manual_started_at))", name="ck_tenant_manual_shape"),
        sa.CheckConstraint("platform_account_id IS NULL OR manual_plan_code IS NULL", name="ck_tenant_platform_plan"),
    )
    _private_table("tenant_plan_controls")
    op.execute("GRANT SELECT ON public.tenant_plan_controls TO app_support")
    editable = "version,manual_plan_code,manual_started_at,manual_expires_at,manual_actor_id,manual_reason,updated_at"
    op.execute(f"GRANT INSERT(tenant_id,{editable}),UPDATE({editable}) ON public.tenant_plan_controls TO app_support")
    op.create_table("tenant_plan_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("previous_plan", JSONB(), nullable=False),
        sa.Column("new_plan", JSONB(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("access_type", sa.String(20)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "operation_id", name="uq_tenant_plan_operation"),
        sa.CheckConstraint("action IN ('manual_assign','manual_remove','bootstrap_essential','bootstrap_platform')", name="ck_tenant_plan_action"),
        sa.CheckConstraint("access_type IS NULL OR access_type IN ('indefinite','temporary')", name="ck_tenant_plan_access_type"),
        sa.CheckConstraint("request_fingerprint ~ '^[a-f0-9]{64}$'", name="ck_tenant_plan_fingerprint"),
        sa.CheckConstraint("jsonb_typeof(previous_plan)='object' AND jsonb_typeof(new_plan)='object'", name="ck_tenant_plan_audit_objects"),
    )
    _private_table("tenant_plan_audit")
    op.create_index("ix_tenant_plan_audit_history", "tenant_plan_audit", ["tenant_id", "occurred_at"])
    op.execute("GRANT SELECT,INSERT ON public.tenant_plan_audit TO app_support")
    op.create_table("plan_permissions_release",
        sa.Column("marker", sa.String(32), primary_key=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bootstrap_count", sa.Integer(), nullable=False),
        sa.Column("bootstrap_admin", UUID(as_uuid=True)),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("marker='0050_plan_permissions' AND bootstrap_count>=0", name="ck_plan_release_marker"),
    )
    _private_table("plan_permissions_release")
    # Serialize rights changes with protected domain transactions. A snapshot
    # itself is read-only; locks are acquired by the central Python resolver.
    op.execute("""
      CREATE FUNCTION public.orion_lock_plan_change() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      BEGIN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('orion-plan-v1:'||NEW.tenant_id::text,0));
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION public.orion_lock_plan_change() FROM PUBLIC;
      CREATE TRIGGER tenant_plan_serial BEFORE INSERT OR UPDATE ON public.tenant_plan_controls
        FOR EACH ROW EXECUTE FUNCTION public.orion_lock_plan_change();
      CREATE TRIGGER commercial_plan_serial BEFORE INSERT OR UPDATE ON public.subscriptions
        FOR EACH ROW EXECUTE FUNCTION public.orion_lock_plan_change();
      CREATE TRIGGER cakto_plan_serial BEFORE INSERT OR UPDATE ON public.orion_cakto_bindings
        FOR EACH ROW EXECUTE FUNCTION public.orion_lock_plan_change();
      CREATE TRIGGER trial_plan_serial BEFORE INSERT OR UPDATE ON public.orion_plan_trial_requests
        FOR EACH ROW EXECUTE FUNCTION public.orion_lock_plan_change();
      CREATE FUNCTION public.orion_plan_snapshot(target uuid) RETURNS jsonb
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
      BEGIN
        IF session_user='app_runtime' THEN
          IF target IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
            RAISE EXCEPTION 'tenant_plan_scope_required' USING ERRCODE='42501';
          END IF;
        ELSIF session_user<>'app_support' AND NOT pg_catalog.pg_has_role(session_user,'app_migrator','MEMBER') THEN
          RAISE EXCEPTION 'tenant_plan_role_required' USING ERRCODE='42501';
        END IF;
        RETURN (SELECT jsonb_build_object(
          'tenant_id',t.id,'tenant_status',t.status,'version',COALESCE(c.version,0),
          'platform_workspace',COALESCE(c.platform_account_id IS NOT NULL AND a.role='admin'
            AND COALESCE(ac.state,'active')='active' AND EXISTS(
              SELECT 1 FROM public.tenant_memberships m WHERE m.tenant_id=t.id
              AND m.account_id=c.platform_account_id AND m.role='owner' AND m.active),false),
          'manual',CASE WHEN c.manual_plan_code IS NULL THEN NULL ELSE jsonb_build_object(
            'plan_code',c.manual_plan_code,'started_at',c.manual_started_at,'expires_at',c.manual_expires_at) END,
          'commercial',CASE WHEN s.id IS NULL THEN NULL ELSE jsonb_build_object(
            'id',s.id,'code',p.code,'name',p.name,'monthly_cents',p.monthly_cents,
            'max_professionals',p.max_professionals,'status',s.status,'trial_ends_at',s.trial_ends_at,
            'grace_until',s.grace_until,'last_verified_payment_at',s.last_verified_payment_at,
            'updated_at',s.updated_at,'cakto_bound',COALESCE(b.subscription_id=s.id
              AND b.external_subscription_id=s.external_subscription_id
              AND p.code='orion_'||b.plan_code AND p.active,false)) END,
          'trial',CASE WHEN r.id IS NULL THEN NULL ELSE jsonb_build_object('id',r.id,
            'status',r.status,'started_at',r.started_at,'ends_at',r.ends_at,'version',r.version) END,
          'active_professionals',(SELECT count(*) FROM public.professionals pr WHERE pr.tenant_id=t.id AND pr.active),
          'contact_phone',(SELECT bs.config->>'contact_phone' FROM public.business_settings bs WHERE bs.tenant_id=t.id)
        ) FROM public.tenants t LEFT JOIN public.tenant_plan_controls c ON c.tenant_id=t.id
          LEFT JOIN public.panel_accounts a ON a.id=c.platform_account_id
          LEFT JOIN public.admin_account_controls ac ON ac.account_id=a.id
          LEFT JOIN public.subscriptions s ON s.tenant_id=t.id
          LEFT JOIN public.plans p ON p.id=s.plan_id
          LEFT JOIN public.orion_cakto_bindings b ON b.tenant_id=t.id
          LEFT JOIN public.orion_plan_trial_requests r ON r.tenant_id=t.id
          WHERE t.id=target);
      END $$;
      REVOKE ALL ON FUNCTION public.orion_plan_snapshot(uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.orion_plan_snapshot(uuid) TO app_runtime,app_support;
      CREATE FUNCTION public.orion_plan_release_ready() RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT EXISTS(SELECT 1 FROM public.plan_permissions_release
          WHERE marker='0050_plan_permissions' AND completed_at IS NOT NULL)
      $$;
      REVOKE ALL ON FUNCTION public.orion_plan_release_ready() FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.orion_plan_release_ready() TO app_runtime,app_support;
    """)


def downgrade():
    raise RuntimeError("Plan grants and audit history must be preserved; use a reviewed forward migration")
