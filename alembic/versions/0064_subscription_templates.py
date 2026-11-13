"""Independent subscription template library using the existing private graph.

No template, channel, message, rule or provider request is created or enabled.
Existing bodies and immutable media identities remain unchanged.
"""
from alembic import op

revision = "0064_subscription_templates"
down_revision = "0063_subscription_chatbot"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      ALTER TABLE public.subscription_chatbot_templates
        ADD COLUMN followup_body text NOT NULL DEFAULT '',
        ADD COLUMN location jsonb,
        ADD COLUMN file_name varchar(120),
        DROP CONSTRAINT subscription_chatbot_templates_kind_check,
        ADD CONSTRAINT subscription_chatbot_templates_kind_check CHECK(kind IN
          ('text','image','sticker','video','ptv','document','audio','location')),
        DROP CONSTRAINT subscription_chatbot_templates_check,
        ADD CONSTRAINT subscription_chatbot_templates_asset_kind_check
          CHECK((kind IN ('text','location'))=(asset_id IS NULL)),
        ADD CONSTRAINT subscription_chatbot_templates_followup_check
          CHECK(octet_length(followup_body)<=128000
            AND (kind NOT IN ('text','location','sticker','ptv') OR followup_body='')),
        ADD CONSTRAINT subscription_chatbot_templates_filename_check
          CHECK(file_name IS NULL OR (length(trim(file_name))>0
            AND octet_length(file_name)<=480
            AND kind NOT IN ('text','location'))),
        ADD CONSTRAINT subscription_chatbot_templates_location_check CHECK(
          CASE WHEN kind='location' THEN
            location IS NOT NULL AND body='' AND file_name IS NULL
            AND CASE WHEN jsonb_typeof(location)='object'
              AND jsonb_typeof(location->'latitude')='number'
              AND jsonb_typeof(location->'longitude')='number'
              AND location-'latitude'-'longitude'='{}'::jsonb
              THEN (location->>'latitude')::numeric BETWEEN -90 AND 90
                AND (location->>'longitude')::numeric BETWEEN -180 AND 180
              ELSE false END
          ELSE location IS NULL END);
      ALTER TABLE public.subscription_chatbot_assets
        DROP CONSTRAINT subscription_chatbot_assets_kind_check,
        ADD CONSTRAINT subscription_chatbot_assets_kind_check
          CHECK(kind IN ('image','sticker','video','ptv','document','audio'));
      ALTER TABLE public.subscription_chatbot_jobs
        DROP CONSTRAINT subscription_chatbot_jobs_kind_check,
        ADD CONSTRAINT subscription_chatbot_jobs_kind_check CHECK(kind IN
          ('create','update','toggle','duplicate','delete','delete_all','test_response','url_tags','export','restore',
           'template_create','template_update','template_delete','template_asset'));
    """)


def downgrade():
    raise RuntimeError("Preserve template content and delivery evidence; use a forward migration")
