"""Spread public counters without changing any existing booking or metric.

Revision ID: 0037_public_runtime
Revises: 0036_site_publication
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0037_public_runtime'
down_revision = '0036_site_publication'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('public_site_metric_shards',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('shard', sa.SmallInteger(), primary_key=True),
        sa.Column('visits', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bookings', sa.BigInteger(), nullable=False, server_default='0'),
        sa.CheckConstraint('shard>=0 AND shard<128', name='ck_public_metric_shard'),
        sa.CheckConstraint('visits>=0 AND bookings>=0', name='ck_public_metric_counts'))
    tenant="(SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
    op.execute('ALTER TABLE public_site_metric_shards ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public_site_metric_shards FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY public_metric_shards_runtime ON public_site_metric_shards FOR ALL TO app_runtime USING(tenant_id={tenant}) WITH CHECK(tenant_id={tenant})')
    op.execute('CREATE POLICY public_metric_shards_migrator ON public_site_metric_shards FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
    op.execute('REVOKE ALL ON public_site_metric_shards FROM PUBLIC,app_runtime,app_support')
    op.execute('GRANT SELECT,INSERT,UPDATE ON public_site_metric_shards TO app_runtime')
    op.create_table('public_site_catalogue_versions',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.BigInteger(), nullable=False, server_default='0'))
    op.execute('ALTER TABLE public_site_catalogue_versions ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public_site_catalogue_versions FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY public_catalogue_versions_runtime ON public_site_catalogue_versions FOR SELECT TO app_runtime USING(tenant_id={tenant})')
    op.execute('CREATE POLICY public_catalogue_versions_migrator ON public_site_catalogue_versions FOR ALL TO app_migrator USING(true) WITH CHECK(true)')
    op.execute('REVOKE ALL ON public_site_catalogue_versions FROM PUBLIC,app_runtime,app_support')
    op.execute('GRANT SELECT ON public_site_catalogue_versions TO app_runtime')
    op.execute('''CREATE FUNCTION public.bump_public_catalogue_version() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        DECLARE row_data jsonb; target uuid;
        BEGIN
          IF TG_OP='UPDATE' AND to_jsonb(OLD)=to_jsonb(NEW) THEN RETURN NEW; END IF;
          IF TG_OP='DELETE' THEN row_data=to_jsonb(OLD); ELSE row_data=to_jsonb(NEW); END IF;
          IF TG_TABLE_NAME='tenants' THEN target=(row_data->>'id')::uuid;
          ELSE target=(row_data->>'tenant_id')::uuid; END IF;
          IF target IS NOT NULL AND EXISTS(SELECT 1 FROM public.tenants WHERE id=target) THEN
            INSERT INTO public.public_site_catalogue_versions(tenant_id,version) VALUES(target,1)
            ON CONFLICT(tenant_id) DO UPDATE SET version=public.public_site_catalogue_versions.version+1;
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$''')
    op.execute('REVOKE ALL ON FUNCTION public.bump_public_catalogue_version() FROM PUBLIC,app_runtime,app_support')
    for table in ('tenants','professionals','services','professional_services','working_hours','working_breaks',
                  'service_addons','service_packages','service_package_items'):
        op.execute(f'''CREATE TRIGGER public_catalogue_version AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION public.bump_public_catalogue_version()''')


def downgrade():
    raise RuntimeError('Public metric history must be preserved; use reviewed forward recovery')
