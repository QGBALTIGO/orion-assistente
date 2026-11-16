"""Isolated subscription devices and durable, tenant-scoped effect ledgers.

No existing channel, plan, integration or message is activated by this migration.
"""
from alembic import op

revision = "0062_subscription_devices"
down_revision = "0061_appointment_consent"
branch_labels = None
depends_on = None


TABLES = (
    "subscription_devices", "subscription_device_operations",
    "subscription_device_events", "subscription_device_effects",
    "subscription_device_cooldowns", "subscription_device_audio_usage",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.subscription_devices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        channel_id uuid NOT NULL,
        owner_account_id uuid NOT NULL,
        generation uuid NOT NULL DEFAULT gen_random_uuid(),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        applied_revision bigint NOT NULL DEFAULT 1 CHECK(applied_revision>0),
        name varchar(100) NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),
        settings jsonb NOT NULL CHECK(jsonb_typeof(settings)='object'),
        proxy_secret_encrypted text,
        restart_required boolean NOT NULL DEFAULT false,
        status varchar(20) NOT NULL DEFAULT 'disabled'
          CHECK(status IN ('disabled','connecting','qr','pairing','connected','disconnected','error','removing','removed')),
        runtime jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(runtime)='object'),
        runtime_at timestamptz,
        webhook_failures integer NOT NULL DEFAULT 0 CHECK(webhook_failures>=0),
        idempotency_key uuid NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        removed_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,generation), UNIQUE(tenant_id,idempotency_key),
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.qr_channels(tenant_id,id),
        FOREIGN KEY(tenant_id,owner_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id),
        CHECK((removed_at IS NULL)=(status<>'removed'))
      );
      CREATE UNIQUE INDEX uq_subscription_device_active_tenant
        ON public.subscription_devices(tenant_id) WHERE removed_at IS NULL;
      CREATE UNIQUE INDEX uq_subscription_device_active_channel
        ON public.subscription_devices(channel_id) WHERE removed_at IS NULL;
      CREATE TABLE public.subscription_device_operations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL CHECK(settings_revision>0),
        idempotency_key uuid NOT NULL,
        action varchar(20) NOT NULL CHECK(action IN ('connect','restart','relink','remove','pair')),
        status varchar(20) NOT NULL DEFAULT 'pending'
          CHECK(status IN ('pending','running','succeeded','failed','uncertain')),
        request_payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(request_payload)='object'),
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        started_at timestamptz,
        finished_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,device_id,idempotency_key),
        FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation)
      );
      CREATE UNIQUE INDEX uq_subscription_device_pending_control
        ON public.subscription_device_operations(device_id) WHERE status IN ('pending','running','uncertain');
      CREATE TABLE public.subscription_device_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        provider_event_id varchar(200) NOT NULL,
        kind varchar(20) NOT NULL CHECK(kind IN ('text','audio','call','status','outbound')),
        occurred_at timestamptz NOT NULL,
        received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        UNIQUE(tenant_id,id), UNIQUE(device_id,provider_event_id),
        FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation)
      );
      CREATE INDEX ix_subscription_device_event_metrics
        ON public.subscription_device_events(tenant_id,device_id,occurred_at)
        WHERE kind IN ('text','audio','outbound');
      CREATE UNIQUE INDEX uq_subscription_device_outbound_message
        ON public.subscription_device_events(device_id,(payload->>'message_id'))
        WHERE kind='outbound';
      CREATE TABLE public.subscription_device_effects (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL CHECK(settings_revision>0),
        event_id uuid NOT NULL,
        effect_key varchar(80) NOT NULL,
        type varchar(20) NOT NULL CHECK(type IN ('send_text','reject_call','transcribe','webhook')),
        purpose varchar(40) NOT NULL,
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        status varchar(20) NOT NULL DEFAULT 'pending'
          CHECK(status IN ('pending','running','accepted','failed','uncertain','not_sent')),
        attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '10 minutes',
        authorized_at timestamptz,
        claimed_owner uuid,
        claimed_fence bigint,
        provider_id varchar(200),
        error_code varchar(80),
        audio_reserved_seconds integer NOT NULL DEFAULT 0 CHECK(audio_reserved_seconds>=0),
        previous_effect uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(device_id,event_id,effect_key),
        FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        FOREIGN KEY(tenant_id,event_id) REFERENCES public.subscription_device_events(tenant_id,id),
        FOREIGN KEY(tenant_id,previous_effect) REFERENCES public.subscription_device_effects(tenant_id,id),
        CHECK((claimed_owner IS NULL)=(claimed_fence IS NULL))
      );
      CREATE INDEX ix_subscription_device_effect_due
        ON public.subscription_device_effects(device_id,due_at) WHERE status IN ('pending','failed','running');
      CREATE TABLE public.subscription_device_cooldowns (
        tenant_id uuid NOT NULL,
        device_id uuid NOT NULL,
        phone varchar(15) NOT NULL CHECK(phone ~ '^[1-9][0-9]{7,14}$'),
        reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        effect_id uuid NOT NULL,
        PRIMARY KEY(tenant_id,device_id,phone),
        FOREIGN KEY(tenant_id,device_id) REFERENCES public.subscription_devices(tenant_id,id),
        FOREIGN KEY(tenant_id,effect_id) REFERENCES public.subscription_device_effects(tenant_id,id)
      );
      CREATE TABLE public.subscription_device_audio_usage (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        usage_day date NOT NULL,
        reserved_seconds bigint NOT NULL DEFAULT 0 CHECK(reserved_seconds>=0),
        attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
        PRIMARY KEY(tenant_id,usage_day)
      );
    """)
    for table in TABLES:
        op.execute(f"""
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support;
          GRANT SELECT,INSERT,UPDATE ON public.{table} TO app_support;
          CREATE POLICY {table}_runtime ON public.{table} FOR SELECT TO app_runtime
            USING(tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid));
          CREATE POLICY {table}_support ON public.{table} FOR ALL TO app_support
            USING(true) WITH CHECK(true);
          CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator
            USING(true) WITH CHECK(true);
        """)
    # Runtime can inspect its own safe metadata, never proxy secrets or private
    # event/effect bodies. Writes go through the owner/broker service boundary.
    op.execute("""
      GRANT SELECT(id,tenant_id,channel_id,name,revision,status,created_at,updated_at,removed_at)
        ON public.subscription_devices TO app_runtime;
      GRANT SELECT(id,tenant_id,device_id,action,status,error_code,created_at,finished_at)
        ON public.subscription_device_operations TO app_runtime;
      GRANT SELECT ON public.subscription_device_audio_usage TO app_runtime;
    """)


def downgrade():
    raise RuntimeError("Preserve device identities and operation evidence; use a forward migration")
