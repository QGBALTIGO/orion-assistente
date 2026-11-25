"""Required manual subscription terms and scheduled plan reductions."""
from alembic import op

revision = "0096_manual_subscription_terms"
down_revision = "0095_orion_welcome_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.tenant_plan_controls
            ADD COLUMN manual_next_code varchar(20),
            ADD COLUMN manual_next_starts_at timestamptz,
            ADD COLUMN manual_next_ends_at timestamptz,
            ADD CONSTRAINT ck_manual_next_plan CHECK (
                (manual_next_code IS NULL AND manual_next_starts_at IS NULL AND manual_next_ends_at IS NULL)
                OR (manual_next_code IS NOT NULL AND manual_next_code IN ('essencial','profissional','elite')
                    AND manual_plan_code IS NOT NULL AND manual_expires_at IS NOT NULL
                    AND manual_next_starts_at IS NOT NULL AND manual_next_starts_at=manual_expires_at
                    AND manual_next_ends_at IS NOT NULL AND manual_next_ends_at>manual_next_starts_at));
        ALTER TABLE public.tenant_plan_audit
            ADD COLUMN duration_months smallint CHECK (duration_months IN (1,3,6,12)),
            ADD COLUMN term_starts_at timestamptz;
        GRANT INSERT(manual_next_code,manual_next_starts_at,manual_next_ends_at),
              UPDATE(manual_next_code,manual_next_starts_at,manual_next_ends_at)
            ON public.tenant_plan_controls TO app_support;

        CREATE FUNCTION public.orion_manual_schedule(target uuid) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $term$
        BEGIN
            IF session_user='app_runtime' THEN
                IF target IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
                    RAISE EXCEPTION 'tenant_plan_scope_required' USING ERRCODE='42501';
                END IF;
            ELSIF session_user<>'app_support' AND NOT pg_catalog.pg_has_role(session_user,'app_migrator','MEMBER') THEN
                RAISE EXCEPTION 'tenant_plan_role_required' USING ERRCODE='42501';
            END IF;
            RETURN (SELECT jsonb_build_object('plan_code',manual_next_code,'started_at',manual_next_starts_at,
                    'expires_at',manual_next_ends_at) FROM public.tenant_plan_controls
                    WHERE tenant_id=target AND manual_next_code IS NOT NULL);
        END
        $term$;
        REVOKE ALL ON FUNCTION public.orion_manual_schedule(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.orion_manual_schedule(uuid) TO app_runtime,app_support;

        CREATE FUNCTION public.orion_manual_term_required() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $term$
        BEGIN
            IF current_user='app_support' AND NEW.manual_plan_code IS NOT NULL
                AND NEW.manual_expires_at IS NULL AND (TG_OP='INSERT'
                 OR OLD.manual_plan_code IS DISTINCT FROM NEW.manual_plan_code
                 OR OLD.manual_started_at IS DISTINCT FROM NEW.manual_started_at
                 OR OLD.manual_expires_at IS DISTINCT FROM NEW.manual_expires_at) THEN
                RAISE EXCEPTION 'manual_subscription_end_required' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END
        $term$;
        CREATE TRIGGER manual_term_required BEFORE INSERT OR UPDATE ON public.tenant_plan_controls
            FOR EACH ROW EXECUTE FUNCTION public.orion_manual_term_required();
    """)


def downgrade():
    raise RuntimeError("Manual terms and scheduled plan changes must survive application rollback")
