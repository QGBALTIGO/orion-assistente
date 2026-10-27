"""Private per-professional WhatsApp contact; existing profiles stay unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0040_professional_phone"
down_revision = "0039_registration_confirmation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.add_column("professionals", sa.Column("phone_e164", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_professional_phone_e164", "professionals",
        r"phone_e164 IS NULL OR phone_e164 ~ '^\+55[1-9][0-9]{9,10}$'",
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    # Refuse a rollback that would discard already registered contacts.
    op.execute("LOCK TABLE professionals IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM professionals WHERE phone_e164 IS NOT NULL)"
    )).scalar_one():
        raise RuntimeError("professional_phone_downgrade_would_discard_contacts")
    op.drop_constraint("ck_professional_phone_e164", "professionals", type_="check")
    op.drop_column("professionals", "phone_e164")
