"""Fence notices to the HMAC-verified old PN of the preserved session only.

The ordinary automation_notice_guard and channel registration stay unchanged.
No new tables, bot authority, sessions or contact authorization are created.
"""
from alembic import op

revision='0049_preserved_phone_guard'
down_revision='0048_appointment_notifications'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
        CREATE FUNCTION public.orion_preserved_notice_guard(
            p_tenant uuid,p_channel uuid,p_account uuid,p_owner uuid,p_fence bigint,p_sender text,p_session_proof text)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
        DECLARE membership record; channel record; company record;
        BEGIN
            IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid
                OR p_tenant IS NULL OR p_channel IS NULL OR p_account IS NULL
                OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1
                OR p_sender IS NULL OR p_sender !~ '^55[1-9][0-9][2-9][0-9]{7}$'
                OR p_session_proof IS NULL OR p_session_proof !~ '^[a-f0-9]{64}$' THEN RETURN false; END IF;
            SELECT m.active,m.role INTO membership FROM public.tenant_memberships m
                WHERE m.tenant_id=p_tenant AND m.account_id=p_account FOR SHARE;
            IF NOT FOUND OR NOT membership.active OR membership.role<>'owner' THEN RETURN false; END IF;
            SELECT c.enabled,c.provider,c.fence,c.lease_owner,c.lease_expires_at,c.allowed_senders,c.request_fingerprint
                INTO channel FROM public.qr_channels c WHERE c.id=p_channel
                AND c.tenant_id=p_tenant AND c.owner_account_id=p_account FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            SELECT t.status INTO company FROM public.tenants t WHERE t.id=p_tenant FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            RETURN channel.enabled AND channel.provider='baileys' AND company.status='active'
                AND channel.request_fingerprint=p_session_proof AND channel.allowed_senders ? p_sender
                AND channel.lease_owner=p_owner AND channel.fence=p_fence
                AND channel.lease_expires_at>clock_timestamp();
        END $$;
        REVOKE ALL ON FUNCTION public.orion_preserved_notice_guard(uuid,uuid,uuid,uuid,bigint,text,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.orion_preserved_notice_guard(uuid,uuid,uuid,uuid,bigint,text,text) TO app_runtime;
    """)


def downgrade():
    op.execute('DROP FUNCTION public.orion_preserved_notice_guard(uuid,uuid,uuid,uuid,bigint,text,text)')
