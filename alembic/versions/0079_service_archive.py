"""Remove services from the live catalogue while retaining historical references."""
from alembic import op
import sqlalchemy as sa

revision = "0079_service_archive"
down_revision = "0078_customer_legacy_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.add_column("services", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_service_archive_inactive", "services",
                               "deleted_at IS NULL OR active=false")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM services WHERE deleted_at IS NOT NULL)
        THEN RAISE EXCEPTION 'Restore or export archived services before removing the archive column';
        END IF; END $$""")
    op.drop_constraint("ck_service_archive_inactive", "services", type_="check")
    op.drop_column("services", "deleted_at")
