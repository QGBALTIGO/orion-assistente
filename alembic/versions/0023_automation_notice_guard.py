"""Fence managed inbound invalidation even when the assistant is disabled.

This grants no booking/assistant authority and creates no contact or consent.
"""
from alembic import op

revision = "0023_automation_notice_guard"
down_revision = "0022_marketing_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION public.automation_notice_guard(
            p_tenant uuid, p_channel uuid, p_account uuid, p_owner uuid, p_fence bigint, p_sender text)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
        DECLARE membership record; channel record; company record;
        BEGIN
            IF p_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid
                OR p_tenant IS NULL OR p_channel IS NULL OR p_account IS NULL
                OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1
                OR p_sender IS NULL OR p_sender !~ '^55[1-9][0-9](9[0-9]{8}|[2-5][0-9]{7})$' THEN
                RETURN false;
            END IF;
            -- Same lock order as managed domain writes, configuration and lease
            -- acquisition. No support connection is held during this call.
            SELECT m.active,m.role INTO membership FROM public.tenant_memberships m
                WHERE m.tenant_id=p_tenant AND m.account_id=p_account FOR SHARE;
            IF NOT FOUND OR NOT membership.active OR membership.role<>'owner' THEN RETURN false; END IF;
            SELECT c.enabled,c.provider,c.fence,c.lease_owner,c.lease_expires_at,c.allowed_senders
                INTO channel FROM public.qr_channels c WHERE c.id=p_channel
                AND c.tenant_id=p_tenant AND c.owner_account_id=p_account FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            SELECT t.status INTO company FROM public.tenants t WHERE t.id=p_tenant FOR SHARE;
            IF NOT FOUND THEN RETURN false; END IF;
            -- Deliberately does not authorize the assistant. A real inbound
            -- message must invalidate marketing even with assistant/bot off.
            RETURN channel.enabled AND channel.provider='baileys' AND company.status='active'
                AND channel.allowed_senders ? p_sender
                AND channel.lease_owner=p_owner AND channel.fence=p_fence
                AND channel.lease_expires_at>clock_timestamp();
        END $$;
        REVOKE ALL ON FUNCTION public.automation_notice_guard(uuid,uuid,uuid,uuid,bigint,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.automation_notice_guard(uuid,uuid,uuid,uuid,bigint,text) TO app_runtime;
    """)


def downgrade():
    op.execute("DROP FUNCTION public.automation_notice_guard(uuid,uuid,uuid,uuid,bigint,text)")
