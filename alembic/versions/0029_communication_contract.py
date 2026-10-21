"""Additive communication facts; never rewrites sessions, bookings or templates."""
from alembic import op

revision = '0029_communication_contract'
down_revision = '0028_workflow_drafts'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
      ALTER TABLE public.inbound_events ADD COLUMN envelope jsonb NOT NULL DEFAULT '{}'::jsonb;
      ALTER TABLE public.inbound_events ADD COLUMN ingress_order bigint GENERATED ALWAYS AS IDENTITY;
      CREATE INDEX ix_inbound_conversation_order ON public.inbound_events(tenant_id,channel_id,sender_e164,ingress_order DESC);
      ALTER TABLE public.customers ADD COLUMN birth_month smallint;
      ALTER TABLE public.customers ADD COLUMN birth_day smallint;
      ALTER TABLE public.customers ADD CONSTRAINT ck_customer_birthday_partial CHECK(
        (birth_month IS NULL AND birth_day IS NULL) OR
        (birth_month IS NOT NULL AND birth_day IS NOT NULL AND
          CASE WHEN birth_month BETWEEN 1 AND 12 THEN birth_day BETWEEN 1 AND
            EXTRACT(DAY FROM (make_date(2000,birth_month,1)+interval '1 month - 1 day')) ELSE false END));
      ALTER TABLE public.contact_consents ADD COLUMN birthday_opt_in boolean NOT NULL DEFAULT false;
      ALTER TABLE public.contact_consents ADD COLUMN birthday_declined_at timestamptz;
      ALTER TABLE public.contact_consents ADD COLUMN birthday_purpose_version integer NOT NULL DEFAULT 0;
      ALTER TABLE public.contact_consents ADD COLUMN operational_updated_at timestamptz;
      ALTER TABLE public.contact_consents ADD COLUMN marketing_updated_at timestamptz;
      ALTER TABLE public.contact_consents ADD COLUMN birthday_updated_at timestamptz;
      UPDATE public.contact_consents SET operational_updated_at=updated_at,marketing_updated_at=updated_at,birthday_updated_at=updated_at;
      ALTER TABLE public.contact_consents ALTER COLUMN operational_updated_at SET NOT NULL;
      ALTER TABLE public.contact_consents ALTER COLUMN marketing_updated_at SET NOT NULL;
      ALTER TABLE public.contact_consents ALTER COLUMN birthday_updated_at SET NOT NULL;
      CREATE FUNCTION public.communication_consent_versions() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $body$
      BEGIN
        IF TG_OP='INSERT' THEN
          NEW.operational_updated_at:=NEW.updated_at;
          NEW.marketing_updated_at:=NEW.updated_at;
          NEW.birthday_updated_at:=NEW.updated_at;
        ELSE
          NEW.operational_updated_at:=CASE WHEN NEW.operational_opt_in IS DISTINCT FROM OLD.operational_opt_in
            THEN GREATEST(clock_timestamp(),OLD.operational_updated_at+interval '1 microsecond') ELSE OLD.operational_updated_at END;
          NEW.marketing_updated_at:=CASE WHEN NEW.marketing_opt_in IS DISTINCT FROM OLD.marketing_opt_in
            THEN GREATEST(clock_timestamp(),OLD.marketing_updated_at+interval '1 microsecond') ELSE OLD.marketing_updated_at END;
          NEW.birthday_updated_at:=CASE WHEN ROW(NEW.birthday_opt_in,NEW.birthday_declined_at,NEW.birthday_purpose_version)
              IS DISTINCT FROM ROW(OLD.birthday_opt_in,OLD.birthday_declined_at,OLD.birthday_purpose_version)
            THEN GREATEST(clock_timestamp(),OLD.birthday_updated_at+interval '1 microsecond') ELSE OLD.birthday_updated_at END;
        END IF;
        RETURN NEW;
      END $body$;
      REVOKE ALL ON FUNCTION public.communication_consent_versions() FROM PUBLIC;
      CREATE TRIGGER communication_consent_versions BEFORE INSERT OR UPDATE ON public.contact_consents
        FOR EACH ROW EXECUTE FUNCTION public.communication_consent_versions();
      ALTER TABLE public.automation_jobs ADD COLUMN presentation jsonb NOT NULL DEFAULT '{}'::jsonb;
      ALTER TABLE public.inbox_messages ADD COLUMN envelope jsonb NOT NULL DEFAULT '{}'::jsonb;
      CREATE TABLE public.communication_pending (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        contact_e164 varchar(16) NOT NULL CHECK(contact_e164 ~ '^\\+55[1-9][0-9]{9,10}$'),
        event_key varchar(240) NOT NULL,
        event varchar(32) NOT NULL,
        reason varchar(64) NOT NULL,
        appointment_id uuid,
        appointment_version timestamptz,
        status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','acknowledged','obsolete')),
        acknowledged_by uuid REFERENCES public.panel_accounts(id),
        version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,contact_e164,event_key,reason),
        FOREIGN KEY(tenant_id,appointment_id) REFERENCES public.appointments(tenant_id,id)
      );
      CREATE INDEX ix_communication_pending ON public.communication_pending(tenant_id,status,created_at DESC);
    ''')
    tenant = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    op.execute('ALTER TABLE public.communication_pending ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public.communication_pending FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY communication_pending_runtime ON public.communication_pending FOR ALL TO app_runtime USING(tenant_id={tenant}) WITH CHECK(tenant_id={tenant})')
    op.execute('CREATE POLICY communication_pending_migrator ON public.communication_pending FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
    op.execute('REVOKE ALL ON public.communication_pending FROM PUBLIC,app_support')
    op.execute('GRANT SELECT,INSERT,UPDATE ON public.communication_pending TO app_runtime')
    # Identity sequence can only be used through tenant-scoped INSERT.
    op.execute('GRANT USAGE ON SEQUENCE public.inbound_events_ingress_order_seq TO app_runtime')


def downgrade():
    raise RuntimeError('Preserve communication events and consent history; use a forward migration')
