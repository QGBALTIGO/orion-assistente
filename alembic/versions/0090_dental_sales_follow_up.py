"""Start follow-up of one dental sales opportunity with an append-only event."""
from alembic import op

revision = "0090_dental_sales_follow_up"
down_revision = "0089_dental_sales_patient_link"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE public.dental_sales_opportunities
          ADD COLUMN stage_key varchar(64) NOT NULL DEFAULT 'open',
          ADD COLUMN version integer NOT NULL DEFAULT 1;
        ALTER TABLE public.dental_sales_opportunities
          ADD CONSTRAINT ck_dental_sales_stage_key CHECK (
            stage_key IN ('open','in_progress','won','lost') OR
            stage_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          ),
          ADD CONSTRAINT ck_dental_sales_version CHECK (version >= 1);
        CREATE INDEX ix_dental_sales_stage ON public.dental_sales_opportunities
          (tenant_id,stage_key,created_at DESC,id DESC);

        CREATE TABLE public.dental_sales_opportunity_events (
          tenant_id uuid NOT NULL,
          opportunity_id uuid NOT NULL,
          version integer NOT NULL CHECK (version >= 2),
          request_id uuid NOT NULL,
          from_stage varchar(64) NOT NULL CHECK (
            from_stage IN ('open','in_progress','won','lost') OR
            from_stage ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          ),
          to_stage varchar(64) NOT NULL CHECK (
            to_stage IN ('open','in_progress','won','lost') OR
            to_stage ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          ),
          actor_account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id,opportunity_id,version),
          UNIQUE (tenant_id,opportunity_id,request_id),
          FOREIGN KEY (tenant_id,opportunity_id)
            REFERENCES public.dental_sales_opportunities(tenant_id,id)
        );
        CREATE INDEX ix_dental_sales_events_created ON public.dental_sales_opportunity_events
          (tenant_id,opportunity_id,created_at DESC,version DESC);

        ALTER TABLE public.dental_sales_opportunity_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dental_sales_opportunity_events FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.dental_sales_opportunity_events FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT ON public.dental_sales_opportunity_events TO app_runtime;
        GRANT UPDATE(stage_key,version) ON public.dental_sales_opportunities TO app_runtime;

        CREATE POLICY sales_event_migration ON public.dental_sales_opportunity_events
          FOR ALL TO app_migrator USING (true) WITH CHECK (true);
        CREATE POLICY sales_event_read ON public.dental_sales_opportunity_events
          FOR SELECT TO app_runtime USING (
            tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND public.dental_sales_owner()
          );
        CREATE POLICY sales_event_append ON public.dental_sales_opportunity_events
          FOR INSERT TO app_runtime WITH CHECK (
            tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND actor_account_id=NULLIF(current_setting('app.dental_actor',true),'')::uuid
            AND public.dental_sales_owner()
          );
        CREATE POLICY sales_transition ON public.dental_sales_opportunities
          FOR UPDATE TO app_runtime
          USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
                 AND public.dental_sales_owner())
          WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
                      AND public.dental_sales_owner());
    """)


def downgrade():
    op.execute("""
        LOCK TABLE public.dental_sales_opportunities IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.dental_sales_opportunity_events)
             OR EXISTS (SELECT 1 FROM public.dental_sales_opportunities
                         WHERE stage_key <> 'open' OR version <> 1) THEN
            RAISE EXCEPTION 'Preserve o histórico das oportunidades antes de reverter o esquema';
          END IF;
        END $$;
        DROP POLICY sales_transition ON public.dental_sales_opportunities;
        REVOKE UPDATE(stage_key,version) ON public.dental_sales_opportunities FROM app_runtime;
        DROP TABLE public.dental_sales_opportunity_events;
        DROP INDEX public.ix_dental_sales_stage;
        ALTER TABLE public.dental_sales_opportunities
          DROP CONSTRAINT ck_dental_sales_stage_key,
          DROP CONSTRAINT ck_dental_sales_version,
          DROP COLUMN stage_key,
          DROP COLUMN version;
    """)
