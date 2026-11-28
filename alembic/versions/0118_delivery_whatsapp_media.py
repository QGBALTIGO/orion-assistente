"""Allow the scoped, source-bound Delivery photo analysis effect."""
from alembic import op

revision = "0118_delivery_whatsapp_media"
down_revision = "0117_delivery_whatsapp_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.subscription_device_effects
          DROP CONSTRAINT subscription_device_effects_type_check,
          ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action',
                         'campaign_action','scheduled_action','bulk_action','analyze_image')),
          ADD COLUMN image_started_at timestamptz,
          ADD CONSTRAINT ck_delivery_image_scope CHECK(
            (type <> 'analyze_image' OR purpose='delivery_reply') AND
            (image_started_at IS NULL OR type='analyze_image'));
        CREATE INDEX ix_delivery_image_quota ON public.subscription_device_effects (image_started_at,tenant_id)
          WHERE image_started_at IS NOT NULL;
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.subscription_device_effects WHERE type='analyze_image')
          THEN RAISE EXCEPTION 'Preserve a auditoria dos efeitos de análise de imagem'; END IF;
        END $$;
        DROP INDEX public.ix_delivery_image_quota;
        ALTER TABLE public.subscription_device_effects DROP CONSTRAINT ck_delivery_image_scope,
          DROP CONSTRAINT subscription_device_effects_type_check,
          DROP COLUMN image_started_at,
          ADD CONSTRAINT subscription_device_effects_type_check
          CHECK(type IN ('send_text','reject_call','transcribe','webhook','chatbot_action',
                         'campaign_action','scheduled_action','bulk_action'));
    """)
