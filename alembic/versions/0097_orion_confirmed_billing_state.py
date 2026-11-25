"""Verified billing dates and holds independent of the access grant."""
from alembic import op

revision = "0097_confirmed_billing_state"
down_revision = "0096_manual_subscription_terms"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        ALTER TABLE public.orion_plan_trial_requests
            DROP CONSTRAINT ck_orion_trial_status,
            DROP CONSTRAINT ck_orion_trial_window,
            ADD CONSTRAINT ck_orion_trial_status CHECK (status IN ('pending','active','expired','rejected','superseded')),
            ADD CONSTRAINT ck_orion_trial_window CHECK (
                (status IN ('active','superseded'))=(started_at IS NOT NULL AND ends_at IS NOT NULL));

        CREATE TABLE public.orion_confirmed_billing_state (
            tenant_id uuid PRIMARY KEY REFERENCES public.tenants(id),
            order_id varchar(255) NOT NULL,
            payment_channel text NOT NULL CHECK (payment_channel IN ('cakto','pix_prepaid')),
            paid_at timestamptz NOT NULL,
            period_starts_at timestamptz,
            period_ends_at timestamptz,
            next_payment_at timestamptz,
            amount_cents int NOT NULL CHECK (amount_cents>0),
            hold text CHECK (hold IN ('refund_requested','refunded','chargeback')),
            checked_at timestamptz NOT NULL DEFAULT now(),
            CHECK (period_ends_at IS NULL OR
                   (period_starts_at IS NOT NULL AND period_ends_at>period_starts_at))
        );
        CREATE INDEX orion_confirmed_billing_holds
            ON public.orion_confirmed_billing_state (checked_at) WHERE hold IS NOT NULL;
        ALTER TABLE public.orion_confirmed_billing_state ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.orion_confirmed_billing_state FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.orion_confirmed_billing_state FROM PUBLIC, app_runtime;
        GRANT SELECT, INSERT, UPDATE ON public.orion_confirmed_billing_state TO app_support;
        CREATE POLICY orion_confirmed_billing_support ON public.orion_confirmed_billing_state
            FOR ALL TO app_support USING (true) WITH CHECK (true);
        CREATE POLICY orion_confirmed_billing_migrator ON public.orion_confirmed_billing_state
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);

        CREATE FUNCTION public.orion_confirmed_billing_snapshot(target uuid) RETURNS jsonb
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
                        SELECT 1 FROM public.orion_prepaid_pix_access p WHERE p.tenant_id=target
                          AND p.last_order_id=b.order_id)))
            );
        END
        $state$;
        REVOKE ALL ON FUNCTION public.orion_confirmed_billing_snapshot(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.orion_confirmed_billing_snapshot(uuid) TO app_runtime, app_support;
    """)


def downgrade():
    raise RuntimeError("Verified financial periods and holds must survive application rollback")
