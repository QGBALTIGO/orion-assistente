"""Add explicit setup progress without changing any legacy business or channel."""
from alembic import op

revision = "0020_business_onboarding"
down_revision = "0019_appointment_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.business_onboarding (
        tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id) ON DELETE RESTRICT,
        created_by uuid REFERENCES public.panel_accounts(id) ON DELETE RESTRICT,
        is_new boolean NOT NULL DEFAULT false,
        configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
        managed_ids jsonb NOT NULL DEFAULT '{}'::jsonb,
        current_step varchar(24) NOT NULL DEFAULT 'business',
        version integer NOT NULL DEFAULT 0 CHECK(version>=0),
        completed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(jsonb_typeof(configuration)='object' AND octet_length(configuration::text)<=20000),
        CHECK(jsonb_typeof(managed_ids)='object' AND octet_length(managed_ids::text)<=50000)
      );
      CREATE TABLE public.business_onboarding_operations (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,
        idempotency_key varchar(120) NOT NULL,
        actor_account_id uuid REFERENCES public.panel_accounts(id) ON DELETE RESTRICT,
        fingerprint char(64) NOT NULL,
        step varchar(24) NOT NULL,
        result jsonb NOT NULL DEFAULT '{}'::jsonb,
        version integer NOT NULL CHECK(version>=0),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,idempotency_key),
        CHECK(jsonb_typeof(result)='object' AND octet_length(result::text)<=8192)
      );
      CREATE TABLE public.business_onboarding_creations (
        account_id uuid NOT NULL REFERENCES public.panel_accounts(id) ON DELETE RESTRICT,
        idempotency_key varchar(120) NOT NULL,
        fingerprint char(64) NOT NULL,
        tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(account_id,idempotency_key)
      );
    """)
    scope = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    for table in ("business_onboarding", "business_onboarding_operations", "business_onboarding_creations"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        for role in ("app_migrator", "app_support"):
            op.execute(f"CREATE POLICY {table}_{role} ON public.{table} FOR ALL TO {role} USING(true) WITH CHECK(true)")
        if table != "business_onboarding_creations":
            op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR ALL TO app_runtime USING({scope}) WITH CHECK({scope})")
        privileges = "SELECT,INSERT,UPDATE" if table == "business_onboarding" else "SELECT,INSERT"
        op.execute(f"GRANT {privileges} ON public.{table} TO app_support")
        if table != "business_onboarding_creations":
            op.execute(f"GRANT {privileges} ON public.{table} TO app_runtime")
    op.execute("""
      CREATE FUNCTION public.onboarding_update_identity(p_tenant uuid,p_name text,p_timezone text)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      BEGIN
        IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid
           OR NOT EXISTS(SELECT 1 FROM public.business_onboarding WHERE tenant_id=p_tenant)
           OR length(btrim(p_name)) NOT BETWEEN 1 AND 160
           OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=p_timezone) THEN
          RAISE EXCEPTION 'Invalid onboarding identity scope' USING ERRCODE='42501';
        END IF;
        UPDATE public.tenants SET name=p_name,timezone=p_timezone WHERE id=p_tenant;
        IF NOT FOUND THEN RAISE EXCEPTION 'Business missing' USING ERRCODE='42501'; END IF;
      END $$;
      REVOKE ALL ON FUNCTION public.onboarding_update_identity(uuid,text,text) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.onboarding_update_identity(uuid,text,text) TO app_runtime;

      CREATE FUNCTION public.onboarding_activate_tenant(p_tenant uuid)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE c jsonb; m public.business_onboarding%ROWTYPE; t_status text; ready_services boolean; ready_products boolean;
      BEGIN
        IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
          RAISE EXCEPTION 'Invalid onboarding scope' USING ERRCODE='42501';
        END IF;
        SELECT * INTO m FROM public.business_onboarding WHERE tenant_id=p_tenant FOR UPDATE;
        IF NOT FOUND OR NOT m.is_new OR m.completed_at IS NULL THEN
          RAISE EXCEPTION 'Setup is not complete' USING ERRCODE='23514';
        END IF;
        c:=m.configuration;
        IF c->>'activation_requested' IS DISTINCT FROM 'true'
          OR c->>'reviewed' IS DISTINCT FROM 'true'
          OR length(coalesce(c->>'niche',''))=0
          OR (lower(c->>'niche') IN ('outro','other') AND length(coalesce(c->>'other_niche',''))=0)
          OR coalesce(c->>'activity','') NOT IN ('services','products','both')
          OR coalesce(c->>'modality','') NOT IN ('onsite','online','client_address','hybrid')
          OR (c->>'modality' IN ('onsite','hybrid') AND (jsonb_typeof(c->'private_address') IS DISTINCT FROM 'object'
             OR length(coalesce(c->'private_address'->>'street',''))=0
             OR length(coalesce(c->'private_address'->>'city',''))=0
             OR length(coalesce(c->'private_address'->>'neighborhood',''))=0
             OR coalesce(c->'private_address'->>'state','') !~ '^[A-Z]{2}$'
             OR ((length(coalesce(c->'private_address'->>'number',''))>0)
                = (coalesce(c->'private_address'->>'no_number','false')='true')))) THEN
          RAISE EXCEPTION 'Incomplete setup facts' USING ERRCODE='23514';
        END IF;
        SELECT status INTO t_status FROM public.tenants WHERE id=p_tenant FOR UPDATE;
        IF t_status NOT IN ('paused','active') THEN
          RAISE EXCEPTION 'Invalid business state' USING ERRCODE='23514';
        END IF;
        WITH fits AS (
          SELECT s.id AS service_id,p.id AS professional_id FROM public.services s
          JOIN public.professional_services ps ON ps.tenant_id=s.tenant_id AND ps.service_id=s.id
          JOIN public.professionals p ON p.tenant_id=ps.tenant_id AND p.id=ps.professional_id
          JOIN public.working_hours w ON w.tenant_id=p.tenant_id AND w.professional_id=p.id
          CROSS JOIN LATERAL (
            SELECT w.start_time AS a UNION SELECT b.end_time FROM public.working_breaks b
              WHERE b.tenant_id=w.tenant_id AND b.working_hour_id=w.id
          ) starts
          WHERE s.tenant_id=p_tenant AND s.active AND p.active AND p.accepts_appointments
            AND EXTRACT(EPOCH FROM (w.end_time-starts.a))/60>=s.duration_min+s.prep_min+s.cleanup_min
            AND NOT EXISTS(SELECT 1 FROM public.working_breaks b WHERE b.tenant_id=w.tenant_id
              AND b.working_hour_id=w.id AND b.start_time<starts.a+make_interval(mins=>s.duration_min+s.prep_min+s.cleanup_min)
              AND b.end_time>starts.a)
        ) SELECT EXISTS(SELECT 1 FROM fits)
          AND NOT EXISTS(SELECT 1 FROM public.services s WHERE s.tenant_id=p_tenant AND s.active
             AND NOT EXISTS(SELECT 1 FROM fits WHERE fits.service_id=s.id))
          AND NOT EXISTS(SELECT 1 FROM public.professionals p WHERE p.tenant_id=p_tenant AND p.active AND p.accepts_appointments
             AND NOT EXISTS(SELECT 1 FROM fits WHERE fits.professional_id=p.id)) INTO ready_services;
        SELECT EXISTS(SELECT 1 FROM public.commercial_products p JOIN public.commercial_variants v
          ON v.tenant_id=p.tenant_id AND v.product_id=p.id WHERE p.tenant_id=p_tenant AND p.active AND v.active
          AND ((v.price_kind IN ('fixed','from') AND v.unit_price_cents IS NOT NULL)
            OR (v.price_kind IN ('on_request','custom') AND v.unit_price_cents IS NULL))) INTO ready_products;
        IF (c->>'activity' IN ('services','both') AND NOT ready_services)
           OR (c->>'activity' IN ('products','both') AND NOT ready_products) THEN
          RAISE EXCEPTION 'Operational catalog is not ready' USING ERRCODE='23514';
        END IF;
        UPDATE public.tenants SET status='active' WHERE id=p_tenant;
      END $$;
      REVOKE ALL ON FUNCTION public.onboarding_activate_tenant(uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.onboarding_activate_tenant(uuid) TO app_runtime;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.business_onboarding) THEN
      RAISE EXCEPTION 'Onboarding data must be preserved; use application rollback'; END IF; END $$""")
    op.execute("DROP FUNCTION public.onboarding_activate_tenant(uuid)")
    op.execute("DROP FUNCTION public.onboarding_update_identity(uuid,text,text)")
    for table in ("business_onboarding_creations", "business_onboarding_operations", "business_onboarding"):
        op.drop_table(table)
