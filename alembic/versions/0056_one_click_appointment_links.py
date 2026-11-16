"""Per-recipient one-click short appointment proofs.

The public alias remains non-authorizing. A support-only SECURITY DEFINER
lookup returns booking identity for server-side HMAC verification, never to
the browser. No customer or booking data is rewritten.
"""
from alembic import op

revision = "0056_one_click_appointment_links"
down_revision = "0055_combo_duration_override"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(r"""
      CREATE FUNCTION public.orion_resolve_direct_appointment(requested_code text)
      RETURNS TABLE(tenant_id uuid,appointment_id uuid,customer_id uuid,
                    professional_id uuid,customer_phone text,
                    professional_phone text,customer_version bigint,
                    professional_active boolean)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
      BEGIN
        IF session_user <> 'app_support'
          OR requested_code !~ '^AG-[A-HJ-NP-Z2-9]{6}(-[A-F0-9]{8})?$' THEN
          RETURN;
        END IF;
        RETURN QUERY
          SELECT a.tenant_id,a.id,a.customer_id,a.professional_id,
                 c.whatsapp_e164::text,p.phone_e164::text,c.version::bigint,p.active
          FROM public.appointment_short_codes short
          JOIN public.appointments a ON a.tenant_id=short.tenant_id
            AND a.id=short.appointment_id
          JOIN public.tenants t ON t.id=a.tenant_id AND t.status='active'
          JOIN public.customers c ON c.tenant_id=a.tenant_id
            AND c.id=a.customer_id AND c.active AND c.deleted_at IS NULL
          JOIN public.professionals p ON p.tenant_id=a.tenant_id
            AND p.id=a.professional_id
          WHERE short.code=requested_code
            AND a.status<>'hold' AND NOT a.customer_access_blocked
            AND a.end_at > clock_timestamp() - interval '90 days';
      END $fn$;
      REVOKE ALL ON FUNCTION public.orion_resolve_direct_appointment(text)
        FROM PUBLIC,app_runtime,app_migrator;
      GRANT EXECUTE ON FUNCTION public.orion_resolve_direct_appointment(text)
        TO app_support;
    """)


def downgrade():
    raise RuntimeError("Keep the direct-link resolver for previously issued links")
