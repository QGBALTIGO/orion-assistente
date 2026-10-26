"""Durable tenant site addresses and immutable publication receipts.

Revision ID: 0036_site_publication
Revises: 0035_public_amenities

No site is published by this migration. Existing snapshots, reservations and
appointments are preserved. Ambiguous legacy addresses abort the transaction.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0036_site_publication"
down_revision = "0035_public_amenities"
branch_labels = None
depends_on = None

TENANT = "(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"


def upgrade():
    # Serialize the finite backfill against legacy site editors. A collision is
    # evidence requiring review, never permission to silently rename a company.
    op.execute("LOCK TABLE management_site_preferences IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT address FROM (
            SELECT tenant_id,slug AS address FROM management_site_preferences WHERE slug IS NOT NULL
            UNION ALL SELECT tenant_id,public_slug FROM management_site_preferences WHERE public_slug IS NOT NULL
        ) addresses GROUP BY address HAVING count(DISTINCT tenant_id)>1) THEN
            RAISE EXCEPTION 'Public site address collision: resolve draft/published namespace conflicts before upgrading';
        END IF;
    END $$""")
    op.add_column("management_site_preferences", sa.Column("public_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("management_site_preferences", sa.Column("public_disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_site_public_version", "management_site_preferences", "public_version>=0")
    op.create_table("public_site_slugs",
        sa.Column("slug", sa.String(80), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('draft','canonical','alias')", name="ck_public_slug_kind"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) BETWEEN 3 AND 80", name="ck_public_slug_format"))
    op.create_index("ix_public_site_slugs_tenant", "public_site_slugs", ["tenant_id", "kind"])
    op.create_index("uq_public_site_canonical_tenant", "public_site_slugs", ["tenant_id"],
                    unique=True, postgresql_where=sa.text("kind='canonical'"))
    op.create_table("public_site_publications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=True),
        sa.Column("slug", sa.String(80), nullable=True),
        sa.Column("actor_account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "version", name="uq_site_publication_version"),
        sa.CheckConstraint("version>=1 AND action IN ('publish','unpublish','restore')", name="ck_site_publication_action"))
    op.create_index("ix_public_site_history_tenant", "public_site_publications", ["tenant_id", "id"])
    op.execute("""INSERT INTO public_site_slugs(slug,tenant_id,kind)
        SELECT public_slug,tenant_id,'canonical' FROM management_site_preferences WHERE public_slug IS NOT NULL""")
    op.execute("""INSERT INTO public_site_slugs(slug,tenant_id,kind)
        SELECT slug,tenant_id,'draft' FROM management_site_preferences WHERE slug IS NOT NULL
        ON CONFLICT(slug) DO NOTHING""")
    op.execute("UPDATE management_site_preferences SET public_version=version WHERE public_snapshot IS NOT NULL")
    op.execute("""INSERT INTO public_site_publications(tenant_id,version,action,snapshot,slug,published_at)
        SELECT tenant_id,version,CASE WHEN public_enabled THEN 'publish' ELSE 'unpublish' END,
            public_snapshot,public_slug,COALESCE(public_enabled_at,updated_at)
        FROM management_site_preferences WHERE public_snapshot IS NOT NULL""")
    for table in ("public_site_slugs", "public_site_publications"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={TENANT}) WITH CHECK(tenant_id={TENANT})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public_site_slugs TO app_runtime")
    op.execute("GRANT SELECT,INSERT ON public_site_publications TO app_runtime")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE public_site_publications_id_seq TO app_runtime")
    op.execute("""CREATE FUNCTION public.site_history_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        IF current_user<>'app_migrator' THEN RAISE EXCEPTION 'Site publication history is immutable' USING ERRCODE='42501'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.site_history_immutable() FROM PUBLIC")
    op.execute("""CREATE TRIGGER site_history_immutable BEFORE UPDATE OR DELETE ON public_site_publications
        FOR EACH ROW EXECUTE FUNCTION public.site_history_immutable()""")
    op.execute("""CREATE FUNCTION public.public_site_slug_available(requested_slug text,requested_tenant uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT requested_tenant=NULLIF(current_setting('app.tenant_id',true),'')::uuid
          AND NOT EXISTS(SELECT 1 FROM public.public_site_slugs WHERE slug=requested_slug AND tenant_id<>requested_tenant)
    $$""")
    op.execute("REVOKE ALL ON FUNCTION public.public_site_slug_available(text,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.public_site_slug_available(text,uuid) TO app_runtime")
    op.execute("""CREATE OR REPLACE FUNCTION public.resolve_published_site(requested_slug text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT p.tenant_id FROM public.public_site_slugs r
        JOIN public.management_site_preferences p ON p.tenant_id=r.tenant_id
        JOIN public.tenants t ON t.id=p.tenant_id
        WHERE r.slug=requested_slug AND r.kind IN ('canonical','alias') AND p.public_enabled AND t.status='active'
    $$""")
    # A pre-deploy migration can coexist briefly with the preceding API image.
    # Never let that legacy writer rename a site without updating the registry.
    # Refuse its writes; do not invent an automatic parallel publication flow.
    op.execute("""CREATE FUNCTION public.guard_site_publication_writer() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        IF current_user<>'app_migrator'
           AND current_setting('app.site_publication_v2',true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'Site configuration requires the current publication writer'
                USING ERRCODE='55000';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.guard_site_publication_writer() FROM PUBLIC")
    op.execute("""CREATE TRIGGER guard_site_publication_writer BEFORE INSERT OR UPDATE OR DELETE
        ON management_site_preferences FOR EACH ROW EXECUTE FUNCTION public.guard_site_publication_writer()""")


def downgrade():
    raise RuntimeError("Public address aliases and publication history require reviewed forward recovery")
