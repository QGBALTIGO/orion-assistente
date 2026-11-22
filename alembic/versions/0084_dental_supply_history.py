"""Index the tenant-scoped stock history by item and date."""
from alembic import op

revision = "0084_dental_supply_history"
down_revision = "0083_dental_intake_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE INDEX ix_dental_supply_movement_history
        ON public.dental_supply_movements (tenant_id,supply_id,occurred_at DESC,id DESC)
    """)


def downgrade():
    op.execute("DROP INDEX public.ix_dental_supply_movement_history")
