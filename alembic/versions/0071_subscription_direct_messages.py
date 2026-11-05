"""Separate owner-confirmed direct sends from the existing bulk workbench."""
from alembic import op

revision = "0071_subscription_direct"
down_revision = "0070_subscription_bulk"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("""
      ALTER TABLE public.subscription_bulk_batches
        ADD COLUMN origin varchar(8) NOT NULL DEFAULT 'bulk',
        ADD CONSTRAINT subscription_bulk_batch_origin CHECK(origin IN ('bulk','direct')),
        ADD CONSTRAINT uq_subscription_bulk_batch_origin UNIQUE(tenant_id,id,origin);
      ALTER TABLE public.subscription_bulk_requests
        ADD COLUMN origin varchar(8) NOT NULL DEFAULT 'bulk',
        ADD CONSTRAINT subscription_bulk_request_origin CHECK(origin IN ('bulk','direct')),
        ADD CONSTRAINT fk_subscription_bulk_request_origin FOREIGN KEY(tenant_id,bulk_id,origin)
          REFERENCES public.subscription_bulk_batches(tenant_id,id,origin);
    """)
    # Existing column-level UPDATE grants deliberately exclude both origins.
    # The default classifies every existing row without rewriting its snapshot,
    # fingerprint, receipt or recovery key.


def downgrade():
    raise RuntimeError("Preserve direct-send origin and transport evidence; use a forward migration")
