"""Transactional appointment outbox, separate from promotional audiences."""
from alembic import op

revision='0048_appointment_notifications'
down_revision='0047_appointment_access'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.execute('''CREATE TABLE appointment_communication_settings (
      tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
      enabled boolean NOT NULL DEFAULT false,
      channel_id uuid,
      confirmation boolean NOT NULL DEFAULT true,
      professional_notice boolean NOT NULL DEFAULT true,
      cancellation boolean NOT NULL DEFAULT true,
      rescheduling boolean NOT NULL DEFAULT true,
      reschedule_acceptance boolean NOT NULL DEFAULT false,
      reminder boolean NOT NULL DEFAULT true,
      review boolean NOT NULL DEFAULT false,
      return_invitation boolean NOT NULL DEFAULT false,
      review_delay_min integer NOT NULL DEFAULT 15 CHECK(review_delay_min BETWEEN 1 AND 1440),
      signature varchar(80) NOT NULL DEFAULT 'Orion Agenda',
      version integer NOT NULL DEFAULT 1,
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(tenant_id,channel_id) REFERENCES qr_channels(tenant_id,id)
    );
    CREATE TABLE appointment_notifications (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id),
      appointment_id uuid NOT NULL,appointment_version timestamptz NOT NULL,
      event varchar(40) NOT NULL,recipient_kind varchar(16) NOT NULL CHECK(recipient_kind IN ('customer','professional')),
      recipient_id uuid NOT NULL,channel_id uuid NOT NULL,settings_version integer NOT NULL,
      event_key varchar(240) NOT NULL,previous jsonb NOT NULL DEFAULT '{}'::jsonb,
      due_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
      status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','accepted','delivered','read','failed','unknown','cancelled')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
      recipient_hash char(64),body_encrypted text,provider_id varchar(200),last_error varchar(64),
      attempted_at timestamptz,updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(tenant_id,event_key,recipient_kind,recipient_id),
      FOREIGN KEY(tenant_id,appointment_id) REFERENCES appointments(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,channel_id) REFERENCES qr_channels(tenant_id,id),CHECK(expires_at>due_at)
    );
    CREATE INDEX ix_appointment_notifications_due ON appointment_notifications(status,due_at,id);
    CREATE TABLE appointment_confirmation_receipts (
      tenant_id uuid NOT NULL REFERENCES tenants(id),channel_key varchar(200) NOT NULL,
      message_id varchar(200) NOT NULL,appointment_version timestamptz NOT NULL,body_encrypted text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,channel_key,message_id)
    );''')
    op.execute('''CREATE TABLE appointment_change_proposals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id),
      appointment_id uuid NOT NULL,appointment_version timestamptz NOT NULL,new_start_at timestamptz NOT NULL,
      actor_account_id uuid REFERENCES panel_accounts(id),actor_role varchar(16) NOT NULL,
      idempotency_key varchar(120) NOT NULL,UNIQUE(tenant_id,appointment_id,idempotency_key),
      expires_at timestamptz NOT NULL,status varchar(16) NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','accepted','declined','superseded')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(tenant_id,appointment_id) REFERENCES appointments(tenant_id,id) ON DELETE CASCADE
    ); CREATE INDEX ix_appointment_change_pending ON appointment_change_proposals(tenant_id,appointment_id,status);''')
    setting="(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    for table in ('appointment_communication_settings','appointment_notifications','appointment_confirmation_receipts','appointment_change_proposals'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})')
        op.execute(f'CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
        op.execute(f'REVOKE ALL ON {table} FROM PUBLIC,app_support,app_runtime')
        op.execute(f'GRANT SELECT,INSERT,UPDATE ON {table} TO app_runtime')
    op.execute('''CREATE FUNCTION orion_due_appointment_notifications(batch_limit integer)
      RETURNS TABLE(tenant_id uuid,id uuid) LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,public AS $$
        SELECT n.tenant_id,n.id FROM public.appointment_notifications n
        WHERE $1 BETWEEN 1 AND 100 AND ((n.status='pending' AND n.due_at<=now())
          OR (n.status='sending' AND n.attempted_at<now()-interval '2 minutes')
          OR (n.status IN ('accepted','unknown','delivered') AND n.attempted_at>now()-interval '7 days'
            AND n.updated_at<now()-interval '5 minutes'))
        ORDER BY CASE WHEN n.status='pending' THEN 0 WHEN n.status='sending' THEN 1 ELSE 2 END,n.updated_at,n.due_at,n.id LIMIT LEAST(GREATEST($1,1),100)
      $$;
      REVOKE ALL ON FUNCTION orion_due_appointment_notifications(integer) FROM PUBLIC,app_runtime;
      GRANT EXECUTE ON FUNCTION orion_due_appointment_notifications(integer) TO app_support;''')


def downgrade():
    raise RuntimeError('Preserve communication history; disable feature and use a forward migration')
