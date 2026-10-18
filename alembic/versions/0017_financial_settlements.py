"""Immutable commission settlements and partial allocation of staff advances.

Revision ID: 0017_financial_settlements
Revises: 0016_management_extras
"""
from alembic import op

revision = "0017_financial_settlements"
down_revision = "0016_management_extras"
branch_labels = None
depends_on = None
TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"


def upgrade():
    op.execute("""
        CREATE TABLE commission_settlements (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id),
            professional_id uuid NOT NULL,professional_name varchar(120) NOT NULL,
            earned_cents bigint NOT NULL,payments_before_cents bigint NOT NULL,advances_cents bigint NOT NULL,
            allocated_before_cents bigint NOT NULL,allocated_cents bigint NOT NULL,payment_cents bigint NOT NULL,
            payment_entry_id uuid,snapshot_hash char(64) NOT NULL,snapshot jsonb NOT NULL,
            notes text,operator varchar(30) NOT NULL,settled_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(tenant_id,id),UNIQUE(tenant_id,payment_entry_id),
            FOREIGN KEY(tenant_id,professional_id) REFERENCES professionals(tenant_id,id),
            FOREIGN KEY(tenant_id,payment_entry_id) REFERENCES financial_entries(tenant_id,id),
            CHECK(earned_cents>=0 AND payments_before_cents>=0 AND advances_cents>=0 AND allocated_before_cents>=0
                AND allocated_cents>=0 AND payment_cents>=0 AND allocated_cents+payment_cents>0),
            CHECK(allocated_before_cents+allocated_cents<=advances_cents),
            CHECK(payments_before_cents+allocated_before_cents+allocated_cents+payment_cents<=earned_cents),
            CHECK((payment_cents=0 AND payment_entry_id IS NULL) OR (payment_cents>0 AND payment_entry_id IS NOT NULL)),
            CHECK(jsonb_typeof(snapshot)='object' AND (notes IS NULL OR length(notes)<=5000))
        );
        CREATE INDEX ix_commission_settlement_professional ON commission_settlements(tenant_id,professional_id,settled_at);
        CREATE TABLE commission_advance_allocations (
            tenant_id uuid NOT NULL,settlement_id uuid NOT NULL,advance_entry_id uuid NOT NULL,amount_cents bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,settlement_id,advance_entry_id),
            FOREIGN KEY(tenant_id,settlement_id) REFERENCES commission_settlements(tenant_id,id),
            FOREIGN KEY(tenant_id,advance_entry_id) REFERENCES financial_entries(tenant_id,id),CHECK(amount_cents>0)
        );
        CREATE INDEX ix_advance_allocation_entry ON commission_advance_allocations(tenant_id,advance_entry_id);
    """)
    for table in ("commission_settlements", "commission_advance_allocations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={TENANT}) WITH CHECK(tenant_id={TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO app_runtime")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM commission_settlements) THEN
        RAISE EXCEPTION 'Cannot downgrade while commission settlement history exists'; END IF; END $$""")
    op.drop_table("commission_advance_allocations")
    op.drop_table("commission_settlements")
