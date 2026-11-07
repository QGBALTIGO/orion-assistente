"""Explicit, appointment-scoped WhatsApp permission without marketing enrollment.

No historical booking or contact preference is converted into permission.
The central sender checks the same scope and purpose as the application.
"""
from alembic import op

revision = "0061_appointment_consent"
down_revision = "0060_manual_start_times"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
      CREATE TABLE public.appointment_message_consents (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        appointment_id uuid NOT NULL,
        customer_id uuid NOT NULL,
        contact_e164 varchar(40) NOT NULL,
        operational_opt_in boolean NOT NULL DEFAULT false,
        review_opt_in boolean NOT NULL DEFAULT false,
        source varchar(32) NOT NULL CHECK (source IN ('panel','professional_portal','customer_whatsapp')),
        actor jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(actor)='object'),
        purpose_version integer NOT NULL DEFAULT 1 CHECK (purpose_version=1),
        evidence_hash varchar(64) NOT NULL CHECK (evidence_hash ~ '^[a-f0-9]{64}$'),
        granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,appointment_id),
        FOREIGN KEY(tenant_id,appointment_id) REFERENCES public.appointments(tenant_id,id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,customer_id) REFERENCES public.customers(tenant_id,id) ON DELETE CASCADE
      );
      CREATE INDEX ix_appointment_message_consents_contact
        ON public.appointment_message_consents(tenant_id,contact_e164);
      ALTER TABLE public.appointment_message_consents ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.appointment_message_consents FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.appointment_message_consents FROM PUBLIC,app_runtime,app_support;
      GRANT SELECT,INSERT,UPDATE ON public.appointment_message_consents TO app_runtime;
      CREATE POLICY appointment_message_consents_runtime ON public.appointment_message_consents
        FOR ALL TO app_runtime
        USING (tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid))
        WITH CHECK (tenant_id=(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid));
      CREATE POLICY appointment_message_consents_migrator ON public.appointment_message_consents
        FOR ALL TO app_migrator USING(true) WITH CHECK(true);
      ALTER TABLE public.appointment_notifications
        ADD COLUMN requires_marketing boolean NOT NULL DEFAULT false;
      -- A review prepared by the previous release always required marketing.
      -- Keep that requirement during overlapping old/new worker deployments.
      UPDATE public.appointment_notifications SET requires_marketing=true
        WHERE event='review' AND body_encrypted IS NOT NULL;
    """)
    op.execute(CENTRAL_AUTHORIZATION_SQL)


def downgrade():
    raise RuntimeError("Preserve explicit appointment permissions and delivery evidence; use a forward migration")


CENTRAL_AUTHORIZATION_SQL = r"""
  CREATE OR REPLACE FUNCTION public.orion_authorize_central_appointment_notification(
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
        SELECT 1 FROM public.appointment_notifications n
        JOIN public.appointments a ON a.tenant_id=n.tenant_id AND a.id=n.appointment_id
        JOIN public.tenants t ON t.id=n.tenant_id
        LEFT JOIN public.appointment_communication_settings setting ON setting.tenant_id=n.tenant_id
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
        LEFT JOIN public.appointment_message_consents scoped ON scoped.tenant_id=n.tenant_id
          AND scoped.appointment_id=n.appointment_id AND scoped.customer_id=c.id
          AND scoped.contact_e164=c.whatsapp_e164 AND scoped.purpose_version=1
        WHERE n.id=requested_operation AND n.central_channel_id=sender_channel
          AND n.channel_id IS NULL AND n.status='sending'
          AND n.body_sha256=message_digest AND n.recipient_hash=recipient_digest
          AND n.attempted_at>clock_timestamp()-interval '10 minutes'
          AND n.expires_at>clock_timestamp() AND n.due_at<=clock_timestamp()
          AND n.appointment_version=a.updated_at AND t.status='active'
          AND (setting.tenant_id IS NULL OR setting.enabled)
          AND coalesce(setting.version,0)=n.settings_version
          AND a.status=CASE WHEN n.event='review' THEN 'completed'
            WHEN n.event LIKE '%cancelled' THEN 'cancelled' ELSE 'confirmed' END
          AND (n.event<>'review' OR (
            NOT a.customer_access_blocked AND a.start_at<=clock_timestamp()
            AND EXISTS (SELECT 1 FROM public.appointment_history h
              WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
                AND h.event='completed' AND h.details->>'v'='1'
                AND h.occurred_at<=clock_timestamp()
                AND ((h.details->>'source'='panel'
                    AND h.details->'actor'->>'role' IN ('owner','editor','admin','support','professional'))
                  OR (h.details->>'source'='system' AND h.details->'actor'->>'role'='system'
                    AND h.details->>'requested_by'='system'
                    AND h.details->'previous'->>'status'='confirmed'
                    AND a.end_at<=clock_timestamp() AND h.occurred_at>=a.end_at)))))
          AND ((n.recipient_kind='professional' AND p.active
                AND p.phone_e164='+'||recipient_phone AND p.id=a.professional_id)
            OR (n.recipient_kind='customer' AND c.active AND c.deleted_at IS NULL
                AND c.id=a.customer_id AND c.whatsapp_e164='+'||recipient_phone
                AND ((consent.operational_opt_in IS TRUE
                      AND (n.event<>'review' OR consent.marketing_opt_in IS TRUE))
                  OR (scoped.operational_opt_in IS TRUE
                      AND (n.event<>'review' OR scoped.review_opt_in IS TRUE)))
                AND (NOT n.requires_marketing OR consent.marketing_opt_in IS TRUE)))
      ) END
  $body$;
  REVOKE ALL ON FUNCTION public.orion_authorize_central_appointment_notification(
    uuid,uuid,uuid,text,text,text) FROM PUBLIC,app_runtime;
  GRANT EXECUTE ON FUNCTION public.orion_authorize_central_appointment_notification(
    uuid,uuid,uuid,text,text,text) TO app_support;
"""
