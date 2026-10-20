"""Unify QR/Meta authority and durable, tenant-scoped conversation history."""
from alembic import op

revision = "0027_unified_inbox"
down_revision = "0026_cancelled_notification"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.inbox_channel_links (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        channel_id uuid NOT NULL,
        provider text NOT NULL CHECK(provider IN ('qr','meta')),
        qr_channel_id uuid,
        meta_channel_id uuid,
        PRIMARY KEY(tenant_id,channel_id),
        FOREIGN KEY(tenant_id,qr_channel_id) REFERENCES public.qr_channels(tenant_id,id),
        FOREIGN KEY(tenant_id,meta_channel_id) REFERENCES public.whatsapp_channels(tenant_id,id),
        CHECK((provider='qr' AND qr_channel_id=channel_id AND meta_channel_id IS NULL)
           OR (provider='meta' AND meta_channel_id=channel_id AND qr_channel_id IS NULL)),
        CHECK((qr_channel_id IS NOT NULL) <> (meta_channel_id IS NOT NULL))
      );
      INSERT INTO public.inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id)
        SELECT DISTINCT tenant_id,channel_id,'qr',channel_id FROM public.inbox_controls;
      ALTER TABLE public.inbox_controls DROP CONSTRAINT inbox_controls_tenant_id_channel_id_fkey;
      ALTER TABLE public.inbox_controls ADD CONSTRAINT inbox_control_channel_link
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.inbox_channel_links(tenant_id,channel_id);

      -- Import existing Meta pauses conservatively. Their old timeout must not
      -- silently resume an operator's conversation after enabling the central.
      INSERT INTO public.inbox_channel_links(tenant_id,channel_id,provider,meta_channel_id)
        SELECT DISTINCT tenant_id,channel_id,'meta',channel_id FROM public.human_handoffs
        WHERE status='active' ON CONFLICT DO NOTHING;
      INSERT INTO public.inbox_controls(tenant_id,channel_id,contact_e164,mode)
        SELECT tenant_id,channel_id,contact_e164,'human' FROM public.human_handoffs
        WHERE status='active' ON CONFLICT DO NOTHING;

      CREATE TABLE public.inbox_messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        control_id uuid NOT NULL,
        direction text NOT NULL CHECK(direction IN ('in','out')),
        source text NOT NULL CHECK(source IN ('customer','manual','assistant','automation')),
        kind text NOT NULL DEFAULT 'text' CHECK(kind IN ('text','audio','unsupported')),
        body varchar(1500) NOT NULL DEFAULT '',
        external_id varchar(200),
        provider_at timestamptz,
        fingerprint varchar(64),
        actor_account_id uuid REFERENCES public.panel_accounts(id),
        actor_auth_version integer,
        control_revision bigint NOT NULL CHECK(control_revision>0),
        status text NOT NULL CHECK(status IN ('received','queued','sending','accepted','delivered','read','failed','uncertain')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(tenant_id,control_id) REFERENCES public.inbox_controls(tenant_id,id),
        UNIQUE(tenant_id,control_id,id),
        UNIQUE(tenant_id,control_id,direction,external_id),
        CHECK((direction='in' AND source='customer' AND status='received') OR
              (direction='out' AND source<>'customer' AND fingerprint IS NOT NULL)),
        CHECK(source<>'manual' OR (actor_account_id IS NOT NULL AND actor_auth_version IS NOT NULL AND actor_auth_version>0))
      );
      CREATE INDEX ix_inbox_messages_history ON public.inbox_messages(tenant_id,control_id,created_at DESC,id DESC);
      CREATE INDEX ix_inbox_messages_queue ON public.inbox_messages(status,created_at) WHERE status='queued';
      CREATE INDEX ix_inbox_messages_receipt ON public.inbox_messages(tenant_id,external_id) WHERE direction='out';
      CREATE TABLE public.inbox_reads (
        tenant_id uuid NOT NULL,
        control_id uuid NOT NULL,
        account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
        message_id uuid NOT NULL,
        read_at timestamptz NOT NULL,
        PRIMARY KEY(tenant_id,control_id,account_id),
        FOREIGN KEY(tenant_id,control_id,message_id) REFERENCES public.inbox_messages(tenant_id,control_id,id)
      );
      CREATE TABLE public.inbox_provider_receipts (
        tenant_id uuid NOT NULL,
        channel_id uuid NOT NULL,
        external_id varchar(200) NOT NULL,
        contact_e164 varchar(16) NOT NULL,
        status text NOT NULL CHECK(status IN ('accepted','delivered','read','failed')),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,channel_id,external_id),
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.inbox_channel_links(tenant_id,channel_id)
      );

      CREATE FUNCTION public.inbox_operator_version(t uuid,a uuid) RETURNS integer
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE v integer; r text; m record;
      BEGIN
        IF t IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN RETURN NULL; END IF;
        SELECT auth_version,role INTO v,r FROM public.panel_accounts WHERE id=a FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF r='admin' THEN RETURN v; END IF;
        SELECT active,role INTO m FROM public.tenant_memberships WHERE tenant_id=t AND account_id=a FOR SHARE;
        IF NOT FOUND OR NOT m.active OR m.role NOT IN ('owner','editor') THEN RETURN NULL; END IF;
        RETURN v;
      END $fn$;
      REVOKE ALL ON FUNCTION public.inbox_operator_version(uuid,uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.inbox_operator_version(uuid,uuid) TO app_runtime;
      CREATE FUNCTION public.inbox_meta_guard(t uuid,c uuid,assistant boolean) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE channel record; company record;
      BEGIN
        IF t IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid OR assistant IS NULL THEN RETURN false; END IF;
        SELECT status,bot_enabled INTO channel FROM public.whatsapp_channels WHERE tenant_id=t AND id=c FOR SHARE;
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT status,bot_enabled INTO company FROM public.tenants WHERE id=t FOR SHARE;
        IF NOT FOUND THEN RETURN false; END IF;
        RETURN company.status='active' AND channel.status IN ('testing','connected')
          AND (NOT assistant OR channel.bot_enabled AND company.bot_enabled);
      END $fn$;
      REVOKE ALL ON FUNCTION public.inbox_meta_guard(uuid,uuid,boolean) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.inbox_meta_guard(uuid,uuid,boolean) TO app_runtime;

      CREATE OR REPLACE FUNCTION public.inbox_channel_contact_allowed(t uuid,c uuid,phone text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        SELECT t=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS(
          SELECT 1 FROM public.tenants b WHERE b.id=t AND b.status='active' AND (
            EXISTS(SELECT 1 FROM public.qr_channels q JOIN public.tenant_memberships m
              ON m.tenant_id=q.tenant_id AND m.account_id=q.owner_account_id
              WHERE q.tenant_id=t AND q.id=c AND q.enabled AND m.active AND m.role='owner'
                AND q.allowed_senders @> jsonb_build_array(phone))
            OR EXISTS(SELECT 1 FROM public.whatsapp_channels w WHERE w.tenant_id=t AND w.id=c
                AND w.status IN ('testing','connected') AND phone ~ '^55[1-9][0-9]{9,10}$')))
      $fn$;
    """)
    setting = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    for table in ("inbox_channel_links", "inbox_messages", "inbox_provider_receipts", "inbox_reads"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT ON public.inbox_channel_links TO app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.inbox_messages TO app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.inbox_provider_receipts TO app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.inbox_reads TO app_runtime")
    op.execute("CREATE POLICY inbox_messages_scan ON public.inbox_messages FOR SELECT TO app_support USING(true)")
    op.execute("GRANT SELECT(id,tenant_id,status,created_at) ON public.inbox_messages TO app_support")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.inbox_messages) OR EXISTS(SELECT 1 FROM public.inbox_provider_receipts) OR
         EXISTS(SELECT 1 FROM public.inbox_channel_links WHERE provider='meta') THEN
        RAISE EXCEPTION 'Preserve unified inbox history and Meta controls before downgrade';
      END IF; END $$""")
    op.execute("DROP TABLE public.inbox_reads")
    op.execute("DROP TABLE public.inbox_messages")
    op.execute("DROP TABLE public.inbox_provider_receipts")
    op.execute("DROP FUNCTION public.inbox_operator_version(uuid,uuid)")
    op.execute("DROP FUNCTION public.inbox_meta_guard(uuid,uuid,boolean)")
    op.execute("ALTER TABLE public.inbox_controls DROP CONSTRAINT inbox_control_channel_link")
    op.execute("ALTER TABLE public.inbox_controls ADD FOREIGN KEY(tenant_id,channel_id) REFERENCES public.qr_channels(tenant_id,id)")
    op.execute("DROP TABLE public.inbox_channel_links")
    # Restore the exact QR-only authorization function of revision 0025.
    op.execute("""CREATE OR REPLACE FUNCTION public.inbox_channel_contact_allowed(t uuid,c uuid,phone text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        SELECT t=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS(
          SELECT 1 FROM public.qr_channels q JOIN public.tenants b ON b.id=q.tenant_id
          JOIN public.tenant_memberships m ON m.tenant_id=q.tenant_id AND m.account_id=q.owner_account_id
          WHERE q.tenant_id=t AND q.id=c AND q.enabled AND b.status='active' AND m.active AND m.role='owner'
            AND q.allowed_senders @> jsonb_build_array(phone)) $fn$""")
