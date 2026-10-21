"""CRM labels and factual quick replies, private to each company/account."""
from alembic import op

revision = "0024_inbox_tools"
down_revision = "0023_automation_notice_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.inbox_labels (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        name varchar(60) NOT NULL CHECK(length(trim(name))>0),
        color varchar(7) NOT NULL CHECK(color ~ '^#[0-9a-fA-F]{6}$'),
        version integer NOT NULL DEFAULT 1 CHECK(version>0),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id)
      );
      CREATE UNIQUE INDEX uq_inbox_label_name ON public.inbox_labels(tenant_id,lower(name));
      CREATE TABLE public.inbox_customer_labels (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        customer_id uuid NOT NULL,
        label_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,customer_id,label_id),
        FOREIGN KEY(tenant_id,customer_id) REFERENCES public.customers(tenant_id,id),
        FOREIGN KEY(tenant_id,label_id) REFERENCES public.inbox_labels(tenant_id,id) ON DELETE CASCADE
      );
      CREATE INDEX ix_inbox_label_customers ON public.inbox_customer_labels(tenant_id,label_id,customer_id);
      CREATE TABLE public.inbox_quick_replies (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        owner_account_id uuid REFERENCES public.panel_accounts(id),
        title varchar(100) NOT NULL CHECK(length(trim(title))>0),
        category varchar(60) NOT NULL,
        body varchar(1200) NOT NULL CHECK(length(trim(body))>0),
        version integer NOT NULL DEFAULT 1 CHECK(version>0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id)
      );
      CREATE INDEX ix_inbox_quick_reply_scope ON public.inbox_quick_replies(tenant_id,owner_account_id,category,id);
    """)
    setting = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    for table in ("inbox_labels", "inbox_customer_labels", "inbox_quick_replies"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON public.{table} TO app_runtime")


def downgrade():
    # Never silently erase operational records during a rollback.
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.inbox_labels) OR EXISTS(SELECT 1 FROM public.inbox_quick_replies)
        OR EXISTS(SELECT 1 FROM public.inbox_customer_labels) THEN
        RAISE EXCEPTION 'Preserve inbox tools data before downgrade';
      END IF;
    END $$""")
    for table in ("inbox_customer_labels", "inbox_quick_replies", "inbox_labels"):
        op.execute(f"DROP TABLE public.{table}")
