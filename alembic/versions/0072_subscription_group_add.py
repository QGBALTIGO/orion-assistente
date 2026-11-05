"""Durable, explicit additions to an existing WhatsApp group."""
from alembic import op

revision = "0072_subscription_group_add"
down_revision = "0071_subscription_direct"
branch_labels = None
depends_on = None
TABLES = ("subscription_group_add_batches", "subscription_group_add_items")


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("""
      CREATE TABLE public.subscription_group_add_batches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        owner_account_id uuid NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL CHECK(settings_revision>0),
        channel_id uuid NOT NULL,
        device_name varchar(100) NOT NULL,
        destination_group_id varchar(47) NOT NULL,
        destination_name varchar(100) NOT NULL,
        source varchar(8) NOT NULL CHECK(source IN ('contacts','group')),
        filters jsonb NOT NULL CHECK(jsonb_typeof(filters)='object' AND octet_length(filters::text)<=16384),
        source_hash char(64) NOT NULL CHECK(source_hash ~ '^[a-f0-9]{64}$'),
        idempotency_key uuid NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK(request_fingerprint ~ '^[a-f0-9]{64}$'),
        status varchar(16) NOT NULL DEFAULT 'pending'
          CHECK(status IN ('pending','running','completed','partial','unknown','cancelled')),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        total integer NOT NULL CHECK(total BETWEEN 1 AND 5000),
        next_item_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        started_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        CONSTRAINT uq_subscription_group_add_batch UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_group_add_key UNIQUE(tenant_id,idempotency_key),
        CONSTRAINT uq_subscription_group_add_context UNIQUE(tenant_id,id,device_id,generation,settings_revision),
        CONSTRAINT fk_subscription_group_add_owner FOREIGN KEY(tenant_id,owner_account_id)
          REFERENCES public.tenant_memberships(tenant_id,account_id),
        CONSTRAINT fk_subscription_group_add_device FOREIGN KEY(tenant_id,device_id,generation)
          REFERENCES public.subscription_devices(tenant_id,id,generation),
        CONSTRAINT fk_subscription_group_add_channel FOREIGN KEY(tenant_id,channel_id)
          REFERENCES public.qr_channels(tenant_id,id),
        CONSTRAINT subscription_group_add_destination CHECK(destination_group_id ~ '^[0-9]{5,20}(-[0-9]{1,20})?@g[.]us$'),
        CONSTRAINT subscription_group_add_batch_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('completed','partial','unknown','cancelled') AND finished_at IS NOT NULL)
      );
      CREATE UNIQUE INDEX uq_subscription_group_add_active_device
        ON public.subscription_group_add_batches(tenant_id,device_id)
        WHERE status IN ('pending','running');
      CREATE INDEX ix_subscription_group_add_due
        ON public.subscription_group_add_batches(next_item_at,tenant_id,id)
        WHERE status IN ('pending','running');

      CREATE TABLE public.subscription_group_add_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        batch_id uuid NOT NULL,
        device_id uuid NOT NULL,
        generation uuid NOT NULL,
        settings_revision bigint NOT NULL,
        ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 5000),
        phone varchar(16) NOT NULL CHECK(phone ~ '^\\+[1-9][0-9]{7,14}$'),
        name varchar(100) NOT NULL,
        source_context jsonb NOT NULL CHECK(jsonb_typeof(source_context)='object' AND octet_length(source_context::text)<=32768),
        permission_version bigint NOT NULL CHECK(permission_version>=0),
        status varchar(16) NOT NULL DEFAULT 'pending'
          CHECK(status IN ('pending','running','added','already_member','not_added','unknown','cancelled')),
        claim_token uuid NOT NULL DEFAULT gen_random_uuid(),
        claimed_owner uuid,
        claimed_fence bigint CHECK(claimed_fence>0),
        dispatch_at timestamptz,
        finished_at timestamptz,
        error_code varchar(80),
        last_receipt_at timestamptz,
        provider_settled boolean NOT NULL DEFAULT false,
        CONSTRAINT uq_subscription_group_add_item UNIQUE(tenant_id,id),
        CONSTRAINT uq_subscription_group_add_ordinal UNIQUE(tenant_id,batch_id,ordinal),
        CONSTRAINT uq_subscription_group_add_phone UNIQUE(tenant_id,batch_id,phone),
        CONSTRAINT fk_subscription_group_add_item_context
          FOREIGN KEY(tenant_id,batch_id,device_id,generation,settings_revision)
          REFERENCES public.subscription_group_add_batches(tenant_id,id,device_id,generation,settings_revision),
        CONSTRAINT subscription_group_add_claim CHECK((claimed_owner IS NULL)=(claimed_fence IS NULL)),
        CONSTRAINT subscription_group_add_item_terminal CHECK(
          status IN ('pending','running') AND finished_at IS NULL
          OR status IN ('added','already_member','not_added','unknown','cancelled') AND finished_at IS NOT NULL),
        CONSTRAINT subscription_group_add_dispatch CHECK(
          status='pending' AND dispatch_at IS NULL
          OR status IN ('running','added','already_member','not_added','unknown') AND dispatch_at IS NOT NULL
          OR status='cancelled')
      );
      CREATE INDEX ix_subscription_group_add_item_next
        ON public.subscription_group_add_items(tenant_id,batch_id,ordinal) WHERE status='pending';
      CREATE INDEX ix_subscription_group_add_item_unknown
        ON public.subscription_group_add_items(tenant_id,dispatch_at) WHERE status IN ('running','unknown');
    """)
    updates = {
        TABLES[0]: "status,version,next_item_at,started_at,finished_at,error_code",
        TABLES[1]: "status,claimed_owner,claimed_fence,dispatch_at,finished_at,error_code,last_receipt_at,provider_settled",
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
    raise RuntimeError("Preserve membership attempts and unknown outcomes; use a forward migration")
