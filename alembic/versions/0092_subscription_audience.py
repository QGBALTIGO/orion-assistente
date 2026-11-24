"""Subscription audience overlays; preserve existing customers, groups and opt-outs."""
from alembic import op

revision = "0092_subscription_audience"
down_revision = "0091_dental_insurance_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      SET LOCAL lock_timeout = '5s';
      CREATE TABLE public.subscription_audience_contacts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        phone varchar(15), telegram varchar(64), name varchar(120) NOT NULL,
        email varchar(320), notes text, chatbot_enabled boolean NOT NULL DEFAULT true,
        audio_transcription_enabled boolean NOT NULL DEFAULT true,
        plan_name varchar(120), price_cents bigint, due_date date,
        service_username varchar(255), service_password_encrypted text,
        version bigint NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), removed_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,phone), UNIQUE(tenant_id,telegram),
        CHECK(phone IS NULL OR phone ~ '^[1-9][0-9]{7,14}$'),
        CHECK(phone IS NOT NULL OR telegram IS NOT NULL),
        CHECK(length(trim(name)) > 0), CHECK(version > 0),
        CHECK(notes IS NULL OR length(notes) <= 5000),
        CHECK(price_cents IS NULL OR price_cents BETWEEN 0 AND 100000000000),
        CHECK(service_password_encrypted IS NULL OR length(service_password_encrypted) <= 8192)
      );
      CREATE INDEX ix_subscription_audience_contacts_active
        ON public.subscription_audience_contacts(tenant_id,lower(name),id) WHERE removed_at IS NULL;
      CREATE TABLE public.subscription_audience_group_links (
        tenant_id uuid NOT NULL, group_id uuid NOT NULL, contact_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,group_id,contact_id),
        FOREIGN KEY(tenant_id,group_id) REFERENCES public.subscription_campaign_groups(tenant_id,id),
        FOREIGN KEY(tenant_id,contact_id) REFERENCES public.subscription_audience_contacts(tenant_id,id)
      );
      CREATE INDEX ix_subscription_audience_group_contact
        ON public.subscription_audience_group_links(tenant_id,contact_id,group_id);
      CREATE TABLE public.subscription_audience_operations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        actor_account_id uuid NOT NULL, idempotency_key uuid NOT NULL,
        kind varchar(32) NOT NULL, request_fingerprint char(64) NOT NULL,
        result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,idempotency_key),
        FOREIGN KEY(tenant_id,actor_account_id) REFERENCES public.tenant_memberships(tenant_id,account_id),
        CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        CHECK(octet_length(result::text) <= 4194304)
      );
    """)
    for table in ("subscription_audience_contacts", "subscription_audience_group_links", "subscription_audience_operations"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT ON public.{table} TO app_support")
        if table != "subscription_audience_operations":
            op.execute(f"GRANT UPDATE ON public.{table} TO app_support")
        if table == "subscription_audience_group_links":
            op.execute(f"GRANT DELETE ON public.{table} TO app_support")
        op.execute(f"CREATE POLICY {table}_support ON public.{table} FOR ALL TO app_support USING(true) WITH CHECK(true)")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
    # The ordinary runtime must not SELECT encrypted service credentials.
    op.execute("""GRANT SELECT(id,tenant_id,phone,telegram,name,email,notes,chatbot_enabled,
        audio_transcription_enabled,plan_name,price_cents,due_date,version,created_at,updated_at,removed_at)
        ON public.subscription_audience_contacts TO app_runtime""")
    op.execute("""CREATE POLICY subscription_audience_contacts_runtime ON public.subscription_audience_contacts
        FOR SELECT TO app_runtime USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")


def downgrade():
    raise RuntimeError("Preserve subscription contacts, credentials and import receipts; use a forward migration")
