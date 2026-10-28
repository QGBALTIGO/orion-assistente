"""One persistent WhatsApp identity per tenant; retain every historical channel.

The nullable pointer deliberately leaves ambiguous registries unresolved. This
migration never edits credentials, enabled state, leases or notice receipts.
"""
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0044_single_whatsapp_connection"
down_revision = "0043_cakto_events"
branch_labels = None
depends_on = None


def backfill_bindings(connection):
    # Only identities/state, never phone numbers, policy or auth material.
    rows = connection.execute(sa.text("SELECT id,tenant_id,enabled FROM qr_channels ORDER BY tenant_id,id")).mappings()
    tenants = {}
    for row in rows:
        tenants.setdefault(row["tenant_id"], []).append(row)
    for tenant, channels in tenants.items():
        active = [row for row in channels if row["enabled"]]
        alias = uuid5(NAMESPACE_URL, "orion:preserved-primary-qr:" + str(tenant))
        aliases = [row for row in channels if row["id"] == alias]
        # A second active socket is never silently hidden/fenced by backfill.
        selected = (aliases[0] if aliases and all(row["id"] == alias for row in active)
                    else channels[0] if len(channels) == 1
                    else active[0] if len(active) == 1 and not aliases else None)
        if selected:
            connection.execute(sa.text("UPDATE tenants SET whatsapp_channel_id=:channel WHERE id=:tenant AND whatsapp_channel_id IS NULL"),
                                  {"channel": selected["id"], "tenant": tenant})


def upgrade():
    op.add_column("tenants", sa.Column("whatsapp_channel_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tenant_whatsapp_channel", "tenants", "qr_channels",
                          ["id", "whatsapp_channel_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_index("ix_tenant_whatsapp_channel", "tenants", ["whatsapp_channel_id"],
                    unique=True, postgresql_where=sa.text("whatsapp_channel_id IS NOT NULL"))
    op.execute("GRANT SELECT(whatsapp_channel_id),UPDATE(whatsapp_channel_id) ON public.tenants TO app_support")
    op.execute("GRANT SELECT(whatsapp_channel_id) ON public.tenants TO app_runtime")
    backfill_bindings(op.get_bind())
    op.drop_constraint("ck_qr_channel_senders", "qr_channels", type_="check")
    op.create_check_constraint("ck_qr_channel_senders", "qr_channels",
        "jsonb_typeof(allowed_senders)='array' AND jsonb_array_length(allowed_senders) BETWEEN 0 AND 10 "
        "AND NOT jsonb_path_exists(allowed_senders, '$[*] ? (@.type() != \"string\")')")
    op.create_check_constraint("ck_qr_channel_private_policy", "qr_channels",
        "NOT assistant_enabled OR jsonb_array_length(allowed_senders)>0")
    # Older API replicas may still run briefly after predeploy. Enforce the same
    # single identity at the write boundary without touching historical rows.
    op.execute("""
        CREATE FUNCTION public.qr_single_connection_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
        DECLARE canonical uuid;
        BEGIN
          IF pg_has_role(current_user,'app_migrator','MEMBER') THEN RETURN NEW; END IF;
          IF TG_OP='INSERT' THEN
            SELECT whatsapp_channel_id INTO canonical FROM public.tenants
              WHERE id=NEW.tenant_id FOR UPDATE;
            IF canonical IS NOT NULL OR EXISTS(SELECT 1 FROM public.qr_channels
                WHERE tenant_id=NEW.tenant_id AND id<>NEW.id) THEN
              RAISE EXCEPTION 'single_whatsapp_connection_required' USING ERRCODE='23514';
            END IF;
            UPDATE public.tenants SET whatsapp_channel_id=NEW.id WHERE id=NEW.tenant_id;
          ELSIF (NEW.enabled AND NOT OLD.enabled) OR (NEW.assistant_enabled AND NOT OLD.assistant_enabled)
              OR (NEW.lease_owner IS NOT NULL AND (NEW.lease_owner IS DISTINCT FROM OLD.lease_owner OR NEW.fence>OLD.fence)) THEN
            SELECT whatsapp_channel_id INTO canonical FROM public.tenants WHERE id=NEW.tenant_id;
            IF canonical IS DISTINCT FROM NEW.id THEN
              RAISE EXCEPTION 'canonical_whatsapp_connection_required' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION public.qr_single_connection_guard() FROM PUBLIC;
        CREATE TRIGGER qr_single_connection_insert AFTER INSERT ON public.qr_channels
          FOR EACH ROW EXECUTE FUNCTION public.qr_single_connection_guard();
        CREATE TRIGGER qr_single_connection_activate BEFORE UPDATE OF enabled,assistant_enabled ON public.qr_channels
          FOR EACH ROW EXECUTE FUNCTION public.qr_single_connection_guard();
        CREATE TRIGGER qr_single_connection_claim BEFORE UPDATE OF lease_owner,fence ON public.qr_channels
          FOR EACH ROW EXECUTE FUNCTION public.qr_single_connection_guard();
    """)


def downgrade():
    # Empty deny-all policies cannot be made permissive merely to roll back.
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM qr_channels WHERE jsonb_array_length(allowed_senders)=0)")).scalar():
        raise RuntimeError("Cannot downgrade while deny-all WhatsApp policies exist")
    op.execute("DROP TRIGGER qr_single_connection_activate ON public.qr_channels")
    op.execute("DROP TRIGGER qr_single_connection_claim ON public.qr_channels")
    op.execute("DROP TRIGGER qr_single_connection_insert ON public.qr_channels")
    op.execute("DROP FUNCTION public.qr_single_connection_guard()")
    op.drop_constraint("ck_qr_channel_private_policy", "qr_channels", type_="check")
    op.drop_constraint("ck_qr_channel_senders", "qr_channels", type_="check")
    op.create_check_constraint("ck_qr_channel_senders", "qr_channels",
        "jsonb_typeof(allowed_senders)='array' AND jsonb_array_length(allowed_senders) BETWEEN 1 AND 10 "
        "AND NOT jsonb_path_exists(allowed_senders, '$[*] ? (@.type() != \"string\")')")
    op.drop_index("ix_tenant_whatsapp_channel", table_name="tenants")
    op.drop_constraint("fk_tenant_whatsapp_channel", "tenants", type_="foreignkey")
    op.drop_column("tenants", "whatsapp_channel_id")
