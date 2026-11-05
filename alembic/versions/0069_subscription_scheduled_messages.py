"""Private scheduled series, immutable occurrences and scoped transport effects."""
from alembic import op

revision = "0069_subscription_schedules"
down_revision = "0068_subscription_campaigns"
branch_labels = None
depends_on = None

TABLES = (
    "subscription_schedules", "subscription_schedule_runs",
    "subscription_schedule_recipients", "subscription_schedule_requests",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.subscription_schedules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        owner_account_id uuid NOT NULL,
        title varchar(120) NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        status varchar(16) NOT NULL DEFAULT 'active',
        definition jsonb NOT NULL,
        snapshot jsonb NOT NULL,
        run_version char(64) NOT NULL,
        device_id uuid NOT NULL,
        template_id uuid,
        template_revision bigint,
        timezone varchar(64) NOT NULL,
        anchor_date date NOT NULL,
        anchor_time char(5) NOT NULL,
        interval_seconds smallint NOT NULL,
        repeat_every_30_days boolean NOT NULL DEFAULT false,
        confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        cancelled_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_schedule_scope UNIQUE(tenant_id,id),
        CONSTRAINT fk_subscription_schedule_owner FOREIGN KEY(tenant_id,owner_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_schedule_device FOREIGN KEY(tenant_id,device_id)
          REFERENCES public.subscription_devices(tenant_id,id),
        CONSTRAINT fk_subscription_schedule_template FOREIGN KEY(tenant_id,template_id)
          REFERENCES public.subscription_chatbot_templates(tenant_id,id),
        CONSTRAINT subscription_schedule_values CHECK(
          length(trim(title))>0 AND version>0 AND status IN ('active','cancelled','completed')
          AND jsonb_typeof(definition)='object' AND octet_length(definition::text)<=1048576
          AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=1048576
          AND run_version ~ '^[a-f0-9]{64}$' AND length(trim(timezone))>0
          AND anchor_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$'
          AND interval_seconds IN (10,20,30,40,50,60)),
        CONSTRAINT subscription_schedule_template CHECK(
          template_id IS NULL AND template_revision IS NULL
          OR template_id IS NOT NULL AND template_revision IS NOT NULL AND template_revision>0),
        CONSTRAINT subscription_schedule_cancellation CHECK(
          status='cancelled' AND cancelled_at IS NOT NULL
          OR status<>'cancelled' AND cancelled_at IS NULL)
      );
      CREATE INDEX ix_subscription_schedule_list
        ON public.subscription_schedules(tenant_id,created_at DESC,id DESC);
      CREATE INDEX ix_subscription_schedule_active
        ON public.subscription_schedules(tenant_id,device_id,id) WHERE status='active';

      CREATE TABLE public.subscription_schedule_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        schedule_id uuid NOT NULL,
        occurrence_index integer NOT NULL DEFAULT 0,
        schedule_version bigint NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        run_version char(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        due_at timestamptz,
        local_date date NOT NULL,
        timezone varchar(64) NOT NULL,
        snapshot jsonb NOT NULL,
        first_admitted_at timestamptz,
        last_admitted_at timestamptz,
        next_admission_at timestamptz,
        manual_released_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        source_run_id uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_schedule_run_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_schedule_run_scope UNIQUE(tenant_id,schedule_id,id),
        CONSTRAINT uq_subscription_schedule_run_occurrence UNIQUE(tenant_id,schedule_id,occurrence_index),
        CONSTRAINT fk_subscription_schedule_run_schedule FOREIGN KEY(tenant_id,schedule_id)
          REFERENCES public.subscription_schedules(tenant_id,id),
        CONSTRAINT fk_subscription_schedule_run_source FOREIGN KEY(tenant_id,schedule_id,source_run_id)
          REFERENCES public.subscription_schedule_runs(tenant_id,schedule_id,id),
        CONSTRAINT subscription_schedule_run_values CHECK(
          occurrence_index>=0 AND schedule_version>0 AND version>0
          AND run_version ~ '^[a-f0-9]{64}$' AND length(trim(timezone))>0
          AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=1048576
          AND status IN ('pending','running','completed','failed','uncertain','cancelled')),
        CONSTRAINT subscription_schedule_run_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('completed','failed','uncertain','cancelled') AND finished_at IS NOT NULL),
        CONSTRAINT subscription_schedule_run_admission CHECK(
          (status<>'running' OR first_admitted_at IS NOT NULL)
          AND (first_admitted_at IS NULL AND last_admitted_at IS NULL
            OR first_admitted_at IS NOT NULL AND last_admitted_at IS NOT NULL
              AND last_admitted_at>=first_admitted_at)),
        CONSTRAINT subscription_schedule_run_due CHECK(
          due_at IS NOT NULL AND error_code IS DISTINCT FROM 'SCHEDULE_RECURRENCE_INVALID'
          OR due_at IS NULL AND error_code IS NOT NULL AND error_code='SCHEDULE_RECURRENCE_INVALID'
            AND status IN ('pending','cancelled') AND first_admitted_at IS NULL
            AND manual_released_at IS NULL AND next_admission_at IS NULL),
        CONSTRAINT subscription_schedule_run_origin CHECK(
          occurrence_index=0 AND source_run_id IS NULL
          OR occurrence_index>0 AND source_run_id IS NOT NULL AND source_run_id<>id)
      );
      CREATE INDEX ix_subscription_schedule_run_list
        ON public.subscription_schedule_runs(tenant_id,created_at DESC,id DESC);
      CREATE INDEX ix_subscription_schedule_run_due
        ON public.subscription_schedule_runs(tenant_id,next_admission_at,due_at,id)
        WHERE status IN ('pending','running');

      CREATE TABLE public.subscription_schedule_recipients (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        schedule_id uuid NOT NULL,
        run_id uuid NOT NULL,
        run_version char(64) NOT NULL,
        ordinal integer NOT NULL,
        phone varchar(15) NOT NULL,
        name varchar(120) NOT NULL,
        origins jsonb NOT NULL,
        permission_version bigint NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL,
        plan jsonb NOT NULL,
        plan_hash char(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        admitted_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_schedule_recipient_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_schedule_recipient_scope UNIQUE(tenant_id,schedule_id,run_id,id),
        CONSTRAINT uq_subscription_schedule_recipient_phone UNIQUE(tenant_id,run_id,run_version,phone),
        CONSTRAINT uq_subscription_schedule_recipient_order UNIQUE(tenant_id,run_id,run_version,ordinal),
        CONSTRAINT fk_subscription_schedule_recipient_run FOREIGN KEY(tenant_id,schedule_id,run_id)
          REFERENCES public.subscription_schedule_runs(tenant_id,schedule_id,id),
        CONSTRAINT fk_subscription_schedule_recipient_device FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        CONSTRAINT subscription_schedule_recipient_values CHECK(
          run_version ~ '^[a-f0-9]{64}$' AND plan_hash ~ '^[a-f0-9]{64}$'
          AND ordinal BETWEEN 1 AND 10000 AND phone ~ '^[1-9][0-9]{7,14}$' AND length(trim(name))>0
          AND jsonb_typeof(origins)='array' AND jsonb_array_length(origins) BETWEEN 1 AND 101
          AND octet_length(origins::text)<=131072 AND permission_version>=0 AND settings_revision>0
          AND jsonb_typeof(plan)='array' AND jsonb_array_length(plan) BETWEEN 1 AND 3
          AND octet_length(plan::text)<=131072
          AND status IN ('pending','running','sent','failed','uncertain','cancelled')),
        CONSTRAINT subscription_schedule_recipient_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('sent','failed','uncertain','cancelled') AND finished_at IS NOT NULL),
        CONSTRAINT subscription_schedule_recipient_admission CHECK(
          status NOT IN ('running','sent','uncertain') OR admitted_at IS NOT NULL)
      );
      CREATE INDEX ix_subscription_schedule_recipient_pending
        ON public.subscription_schedule_recipients(tenant_id,run_id,run_version,device_id,ordinal)
        WHERE status='pending';
      CREATE INDEX ix_subscription_schedule_recipient_rate
        ON public.subscription_schedule_recipients(tenant_id,admitted_at) WHERE admitted_at IS NOT NULL;
      CREATE INDEX ix_subscription_schedule_recipient_contact
        ON public.subscription_schedule_recipients(tenant_id,phone,status);

      CREATE TABLE public.subscription_schedule_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL,
        idempotency_key uuid NOT NULL,
        kind varchar(24) NOT NULL,
        request_fingerprint char(64) NOT NULL,
        schedule_id uuid,
        run_id uuid,
        status varchar(16) NOT NULL,
        review_version char(64),
        snapshot jsonb,
        preview jsonb,
        result jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz,
        confirmed_at timestamptz,
        CONSTRAINT uq_subscription_schedule_request_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_schedule_request_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT fk_subscription_schedule_request_actor FOREIGN KEY(tenant_id,actor_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_schedule_request_schedule FOREIGN KEY(tenant_id,schedule_id)
          REFERENCES public.subscription_schedules(tenant_id,id),
        CONSTRAINT fk_subscription_schedule_request_run FOREIGN KEY(tenant_id,schedule_id,run_id)
          REFERENCES public.subscription_schedule_runs(tenant_id,schedule_id,id),
        CONSTRAINT subscription_schedule_request_values CHECK(
          kind IN ('review_create','review_edit','pending_review','cancel_run','cancel_series')
          AND status IN ('prepared','succeeded') AND request_fingerprint ~ '^[a-f0-9]{64}$'
          AND (result IS NULL OR jsonb_typeof(result)='object' AND octet_length(result::text)<=4194304)),
        CONSTRAINT subscription_schedule_request_scope CHECK(
          kind='review_create' AND ((schedule_id IS NULL AND run_id IS NULL)
            OR (schedule_id IS NOT NULL AND run_id IS NOT NULL))
          OR kind IN ('review_edit','cancel_run') AND schedule_id IS NOT NULL AND run_id IS NOT NULL
          OR kind='pending_review' AND schedule_id IS NULL AND run_id IS NULL
          OR kind='cancel_series' AND schedule_id IS NOT NULL AND run_id IS NULL),
        CONSTRAINT subscription_schedule_request_review CHECK(
          kind IN ('review_create','review_edit','pending_review')
            AND review_version IS NOT NULL AND review_version ~ '^[a-f0-9]{64}$'
            AND snapshot IS NOT NULL AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=67108864
            AND preview IS NOT NULL AND jsonb_typeof(preview)='object' AND octet_length(preview::text)<=1048576
            AND expires_at IS NOT NULL AND expires_at>created_at AND expires_at<=created_at+interval '120 seconds'
            AND ((status='prepared' AND result IS NULL AND confirmed_at IS NULL)
              OR (status='succeeded' AND result IS NOT NULL AND confirmed_at IS NOT NULL))
          OR kind IN ('cancel_run','cancel_series') AND status='succeeded' AND result IS NOT NULL
            AND snapshot IS NULL AND preview IS NULL AND review_version IS NULL
            AND expires_at IS NULL AND confirmed_at IS NULL)
      );
      CREATE INDEX ix_subscription_schedule_request_actor
        ON public.subscription_schedule_requests(tenant_id,actor_account_id,created_at DESC,id DESC);

      ALTER TABLE public.subscription_device_events DROP CONSTRAINT subscription_device_events_kind_check,
        ADD CONSTRAINT subscription_device_events_kind_check
          CHECK(kind IN ('text','audio','call','status','outbound','resend','diagnostic','campaign','scheduled'));
      ALTER TABLE public.subscription_device_effects DROP CONSTRAINT subscription_device_effects_type_check,
        ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action','campaign_action','scheduled_action')),
        ADD COLUMN schedule_id uuid,
        ADD COLUMN schedule_run_id uuid,
        ADD COLUMN schedule_recipient_id uuid,
        ADD COLUMN schedule_part_index smallint,
        ADD CONSTRAINT subscription_device_effect_schedule_scope CHECK(
          type='scheduled_action' AND purpose='scheduled_action' AND schedule_id IS NOT NULL
            AND schedule_run_id IS NOT NULL AND schedule_recipient_id IS NOT NULL
            AND schedule_part_index IS NOT NULL AND schedule_part_index BETWEEN 0 AND 2
          OR type<>'scheduled_action' AND schedule_id IS NULL AND schedule_run_id IS NULL
            AND schedule_recipient_id IS NULL AND schedule_part_index IS NULL),
        ADD CONSTRAINT fk_subscription_device_effect_schedule FOREIGN KEY(tenant_id,schedule_id)
          REFERENCES public.subscription_schedules(tenant_id,id),
        ADD CONSTRAINT fk_subscription_device_effect_schedule_run FOREIGN KEY(tenant_id,schedule_id,schedule_run_id)
          REFERENCES public.subscription_schedule_runs(tenant_id,schedule_id,id),
        ADD CONSTRAINT fk_subscription_device_effect_schedule_recipient
          FOREIGN KEY(tenant_id,schedule_id,schedule_run_id,schedule_recipient_id)
          REFERENCES public.subscription_schedule_recipients(tenant_id,schedule_id,run_id,id),
        ADD CONSTRAINT uq_subscription_device_effect_schedule_part
          UNIQUE(tenant_id,schedule_recipient_id,schedule_part_index);
    """)
    updates = {
        "subscription_schedules": "owner_account_id,title,version,status,definition,snapshot,run_version,device_id,template_id,template_revision,timezone,anchor_date,anchor_time,interval_seconds,repeat_every_30_days,confirmed_at,cancelled_at,updated_at",
        "subscription_schedule_runs": "schedule_version,version,run_version,status,due_at,local_date,timezone,snapshot,first_admitted_at,last_admitted_at,next_admission_at,manual_released_at,finished_at,error_code,updated_at",
        "subscription_schedule_recipients": "status,admitted_at,finished_at,error_code",
        "subscription_schedule_requests": "schedule_id,run_id,status,result,confirmed_at",
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
    raise RuntimeError("Preserve scheduled audiences, revocations and transport evidence; use a forward migration")
