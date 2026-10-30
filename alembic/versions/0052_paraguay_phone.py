"""Accept an explicitly country-prefixed Paraguayan mobile alongside BR phones.

This is an additive extension to customer, consent and professional phone
constraints. Existing +55 records remain unchanged. QR/AI allowlist rules do
not change and no marketing audience is enabled by this migration.
"""
from alembic import op

revision = "0052_paraguay_phone"
down_revision = "0051_central_protocol"
branch_labels = None
depends_on = None

MOBILE = r"^(\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='30s'")
    # All constraints below already exist. Failing to find one aborts the
    # transaction instead of leaving part of the schema internationally open.
    op.execute("ALTER TABLE public.customers DROP CONSTRAINT ck_customer_phone")
    op.execute(f"ALTER TABLE public.customers ADD CONSTRAINT ck_customer_phone CHECK(whatsapp_e164 ~ '{MOBILE}')")
    op.execute("ALTER TABLE public.contact_consents DROP CONSTRAINT ck_consent_phone")
    op.execute(f"ALTER TABLE public.contact_consents ADD CONSTRAINT ck_consent_phone CHECK(contact_e164 ~ '{MOBILE}')")
    op.execute("ALTER TABLE public.professionals DROP CONSTRAINT ck_professional_phone_e164")
    op.execute(f"ALTER TABLE public.professionals ADD CONSTRAINT ck_professional_phone_e164 CHECK(phone_e164 IS NULL OR phone_e164 ~ '{MOBILE}')")
    op.execute(r"""
      CREATE OR REPLACE FUNCTION public.orion_resolve_central_appointment(
          requested_protocol text, verified_phone text)
      RETURNS TABLE(tenant_id uuid,appointment_id uuid)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $body$
      BEGIN
        IF session_user <> 'app_support'
           OR requested_protocol IS NULL
           OR requested_protocol !~ '^AG-[A-HJ-NP-Z2-9]{6}$'
           OR verified_phone IS NULL
           OR verified_phone !~ '^(\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$' THEN
          RETURN;
        END IF;
        RETURN QUERY WITH matches AS MATERIALIZED (
          SELECT a.tenant_id,a.id AS appointment_id
          FROM public.appointments a
          JOIN public.customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
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
    raise RuntimeError("Não reduzir números internacionais sem revisão e preservação de cadastros")
