"""Private sent-message history and append-only, explicitly requested retries."""
from alembic import op

revision = "0065_subscription_sent_messages"
down_revision = "0064_subscription_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      ALTER TABLE public.subscription_device_events
        DROP CONSTRAINT subscription_device_events_kind_check,
        ADD CONSTRAINT subscription_device_events_kind_check
          CHECK(kind IN ('text','audio','call','status','outbound','resend'));
      ALTER TABLE public.subscription_device_effects
        ADD COLUMN audio_duration_seconds numeric(12,3),
        ADD COLUMN audio_completed_at timestamptz,
        ADD CONSTRAINT subscription_device_effect_audio_completion_check CHECK(
          (audio_duration_seconds IS NULL AND audio_completed_at IS NULL)
          OR (type='transcribe' AND status='accepted' AND audio_duration_seconds IS NOT NULL AND audio_duration_seconds>0
            AND audio_duration_seconds<=1800 AND audio_completed_at IS NOT NULL));
      CREATE TABLE public.subscription_sent_message_resends (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        source_effect_id uuid NOT NULL,
        root_effect_id uuid NOT NULL,
        new_effect_id uuid NOT NULL,
        actor_account_id uuid NOT NULL,
        idempotency_key uuid NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        source_snapshot_hash char(64) NOT NULL CHECK(source_snapshot_hash ~ '^[a-f0-9]{64}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_sent_resend_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT uq_subscription_sent_resend_source UNIQUE(tenant_id,source_effect_id),
        CONSTRAINT uq_subscription_sent_resend_effect UNIQUE(tenant_id,new_effect_id),
        FOREIGN KEY(tenant_id,source_effect_id) REFERENCES public.subscription_device_effects(tenant_id,id),
        FOREIGN KEY(tenant_id,root_effect_id) REFERENCES public.subscription_device_effects(tenant_id,id),
        FOREIGN KEY(tenant_id,new_effect_id) REFERENCES public.subscription_device_effects(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT subscription_sent_resend_identity_check
          CHECK(source_effect_id<>new_effect_id AND root_effect_id<>new_effect_id)
      );
      CREATE INDEX ix_subscription_sent_resend_root
        ON public.subscription_sent_message_resends(tenant_id,root_effect_id,created_at,id);
      CREATE INDEX ix_subscription_sent_history
        ON public.subscription_device_effects(tenant_id,created_at DESC,id DESC)
        WHERE type='send_text' OR type='chatbot_action' AND payload->'action'->>'type'<>'http';
      CREATE INDEX ix_subscription_sent_device_history
        ON public.subscription_device_effects(tenant_id,device_id,created_at DESC,id DESC)
        WHERE type='send_text' OR type='chatbot_action' AND payload->'action'->>'type'<>'http';
      CREATE INDEX ix_subscription_sent_contact_history
        ON public.subscription_device_effects(tenant_id,(payload->>'phone'),created_at DESC,id DESC)
        WHERE type='send_text' OR type='chatbot_action' AND payload->'action'->>'type'<>'http';
      ALTER TABLE public.subscription_sent_message_resends ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.subscription_sent_message_resends FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.subscription_sent_message_resends FROM PUBLIC,app_runtime,app_support;
      GRANT SELECT,INSERT ON public.subscription_sent_message_resends TO app_support;
      CREATE POLICY subscription_sent_message_resends_runtime
        ON public.subscription_sent_message_resends FOR SELECT TO app_runtime
        USING(tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid));
      CREATE POLICY subscription_sent_message_resends_support
        ON public.subscription_sent_message_resends FOR ALL TO app_support USING(true) WITH CHECK(true);
      CREATE POLICY subscription_sent_message_resends_migrator
        ON public.subscription_sent_message_resends FOR ALL TO app_migrator USING(true) WITH CHECK(true);
    """)


def downgrade():
    raise RuntimeError("Preserve message attempts and audit evidence; use a forward migration")
