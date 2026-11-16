"""Keep imported customer history separate from live appointments and revenue."""
from alembic import op

revision = "0078_customer_legacy_history"
down_revision = "0077_commercial_demos"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""CREATE TABLE public.customer_legacy_history (
        tenant_id uuid NOT NULL,
        customer_id uuid NOT NULL,
        source_system varchar(80) NOT NULL,
        source_name varchar(255) NOT NULL,
        source_phone varchar(32) NOT NULL,
        reported_appointments integer NOT NULL CHECK(reported_appointments >= 0),
        reported_spent_cents bigint NOT NULL CHECK(reported_spent_cents >= 0),
        reported_ticket_cents bigint NOT NULL CHECK(reported_ticket_cents >= 0),
        history jsonb NOT NULL CHECK(jsonb_typeof(history)='array'),
        history_complete boolean NOT NULL,
        source_sha256 char(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
        imported_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, customer_id, source_system),
        UNIQUE (tenant_id, source_system, source_phone),
        FOREIGN KEY (tenant_id, customer_id) REFERENCES public.customers(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute("ALTER TABLE public.customer_legacy_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.customer_legacy_history FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.customer_legacy_history FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT ON public.customer_legacy_history TO app_runtime")
    op.execute("GRANT SELECT,INSERT ON public.customer_legacy_history TO app_migrator")
    op.execute("""CREATE POLICY customer_legacy_runtime_read ON public.customer_legacy_history
        FOR SELECT TO app_runtime USING
        (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
    op.execute("""CREATE POLICY customer_legacy_migrator ON public.customer_legacy_history
        FOR ALL TO app_migrator USING (true) WITH CHECK (true)""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.customer_legacy_history)
        THEN RAISE EXCEPTION 'Export legacy histories before removing this schema'; END IF; END $$""")
    op.drop_table("customer_legacy_history")
