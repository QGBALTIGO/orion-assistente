"""Explicit QR channel identities and database-fenced session ownership.

No legacy session, credential, channel or tenant is imported or enabled.
"""
from alembic import op

revision = "0021_qr_channels"
down_revision = "0020_business_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.qr_channels (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE RESTRICT,
            owner_account_id uuid NOT NULL,
            provider varchar(20) NOT NULL CHECK (provider='baileys'),
            label varchar(100) NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 100),
            credential_hash char(64) NOT NULL CHECK (credential_hash ~ '^[0-9a-f]{64}$'),
            credential_encrypted text NOT NULL,
            allowed_senders jsonb NOT NULL DEFAULT '[]'::jsonb,
            enabled boolean NOT NULL DEFAULT false,
            assistant_enabled boolean NOT NULL DEFAULT false,
            revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
            fence bigint NOT NULL DEFAULT 0 CHECK (fence>=0),
            lease_owner uuid,
            lease_expires_at timestamptz,
            idempotency_key uuid NOT NULL,
            request_fingerprint char(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_qr_channel_owner FOREIGN KEY (tenant_id,owner_account_id)
                REFERENCES public.tenant_memberships(tenant_id,account_id) ON DELETE RESTRICT,
            CONSTRAINT uq_qr_channel_scope UNIQUE (tenant_id,id),
            CONSTRAINT uq_qr_channel_creation UNIQUE (tenant_id,idempotency_key),
            CONSTRAINT ck_qr_channel_senders CHECK (jsonb_typeof(allowed_senders)='array'
                AND jsonb_array_length(allowed_senders) BETWEEN 1 AND 10
                AND NOT jsonb_path_exists(allowed_senders, '$[*] ? (@.type() != "string")')),
            CONSTRAINT ck_qr_channel_lease CHECK ((lease_owner IS NULL)=(lease_expires_at IS NULL))
        );
        CREATE INDEX ix_qr_channel_owner ON public.qr_channels(tenant_id,owner_account_id);
        ALTER TABLE public.qr_channels ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.qr_channels FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.qr_channels FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT (id,tenant_id,owner_account_id,provider,label,enabled,assistant_enabled,revision,created_at,updated_at)
            ON public.qr_channels TO app_runtime;
        GRANT SELECT,INSERT,UPDATE ON public.qr_channels TO app_support;
        CREATE POLICY qr_channels_runtime ON public.qr_channels FOR SELECT TO app_runtime
            USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        CREATE POLICY qr_channels_support ON public.qr_channels FOR ALL TO app_support
            USING (true) WITH CHECK (true);
        CREATE POLICY qr_channels_migrator ON public.qr_channels FOR ALL TO app_migrator
            USING (true) WITH CHECK (true);

        CREATE FUNCTION public.qr_channel_transaction_guard(
            p_tenant uuid, p_channel uuid, p_account uuid, p_owner uuid, p_fence bigint)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
        DECLARE membership record; channel record; company record;
        BEGIN
            IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid
                OR p_tenant IS NULL OR p_channel IS NULL OR p_account IS NULL
                OR p_owner IS NULL OR p_fence IS NULL THEN
                RETURN false;
            END IF;
            -- Same order as owner configuration and lease acquisition. SHARE
            -- (not KEY SHARE) blocks revocation and non-key fence updates.
            SELECT m.active,m.role INTO membership FROM public.tenant_memberships m
                WHERE m.tenant_id=p_tenant AND m.account_id=p_account FOR SHARE;
            IF NOT FOUND OR NOT membership.active OR membership.role<>'owner' THEN
                RETURN false;
            END IF;
            SELECT c.enabled,c.assistant_enabled,c.provider,c.fence,c.lease_owner,c.lease_expires_at
                INTO channel FROM public.qr_channels c WHERE c.id=p_channel
                AND c.tenant_id=p_tenant AND c.owner_account_id=p_account FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            SELECT t.status,t.bot_enabled INTO company FROM public.tenants t
                WHERE t.id=p_tenant FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            RETURN channel.enabled AND channel.assistant_enabled AND channel.provider='baileys'
                AND company.status='active' AND company.bot_enabled
                AND channel.lease_owner=p_owner AND channel.fence=p_fence
                AND channel.lease_expires_at>clock_timestamp();
        END $$;
        REVOKE ALL ON FUNCTION public.qr_channel_transaction_guard(uuid,uuid,uuid,uuid,bigint) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.qr_channel_transaction_guard(uuid,uuid,uuid,uuid,bigint) TO app_runtime;
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.qr_channels) THEN
            RAISE EXCEPTION 'QR channel records must be preserved; downgrade refused';
        END IF;
    END $$""")
    op.execute("DROP FUNCTION public.qr_channel_transaction_guard(uuid,uuid,uuid,uuid,bigint)")
    op.drop_table("qr_channels", schema="public")
