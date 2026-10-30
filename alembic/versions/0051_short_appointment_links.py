"""Global short codes and single-use WhatsApp verification; additive, tenant-fenced.

This migration never changes existing appointment protocols or old capabilities.
A code identifies an appointment but grants absolutely no access to its data.
"""
from alembic import op

revision = "0051_short_appointment_links"
down_revision = "0050_plan_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='8s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""
        CREATE TABLE public.appointment_short_codes (
            code varchar(18) PRIMARY KEY
                 CHECK (code ~ '^AG[A-HJ-NP-Z2-9]{6,12}$'),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            appointment_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT uq_appointment_short_owner UNIQUE (tenant_id,appointment_id),
            CONSTRAINT fk_appointment_short_booking FOREIGN KEY (tenant_id,appointment_id)
               REFERENCES public.appointments(tenant_id,id) ON DELETE CASCADE
        );
        CREATE TABLE public.appointment_short_challenges (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            appointment_id uuid NOT NULL,
            recipient_kind varchar(16) NOT NULL CHECK(recipient_kind IN ('customer','professional')),
            recipient_id uuid NOT NULL,
            recipient_hash char(64) NOT NULL,
            otp_hash char(64) NOT NULL,
            operation_id uuid NOT NULL UNIQUE,
            status varchar(16) NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','sending','accepted','failed','consumed')),
            body_encrypted text, attempted_at timestamptz,
            attempts smallint NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            sent_at timestamptz,
            used_at timestamptz,
            CONSTRAINT fk_appointment_short_challenge FOREIGN KEY(tenant_id,appointment_id)
               REFERENCES public.appointments(tenant_id,id) ON DELETE CASCADE
        );
        CREATE INDEX ix_appointment_short_challenges_due ON
            public.appointment_short_challenges(status,created_at,id);
        CREATE INDEX ix_appointment_short_challenges_recent ON
            public.appointment_short_challenges(tenant_id,appointment_id,recipient_kind,created_at DESC);
        CREATE TABLE public.appointment_short_staff_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id),
            appointment_id uuid NOT NULL,
            professional_id uuid NOT NULL,
            phone_hash char(64) NOT NULL,
            token_hash char(64) NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT fk_appointment_short_staff_booking FOREIGN KEY(tenant_id,appointment_id)
               REFERENCES public.appointments(tenant_id,id) ON DELETE CASCADE,
            CONSTRAINT fk_appointment_short_staff_professional FOREIGN KEY(tenant_id,professional_id)
               REFERENCES public.professionals(tenant_id,id)
        );
        CREATE INDEX ix_appointment_short_staff_recent ON
            public.appointment_short_staff_sessions(tenant_id,appointment_id,expires_at);
    """)
    setting = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    for table in ("appointment_short_codes","appointment_short_challenges","appointment_short_staff_sessions"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR ALL TO app_runtime USING(tenant_id={setting}) WITH CHECK(tenant_id={setting})")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_support,app_runtime")
    op.execute("CREATE POLICY appointment_short_codes_support ON public.appointment_short_codes FOR SELECT TO app_support USING(true)")
    op.execute("GRANT SELECT,INSERT ON public.appointment_short_codes TO app_runtime")
    op.execute("GRANT SELECT ON public.appointment_short_codes TO app_support")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.appointment_short_challenges TO app_runtime")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.appointment_short_staff_sessions TO app_runtime")
    op.execute("""
        CREATE FUNCTION public.orion_due_short_challenges(batch_limit integer)
        RETURNS TABLE(tenant_id uuid,id uuid) LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public AS $
          SELECT c.tenant_id,c.id FROM public.appointment_short_challenges c
          WHERE $1 BETWEEN 1 AND 100 AND c.status='pending'
            AND c.expires_at>clock_timestamp()
          ORDER BY c.created_at,c.id LIMIT LEAST(GREATEST($1,1),100)
        $;
        REVOKE ALL ON FUNCTION public.orion_due_short_challenges(integer) FROM PUBLIC,app_runtime;
        GRANT EXECUTE ON FUNCTION public.orion_due_short_challenges(integer) TO app_support;
    """)
    # Preserve existing protocol text. For a duplicated protocol across tenants,
    # only its first appointment keeps the natural alias; others receive a new
    # globally unique alias on first use. No ambiguous lookup is ever possible.
    op.execute("""
        INSERT INTO public.appointment_short_codes(code,tenant_id,appointment_id)
        SELECT normalized,tenant_id,id FROM (
            SELECT a.tenant_id,a.id,replace(a.protocol,'-','') AS normalized,
              row_number() OVER (
                PARTITION BY replace(a.protocol,'-','')
                ORDER BY a.created_at,a.tenant_id,a.id
              ) AS sequence
            FROM public.appointments a
        ) AS existing WHERE sequence=1
          AND normalized ~ '^AG[A-HJ-NP-Z2-9]{6,12}$'
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    raise RuntimeError("Short-link verification history must be preserved; use a reviewed forward migration")
