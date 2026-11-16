"""Private one-shot batches with intentional duplicate rows and scoped effects."""
from alembic import op

revision = "0070_subscription_bulk"
down_revision = "0069_subscription_schedules"
branch_labels = None
depends_on = None

TABLES = (
    "subscription_bulk_batches", "subscription_bulk_recipients", "subscription_bulk_requests",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.subscription_bulk_batches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        owner_account_id uuid NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        run_version char(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        interval_seconds smallint NOT NULL DEFAULT 10,
        snapshot jsonb NOT NULL,
        confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        first_admitted_at timestamptz,
        last_admitted_at timestamptz,
        next_admission_at timestamptz,
        finished_at timestamptz,
        cancelled_at timestamptz,
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_bulk_scope UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_bulk_version UNIQUE(tenant_id,id,run_version),
        CONSTRAINT fk_subscription_bulk_owner FOREIGN KEY(tenant_id,owner_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT subscription_bulk_values CHECK(
          version>0 AND run_version ~ '^[a-f0-9]{64}$'
          AND status IN ('pending','running','completed','failed','uncertain','cancelled')
          AND interval_seconds BETWEEN 10 AND 60
          AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=1048576),
        CONSTRAINT subscription_bulk_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('completed','failed','uncertain','cancelled') AND finished_at IS NOT NULL),
        CONSTRAINT subscription_bulk_admission CHECK(
          (status<>'running' OR first_admitted_at IS NOT NULL)
          AND (first_admitted_at IS NULL AND last_admitted_at IS NULL
            OR first_admitted_at IS NOT NULL AND last_admitted_at IS NOT NULL
              AND last_admitted_at>=first_admitted_at)),
        CONSTRAINT subscription_bulk_cancellation CHECK(
          (status<>'cancelled' OR cancelled_at IS NOT NULL)
          AND (cancelled_at IS NULL OR cancelled_at>=confirmed_at))
      );
      CREATE INDEX ix_subscription_bulk_list
        ON public.subscription_bulk_batches(tenant_id,created_at DESC,id DESC);
      CREATE INDEX ix_subscription_bulk_pending
        ON public.subscription_bulk_batches(tenant_id,next_admission_at,confirmed_at,id)
        WHERE status IN ('pending','running');

      CREATE TABLE public.subscription_bulk_recipients (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        bulk_id uuid NOT NULL,
        row_key uuid NOT NULL,
        ordinal integer NOT NULL,
        run_version char(64) NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL,
        phone varchar(15) NOT NULL,
        name varchar(120) NOT NULL,
        source varchar(8) NOT NULL,
        origins jsonb NOT NULL,
        manual_consent boolean NOT NULL DEFAULT false,
        permission_version bigint NOT NULL,
        template_id uuid,
        template_revision bigint,
        context jsonb NOT NULL,
        plan jsonb NOT NULL,
        plan_hash char(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        admitted_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_bulk_recipient_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_bulk_recipient_scope UNIQUE(tenant_id,bulk_id,id),
        CONSTRAINT uq_subscription_bulk_recipient_key UNIQUE(tenant_id,bulk_id,row_key),
        CONSTRAINT uq_subscription_bulk_recipient_order UNIQUE(tenant_id,bulk_id,ordinal),
        CONSTRAINT fk_subscription_bulk_recipient_batch FOREIGN KEY(tenant_id,bulk_id)
          REFERENCES public.subscription_bulk_batches(tenant_id,id),
        CONSTRAINT fk_subscription_bulk_recipient_version FOREIGN KEY(tenant_id,bulk_id,run_version)
          REFERENCES public.subscription_bulk_batches(tenant_id,id,run_version),
        CONSTRAINT fk_subscription_bulk_recipient_device FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        CONSTRAINT fk_subscription_bulk_recipient_template FOREIGN KEY(tenant_id,template_id)
          REFERENCES public.subscription_chatbot_templates(tenant_id,id),
        CONSTRAINT subscription_bulk_recipient_values CHECK(
          run_version ~ '^[a-f0-9]{64}$' AND plan_hash ~ '^[a-f0-9]{64}$'
          AND ordinal BETWEEN 1 AND 500 AND phone ~ '^[1-9][0-9]{7,14}$' AND length(trim(name))>0
          AND settings_revision>0 AND permission_version>=0
          AND jsonb_typeof(context)='object' AND octet_length(context::text)<=131072
          AND jsonb_typeof(origins)='array' AND octet_length(origins::text)<=131072
          AND jsonb_typeof(plan)='array' AND jsonb_array_length(plan) BETWEEN 1 AND 3
          AND octet_length(plan::text)<=131072
          AND status IN ('pending','running','sent','failed','uncertain','cancelled')),
        CONSTRAINT subscription_bulk_recipient_source CHECK(
          source='manual' AND origins='[]'::jsonb AND manual_consent
          OR source='catalog' AND jsonb_array_length(origins) BETWEEN 1 AND 101 AND NOT manual_consent),
        CONSTRAINT subscription_bulk_recipient_template CHECK(
          template_id IS NULL AND template_revision IS NULL
          OR template_id IS NOT NULL AND template_revision IS NOT NULL AND template_revision>0),
        CONSTRAINT subscription_bulk_recipient_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('sent','failed','uncertain','cancelled') AND finished_at IS NOT NULL),
        CONSTRAINT subscription_bulk_recipient_admission CHECK(
          status NOT IN ('running','sent','uncertain') OR admitted_at IS NOT NULL)
      );
      CREATE INDEX ix_subscription_bulk_recipient_pending
        ON public.subscription_bulk_recipients(tenant_id,bulk_id,ordinal,device_id)
        WHERE status='pending' AND admitted_at IS NULL;
      CREATE INDEX ix_subscription_bulk_recipient_rate
        ON public.subscription_bulk_recipients(tenant_id,admitted_at) WHERE admitted_at IS NOT NULL;
      CREATE INDEX ix_subscription_bulk_recipient_contact
        ON public.subscription_bulk_recipients(tenant_id,phone,status);
      CREATE INDEX ix_subscription_bulk_recipient_history
        ON public.subscription_bulk_recipients(tenant_id,finished_at DESC,id DESC)
        WHERE status='sent';

      CREATE TABLE public.subscription_bulk_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL,
        idempotency_key uuid NOT NULL,
        kind varchar(16) NOT NULL,
        request_fingerprint char(64) NOT NULL,
        bulk_id uuid,
        status varchar(16) NOT NULL,
        review_version char(64),
        snapshot jsonb,
        preview jsonb,
        result jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz,
        confirmed_at timestamptz,
        CONSTRAINT uq_subscription_bulk_request_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_bulk_request_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT fk_subscription_bulk_request_actor FOREIGN KEY(tenant_id,actor_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_bulk_request_batch FOREIGN KEY(tenant_id,bulk_id)
          REFERENCES public.subscription_bulk_batches(tenant_id,id),
        CONSTRAINT subscription_bulk_request_values CHECK(
          kind IN ('review','cancel') AND status IN ('prepared','succeeded')
          AND request_fingerprint ~ '^[a-f0-9]{64}$'
          AND (result IS NULL OR jsonb_typeof(result)='object' AND octet_length(result::text)<=4194304)),
        CONSTRAINT subscription_bulk_request_scope CHECK(
          kind='review' AND ((status='prepared' AND bulk_id IS NULL)
            OR (status='succeeded' AND bulk_id IS NOT NULL))
          OR kind='cancel' AND bulk_id IS NOT NULL),
        CONSTRAINT subscription_bulk_request_review CHECK(
          kind='review' AND review_version IS NOT NULL AND review_version ~ '^[a-f0-9]{64}$'
            AND snapshot IS NOT NULL AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=67108864
            AND preview IS NOT NULL AND jsonb_typeof(preview)='object' AND octet_length(preview::text)<=1048576
            AND expires_at IS NOT NULL AND expires_at>created_at AND expires_at<=created_at+interval '120 seconds'
            AND ((status='prepared' AND result IS NULL AND confirmed_at IS NULL)
              OR (status='succeeded' AND result IS NOT NULL AND confirmed_at IS NOT NULL))
          OR kind='cancel' AND status='succeeded' AND result IS NOT NULL
            AND snapshot IS NULL AND preview IS NULL AND review_version IS NULL
            AND expires_at IS NULL AND confirmed_at IS NULL)
      );
      CREATE INDEX ix_subscription_bulk_request_actor
        ON public.subscription_bulk_requests(tenant_id,actor_account_id,created_at DESC,id DESC);

      ALTER TABLE public.subscription_device_events DROP CONSTRAINT subscription_device_events_kind_check,
        ADD CONSTRAINT subscription_device_events_kind_check
          CHECK(kind IN ('text','audio','call','status','outbound','resend','diagnostic','campaign','scheduled','bulk'));
      ALTER TABLE public.subscription_device_effects DROP CONSTRAINT subscription_device_effects_type_check,
        ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action','campaign_action','scheduled_action','bulk_action')),
        ADD COLUMN bulk_id uuid,
        ADD COLUMN bulk_recipient_id uuid,
        ADD COLUMN bulk_part_index smallint,
        ADD CONSTRAINT subscription_device_effect_bulk_scope CHECK(
          type='bulk_action' AND purpose='bulk_action' AND bulk_id IS NOT NULL
            AND bulk_recipient_id IS NOT NULL AND bulk_part_index IS NOT NULL AND bulk_part_index BETWEEN 0 AND 2
          OR type<>'bulk_action' AND bulk_id IS NULL AND bulk_recipient_id IS NULL AND bulk_part_index IS NULL),
        ADD CONSTRAINT fk_subscription_device_effect_bulk FOREIGN KEY(tenant_id,bulk_id)
          REFERENCES public.subscription_bulk_batches(tenant_id,id),
        ADD CONSTRAINT fk_subscription_device_effect_bulk_recipient
          FOREIGN KEY(tenant_id,bulk_id,bulk_recipient_id)
          REFERENCES public.subscription_bulk_recipients(tenant_id,bulk_id,id),
        ADD CONSTRAINT uq_subscription_device_effect_bulk_part
          UNIQUE(tenant_id,bulk_recipient_id,bulk_part_index);
    """)
    updates = {
        "subscription_bulk_batches": "version,status,first_admitted_at,last_admitted_at,next_admission_at,finished_at,cancelled_at,error_code,updated_at",
        "subscription_bulk_recipients": "status,admitted_at,finished_at,error_code",
        "subscription_bulk_requests": "bulk_id,status,result,confirmed_at",
    }
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT ON public.{table} TO app_support")
        op.execute(f"GRANT UPDATE({updates[table]}) ON public.{table} TO app_support")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR SELECT TO app_runtime "
                   "USING(tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid))")
        op.execute(f"CREATE POLICY {table}_support ON public.{table} FOR ALL TO app_support USING(true) WITH CHECK(true)")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")


def downgrade():
    raise RuntimeError("Preserve bulk audiences, revocations and transport evidence; use a forward migration")
