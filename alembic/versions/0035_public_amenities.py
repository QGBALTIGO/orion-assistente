"""Opt-in amenities on each tenant's public site preferences.

Revision ID: 0035_public_amenities
Revises: 0034_variable_combo_pricing

Metadata-only PostgreSQL 16 column additions with safe false defaults. No
changes to customers, appointments, existing site snapshots or booking flows.
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_public_amenities"
down_revision = "0034_variable_combo_pricing"
branch_labels = None
depends_on = None

FIELDS = (
    "amenity_wifi",
    "amenity_parking",
    "amenity_accessibility",
    "amenity_children",
)


def upgrade():
    for name in FIELDS:
        op.add_column(
            "management_site_preferences",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    # Existing businesses may have opted in to specific amenities. Dropping the
    # columns would silently destroy that data; require a reviewed rollback.
    raise RuntimeError("Public amenity preferences require reviewed forward recovery.")
