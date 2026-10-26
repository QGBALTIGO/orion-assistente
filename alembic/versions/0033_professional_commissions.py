"""Commission modes, per-service rules and immutable per-item payout snapshots.

Revision ID: 0033_professional_commissions
Revises: 0032_variable_service_prices
"""
from alembic import op

revision = "0033_professional_commissions"
down_revision = "0032_variable_service_prices"
branch_labels = None
depends_on = None

TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade():
    op.execute("""
        ALTER TABLE public.professionals
          ADD COLUMN commission_mode varchar(12) NOT NULL DEFAULT 'percent',
          ADD COLUMN commission_config_version integer NOT NULL DEFAULT 1,
          ADD CONSTRAINT ck_professional_commission_mode
            CHECK (commission_mode IN ('percent','per_service') AND commission_config_version>=1);

        CREATE TABLE public.professional_commission_rules (
          tenant_id uuid NOT NULL REFERENCES public.tenants(id),
          professional_id uuid NOT NULL,
          service_id uuid NOT NULL,
          commission_kind varchar(8) NOT NULL,
          commission_percent numeric(5,2),
          commission_fixed_cents integer,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id,professional_id,service_id),
          FOREIGN KEY (tenant_id,professional_id)
            REFERENCES public.professionals(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY (tenant_id,service_id)
            REFERENCES public.services(tenant_id,id) ON DELETE CASCADE,
          CONSTRAINT ck_professional_commission_rule CHECK (
            (commission_kind='percent' AND commission_percent IS NOT NULL AND commission_percent BETWEEN 0 AND 100
              AND commission_fixed_cents IS NULL)
            OR (commission_kind='fixed' AND commission_percent IS NULL
              AND commission_fixed_cents IS NOT NULL AND commission_fixed_cents BETWEEN 0 AND 100000000)
          )
        );
        CREATE INDEX ix_professional_commission_service
          ON public.professional_commission_rules(tenant_id,service_id);
        ALTER TABLE public.professional_commission_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.professional_commission_rules FORCE ROW LEVEL SECURITY;
    """)
    op.execute(f"""CREATE POLICY professional_commission_rules_runtime
       ON public.professional_commission_rules FOR ALL TO app_runtime
       USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})""")
    op.execute("""CREATE POLICY professional_commission_rules_migrator
       ON public.professional_commission_rules FOR ALL TO app_migrator
       USING (true) WITH CHECK (true)""")
    op.execute("""REVOKE ALL ON public.professional_commission_rules
       FROM PUBLIC,app_support,app_runtime""")
    op.execute("""GRANT SELECT,INSERT,UPDATE,DELETE
       ON public.professional_commission_rules TO app_runtime""")
    # Previously a payout could never exceed gross revenue. A fixed per-service
    # payout may legitimately create a loss, so retain a monetary bound without
    # silently truncating that payable to the price paid by the customer.
    op.execute("""
        ALTER TABLE public.appointments
          ADD COLUMN commission_mode_snapshot varchar(12);
        ALTER TABLE public.appointments DROP CONSTRAINT ck_appointment_commission;
        ALTER TABLE public.appointments ADD CONSTRAINT ck_appointment_commission CHECK (
          (commission_percent_snapshot IS NULL AND commission_cents IS NULL
            AND commission_mode_snapshot IS NULL)
          OR (commission_cents IS NOT NULL AND commission_cents BETWEEN 0 AND 1000000000 AND
            ((commission_mode_snapshot='per_service' AND commission_percent_snapshot IS NULL)
            OR (COALESCE(commission_mode_snapshot,'percent')='percent'
              AND commission_percent_snapshot IS NOT NULL
              AND commission_percent_snapshot BETWEEN 0 AND 100)))
        );

        ALTER TABLE public.appointment_items
          ADD COLUMN commission_kind_snapshot varchar(8),
          ADD COLUMN commission_source_snapshot varchar(12),
          ADD COLUMN commission_percent_snapshot numeric(5,2),
          ADD COLUMN commission_fixed_cents_snapshot integer,
          ADD COLUMN commission_base_cents bigint,
          ADD COLUMN commission_cents bigint,
          ADD CONSTRAINT ck_appointment_item_commission CHECK (
            (commission_kind_snapshot IS NULL AND commission_source_snapshot IS NULL
              AND commission_percent_snapshot IS NULL AND commission_fixed_cents_snapshot IS NULL
              AND commission_base_cents IS NULL AND commission_cents IS NULL)
            OR (commission_base_cents IS NOT NULL AND commission_base_cents>=0
              AND commission_cents IS NOT NULL AND commission_cents BETWEEN 0 AND 1000000000
              AND commission_source_snapshot IN ('rule','default')
              AND ((commission_kind_snapshot='percent'
                AND commission_percent_snapshot IS NOT NULL
                AND commission_percent_snapshot BETWEEN 0 AND 100
                AND commission_fixed_cents_snapshot IS NULL)
                OR (commission_kind_snapshot='fixed'
                  AND commission_percent_snapshot IS NULL
                  AND commission_fixed_cents_snapshot IS NOT NULL
                  AND commission_fixed_cents_snapshot BETWEEN 0 AND 100000000)))
          );
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.professionals WHERE commission_mode='per_service')
        OR EXISTS (SELECT 1 FROM public.professional_commission_rules)
        OR EXISTS (SELECT 1 FROM public.appointments WHERE commission_mode_snapshot='per_service')
      THEN RAISE EXCEPTION 'Cannot drop per-service commission history'; END IF;
    END $$""")
    op.execute("""ALTER TABLE public.appointment_items
      DROP CONSTRAINT ck_appointment_item_commission,
      DROP COLUMN commission_kind_snapshot,
      DROP COLUMN commission_source_snapshot,
      DROP COLUMN commission_percent_snapshot,
      DROP COLUMN commission_fixed_cents_snapshot,
      DROP COLUMN commission_base_cents,
      DROP COLUMN commission_cents""")
    op.execute("""ALTER TABLE public.appointments
      DROP CONSTRAINT ck_appointment_commission,
      DROP COLUMN commission_mode_snapshot""")
    op.execute("""ALTER TABLE public.appointments ADD CONSTRAINT ck_appointment_commission CHECK (
      (commission_percent_snapshot IS NULL AND commission_cents IS NULL) OR
      (commission_percent_snapshot IS NOT NULL AND commission_cents IS NOT NULL AND
       commission_percent_snapshot BETWEEN 0 AND 100 AND commission_cents BETWEEN 0 AND total_cents))""")
    op.execute("DROP TABLE public.professional_commission_rules")
    op.execute("""ALTER TABLE public.professionals
      DROP CONSTRAINT ck_professional_commission_mode,
      DROP COLUMN commission_mode,
      DROP COLUMN commission_config_version""")
