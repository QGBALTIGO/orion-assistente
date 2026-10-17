"""Maximum five attempts for each one-time email code.

Revision ID: 0009_email_otp
Revises: 0008_panel_accounts
"""
from alembic import op
import sqlalchemy as sa

revision="0009_email_otp"
down_revision="0008_panel_accounts"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("panel_email_tokens",
                  sa.Column("attempts",sa.Integer(),nullable=False,server_default="0"))
    op.create_check_constraint("ck_panel_email_attempts","panel_email_tokens",
                               "attempts >= 0 AND attempts <= 5")

def downgrade():
    op.drop_constraint("ck_panel_email_attempts","panel_email_tokens",type_="check")
    op.drop_column("panel_email_tokens","attempts")
