"""Private diagnostic intentions, explicit confirmation and fenced proxy probes."""
from alembic import op

revision = "0066_subscription_diagnostics"
down_revision = "0065_subscription_sent_messages"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      ALTER TABLE public.subscription_device_events
        DROP CONSTRAINT subscription_device_events_kind_check,
        ADD CONSTRAINT subscription_device_events_kind_check
          CHECK(kind IN ('text','audio','call','status','outbound','resend','diagnostic'));
      CREATE TABLE public.subscription_diagnostic_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL CHECK(settings_revision>0),
        actor_account_id uuid NOT NULL,
        kind varchar(20) NOT NULL CHECK(kind IN ('message_test','proxy_test')),
        idempotency_key uuid NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=8192),
        confirmation_version char(64) CHECK(confirmation_version ~ '^[a-f0-9]{64}$'),
        status varchar(20) NOT NULL CHECK(status IN ('prepared','pending','running','succeeded','failed','uncertain')),
        effect_id uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL,
        confirmed_at timestamptz,
        execution_expires_at timestamptz,
        started_at timestamptz,
        finished_at timestamptz,
        claimed_owner uuid,
        claim_token uuid,
        claim_fence bigint NOT NULL DEFAULT 0 CHECK(claim_fence>=0),
        claim_expires_at timestamptz,
        result jsonb CHECK(result IS NULL OR jsonb_typeof(result)='object' AND octet_length(result::text)<=4096),
        UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_diagnostic_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT uq_subscription_diagnostic_effect UNIQUE(tenant_id,effect_id),
        CONSTRAINT fk_subscription_diagnostic_device FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        CONSTRAINT fk_subscription_diagnostic_actor FOREIGN KEY(tenant_id,actor_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_diagnostic_effect FOREIGN KEY(tenant_id,effect_id)
          REFERENCES public.subscription_device_effects(tenant_id,id),
        CONSTRAINT subscription_diagnostic_expiry_check CHECK(expires_at>created_at
          AND expires_at<=created_at+interval '120 seconds'
          AND (execution_expires_at IS NULL OR execution_expires_at>created_at)),
        CONSTRAINT subscription_diagnostic_kind_check CHECK(
          (kind='message_test' AND confirmation_version IS NOT NULL
            AND ((status='prepared' AND effect_id IS NULL AND confirmed_at IS NULL AND execution_expires_at IS NULL)
              OR (status='pending' AND effect_id IS NOT NULL AND confirmed_at IS NOT NULL AND execution_expires_at IS NOT NULL))
            AND claimed_owner IS NULL AND claim_token IS NULL AND claim_fence=0 AND claim_expires_at IS NULL AND result IS NULL)
          OR (kind='proxy_test' AND confirmation_version IS NULL AND effect_id IS NULL AND confirmed_at IS NULL
            AND execution_expires_at IS NOT NULL AND status<>'prepared')),
        CONSTRAINT subscription_diagnostic_claim_check CHECK(
          (claimed_owner IS NULL AND claim_token IS NULL AND claim_fence=0 AND claim_expires_at IS NULL AND started_at IS NULL)
          OR (kind='proxy_test' AND claimed_owner IS NOT NULL AND claim_token IS NOT NULL AND claim_fence>0
            AND claim_expires_at IS NOT NULL AND started_at IS NOT NULL AND claim_expires_at>started_at
            AND claim_expires_at<=execution_expires_at)),
        CONSTRAINT subscription_diagnostic_result_check CHECK(
          (kind='message_test' OR status IN ('pending','running')) AND result IS NULL AND finished_at IS NULL
          OR kind='proxy_test' AND status IN ('succeeded','failed','uncertain') AND result IS NOT NULL AND finished_at IS NOT NULL)
      );
      CREATE INDEX ix_subscription_diagnostic_device
        ON public.subscription_diagnostic_requests(tenant_id,device_id,created_at DESC,id DESC);
      CREATE INDEX ix_subscription_diagnostic_active
        ON public.subscription_diagnostic_requests(tenant_id,device_id,kind,execution_expires_at)
        WHERE status IN ('pending','running');
      ALTER TABLE public.subscription_diagnostic_requests ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.subscription_diagnostic_requests FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.subscription_diagnostic_requests FROM PUBLIC,app_runtime,app_support;
      GRANT SELECT,INSERT ON public.subscription_diagnostic_requests TO app_support;
      GRANT UPDATE(status,effect_id,confirmed_at,execution_expires_at,started_at,finished_at,
        claimed_owner,claim_token,claim_fence,claim_expires_at,result)
        ON public.subscription_diagnostic_requests TO app_support;
      CREATE POLICY subscription_diagnostic_requests_runtime
        ON public.subscription_diagnostic_requests FOR SELECT TO app_runtime
        USING(tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid));
      CREATE POLICY subscription_diagnostic_requests_support
        ON public.subscription_diagnostic_requests FOR ALL TO app_support USING(true) WITH CHECK(true);
      CREATE POLICY subscription_diagnostic_requests_migrator
        ON public.subscription_diagnostic_requests FOR ALL TO app_migrator USING(true) WITH CHECK(true);
    """)


def downgrade():
    raise RuntimeError("Preserve confirmed diagnostic and transport evidence; use a forward migration")
