"""Tenant-scoped appointment outbox with one authenticated platform WhatsApp sender.

Keep legacy per-tenant channel FKs. Central rows have no local channel and can
only be authorized by a boolean, support-only SECURITY DEFINER receipt check.
No customer or professional data are exposed through this function.
"""
from alembic import op

revision = "0053_central_notifications"
down_revision = "0052_paraguay_phone"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.execute("""
      ALTER TABLE public.appointment_notifications
        ALTER COLUMN channel_id DROP NOT NULL;
      ALTER TABLE public.appointment_notifications
        ADD COLUMN central_channel_id uuid,
        ADD COLUMN body_sha256 varchar(64);
      ALTER TABLE public.appointment_notifications
        ADD CONSTRAINT ck_appointment_notice_exactly_one_channel CHECK (
          (channel_id IS NULL) <> (central_channel_id IS NULL));
      ALTER TABLE public.appointment_notifications
        ADD CONSTRAINT ck_appointment_notice_body_hash CHECK (
          body_sha256 IS NULL OR body_sha256 ~ '^[a-f0-9]{64}$');
      CREATE INDEX ix_appointment_notifications_central_receipt
        ON public.appointment_notifications(central_channel_id,id)
        WHERE central_channel_id IS NOT NULL;
    """)
    op.execute(r"""
      CREATE FUNCTION public.orion_authorize_central_appointment_notification(
        requested_operation uuid, configured_admin uuid, sender_channel uuid,
        recipient_phone text, recipient_digest text, message_digest text)
      RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog AS $body$
        SELECT CASE WHEN session_user <> 'app_support'
            OR requested_operation IS NULL OR configured_admin IS NULL
            OR sender_channel IS NULL OR recipient_phone IS NULL
            OR recipient_phone !~ '^(55[1-9][0-9]{9,10}|5959[0-9]{8})$'
            OR recipient_digest !~ '^[a-f0-9]{64}$'
            OR message_digest !~ '^[a-f0-9]{64}$'
          THEN false ELSE EXISTS (
            SELECT 1
            FROM public.appointment_notifications n
            JOIN public.appointments a ON a.tenant_id=n.tenant_id
              AND a.id=n.appointment_id
            JOIN public.tenants t ON t.id=n.tenant_id
            LEFT JOIN public.appointment_communication_settings setting
              ON setting.tenant_id=n.tenant_id
            JOIN public.qr_channels hub ON hub.id=n.central_channel_id
              AND hub.tenant_id=configured_admin AND hub.enabled
            JOIN public.tenant_memberships m ON m.tenant_id=hub.tenant_id
              AND m.account_id=hub.owner_account_id AND m.active AND m.role='owner'
            LEFT JOIN public.professionals p ON p.tenant_id=n.tenant_id
              AND p.id=n.recipient_id AND n.recipient_kind='professional'
            LEFT JOIN public.customers c ON c.tenant_id=n.tenant_id
              AND c.id=n.recipient_id AND n.recipient_kind='customer'
            LEFT JOIN public.contact_consents consent ON consent.tenant_id=c.tenant_id
              AND consent.contact_e164=c.whatsapp_e164
            WHERE n.id=requested_operation AND n.central_channel_id=sender_channel
              AND n.channel_id IS NULL AND n.status='sending'
              AND n.body_sha256=message_digest AND n.recipient_hash=recipient_digest
              AND n.attempted_at>clock_timestamp()-interval '10 minutes'
              AND n.expires_at>clock_timestamp() AND n.due_at<=clock_timestamp()
              AND n.appointment_version=a.updated_at
              AND t.status='active'
              AND (setting.tenant_id IS NULL OR setting.enabled)
              AND coalesce(setting.version,0)=n.settings_version
              AND a.status=CASE WHEN n.event='review' THEN 'completed'
                WHEN n.event LIKE '%cancelled' THEN 'cancelled' ELSE 'confirmed' END
              AND ((n.recipient_kind='professional'
                    AND p.active AND p.phone_e164='+'||recipient_phone
                    AND p.id=a.professional_id)
                OR (n.recipient_kind='customer'
                    AND c.active AND c.deleted_at IS NULL
                    AND c.id=a.customer_id AND c.whatsapp_e164='+'||recipient_phone
                    AND consent.operational_opt_in IS TRUE
                    AND (n.event<>'review' OR consent.marketing_opt_in IS TRUE)))
          ) END
      $body$;
      REVOKE ALL ON FUNCTION public.orion_authorize_central_appointment_notification(
        uuid,uuid,uuid,text,text,text) FROM PUBLIC,app_runtime;
      GRANT EXECUTE ON FUNCTION public.orion_authorize_central_appointment_notification(
        uuid,uuid,uuid,text,text,text) TO app_support;
    """)


def downgrade():
    raise RuntimeError("Preserve central communication receipts and existing appointments")
