"""Structured, tenant-scoped appointment lifecycle history.

Revision ID: 0019_appointment_lifecycle
Revises: 0018_sales_requests

Existing events retain an empty object: the historical operator field did not
distinguish automated, customer and panel actions, so attribution is not inferred.
"""
from alembic import op

revision = "0019_appointment_lifecycle"
down_revision = "0018_sales_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.appointment_history
            ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE public.appointment_history
            ADD CONSTRAINT ck_appointment_history_details
            CHECK (jsonb_typeof(details)='object' AND octet_length(details::text)<=8192);
        CREATE INDEX ix_appointment_history_timeline
            ON public.appointment_history(tenant_id,appointment_id,occurred_at DESC,id DESC);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.appointment_history WHERE details<>'{}'::jsonb) THEN
            RAISE EXCEPTION 'Cannot downgrade while structured appointment history exists';
        END IF;
        END $$""")
    op.drop_index("ix_appointment_history_timeline", table_name="appointment_history", schema="public")
    op.drop_constraint("ck_appointment_history_details", "appointment_history", type_="check", schema="public")
    op.drop_column("appointment_history", "details", schema="public")
