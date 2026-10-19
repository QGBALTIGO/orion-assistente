"""Durable transactional appointment notices, independent of marketing and AI."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
revision = '0020_appointment_notices'
down_revision = '0019_appointment_lifecycle'
branch_labels = depends_on = None
TABLE = 'appointment_notices'
SETTING = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"

def upgrade():
    op.create_table(TABLE,
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('appointment_id', UUID(as_uuid=True), nullable=False),
        sa.Column('appointment_version', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event', sa.String(20), nullable=False),
        sa.Column('snapshot', JSONB, nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('account_phone', sa.String(16)),
        sa.Column('message_id', sa.String(100)),
        sa.Column('last_error', sa.String(80)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.ForeignKeyConstraint(['tenant_id','appointment_id'], ['appointments.tenant_id','appointments.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('tenant_id','appointment_id','event','appointment_version',name='uq_appointment_notice_version'),
        sa.CheckConstraint("event IN ('confirmed','rescheduled','cancelled','completed','no_show')",name='ck_appointment_notice_event'),
        sa.CheckConstraint("status IN ('pending','blocked','sending','sent','delivered','read','uncertain','superseded','expired','failed')",name='ck_appointment_notice_status'),
        sa.CheckConstraint("attempts>=0 AND jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=8192",name='ck_appointment_notice_payload'))
    op.create_index('ix_appointment_notices_due',TABLE,['status','next_attempt_at'])
    op.create_index('ix_appointment_notices_booking',TABLE,['tenant_id','appointment_id','created_at'])
    op.execute(f'ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {TABLE} FROM PUBLIC')
    op.execute(f'CREATE POLICY {TABLE}_runtime ON {TABLE} FOR ALL TO app_runtime USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})')
    op.execute(f'CREATE POLICY {TABLE}_migrator ON {TABLE} FOR ALL TO app_migrator USING (true) WITH CHECK (true)')
    op.execute(f'GRANT SELECT,INSERT,UPDATE ON {TABLE} TO app_runtime')
    # Cross-tenant scanning sees routing metadata, never phone, text, or snapshot.
    op.execute(f'CREATE POLICY {TABLE}_scan ON {TABLE} FOR SELECT TO app_support USING (true)')
    op.execute(f'GRANT SELECT(id,tenant_id,status,next_attempt_at) ON {TABLE} TO app_support')

def downgrade():
    op.drop_table(TABLE)
