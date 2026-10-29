"""Revocable customer appointment capabilities and private reviews; additive only."""
from alembic import op

revision = '0047_appointment_access'
down_revision = '0046_orion_direct_checkout'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.execute('''
        CREATE TABLE appointment_access (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES tenants(id),
          appointment_id uuid NOT NULL,
          customer_id uuid NOT NULL,
          customer_version integer NOT NULL,
          token_hash char(64) NOT NULL,
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(tenant_id,token_hash),
          FOREIGN KEY(tenant_id,appointment_id) REFERENCES appointments(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,customer_id) REFERENCES customers(tenant_id,id) ON DELETE CASCADE
        );
        CREATE INDEX ix_appointment_access_booking ON appointment_access(tenant_id,appointment_id);
        CREATE TABLE appointment_reviews (
          tenant_id uuid NOT NULL REFERENCES tenants(id),
          appointment_id uuid NOT NULL,
          customer_id uuid NOT NULL,
          professional_id uuid NOT NULL,
          rating smallint NOT NULL CHECK(rating BETWEEN 1 AND 5),
          comment varchar(1000),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(tenant_id,appointment_id),
          FOREIGN KEY(tenant_id,appointment_id) REFERENCES appointments(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,customer_id) REFERENCES customers(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,professional_id) REFERENCES professionals(tenant_id,id)
        );
    ''')
    setting = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    for table in ('appointment_access', 'appointment_reviews'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})')
        op.execute(f'CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
        op.execute(f'REVOKE ALL ON {table} FROM PUBLIC,app_support,app_runtime')
        op.execute(f'GRANT SELECT,INSERT,UPDATE ON {table} TO app_runtime')


def downgrade():
    raise RuntimeError('Preserve appointment links and reviews; disable flag and use a forward migration')
