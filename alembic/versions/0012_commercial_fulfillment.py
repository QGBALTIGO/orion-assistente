"""Persist explicit receiving contracts without changing historical orders.

Revision ID: 0012_commercial_fulfillment
Revises: 0011_commercial_catalog
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0012_commercial_fulfillment"
down_revision = "0011_commercial_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("commercial_quotes", sa.Column("fulfillment_mode", sa.String(16), nullable=True))
    op.add_column("commercial_quotes", sa.Column("pickup_appointment_id", UUID(as_uuid=True), nullable=True))
    op.add_column("commercial_quotes", sa.Column("fulfillment_snapshot", JSONB(), nullable=True))
    op.create_foreign_key("fk_quote_pickup_appointment", "commercial_quotes", "appointments",
        ["tenant_id", "pickup_appointment_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_quote_fulfillment", "commercial_quotes", """
        (fulfillment_mode IS NULL AND pickup_appointment_id IS NULL AND fulfillment_snapshot IS NULL)
        OR (fulfillment_mode IS NOT NULL AND fulfillment_mode IN ('pickup','delivery') AND fulfillment_snapshot IS NOT NULL
            AND jsonb_typeof(fulfillment_snapshot)='object'
            AND COALESCE(fulfillment_snapshot->>'schema_version'='1',false)
            AND COALESCE(fulfillment_snapshot->>'mode'=fulfillment_mode,false)
            AND (pickup_appointment_id IS NULL OR fulfillment_mode='pickup'))
    """)
    op.create_index("ix_quote_pickup_appointment", "commercial_quotes", ["tenant_id", "pickup_appointment_id"],
        postgresql_where=sa.text("pickup_appointment_id IS NOT NULL"))


def downgrade():
    # Operational rollback keeps these fields after they contain real contracts.
    op.drop_index("ix_quote_pickup_appointment", table_name="commercial_quotes")
    op.drop_constraint("ck_quote_fulfillment", "commercial_quotes", type_="check")
    op.drop_constraint("fk_quote_pickup_appointment", "commercial_quotes", type_="foreignkey")
    for name in ("fulfillment_snapshot", "pickup_appointment_id", "fulfillment_mode"):
        op.drop_column("commercial_quotes", name)
