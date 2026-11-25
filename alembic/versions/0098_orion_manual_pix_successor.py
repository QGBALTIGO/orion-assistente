"""Anchor an optional prepaid term to the end of its existing manual term."""
from alembic import op

revision = "0098_manual_pix_successor"
down_revision = "0097_confirmed_billing_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.orion_prepaid_pix_orders
            ADD COLUMN manual_anchor_code varchar(20),
            ADD COLUMN manual_anchor_started_at timestamptz,
            ADD COLUMN manual_anchor_expires_at timestamptz,
            ADD CONSTRAINT ck_manual_pix_anchor CHECK (
                (manual_anchor_code IS NULL AND manual_anchor_started_at IS NULL
                    AND manual_anchor_expires_at IS NULL)
                OR (manual_anchor_code IS NOT NULL
                    AND manual_anchor_code IN ('essencial','profissional','elite')
                    AND manual_anchor_started_at IS NOT NULL
                    AND manual_anchor_expires_at IS NOT NULL
                    AND manual_anchor_expires_at>manual_anchor_started_at));

        CREATE FUNCTION public.orion_preserve_manual_pix_term() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $guard$
        BEGIN
            IF (OLD.manual_plan_code,OLD.manual_started_at,OLD.manual_expires_at,
                OLD.manual_next_code,OLD.manual_next_starts_at,OLD.manual_next_ends_at)
                IS DISTINCT FROM
                (NEW.manual_plan_code,NEW.manual_started_at,NEW.manual_expires_at,
                 NEW.manual_next_code,NEW.manual_next_starts_at,NEW.manual_next_ends_at)
                AND EXISTS (
                    SELECT 1 FROM public.orion_prepaid_pix_orders o
                    WHERE o.tenant_id=NEW.tenant_id AND o.manual_anchor_expires_at IS NOT NULL
                    AND (o.state IN ('reserved','created','needs_review') OR
                         (o.state='paid' AND o.manual_anchor_expires_at>now()
                          AND EXISTS (SELECT 1 FROM public.orion_prepaid_pix_access a
                                      WHERE a.tenant_id=o.tenant_id AND a.last_order_id=o.external_order_id
                                        AND a.paid_until>now())))) THEN
                RAISE EXCEPTION 'manual_pix_term_in_progress' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END
        $guard$;
        REVOKE ALL ON FUNCTION public.orion_preserve_manual_pix_term() FROM PUBLIC;
        -- The existing tenant_plan_serial trigger acquires the same plan lock as the Pix writer.
        CREATE TRIGGER zz_preserve_manual_pix_term BEFORE UPDATE ON public.tenant_plan_controls
            FOR EACH ROW EXECUTE FUNCTION public.orion_preserve_manual_pix_term();

        CREATE OR REPLACE FUNCTION public.orion_prepaid_plan_snapshot(target uuid)
        RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $pix$
        BEGIN
            IF session_user='app_runtime' THEN
                IF target IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
                    RAISE EXCEPTION 'tenant_plan_scope_required' USING ERRCODE='42501';
                END IF;
            ELSIF session_user<>'app_support'
                  AND NOT pg_catalog.pg_has_role(session_user,'app_migrator','MEMBER') THEN
                RAISE EXCEPTION 'tenant_plan_role_required' USING ERRCODE='42501';
            END IF;
            RETURN (
                SELECT jsonb_build_object('plan_code',a.plan_code,'paid_until',a.paid_until,
                    'last_verified_payment_at',a.last_verified_payment_at)
                FROM public.orion_prepaid_pix_access a
                LEFT JOIN public.orion_prepaid_pix_orders o ON o.tenant_id=a.tenant_id
                    AND o.external_order_id=a.last_order_id
                WHERE a.tenant_id=target
                    AND (o.manual_anchor_expires_at IS NULL OR o.manual_anchor_expires_at<=now())
            );
        END
        $pix$;

        CREATE OR REPLACE FUNCTION public.orion_confirmed_billing_snapshot(target uuid) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $state$
        BEGIN
            IF session_user='app_runtime' THEN
                IF target IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
                    RAISE EXCEPTION 'tenant_plan_scope_required' USING ERRCODE='42501';
                END IF;
            ELSIF session_user<>'app_support' AND NOT pg_catalog.pg_has_role(session_user,'app_migrator','MEMBER') THEN
                RAISE EXCEPTION 'tenant_plan_role_required' USING ERRCODE='42501';
            END IF;
            RETURN (
                SELECT jsonb_build_object('channel',b.payment_channel,'hold',b.hold,
                       'paid_at',b.paid_at,'period_started_at',b.period_starts_at,
                       'period_ends_at',b.period_ends_at,'next_payment_at',b.next_payment_at)
                FROM public.orion_confirmed_billing_state b WHERE b.tenant_id=target AND (
                    (b.payment_channel='cakto' AND EXISTS (
                        SELECT 1 FROM public.orion_cakto_bindings c WHERE c.tenant_id=target
                            AND c.last_order_id=b.order_id))
                    OR (b.payment_channel='pix_prepaid' AND EXISTS (
                        SELECT 1 FROM public.orion_prepaid_pix_access p
                        LEFT JOIN public.orion_prepaid_pix_orders o ON o.tenant_id=p.tenant_id
                            AND o.external_order_id=p.last_order_id
                        WHERE p.tenant_id=target AND p.last_order_id=b.order_id
                            AND (o.manual_anchor_expires_at IS NULL OR o.manual_anchor_expires_at<=now()))))
            );
        END
        $state$;
    """)


def downgrade():
    raise RuntimeError("Preserve approved prepaid terms and their original effective dates")
