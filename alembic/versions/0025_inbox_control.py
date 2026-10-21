"""Persistent per-channel arbitration and non-expiring uncertain send slots."""
from alembic import op

revision = "0025_inbox_control"
down_revision = "0024_inbox_tools"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.inbox_controls (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        channel_id uuid NOT NULL,
        contact_e164 varchar(16) NOT NULL CHECK(contact_e164 ~ '^\\+55[1-9][0-9]{9,10}$'),
        mode varchar(16) NOT NULL DEFAULT 'ai' CHECK(mode IN ('ai','human','paused','waiting','closed')),
        assigned_account_id uuid REFERENCES public.panel_accounts(id),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        pending_operation uuid,
        pending_fingerprint varchar(64),
        pending_since timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,channel_id,contact_e164),
        UNIQUE(tenant_id,id),
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.qr_channels(tenant_id,id),
        CHECK((pending_operation IS NULL)=(pending_fingerprint IS NULL)),
        CHECK((pending_operation IS NULL)=(pending_since IS NULL)),
        CHECK(mode IN ('human','waiting') OR assigned_account_id IS NULL)
      );
      CREATE INDEX ix_inbox_control_queue ON public.inbox_controls(tenant_id,channel_id,mode,updated_at);
      CREATE TABLE public.inbox_control_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        control_id uuid NOT NULL,
        actor_account_id uuid REFERENCES public.panel_accounts(id),
        source varchar(16) NOT NULL CHECK(source IN ('customer','operator')),
        previous_mode varchar(16) NOT NULL,
        mode varchar(16) NOT NULL,
        revision bigint NOT NULL CHECK(revision>0),
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(tenant_id,control_id) REFERENCES public.inbox_controls(tenant_id,id),
        UNIQUE(tenant_id,control_id,revision)
      );
      CREATE FUNCTION public.inbox_channel_contact_allowed(t uuid,c uuid,phone text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        SELECT t=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS(
          SELECT 1 FROM public.qr_channels q JOIN public.tenants b ON b.id=q.tenant_id
          JOIN public.tenant_memberships m ON m.tenant_id=q.tenant_id AND m.account_id=q.owner_account_id
          WHERE q.tenant_id=t AND q.id=c AND q.enabled AND b.status='active' AND m.active AND m.role='owner'
            AND q.allowed_senders @> jsonb_build_array(phone))
      $fn$;
      REVOKE ALL ON FUNCTION public.inbox_channel_contact_allowed(uuid,uuid,text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.inbox_channel_contact_allowed(uuid,uuid,text) TO app_runtime;
    """)
    setting = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    op.execute("ALTER TABLE public.inbox_controls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.inbox_controls FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY inbox_controls_runtime ON public.inbox_controls TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
    op.execute("CREATE POLICY inbox_controls_migrator ON public.inbox_controls TO app_migrator USING(true) WITH CHECK(true)")
    op.execute("REVOKE ALL ON public.inbox_controls FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.inbox_controls TO app_runtime")
    op.execute("ALTER TABLE public.inbox_control_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.inbox_control_events FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY inbox_control_events_runtime ON public.inbox_control_events TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
    op.execute("CREATE POLICY inbox_control_events_migrator ON public.inbox_control_events TO app_migrator USING(true) WITH CHECK(true)")
    op.execute("REVOKE ALL ON public.inbox_control_events FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT ON public.inbox_control_events TO app_runtime")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.inbox_controls) THEN
      RAISE EXCEPTION 'Preserve conversation controls before downgrade'; END IF; END $$""")
    op.execute("DROP TABLE public.inbox_control_events")
    op.execute("DROP TABLE public.inbox_controls")
    op.execute("DROP FUNCTION public.inbox_channel_contact_allowed(uuid,uuid,text)")
