"""Read-only, cross-tenant lookup for the platform WhatsApp confirmation lane.

No appointment facts, customer data or tokens are returned to the support role;
only a unique (tenant, appointment) pair can be used by the runtime under RLS.
"""
from alembic import op

revision="0051_central_appointment_confirmation"
down_revision="0050_plan_permissions"
branch_labels=None
depends_on=None


def upgrade():
    op.execute(r"""
      CREATE FUNCTION public.orion_resolve_central_appointment(
          requested_protocol text, verified_phone text)
      RETURNS TABLE(tenant_id uuid,appointment_id uuid)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $body$
      BEGIN
        -- No public or ordinary runtime caller may enumerate other tenants.
        IF session_user <> 'app_support'
           OR requested_protocol IS NULL
           OR requested_protocol !~ '^AG-[A-HJ-NP-Z2-9]{6}$'
           OR verified_phone IS NULL
           OR verified_phone !~ '^\\+55[1-9][0-9]{9,10}$' THEN
          RETURN;
        END IF;

        -- Protocol is unique only *inside* a tenant. A collision involving
        -- one verified phone must fail closed, not choose an arbitrary company.
        RETURN QUERY
        WITH matches AS MATERIALIZED (
          SELECT a.tenant_id,a.id AS appointment_id
          FROM public.appointments a
          JOIN public.customers c ON c.tenant_id=a.tenant_id
                                 AND c.id=a.customer_id
          JOIN public.tenants t ON t.id=a.tenant_id
          WHERE a.protocol=requested_protocol
            AND c.whatsapp_e164=verified_phone
            AND c.active AND c.deleted_at IS NULL
            AND t.status='active'
            AND a.status <> 'hold'
          LIMIT 2
        )
        SELECT found.tenant_id,found.appointment_id
        FROM matches found
        WHERE (SELECT count(*) FROM matches)=1;
      END $body$;
      REVOKE ALL ON FUNCTION public.orion_resolve_central_appointment(text,text)
        FROM PUBLIC,app_runtime;
      GRANT EXECUTE ON FUNCTION public.orion_resolve_central_appointment(text,text)
        TO app_support;
    """)


def downgrade():
    raise RuntimeError("Do not revoke the central booking resolver without a reviewed forward release")
