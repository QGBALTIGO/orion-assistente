"""Prospective service-receipt policies, durable deadlines and receipt history."""
from alembic import op

revision = '0080_service_receipts'
down_revision = '0079_service_archive'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='90s'")
    op.execute("""
      CREATE TABLE tenant_receipt_settings (
        tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        mode varchar(16) NOT NULL CHECK(mode IN ('manual','after_24h')),
        version integer NOT NULL CHECK(version>0),
        activated_at timestamptz,updated_at timestamptz NOT NULL DEFAULT now());
      ALTER TABLE financial_entries
        ADD COLUMN service_receivable boolean NOT NULL DEFAULT false,
        ADD COLUMN keep_pending boolean NOT NULL DEFAULT false,
        ADD COLUMN auto_receipt_due_at timestamptz,
        ADD COLUMN auto_receipt_policy_version integer,
        ADD COLUMN receipt_source varchar(12) CHECK(receipt_source IN ('manual','automatic')),
        ADD COLUMN received_at timestamptz,
        ADD COLUMN receipt_recorded_at timestamptz,
        ADD COLUMN receipt_actor_id uuid;
      CREATE INDEX ix_service_receipt_due ON financial_entries(auto_receipt_due_at,tenant_id,id)
        WHERE service_receivable AND status='pending' AND NOT keep_pending;
      CREATE INDEX ix_financial_cash_period ON financial_entries(tenant_id,COALESCE(received_at,occurred_at),id);
      CREATE TABLE service_receipt_history (
        id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        entry_id uuid NOT NULL,action varchar(16) NOT NULL,
        source varchar(12) NOT NULL CHECK(source IN ('manual','automatic')),
        actor_id uuid,actor_role varchar(20) NOT NULL,
        details jsonb NOT NULL,occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(tenant_id,entry_id) REFERENCES financial_entries(tenant_id,id) ON DELETE CASCADE);
      CREATE INDEX ix_service_receipt_history ON service_receipt_history(tenant_id,entry_id,occurred_at,id);
    """)
    for table in ('tenant_receipt_settings','service_receipt_history'):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_support,app_runtime")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON {table} TO app_runtime
            USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
        op.execute(f"CREATE POLICY migrator_scope ON {table} TO app_migrator USING(true) WITH CHECK(true)")
    op.execute("GRANT SELECT,INSERT,UPDATE ON tenant_receipt_settings TO app_runtime")
    op.execute("GRANT SELECT,INSERT ON service_receipt_history TO app_runtime")
    op.execute("""
      CREATE FUNCTION public.orion_due_service_receipts(batch_limit integer DEFAULT 100)
        RETURNS TABLE(tenant_id uuid,entry_id uuid) LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public AS $fn$
        SELECT e.tenant_id,e.id FROM public.financial_entries e
        JOIN public.tenant_receipt_settings s ON s.tenant_id=e.tenant_id
        JOIN public.tenants t ON t.id=e.tenant_id
        JOIN public.appointments a ON a.tenant_id=e.tenant_id AND a.id=e.appointment_id
        WHERE t.status='active' AND s.mode='after_24h'
          AND e.auto_receipt_policy_version=s.version AND e.service_receivable
          AND e.kind='income' AND e.entry_type='general' AND e.status='pending' AND NOT e.keep_pending
          AND e.auto_receipt_due_at<=now() AND a.status='completed' AND e.amount_cents=a.total_cents
          AND NOT EXISTS(SELECT 1 FROM public.financial_entries other
            WHERE other.tenant_id=e.tenant_id AND other.appointment_id=e.appointment_id
              AND other.kind='income' AND other.status<>'cancelled' AND other.id<>e.id)
          AND $1 BETWEEN 1 AND 200
        ORDER BY e.auto_receipt_due_at,e.tenant_id,e.id LIMIT LEAST(GREATEST($1,1),200)
        $fn$;
      REVOKE ALL ON FUNCTION public.orion_due_service_receipts(integer) FROM PUBLIC,app_runtime;
      GRANT EXECUTE ON FUNCTION public.orion_due_service_receipts(integer) TO app_support;
    """)


def downgrade():
    raise RuntimeError('Preserve receipt history; use a reviewed forward migration')
