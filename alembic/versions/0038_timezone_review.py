"""Owner review receipts for timezone corrections; no appointment is shifted.

Revision ID: 0038_timezone_review
Revises: 0037_public_runtime
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision='0038_timezone_review'
down_revision='0037_public_runtime'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('public_site_timezone_reviews',
        sa.Column('tenant_id',UUID(as_uuid=True),primary_key=True),
        sa.Column('appointment_id',UUID(as_uuid=True),primary_key=True),
        sa.Column('from_timezone',sa.String(64),nullable=False),
        sa.Column('to_timezone',sa.String(64),nullable=False),
        sa.Column('original_start_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('now()')),
        sa.Column('acknowledged_at',sa.DateTime(timezone=True),nullable=True),
        sa.ForeignKeyConstraint(['tenant_id','appointment_id'],['appointments.tenant_id','appointments.id'],ondelete='CASCADE'))
    tenant="(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    op.execute('ALTER TABLE public_site_timezone_reviews ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public_site_timezone_reviews FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY public_timezone_reviews_runtime ON public_site_timezone_reviews FOR ALL TO app_runtime USING(tenant_id={tenant}) WITH CHECK(tenant_id={tenant})')
    op.execute('CREATE POLICY public_timezone_reviews_migrator ON public_site_timezone_reviews FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
    op.execute('REVOKE ALL ON public_site_timezone_reviews FROM PUBLIC,app_runtime,app_support')
    op.execute('GRANT SELECT ON public_site_timezone_reviews TO app_runtime')
    op.execute('GRANT UPDATE(acknowledged_at) ON public_site_timezone_reviews TO app_runtime')


def downgrade():
    raise RuntimeError('Timezone review receipts must be preserved; use reviewed forward recovery')
