"""Tenant-authorized automation execution, consent and durable delivery attempts.

Existing campaigns, recipients and contact consents remain the business records.
Jobs are an outbox, not a second catalog or a second marketing audience.
"""
from alembic import op

revision = "0022_marketing_execution"
down_revision = "0021_qr_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE public.contact_consents ADD COLUMN operational_opt_in boolean NOT NULL DEFAULT false;
      CREATE TABLE public.automation_execution (
        tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id),
        enabled boolean NOT NULL DEFAULT false,
        channel_id uuid,
        version integer NOT NULL DEFAULT 1 CHECK(version>0),
        approved_by uuid REFERENCES public.panel_accounts(id),
        approved_auth_version integer,
        reviewed_preferences_version integer,
        approved_at timestamptz,
        daily_limit integer NOT NULL DEFAULT 20 CHECK(daily_limit BETWEEN 1 AND 100),
        last_scanned_at timestamptz,
        scan_cursor integer NOT NULL DEFAULT 0 CHECK(scan_cursor>=0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.qr_channels(tenant_id,id),
        CHECK(NOT enabled OR (channel_id IS NOT NULL AND approved_at IS NOT NULL AND approved_by IS NOT NULL
          AND approved_auth_version IS NOT NULL AND reviewed_preferences_version IS NOT NULL))
      );
      CREATE TABLE public.automation_contact_state (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        contact_e164 varchar(16) NOT NULL CHECK(contact_e164 ~ '^\\+55[1-9][0-9]{9,10}$'),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
        last_interaction_at timestamptz NOT NULL DEFAULT now(),
        human_paused boolean NOT NULL DEFAULT false,
        PRIMARY KEY(tenant_id,contact_e164)
      );
      CREATE TABLE public.automation_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        contact_e164 varchar(16) NOT NULL,
        event varchar(32) NOT NULL,
        event_key varchar(240) NOT NULL,
        campaign_id uuid,
        campaign_version integer,
        customer_id uuid NOT NULL,
        customer_version integer NOT NULL CHECK(customer_version>0),
        channel_id uuid NOT NULL,
        appointment_id uuid,
        appointment_version timestamptz,
        coupon_id uuid,
        coupon_version integer,
        trigger_at timestamptz,
        settings_version integer NOT NULL,
        preferences_version integer NOT NULL,
        consent_version timestamptz NOT NULL,
        interaction_revision bigint NOT NULL DEFAULT 0,
        body varchar(1200) NOT NULL CHECK(length(body)>0),
        due_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'pending',
        claim_token uuid,
        external_delivery_id varchar(200),
        delivery_status varchar(16) NOT NULL DEFAULT 'unknown' CHECK(delivery_status IN ('unknown','delivered','read')),
        last_reconciled_at timestamptz,
        last_error varchar(64),
        attempted_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,event_key,contact_e164),
        FOREIGN KEY(tenant_id,contact_e164) REFERENCES public.contact_consents(tenant_id,contact_e164),
        FOREIGN KEY(tenant_id,campaign_id) REFERENCES public.campaigns(tenant_id,id),
        FOREIGN KEY(tenant_id,customer_id) REFERENCES public.customers(tenant_id,id),
        FOREIGN KEY(tenant_id,channel_id) REFERENCES public.qr_channels(tenant_id,id),
        FOREIGN KEY(tenant_id,appointment_id) REFERENCES public.appointments(tenant_id,id),
        FOREIGN KEY(tenant_id,coupon_id) REFERENCES public.loyalty_coupons(tenant_id,id),
        CHECK(event IN ('booking_confirmed','reminder_24h','reminder_2h','reminder_1h','rescheduled','inactive_60d','cancelled_7d','birthday_7d','birthday_2d','loyalty_reward','after_service','campaign')),
        CHECK(status IN ('pending','sending','sent','cancelled','uncertain','failed')),
        CHECK(expires_at>due_at),
        CHECK((campaign_id IS NULL)=(campaign_version IS NULL)),
        CHECK((status NOT IN ('sending','sent','uncertain')) OR (claim_token IS NOT NULL AND attempted_at IS NOT NULL))
      );
      CREATE INDEX ix_automation_jobs_due ON public.automation_jobs(tenant_id,status,due_at,id);
      CREATE INDEX ix_automation_jobs_contact ON public.automation_jobs(tenant_id,contact_e164,status);
    """)
    setting = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    for table in ("automation_execution", "automation_contact_state", "automation_jobs"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR ALL TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON public.{table} TO app_runtime")
    # The scheduler discovers eligible tenant IDs without reading recipients/bodies.
    op.execute("CREATE POLICY automation_execution_support ON public.automation_execution FOR SELECT TO app_support USING(true)")
    op.execute("GRANT SELECT ON public.automation_execution TO app_support")
    op.execute("""CREATE FUNCTION public.automation_owner_active(p_tenant uuid,p_account uuid,p_auth integer)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT p_tenant IS NOT DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid
          AND EXISTS(SELECT 1 FROM public.tenant_memberships m JOIN public.panel_accounts p ON p.id=m.account_id
            WHERE m.tenant_id=p_tenant AND m.account_id=p_account AND m.active AND m.role='owner' AND p.auth_version=p_auth)
      $$;
      REVOKE ALL ON FUNCTION public.automation_owner_active(uuid,uuid,integer) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.automation_owner_active(uuid,uuid,integer) TO app_runtime;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.automation_jobs) OR
         EXISTS(SELECT 1 FROM public.automation_contact_state) OR
         EXISTS(SELECT 1 FROM public.automation_execution) OR
         EXISTS(SELECT 1 FROM public.contact_consents WHERE operational_opt_in) THEN
        RAISE EXCEPTION 'Preserve automation history; use a forward migration';
      END IF;
    END $$""")
    op.execute("DROP FUNCTION public.automation_owner_active(uuid,uuid,integer)")
    for table in ("automation_jobs", "automation_contact_state", "automation_execution"):
        op.drop_table(table)
    op.drop_column("contact_consents", "operational_opt_in")
