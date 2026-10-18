"""Opt-in public catalogue and managed service packages on the booking engine.

Revision ID: 0014_public_catalog
Revises: 0013_management
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014_public_catalog"
down_revision = "0013_management"
branch_labels = None
depends_on = None
TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
TABLES = ("service_packages", "service_package_items", "service_addons", "public_site_daily_metrics", "public_booking_receipts")
SITE_COLUMNS = ("public_enabled", "public_enabled_at", "public_snapshot", "public_slug", "city", "state", "postal_code", "maps_url",
                "facebook_url", "tiktok_url", "external_url", "whatsapp_phone", "hide_orion_brand")


def upgrade():
    op.add_column("appointment_items", sa.Column("catalog_snapshot", JSONB(), nullable=True))
    op.add_column("services", sa.Column("catalog_kind", sa.String(12), nullable=False, server_default="base"))
    op.add_column("services", sa.Column("addons_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_service_catalog_kind", "services", "catalog_kind IN ('base','combo','variant') AND addons_version>=0")
    for column in (
        sa.Column("public_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("public_snapshot", JSONB(), nullable=True),
        sa.Column("public_slug", sa.String(80), nullable=True),
        sa.Column("city", sa.String(120), nullable=True), sa.Column("state", sa.String(2), nullable=True),
        sa.Column("postal_code", sa.String(9), nullable=True),
        *(sa.Column(name, sa.String(2048), nullable=True) for name in ("maps_url", "facebook_url", "tiktok_url", "external_url")),
        sa.Column("whatsapp_phone", sa.String(16), nullable=True),
        sa.Column("hide_orion_brand", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("management_site_preferences", column)
    op.create_unique_constraint("uq_site_published_slug", "management_site_preferences", ["public_slug"])
    op.create_check_constraint("ck_site_public_slug", "management_site_preferences", "NOT public_enabled OR (public_slug IS NOT NULL AND public_snapshot IS NOT NULL AND jsonb_typeof(public_snapshot)='object' AND length(COALESCE(public_snapshot->>'title',''))>0)")
    op.create_table("service_packages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("synthetic_service_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="combo"),
        sa.Column("base_service_id", UUID(as_uuid=True), nullable=True),
        sa.Column("selection_key", sa.String(64), nullable=True),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.String(500), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("color", sa.String(7), nullable=False, server_default="#2e7bf6"),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_service_package_tenant_id"),
        sa.UniqueConstraint("tenant_id", "synthetic_service_id", name="uq_package_synthetic"),
        sa.UniqueConstraint("tenant_id", "selection_key", name="uq_package_selection"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_package_idempotency"),
        sa.ForeignKeyConstraint(["tenant_id", "synthetic_service_id"], ["services.tenant_id", "services.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "base_service_id"], ["services.tenant_id", "services.id"]),
        sa.CheckConstraint("kind IN ('combo','variant') AND ((kind='combo' AND base_service_id IS NULL AND selection_key IS NULL) OR (kind='variant' AND base_service_id IS NOT NULL AND selection_key IS NOT NULL))", name="ck_package_kind"),
        sa.CheckConstraint("price_cents BETWEEN 0 AND 100000000 AND version>=1 AND sort_order BETWEEN 0 AND 1000000 AND color ~ '^#[0-9a-fA-F]{6}$'", name="ck_package_values"),
    )
    op.create_table("service_package_items",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("package_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("service_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("add_duration", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["tenant_id", "package_id"], ["service_packages.tenant_id", "service_packages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "service_id"], ["services.tenant_id", "services.id"]),
        sa.UniqueConstraint("tenant_id", "package_id", "item_order", name="uq_package_item_order"),
        sa.CheckConstraint("item_order BETWEEN 0 AND 6", name="ck_package_item_order"),
    )
    op.create_table("service_addons",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("base_service_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("addon_service_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("add_duration", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id", "base_service_id"], ["services.tenant_id", "services.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "addon_service_id"], ["services.tenant_id", "services.id"]),
        sa.CheckConstraint("base_service_id<>addon_service_id AND sort_order BETWEEN 0 AND 4", name="ck_addon_values"),
    )
    op.create_table("public_site_daily_metrics",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, primary_key=True),
        sa.Column("day", sa.Date(), nullable=False, primary_key=True),
        sa.Column("visits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bookings", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("visits>=0 AND bookings>=0", name="ck_public_site_metrics"),
    )
    op.create_table("public_booking_receipts",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "appointment_id"], ["appointments.tenant_id", "appointments.id"]),
    )
    op.create_index("ix_public_receipts_created", "public_booking_receipts", ["tenant_id", "created_at"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO app_runtime")

    # A finite catalogue resolver exposes only an explicitly published slug.
    # The UUID stays server-side; ordinary table reads still require tenant RLS.
    op.execute("""CREATE FUNCTION public.resolve_published_site(requested_slug text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT p.tenant_id FROM public.management_site_preferences p JOIN public.tenants t ON t.id=p.tenant_id
        WHERE p.public_slug=requested_slug AND p.public_enabled AND t.status='active' $$""")
    op.execute("REVOKE ALL ON FUNCTION public.resolve_published_site(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.resolve_published_site(text) TO app_runtime")

    # Keep synthetic services inside the existing booking contract. All source
    # items must be active, and professionals must serve every component.
    op.execute("""CREATE FUNCTION public.refresh_service_package(scope uuid, package uuid) RETURNS void
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE p public.service_packages%ROWTYPE; n integer; dur integer; prep integer; cleanup integer;
                price bigint; valid boolean;
        BEGIN
          SELECT * INTO p FROM public.service_packages WHERE tenant_id=scope AND id=package FOR UPDATE;
          IF NOT FOUND THEN RETURN; END IF;
          SELECT count(*),COALESCE(sum(CASE WHEN i.add_duration THEN s.duration_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.prep_min ELSE 0 END),0),
                 COALESCE(sum(CASE WHEN i.add_duration THEN s.cleanup_min ELSE 0 END),0),
                 COALESCE(sum(s.price_cents),0),COALESCE(bool_and(s.active),false)
            INTO n,dur,prep,cleanup,price,valid
            FROM public.service_package_items i JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE i.tenant_id=scope AND i.package_id=package;
          valid:=valid AND n BETWEEN 2 AND 6 AND dur BETWEEN 1 AND 1440 AND prep<=240 AND cleanup<=240
                 AND price<=100000000;
          IF p.kind='combo' THEN valid:=valid AND p.price_cents<=price; price:=p.price_cents;
          ELSE
            valid:=valid AND EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope
                AND i.package_id=package AND i.service_id=p.base_service_id AND i.add_duration)
              AND NOT EXISTS (SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope AND i.package_id=package
                AND i.service_id<>p.base_service_id AND NOT EXISTS (SELECT 1 FROM public.service_addons a
                  WHERE a.tenant_id=scope AND a.base_service_id=p.base_service_id AND a.addon_service_id=i.service_id
                    AND a.add_duration=i.add_duration));
          END IF;
          UPDATE public.services SET name=p.name,description=p.description,price_cents=LEAST(100000000,price)::integer,
              duration_min=GREATEST(1,LEAST(1440,dur)),prep_min=LEAST(240,prep),cleanup_min=LEAST(240,cleanup),
              active=p.active AND valid,category=CASE WHEN p.kind='combo' THEN 'Combos' ELSE NULL END,
              color=p.color,image_url=p.image_url,sort_order=p.sort_order
            WHERE tenant_id=scope AND id=p.synthetic_service_id;
          DELETE FROM public.professional_services WHERE tenant_id=scope AND service_id=p.synthetic_service_id;
          IF valid AND p.active THEN
            INSERT INTO public.professional_services (tenant_id,professional_id,service_id)
              SELECT scope,pr.id,p.synthetic_service_id FROM public.professionals pr
              WHERE pr.tenant_id=scope AND NOT EXISTS (
                SELECT 1 FROM public.service_package_items i WHERE i.tenant_id=scope AND i.package_id=package
                  AND NOT EXISTS (SELECT 1 FROM public.professional_services ps WHERE ps.tenant_id=scope
                    AND ps.professional_id=pr.id AND ps.service_id=i.service_id));
          END IF;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.refresh_service_package(uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.refresh_service_package(uuid,uuid) TO app_runtime")
    op.execute("""CREATE FUNCTION public.validate_package_item() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,public AS $$ DECLARE pk text; sk text; synthetic uuid; BEGIN
        SELECT kind,synthetic_service_id INTO pk,synthetic FROM public.service_packages
          WHERE tenant_id=NEW.tenant_id AND id=NEW.package_id;
        SELECT catalog_kind INTO sk FROM public.services WHERE tenant_id=NEW.tenant_id AND id=NEW.service_id;
        IF NEW.service_id=synthetic OR sk='variant' OR (pk='combo' AND (sk<>'base' OR NOT NEW.add_duration)) THEN
          RAISE EXCEPTION 'Invalid package component' USING ERRCODE='23514'; END IF;
        RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER validate_package_item BEFORE INSERT OR UPDATE ON service_package_items FOR EACH ROW EXECUTE FUNCTION public.validate_package_item()")
    op.execute("""CREATE FUNCTION public.package_changed() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,public AS $$ BEGIN
        IF TG_TABLE_NAME='service_packages' THEN PERFORM public.refresh_service_package(NEW.tenant_id,NEW.id);
        ELSIF TG_OP='DELETE' THEN PERFORM public.refresh_service_package(OLD.tenant_id,OLD.package_id);
        ELSE PERFORM public.refresh_service_package(NEW.tenant_id,NEW.package_id); END IF;
        RETURN NULL; END $$""")
    op.execute("CREATE TRIGGER package_changed AFTER INSERT OR UPDATE ON service_packages FOR EACH ROW EXECUTE FUNCTION public.package_changed()")
    op.execute("CREATE TRIGGER package_items_changed AFTER INSERT OR UPDATE OR DELETE ON service_package_items FOR EACH ROW EXECUTE FUNCTION public.package_changed()")
    op.execute("""CREATE FUNCTION public.package_source_changed() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,public AS $$ DECLARE scope uuid; source_id uuid; old_source_id uuid; rec record; BEGIN
        IF TG_OP='DELETE' THEN scope:=OLD.tenant_id;
          IF TG_TABLE_NAME='services' THEN source_id:=OLD.id;
          ELSIF TG_TABLE_NAME='professional_services' THEN source_id:=OLD.service_id;
          ELSE source_id:=OLD.base_service_id; END IF;
        ELSE scope:=NEW.tenant_id;
          IF TG_TABLE_NAME='services' THEN source_id:=NEW.id;
          ELSIF TG_TABLE_NAME='professional_services' THEN source_id:=NEW.service_id;
          ELSE source_id:=NEW.base_service_id; END IF;
        END IF;
        IF TG_OP='UPDATE' THEN
          IF TG_TABLE_NAME='professional_services' THEN old_source_id:=OLD.service_id;
          ELSIF TG_TABLE_NAME='service_addons' THEN old_source_id:=OLD.base_service_id;
          ELSE old_source_id:=OLD.id; END IF;
        END IF;
        FOR rec IN SELECT DISTINCT p.id FROM public.service_packages p
          LEFT JOIN public.service_package_items i ON i.tenant_id=p.tenant_id AND i.package_id=p.id
          WHERE p.tenant_id=scope AND (i.service_id=source_id OR i.service_id=old_source_id OR
             (TG_TABLE_NAME='service_addons' AND (p.base_service_id=source_id OR p.base_service_id=old_source_id))) ORDER BY p.id
        LOOP PERFORM public.refresh_service_package(scope,rec.id); END LOOP;
        RETURN NULL; END $$""")
    op.execute("CREATE TRIGGER package_service_changed AFTER UPDATE OF name,description,duration_min,price_cents,prep_min,cleanup_min,active ON services FOR EACH ROW EXECUTE FUNCTION public.package_source_changed()")
    op.execute("CREATE TRIGGER package_professional_changed AFTER INSERT OR UPDATE OR DELETE ON professional_services FOR EACH ROW EXECUTE FUNCTION public.package_source_changed()")
    op.execute("CREATE TRIGGER package_addons_changed AFTER INSERT OR UPDATE OR DELETE ON service_addons FOR EACH ROW EXECUTE FUNCTION public.package_source_changed()")
    for name in ("validate_package_item", "package_changed", "package_source_changed"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC")
    # Preserve the purchased composition when a combo is edited later. Existing
    # engine inserts/reprices populate this through the same scoped transaction.
    op.execute("""CREATE FUNCTION public.snapshot_service_package_item() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        SELECT jsonb_build_object('kind',p.kind,'name',p.name,'version',p.version,
          'price_cents',NEW.price_snapshot_cents,'duration_min',NEW.duration_snapshot_min,
          'items',(SELECT jsonb_agg(jsonb_build_object('service_id',s.id,'name',s.name,
              'price_cents',s.price_cents,'duration_min',s.duration_min,'prep_min',s.prep_min,
              'cleanup_min',s.cleanup_min,'add_duration',i.add_duration,
              'components',(SELECT jsonb_agg(jsonb_build_object('service_id',cs.id,'name',cs.name,
                  'price_cents',cs.price_cents,'duration_min',cs.duration_min) ORDER BY ci.item_order)
                FROM public.service_packages cp JOIN public.service_package_items ci
                  ON ci.tenant_id=cp.tenant_id AND ci.package_id=cp.id
                JOIN public.services cs ON cs.tenant_id=ci.tenant_id AND cs.id=ci.service_id
                WHERE cp.tenant_id=NEW.tenant_id AND cp.synthetic_service_id=s.id AND cp.kind='combo')) ORDER BY i.item_order)
            FROM public.service_package_items i JOIN public.services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE i.tenant_id=NEW.tenant_id AND i.package_id=p.id))
          INTO NEW.catalog_snapshot FROM public.service_packages p
          WHERE p.tenant_id=NEW.tenant_id AND p.synthetic_service_id=NEW.service_id;
        RETURN NEW;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.snapshot_service_package_item() FROM PUBLIC")
    op.execute("CREATE TRIGGER package_item_snapshot BEFORE INSERT OR UPDATE OF service_id,name_snapshot,duration_snapshot_min,price_snapshot_cents,prep_snapshot_min,cleanup_snapshot_min ON appointment_items FOR EACH ROW EXECUTE FUNCTION public.snapshot_service_package_item()")


def downgrade():
    # Booking snapshots may point to managed services; silently deleting those
    # rows would corrupt business history. Rollback requires an empty catalogue.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM service_packages) THEN
        RAISE EXCEPTION 'Cannot downgrade public catalogue while managed packages exist'; END IF; END $$""")
    for table,trigger in (("appointment_items","package_item_snapshot"),("services","package_service_changed"),("professional_services","package_professional_changed"),
        ("service_addons","package_addons_changed"),("service_packages","package_changed"),
        ("service_package_items","package_items_changed"),("service_package_items","validate_package_item")):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for signature in ("snapshot_service_package_item()", "package_source_changed()", "package_changed()", "validate_package_item()", "refresh_service_package(uuid,uuid)", "resolve_published_site(text)"):
        op.execute(f"DROP FUNCTION public.{signature}")
    for table in reversed(TABLES):op.drop_table(table)
    op.drop_constraint("ck_site_public_slug", "management_site_preferences", type_="check")
    op.drop_constraint("uq_site_published_slug", "management_site_preferences", type_="unique")
    for name in SITE_COLUMNS:op.drop_column("management_site_preferences",name)
    op.drop_constraint("ck_service_catalog_kind", "services", type_="check")
    op.drop_column("services","addons_version")
    op.drop_column("services","catalog_kind")
    op.drop_column("appointment_items","catalog_snapshot")
