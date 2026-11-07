"""Add receipt provenance and fair, read-only provider reconciliation cursors."""
from alembic import op
import sqlalchemy as sa

revision = "0073_cakto_recovery"
down_revision = "0072_subscription_group_add"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cakto_webhook_events", sa.Column(
        "receipt_origin", sa.String(24), nullable=False, server_default="webhook_hmac"))
    op.create_check_constraint("ck_cakto_receipt_origin", "cakto_webhook_events",
                               "receipt_origin IN ('webhook_hmac','provider_read')")
    for table in ("orion_prepaid_pix_orders", "orion_direct_payment_attempts", "orion_cakto_bindings"):
        op.add_column(table, sa.Column("provider_checked_at", sa.DateTime(timezone=True)))
    op.add_column("orion_prepaid_pix_orders", sa.Column("pix_code_enc", sa.Text()))
    op.add_column("orion_prepaid_pix_orders", sa.Column("pix_expires_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_prepaid_pix_resume_cipher", "orion_prepaid_pix_orders", """
        (pix_code_enc IS NULL AND pix_expires_at IS NULL) OR
        (pix_code_enc IS NOT NULL AND pix_expires_at IS NOT NULL
         AND char_length(pix_code_enc)>=100 AND char_length(pix_code_enc)<=32768)
    """)
    op.create_index("ix_prepaid_pix_provider_check", "orion_prepaid_pix_orders",
                    ["state", "provider_checked_at", "id"])
    op.create_index("ix_direct_provider_check", "orion_direct_payment_attempts",
                    ["state", "provider_checked_at", "id"])
    op.create_index("ix_cakto_binding_provider_check", "orion_cakto_bindings",
                    ["provider_checked_at", "tenant_id"])


def downgrade():
    raise RuntimeError("Financial receipt provenance must be preserved; use a forward migration")
