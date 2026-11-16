"""0059: Prepaid, manually renewable 30-day Pix grants. No recurring PSP writes.

Separate one-time payments and access rights from existing Cakto/Asaas
subscriptions. All tables are RLS/force-RLS with support-only writes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision="0059_prepaid_pix"
down_revision="0058_professional_scope"
branch_labels=None
depends_on=None


def _secure(name):
    op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON public.{name} FROM PUBLIC,app_runtime,app_support")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON public.{name} TO app_support")
    op.execute(f"CREATE POLICY {name}_support ON public.{name} FOR ALL TO app_support USING(true) WITH CHECK(true)")
    op.execute(f"CREATE POLICY {name}_migrator ON public.{name} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.create_table("orion_prepaid_pix_orders",
        sa.Column("id",UUID(as_uuid=True),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("account_id",UUID(as_uuid=True),sa.ForeignKey("panel_accounts.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("plan_code",sa.String(20),nullable=False),
        sa.Column("offer_id",sa.String(80),nullable=False),
        sa.Column("expected_cents",sa.Integer,nullable=False),
        sa.Column("callback_hash",sa.String(64),nullable=False,unique=True),
        sa.Column("external_order_id",sa.String(255),unique=True,nullable=True),
        sa.Column("state",sa.String(18),nullable=False,server_default="reserved"),
        sa.Column("previous_paid_until",sa.DateTime(timezone=True),nullable=True),
        sa.Column("paid_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.CheckConstraint("plan_code IN ('essencial','profissional','elite')",name="ck_orion_prepaid_plan"),
        sa.CheckConstraint("expected_cents IN (2990,8990,13990)",name="ck_orion_prepaid_amount"),
        sa.CheckConstraint("state IN ('reserved','created','paid','refunded','needs_review','declined','expired')",name="ck_orion_prepaid_state"),
        sa.CheckConstraint("callback_hash ~ '^[a-f0-9]{64}$'",name="ck_orion_prepaid_callback_hash"),
    )
    _secure("orion_prepaid_pix_orders")
    op.execute("""CREATE UNIQUE INDEX uq_orion_prepaid_open_tenant
                  ON public.orion_prepaid_pix_orders(tenant_id)
                  WHERE state IN ('reserved','created','needs_review')""")
    op.create_index("ix_orion_prepaid_orders_tenant",
                    "orion_prepaid_pix_orders",["tenant_id","created_at"])
    op.create_index("ix_orion_prepaid_orders_order",
                    "orion_prepaid_pix_orders",["external_order_id"],
                    postgresql_where=sa.text("external_order_id IS NOT NULL"))

    op.create_table("orion_prepaid_pix_access",
        sa.Column("tenant_id",UUID(as_uuid=True),sa.ForeignKey("tenants.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("plan_code",sa.String(20),nullable=False),
        sa.Column("paid_until",sa.DateTime(timezone=True),nullable=False),
        sa.Column("last_verified_payment_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("last_order_id",sa.String(255),nullable=False,unique=True),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.text("now()")),
        sa.CheckConstraint("plan_code IN ('essencial','profissional','elite')",name="ck_orion_prepaid_access_plan"),
    )
    _secure("orion_prepaid_pix_access")
    op.execute("""CREATE TRIGGER prepaid_plan_serial BEFORE INSERT OR UPDATE
                  ON public.orion_prepaid_pix_access FOR EACH ROW
                  EXECUTE FUNCTION public.orion_lock_plan_change()""")

    op.execute("""
      CREATE FUNCTION public.orion_prepaid_plan_snapshot(target uuid)
      RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog AS $pix$
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
          SELECT pg_catalog.jsonb_build_object(
            'plan_code',a.plan_code,'paid_until',a.paid_until,
            'last_verified_payment_at',a.last_verified_payment_at)
          FROM public.orion_prepaid_pix_access a
          WHERE a.tenant_id=target
        );
      END $pix$;
      REVOKE ALL ON FUNCTION public.orion_prepaid_plan_snapshot(uuid)
        FROM PUBLIC,app_runtime,app_support;
      GRANT EXECUTE ON FUNCTION public.orion_prepaid_plan_snapshot(uuid)
        TO app_runtime,app_support;
    """)


def downgrade():
    raise RuntimeError("Paid Pix orders are financially material; do not delete via downgrade")
