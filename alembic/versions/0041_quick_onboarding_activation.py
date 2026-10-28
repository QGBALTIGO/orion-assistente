"""Activate a quick-profile tenant without inventing an operational catalogue.

Actual bookings, orders and site publication stay guarded by
onboarding_service.require_operational_ready and the respective feature gates.
Legacy catalogue activation keeps the original stricter PostgreSQL function.
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_quick_onboarding_activation"
down_revision = "0040_professional_phone"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    op.execute("""
      CREATE FUNCTION public.onboarding_activate_quick_tenant(p_tenant uuid)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE
        c jsonb;
        meta public.business_onboarding%ROWTYPE;
        current_status text;
      BEGIN
        IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
          RAISE EXCEPTION 'Invalid onboarding scope' USING ERRCODE='42501';
        END IF;

        SELECT * INTO meta FROM public.business_onboarding WHERE tenant_id=p_tenant FOR UPDATE;
        IF NOT FOUND OR NOT meta.is_new OR meta.completed_at IS NULL THEN
          RAISE EXCEPTION 'Setup is not complete' USING ERRCODE='23514';
        END IF;
        c:=meta.configuration;
        IF c->>'activation_requested' IS DISTINCT FROM 'true'
           OR c->>'reviewed' IS DISTINCT FROM 'true'
           OR length(coalesce(c->>'niche',''))=0
           OR (lower(c->>'niche') IN ('outro','other')
               AND length(coalesce(c->>'other_niche',''))=0)
           OR coalesce(c->>'activity','') NOT IN ('services','products','both')
           OR coalesce(c->>'modality','') NOT IN ('onsite','online','client_address','hybrid')
           OR (c->>'modality' IN ('onsite','hybrid')
               AND (jsonb_typeof(c->'private_address') IS DISTINCT FROM 'object'
                    OR length(coalesce(c->'private_address'->>'street',''))=0
                    OR length(coalesce(c->'private_address'->>'city',''))=0
                    OR length(coalesce(c->'private_address'->>'neighborhood',''))=0
                    OR coalesce(c->'private_address'->>'state','') !~ '^[A-Z]{2}$'
                    OR (length(coalesce(c->'private_address'->>'number',''))>0)
                       = (coalesce(c->'private_address'->>'no_number','false')='true')))
           OR jsonb_typeof(c->'business_metrics') IS DISTINCT FROM 'object'
           OR coalesce(c->'business_metrics'->>'operating_age','') NOT IN (
             'not_started','under_6_months','6_to_12_months','1_to_3_years',
             '3_to_5_years','over_5_years')
           OR coalesce(c->'business_metrics'->>'team_size','') NOT IN (
             'none','1_2','3_5','6_10','11_20','over_20')
           OR coalesce(c->'business_metrics'->>'monthly_revenue','') NOT IN (
             'none','under_5k','5k_10k','10k_25k','25k_50k','50k_100k',
             'over_100k','prefer_not_to_say')
           OR coalesce(c->'business_metrics'->>'monthly_volume','') NOT IN (
             'none','up_to_50','51_100','101_300','301_600','601_1000','over_1000')
           OR jsonb_typeof(c->'business_priorities') IS DISTINCT FROM 'object'
           OR coalesce(c->'business_priorities'->>'main_challenge','') NOT IN (
             'scheduling','cancellations','whatsapp','finance',
             'new_customers','loyalty','team')
           OR coalesce(c->'business_priorities'->>'desired_outcome','') NOT IN (
             'save_time','grow_revenue','professional_service',
             'automate_whatsapp','better_control','more_bookings')
           OR coalesce(c->'business_priorities'->>'respondent_role','') NOT IN (
             'owner','partner','manager','other') THEN
          RAISE EXCEPTION 'Incomplete quick setup facts' USING ERRCODE='23514';
        END IF;

        SELECT status INTO current_status
          FROM public.tenants WHERE id=p_tenant FOR UPDATE;
        IF current_status NOT IN ('paused','active') THEN
          RAISE EXCEPTION 'Invalid business state' USING ERRCODE='23514';
        END IF;
        -- Being active means the business may access its private dashboard.
        -- It does NOT authorize publication, booking, product orders or AI.
        -- Those still require real configured operational facts.
        UPDATE public.tenants SET status='active' WHERE id=p_tenant;
      END $$;
      REVOKE ALL ON FUNCTION public.onboarding_activate_quick_tenant(uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION public.onboarding_activate_quick_tenant(uuid) TO app_runtime;
    """)


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    if op.get_bind().execute(sa.text("""
        SELECT EXISTS (
          SELECT 1 FROM public.business_onboarding
          WHERE is_new AND completed_at IS NOT NULL
            AND configuration ? 'business_priorities'
        )
    """)).scalar_one():
        raise RuntimeError("Quick-profile businesses exist; refusing unsafe rollback")
    op.execute("DROP FUNCTION public.onboarding_activate_quick_tenant(uuid)")
