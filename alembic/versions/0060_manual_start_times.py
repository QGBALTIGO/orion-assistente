"""Optional per-professional/weekday appointment start allowlists.

Existing companies remain automatic: no policy rows or appointments are changed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0060_manual_start_times"
down_revision = "0059_prepaid_pix"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
        CREATE FUNCTION public.orion_valid_manual_start_times(values_to_check time[])
        RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path=pg_catalog AS $$
          SELECT values_to_check IS NOT NULL
            AND COALESCE(array_ndims(values_to_check),1)=1
            AND cardinality(values_to_check)<=1440
            AND NOT EXISTS(SELECT 1 FROM unnest(values_to_check) value
                WHERE value IS NULL OR value='24:00'::time OR EXTRACT(SECOND FROM value)<>0)
            AND cardinality(values_to_check)=(SELECT count(DISTINCT value) FROM unnest(values_to_check) value)
        $$;
        REVOKE ALL ON FUNCTION public.orion_valid_manual_start_times(time[]) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.orion_valid_manual_start_times(time[]) TO app_runtime;
    """)
    op.create_table("professional_start_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("professional_id", UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.SmallInteger, nullable=False),
        sa.Column("mode", sa.String(12), nullable=False, server_default="automatic"),
        sa.Column("start_times", ARRAY(sa.Time(timezone=False)), nullable=False,
                  server_default=sa.text("'{}'::time[]")),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "professional_id"],
            ["professionals.tenant_id", "professionals.id"],
            name="fk_start_policy_professional", ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "professional_id", "weekday", name="uq_start_policy_weekday"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_start_policy_weekday"),
        sa.CheckConstraint("mode IN ('automatic','manual')", name="ck_start_policy_mode"),
        sa.CheckConstraint("version>=1", name="ck_start_policy_version"),
        sa.CheckConstraint("public.orion_valid_manual_start_times(start_times)", name="ck_start_policy_times"),
        sa.CheckConstraint("mode<>'manual' OR cardinality(start_times)>0", name="ck_start_policy_manual_nonempty"),
    )
    op.execute("ALTER TABLE public.professional_start_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.professional_start_policies FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.professional_start_policies FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.professional_start_policies TO app_runtime")
    scope = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    op.execute(f"CREATE POLICY professional_start_policies_runtime ON public.professional_start_policies "
               f"FOR ALL TO app_runtime USING({scope}) WITH CHECK({scope})")
    op.execute("CREATE POLICY professional_start_policies_migrator ON public.professional_start_policies "
               "FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
    op.execute(ACTIVATE_TENANT_SQL)


def downgrade():
    raise RuntimeError("Preserve configured start policies; use an application rollback")


# Keep the existing activation contract, adding allowlist-aware candidates only
# for explicitly manual days. Legacy automatic readiness remains unchanged.
ACTIVATE_TENANT_SQL = """
      CREATE OR REPLACE FUNCTION public.onboarding_activate_tenant(p_tenant uuid)
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
          LEFT JOIN public.professional_start_policies policy
            ON policy.tenant_id=w.tenant_id AND policy.professional_id=w.professional_id
              AND policy.weekday=w.weekday
          CROSS JOIN LATERAL (
            SELECT w.start_time AS a, false AS manual
              WHERE COALESCE(policy.mode,'automatic')='automatic'
            UNION SELECT b.end_time, false FROM public.working_breaks b
              WHERE b.tenant_id=w.tenant_id AND b.working_hour_id=w.id
                AND COALESCE(policy.mode,'automatic')='automatic'
            UNION SELECT candidate, true FROM unnest(policy.start_times) candidate
              WHERE policy.mode='manual'
          ) starts
          WHERE s.tenant_id=p_tenant AND s.active AND p.active AND p.accepts_appointments
            AND starts.a>=w.start_time AND starts.a<w.end_time
            AND EXTRACT(EPOCH FROM (w.end_time-starts.a))/60>=
              s.duration_min+s.cleanup_min+CASE WHEN starts.manual THEN 0 ELSE s.prep_min END
            AND NOT EXISTS(SELECT 1 FROM public.working_breaks b WHERE b.tenant_id=w.tenant_id
              AND b.working_hour_id=w.id AND b.start_time<starts.a+make_interval(mins=>s.duration_min+s.cleanup_min+CASE WHEN starts.manual THEN 0 ELSE s.prep_min END)
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
"""
