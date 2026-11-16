"""Tenant-scoped subscription chatbot graph, evidence and restorable snapshots.

No rule, device, channel, plan, provider request or message is enabled here.
"""
from alembic import op


revision = "0063_subscription_chatbot"
down_revision = "0062_subscription_devices"
branch_labels = None
depends_on = None

TABLES = (
    "subscription_chatbot_state", "subscription_chatbot_assets",
    "subscription_chatbot_templates", "subscription_chatbot_rules",
    "subscription_chatbot_jobs", "subscription_chatbot_runs",
    "subscription_chatbot_cooldowns", "subscription_chatbot_selections",
    "subscription_chatbot_backups", "subscription_chatbot_previews",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.subscription_chatbot_state (
        tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        next_creation_order bigint NOT NULL DEFAULT 1 CHECK(next_creation_order>0),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE public.subscription_chatbot_assets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        kind varchar(20) NOT NULL CHECK(kind IN ('image','sticker','video','ptv','document')),
        mime varchar(100) NOT NULL,
        size integer NOT NULL CHECK(size BETWEEN 1 AND 16000000),
        sha256 char(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
        width integer CHECK(width>0), height integer CHECK(height>0),
        duration_ms integer CHECK(duration_ms>0),
        thumbnail_size integer CHECK(thumbnail_size BETWEEN 1 AND 100000),
        thumbnail_mime varchar(100),
        thumbnail_sha256 char(64) CHECK(thumbnail_sha256 ~ '^[a-f0-9]{64}$'),
        status varchar(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','failed')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,kind,sha256),
        CHECK((thumbnail_size IS NULL)=(thumbnail_sha256 IS NULL)),
        CHECK((thumbnail_size IS NULL)=(thumbnail_mime IS NULL))
      );
      CREATE TABLE public.subscription_chatbot_templates (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        origin_id uuid NOT NULL,
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        name varchar(100) NOT NULL CHECK(length(trim(name))>0),
        kind varchar(20) NOT NULL CHECK(kind IN ('text','image','sticker','video','ptv','document')),
        body text NOT NULL DEFAULT '', asset_id uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        removed_at timestamptz,
        UNIQUE(tenant_id,id),
        FOREIGN KEY(tenant_id,asset_id) REFERENCES public.subscription_chatbot_assets(tenant_id,id),
        CHECK((kind='text')=(asset_id IS NULL)), CHECK(octet_length(body)<=128000)
      );
      CREATE UNIQUE INDEX uq_subscription_chatbot_template_origin
        ON public.subscription_chatbot_templates(tenant_id,origin_id) WHERE removed_at IS NULL;
      CREATE TABLE public.subscription_chatbot_rules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        origin_id uuid NOT NULL,
        creation_order bigint NOT NULL CHECK(creation_order>0),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        device_id uuid,
        enabled boolean NOT NULL DEFAULT false,
        match_mode varchar(10) NOT NULL CHECK(match_mode IN ('equal','like','array')),
        keyword varchar(255) NOT NULL CHECK(length(trim(keyword))>0),
        normalized_keyword text NOT NULL,
        response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'),
        created_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        removed_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,creation_order),
        FOREIGN KEY(tenant_id,device_id) REFERENCES public.subscription_devices(tenant_id,id),
        FOREIGN KEY(tenant_id,created_by) REFERENCES public.tenant_memberships(tenant_id,account_id),
        CHECK(octet_length(response::text)<=2000000)
      );
      CREATE UNIQUE INDEX uq_subscription_chatbot_rule_origin
        ON public.subscription_chatbot_rules(tenant_id,origin_id) WHERE removed_at IS NULL;
      CREATE INDEX ix_subscription_chatbot_rule_match
        ON public.subscription_chatbot_rules(tenant_id,creation_order DESC) WHERE enabled AND removed_at IS NULL;
      CREATE TABLE public.subscription_chatbot_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL,
        kind varchar(30) NOT NULL CHECK(kind IN
          ('create','update','toggle','duplicate','delete','delete_all','test_response','url_tags','export','restore')),
        idempotency_key uuid NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        expected_revision bigint NOT NULL CHECK(expected_revision>0),
        preview_id uuid,
        status varchar(20) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
        payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(payload)='object'),
        result jsonb, counts jsonb,
        safety_backup_id uuid,
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        started_at timestamptz, finished_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
        FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id)
      );
      CREATE INDEX ix_subscription_chatbot_job_queue ON public.subscription_chatbot_jobs(created_at,id)
        WHERE kind='restore' AND status IN ('queued','running');
      CREATE TABLE public.subscription_chatbot_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        device_id uuid NOT NULL, generation uuid NOT NULL,
        source_event_id uuid NOT NULL,
        chatbot_revision bigint NOT NULL CHECK(chatbot_revision>0),
        settings_revision bigint NOT NULL CHECK(settings_revision>0),
        actor_account_id uuid NOT NULL,
        ingress_order bigint NOT NULL CHECK(ingress_order>0),
        input_fence bigint NOT NULL CHECK(input_fence>=0),
        phone varchar(15) CHECK(phone ~ '^[1-9][0-9]{7,14}$'),
        origin varchar(20) NOT NULL CHECK(origin IN ('contact','owner_command','test_response','url_tags')),
        input_kind varchar(20) NOT NULL CHECK(input_kind IN ('text','audio','selection','test','url_tags')),
        input_text text,
        input_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(input_context)='object'),
        rule_id uuid, rule_revision bigint,
        job_id uuid,
        plan jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(plan)='array'),
        status varchar(20) NOT NULL CHECK(status IN
          ('waiting_audio','ready','waiting_order','planned','ignored','succeeded','failed')),
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,source_event_id),
        FOREIGN KEY(tenant_id,device_id,generation) REFERENCES public.subscription_devices(tenant_id,id,generation),
        FOREIGN KEY(tenant_id,source_event_id) REFERENCES public.subscription_device_events(tenant_id,id),
        FOREIGN KEY(tenant_id,rule_id) REFERENCES public.subscription_chatbot_rules(tenant_id,id),
        FOREIGN KEY(tenant_id,job_id) REFERENCES public.subscription_chatbot_jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id),
        CHECK(input_text IS NULL OR octet_length(input_text)<=512000),
        CHECK((rule_id IS NULL)=(rule_revision IS NULL))
      );
      CREATE INDEX ix_subscription_chatbot_run_contact ON public.subscription_chatbot_runs
        (tenant_id,device_id,phone,ingress_order,id) WHERE status IN ('waiting_audio','ready','waiting_order');
      ALTER TABLE public.subscription_device_effects
        DROP CONSTRAINT subscription_device_effects_type_check,
        ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action')),
        ADD COLUMN chatbot_revision bigint CHECK(chatbot_revision>0),
        ADD COLUMN chatbot_run_id uuid,
        ADD COLUMN result jsonb CHECK(result IS NULL OR jsonb_typeof(result)='object'),
        ADD CONSTRAINT subscription_device_effect_chatbot_scope
          CHECK((type='chatbot_action' AND chatbot_revision IS NOT NULL AND chatbot_run_id IS NOT NULL)
            OR (type<>'chatbot_action' AND chatbot_revision IS NULL AND chatbot_run_id IS NULL)),
        ADD CONSTRAINT subscription_device_effect_chatbot_run_fk
          FOREIGN KEY(tenant_id,chatbot_run_id) REFERENCES public.subscription_chatbot_runs(tenant_id,id);
      CREATE TABLE public.subscription_chatbot_cooldowns (
        tenant_id uuid NOT NULL, device_id uuid NOT NULL, rule_id uuid NOT NULL,
        phone varchar(15) NOT NULL CHECK(phone ~ '^[1-9][0-9]{7,14}$'),
        reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        next_allowed_at timestamptz NOT NULL,
        local_day date,
        run_id uuid NOT NULL,
        PRIMARY KEY(tenant_id,device_id,rule_id,phone),
        FOREIGN KEY(tenant_id,device_id) REFERENCES public.subscription_devices(tenant_id,id),
        FOREIGN KEY(tenant_id,rule_id) REFERENCES public.subscription_chatbot_rules(tenant_id,id),
        FOREIGN KEY(tenant_id,run_id) REFERENCES public.subscription_chatbot_runs(tenant_id,id)
      );
      CREATE TABLE public.subscription_chatbot_selections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL, device_id uuid NOT NULL, generation uuid NOT NULL,
        chatbot_revision bigint NOT NULL CHECK(chatbot_revision>0),
        run_id uuid NOT NULL, outgoing_effect_id uuid NOT NULL,
        phone varchar(15) NOT NULL CHECK(phone ~ '^[1-9][0-9]{7,14}$'),
        token_hash char(64) NOT NULL CHECK(token_hash ~ '^[a-f0-9]{64}$'),
        action jsonb NOT NULL CHECK(jsonb_typeof(action)='object'),
        expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '24 hours',
        consumed_event_id uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,id), UNIQUE(token_hash),
        FOREIGN KEY(tenant_id,device_id,generation) REFERENCES public.subscription_devices(tenant_id,id,generation),
        FOREIGN KEY(tenant_id,run_id) REFERENCES public.subscription_chatbot_runs(tenant_id,id),
        FOREIGN KEY(tenant_id,outgoing_effect_id) REFERENCES public.subscription_device_effects(tenant_id,id),
        FOREIGN KEY(tenant_id,consumed_event_id) REFERENCES public.subscription_device_events(tenant_id,id)
      );
      CREATE TABLE public.subscription_chatbot_backups (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        source varchar(20) NOT NULL CHECK(source IN ('scheduled','safety')),
        revision bigint NOT NULL CHECK(revision>0),
        backup_day date,
        snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
        rules_count integer NOT NULL CHECK(rules_count>=0),
        list_rows_count integer NOT NULL CHECK(list_rows_count>=0),
        size bigint NOT NULL CHECK(size>0),
        status varchar(20) NOT NULL DEFAULT 'ready' CHECK(status='ready'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,id),
        CHECK((source='scheduled')=(backup_day IS NOT NULL))
      );
      CREATE UNIQUE INDEX uq_subscription_chatbot_daily_backup ON public.subscription_chatbot_backups(tenant_id,backup_day)
        WHERE source='scheduled';
      CREATE TABLE public.subscription_chatbot_previews (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL,
        revision bigint NOT NULL CHECK(revision>0),
        source varchar(20) NOT NULL CHECK(source IN ('import','backup')),
        backup_id uuid,
        mode varchar(20) NOT NULL CHECK(mode IN ('add_missing','merge','exact')),
        device_id uuid,
        snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
        counts jsonb NOT NULL CHECK(jsonb_typeof(counts)='object'),
        warnings jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(warnings)='array'),
        applied_job_id uuid,
        expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id),
        FOREIGN KEY(tenant_id,device_id) REFERENCES public.subscription_devices(tenant_id,id),
        FOREIGN KEY(tenant_id,backup_id) REFERENCES public.subscription_chatbot_backups(tenant_id,id),
        FOREIGN KEY(tenant_id,applied_job_id) REFERENCES public.subscription_chatbot_jobs(tenant_id,id)
      );
      ALTER TABLE public.subscription_chatbot_jobs
        ADD CONSTRAINT subscription_chatbot_job_preview_fk
          FOREIGN KEY(tenant_id,preview_id) REFERENCES public.subscription_chatbot_previews(tenant_id,id),
        ADD CONSTRAINT subscription_chatbot_job_safety_backup_fk
          FOREIGN KEY(tenant_id,safety_backup_id) REFERENCES public.subscription_chatbot_backups(tenant_id,id);
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
    op.execute("""
      GRANT SELECT(tenant_id,revision,updated_at) ON public.subscription_chatbot_state TO app_runtime;
      GRANT SELECT(id,tenant_id,creation_order,revision,device_id,enabled,match_mode,created_at,updated_at,removed_at)
        ON public.subscription_chatbot_rules TO app_runtime;
      GRANT SELECT(id,tenant_id,kind,status,created_at,finished_at) ON public.subscription_chatbot_jobs TO app_runtime;
      GRANT SELECT(id,tenant_id,source,revision,rules_count,list_rows_count,size,status,created_at)
        ON public.subscription_chatbot_backups TO app_runtime;
    """)


def downgrade():
    raise RuntimeError("Preserve chatbot snapshots and delivery evidence; use a forward migration")
