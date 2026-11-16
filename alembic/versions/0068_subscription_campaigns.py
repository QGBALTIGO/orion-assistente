"""Private campaign audiences, explicit authorization and bounded device admission."""
from alembic import op

revision = "0068_subscription_campaigns"
down_revision = "0067_subscription_diagnostics"
branch_labels = None
depends_on = None

TABLES = (
    "subscription_campaign_groups", "subscription_campaign_members",
    "subscription_campaign_permissions", "subscription_campaigns",
    "subscription_campaign_sources", "subscription_campaign_recipients",
    "subscription_campaign_requests",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.subscription_campaign_groups (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        name varchar(100) NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        archived_at timestamptz,
        CONSTRAINT uq_subscription_campaign_group_scope UNIQUE(tenant_id,id),
        CONSTRAINT subscription_campaign_group_values CHECK(length(trim(name))>0 AND version>0)
      );
      CREATE INDEX ix_subscription_campaign_groups_active
        ON public.subscription_campaign_groups(tenant_id,created_at,id) WHERE archived_at IS NULL;
      CREATE TABLE public.subscription_campaign_members (
        tenant_id uuid NOT NULL,
        group_id uuid NOT NULL,
        phone varchar(15) NOT NULL,
        name varchar(100) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        removed_at timestamptz,
        PRIMARY KEY(tenant_id,group_id,phone),
        CONSTRAINT fk_subscription_campaign_member_group FOREIGN KEY(tenant_id,group_id)
          REFERENCES public.subscription_campaign_groups(tenant_id,id),
        CONSTRAINT subscription_campaign_member_values CHECK(phone ~ '^[1-9][0-9]{7,14}$' AND length(trim(name))>0)
      );
      CREATE INDEX ix_subscription_campaign_member_phone
        ON public.subscription_campaign_members(tenant_id,phone,group_id) WHERE removed_at IS NULL;
      CREATE TABLE public.subscription_campaign_permissions (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        phone varchar(15) NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        revoked boolean NOT NULL,
        source varchar(24) NOT NULL,
        actor_account_id uuid,
        event_id uuid,
        evidence_hash char(64),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,phone),
        CONSTRAINT fk_subscription_campaign_permission_actor FOREIGN KEY(tenant_id,actor_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_campaign_permission_event FOREIGN KEY(tenant_id,event_id)
          REFERENCES public.subscription_device_events(tenant_id,id),
        CONSTRAINT subscription_campaign_permission_values CHECK(
          phone ~ '^[1-9][0-9]{7,14}$' AND version>0 AND (
            source='optout' AND revoked AND event_id IS NOT NULL AND actor_account_id IS NULL AND evidence_hash IS NULL
            OR source='owner_permission' AND NOT revoked AND actor_account_id IS NOT NULL
              AND event_id IS NULL AND evidence_hash IS NOT NULL AND evidence_hash ~ '^[a-f0-9]{64}$'))
      );
      CREATE TABLE public.subscription_campaigns (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        owner_account_id uuid NOT NULL,
        title varchar(120) NOT NULL,
        version bigint NOT NULL DEFAULT 1,
        status varchar(16) NOT NULL DEFAULT 'draft',
        definition jsonb NOT NULL,
        start_at timestamptz NOT NULL,
        template_id uuid,
        template_revision bigint,
        run_version char(64),
        snapshot jsonb,
        confirmed_at timestamptz,
        first_admitted_at timestamptz,
        last_admitted_at timestamptz,
        next_admission_at timestamptz,
        archived_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_campaign_scope UNIQUE(tenant_id,id),
        CONSTRAINT fk_subscription_campaign_owner FOREIGN KEY(tenant_id,owner_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_campaign_template FOREIGN KEY(tenant_id,template_id)
          REFERENCES public.subscription_chatbot_templates(tenant_id,id),
        CONSTRAINT subscription_campaign_values CHECK(length(trim(title))>0 AND version>0
          AND status IN ('draft','scheduled','running','paused','completed','cancelled')
          AND jsonb_typeof(definition)='object' AND octet_length(definition::text)<=32768
          AND (template_revision IS NULL OR template_revision>0)),
        CONSTRAINT subscription_campaign_snapshot CHECK(
          run_version IS NULL AND snapshot IS NULL AND confirmed_at IS NULL
          OR run_version IS NOT NULL AND run_version ~ '^[a-f0-9]{64}$'
            AND snapshot IS NOT NULL AND jsonb_typeof(snapshot)='object'
            AND octet_length(snapshot::text)<=1048576 AND confirmed_at IS NOT NULL),
        CONSTRAINT subscription_campaign_execution CHECK(
          (status NOT IN ('scheduled','running','paused','completed') OR run_version IS NOT NULL)
          AND (first_admitted_at IS NULL OR last_admitted_at IS NOT NULL)
          AND (archived_at IS NULL OR status='cancelled'))
      );
      CREATE INDEX ix_subscription_campaign_list
        ON public.subscription_campaigns(tenant_id,created_at DESC,id DESC);
      CREATE INDEX ix_subscription_campaign_due
        ON public.subscription_campaigns(tenant_id,next_admission_at,id)
        WHERE status IN ('scheduled','running') AND archived_at IS NULL;
      CREATE TABLE public.subscription_campaign_sources (
        tenant_id uuid NOT NULL,
        campaign_id uuid NOT NULL,
        run_version char(64) NOT NULL,
        group_id uuid NOT NULL,
        group_version bigint NOT NULL,
        name varchar(100) NOT NULL,
        member_count integer NOT NULL,
        PRIMARY KEY(tenant_id,campaign_id,run_version,group_id),
        CONSTRAINT fk_subscription_campaign_source_campaign FOREIGN KEY(tenant_id,campaign_id)
          REFERENCES public.subscription_campaigns(tenant_id,id),
        CONSTRAINT fk_subscription_campaign_source_group FOREIGN KEY(tenant_id,group_id)
          REFERENCES public.subscription_campaign_groups(tenant_id,id),
        CONSTRAINT subscription_campaign_source_values CHECK(run_version ~ '^[a-f0-9]{64}$'
          AND group_version>0 AND member_count BETWEEN 0 AND 5000 AND length(trim(name))>0)
      );
      CREATE TABLE public.subscription_campaign_recipients (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        campaign_id uuid NOT NULL,
        run_version char(64) NOT NULL,
        ordinal integer NOT NULL,
        phone varchar(15) NOT NULL,
        name varchar(100) NOT NULL,
        source_group_ids uuid[] NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL,
        permission_version bigint NOT NULL,
        plan jsonb NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        admitted_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_subscription_campaign_recipient_scope UNIQUE(tenant_id,campaign_id,id),
        CONSTRAINT uq_subscription_campaign_recipient_id UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_campaign_recipient_phone UNIQUE(tenant_id,campaign_id,run_version,phone),
        CONSTRAINT uq_subscription_campaign_recipient_order UNIQUE(tenant_id,campaign_id,run_version,ordinal),
        CONSTRAINT fk_subscription_campaign_recipient_campaign FOREIGN KEY(tenant_id,campaign_id)
          REFERENCES public.subscription_campaigns(tenant_id,id),
        CONSTRAINT fk_subscription_campaign_recipient_device FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        CONSTRAINT subscription_campaign_recipient_values CHECK(run_version ~ '^[a-f0-9]{64}$'
          AND ordinal BETWEEN 1 AND 10000 AND phone ~ '^[1-9][0-9]{7,14}$' AND length(trim(name))>0
          AND cardinality(source_group_ids) BETWEEN 1 AND 100 AND array_position(source_group_ids,NULL) IS NULL
          AND settings_revision>0 AND permission_version>=0 AND jsonb_typeof(plan)='array'
          AND jsonb_array_length(plan) BETWEEN 1 AND 3 AND octet_length(plan::text)<=131072
          AND status IN ('pending','running','sent','failed','uncertain','cancelled')),
        CONSTRAINT subscription_campaign_recipient_terminal CHECK(
          (status IN ('pending','running') AND finished_at IS NULL)
          OR (status IN ('sent','failed','uncertain','cancelled') AND finished_at IS NOT NULL)),
        CONSTRAINT subscription_campaign_recipient_admission CHECK(
          status NOT IN ('running','sent','uncertain') OR admitted_at IS NOT NULL)
      );
      CREATE INDEX ix_subscription_campaign_recipient_pending
        ON public.subscription_campaign_recipients(tenant_id,campaign_id,run_version,device_id,ordinal)
        WHERE status='pending';
      CREATE INDEX ix_subscription_campaign_recipient_rate
        ON public.subscription_campaign_recipients(tenant_id,admitted_at) WHERE admitted_at IS NOT NULL;
      CREATE INDEX ix_subscription_campaign_recipient_contact
        ON public.subscription_campaign_recipients(tenant_id,phone,status);
      CREATE TABLE public.subscription_campaign_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL,
        idempotency_key uuid NOT NULL,
        kind varchar(24) NOT NULL,
        request_fingerprint char(64) NOT NULL,
        campaign_id uuid,
        group_id uuid,
        status varchar(16) NOT NULL,
        review_version char(64),
        snapshot jsonb,
        preview jsonb,
        result jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz,
        confirmed_at timestamptz,
        CONSTRAINT uq_subscription_campaign_request_scope UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_campaign_request_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT fk_subscription_campaign_request_actor FOREIGN KEY(tenant_id,actor_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_campaign_request_campaign FOREIGN KEY(tenant_id,campaign_id)
          REFERENCES public.subscription_campaigns(tenant_id,id),
        CONSTRAINT fk_subscription_campaign_request_group FOREIGN KEY(tenant_id,group_id)
          REFERENCES public.subscription_campaign_groups(tenant_id,id),
        CONSTRAINT subscription_campaign_request_values CHECK(
          kind IN ('create','update','review','action','group_create','group_update','group_archive','group_permissions')
          AND status IN ('prepared','succeeded') AND request_fingerprint ~ '^[a-f0-9]{64}$'
          AND ((kind IN ('create','update','review','action') AND campaign_id IS NOT NULL AND group_id IS NULL)
            OR (kind IN ('group_create','group_update','group_archive','group_permissions') AND group_id IS NOT NULL AND campaign_id IS NULL))
          AND (result IS NULL OR jsonb_typeof(result)='object' AND octet_length(result::text)<=4194304)),
        CONSTRAINT subscription_campaign_request_review CHECK(
          kind='review' AND review_version IS NOT NULL AND review_version ~ '^[a-f0-9]{64}$'
            AND snapshot IS NOT NULL AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=67108864
            AND preview IS NOT NULL AND jsonb_typeof(preview)='object' AND octet_length(preview::text)<=1048576
            AND expires_at IS NOT NULL AND expires_at>created_at AND expires_at<=created_at+interval '120 seconds'
            AND ((status='prepared' AND result IS NULL AND confirmed_at IS NULL)
              OR (status='succeeded' AND result IS NOT NULL AND confirmed_at IS NOT NULL))
          OR kind<>'review' AND status='succeeded' AND result IS NOT NULL AND snapshot IS NULL
            AND preview IS NULL AND review_version IS NULL AND expires_at IS NULL AND confirmed_at IS NULL)
      );
      CREATE INDEX ix_subscription_campaign_request_actor
        ON public.subscription_campaign_requests(tenant_id,actor_account_id,created_at DESC,id DESC);
      ALTER TABLE public.subscription_device_events DROP CONSTRAINT subscription_device_events_kind_check,
        ADD CONSTRAINT subscription_device_events_kind_check
          CHECK(kind IN ('text','audio','call','status','outbound','resend','diagnostic','campaign'));
      ALTER TABLE public.subscription_device_effects DROP CONSTRAINT subscription_device_effects_type_check,
        ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action','campaign_action')),
        ADD COLUMN campaign_id uuid,
        ADD COLUMN campaign_recipient_id uuid,
        ADD COLUMN campaign_part_index smallint,
        ADD CONSTRAINT subscription_device_effect_campaign_scope CHECK(
          type='campaign_action' AND purpose='campaign_action' AND campaign_id IS NOT NULL
            AND campaign_recipient_id IS NOT NULL AND campaign_part_index IS NOT NULL AND campaign_part_index BETWEEN 0 AND 2
          OR type<>'campaign_action' AND campaign_id IS NULL AND campaign_recipient_id IS NULL AND campaign_part_index IS NULL),
        ADD CONSTRAINT fk_subscription_device_effect_campaign FOREIGN KEY(tenant_id,campaign_id)
          REFERENCES public.subscription_campaigns(tenant_id,id),
        ADD CONSTRAINT fk_subscription_device_effect_campaign_recipient FOREIGN KEY(tenant_id,campaign_id,campaign_recipient_id)
          REFERENCES public.subscription_campaign_recipients(tenant_id,campaign_id,id),
        ADD CONSTRAINT uq_subscription_device_effect_campaign_part UNIQUE(tenant_id,campaign_recipient_id,campaign_part_index);
    """)
    updates = {
        "subscription_campaign_groups": "name,version,updated_at,archived_at",
        "subscription_campaign_members": "name,updated_at,removed_at",
        "subscription_campaign_permissions": "version,revoked,source,actor_account_id,event_id,evidence_hash,updated_at",
        "subscription_campaigns": "owner_account_id,title,version,status,definition,start_at,template_id,template_revision,run_version,snapshot,confirmed_at,first_admitted_at,last_admitted_at,next_admission_at,archived_at,updated_at",
        "subscription_campaign_recipients": "status,admitted_at,finished_at,error_code",
        "subscription_campaign_requests": "status,result,confirmed_at",
    }
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT ON public.{table} TO app_support")
        if table in updates:
            op.execute(f"GRANT UPDATE({updates[table]}) ON public.{table} TO app_support")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR SELECT TO app_runtime "
                   "USING(tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid))")
        op.execute(f"CREATE POLICY {table}_support ON public.{table} FOR ALL TO app_support USING(true) WITH CHECK(true)")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")


def downgrade():
    raise RuntimeError("Preserve campaign audiences, revocations and transport evidence; use a forward migration")
