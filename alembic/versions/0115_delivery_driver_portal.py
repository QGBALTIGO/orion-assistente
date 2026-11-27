"""Verified individual courier access, shift and dispatch acceptance."""
from alembic import op

revision = "0115_delivery_driver_portal"
down_revision = "0114_delivery_purchases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.delivery_driver_access (
            tenant_id uuid NOT NULL, courier_id uuid NOT NULL,
            email varchar(254), account_id uuid REFERENCES public.panel_accounts(id),
            updated_by uuid NOT NULL REFERENCES public.panel_accounts(id),
            revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
            shift_started_at timestamptz, available boolean NOT NULL DEFAULT false,
            latitude double precision, longitude double precision, location_at timestamptz, sharing_key uuid,
            PRIMARY KEY(tenant_id,courier_id), UNIQUE(tenant_id,email), UNIQUE(tenant_id,account_id),
            FOREIGN KEY(tenant_id,courier_id) REFERENCES public.delivery_couriers(tenant_id,id) ON DELETE CASCADE,
            CHECK (email IS NULL OR (email=lower(btrim(email)) AND length(email)>3)),
            CHECK (shift_started_at IS NOT NULL OR NOT available),
            CHECK ((latitude IS NULL AND longitude IS NULL AND location_at IS NULL) OR
                (latitude IS NOT NULL AND longitude IS NOT NULL AND location_at IS NOT NULL AND shift_started_at IS NOT NULL
                AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))
        );
        CREATE TABLE public.delivery_driver_assignments (
            tenant_id uuid NOT NULL, order_id uuid NOT NULL, courier_id uuid NOT NULL,
            access_revision integer NOT NULL CHECK(access_revision>0),
            status varchar(16) NOT NULL CHECK(status IN ('offered','accepted','arrived','refused','fulfilled','released')),
            offered_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(tenant_id,order_id),
            FOREIGN KEY(tenant_id,order_id) REFERENCES public.delivery_order_details(tenant_id,order_id) ON DELETE CASCADE,
            FOREIGN KEY(tenant_id,courier_id) REFERENCES public.delivery_couriers(tenant_id,id)
        );
        CREATE INDEX delivery_driver_queue ON public.delivery_driver_assignments(tenant_id,courier_id,status,updated_at);
        ALTER TABLE public.commercial_events DROP CONSTRAINT ck_commercial_event_actor;
        ALTER TABLE public.commercial_events ADD CONSTRAINT ck_commercial_event_actor CHECK
            (actor_role IN ('owner','editor','support','admin','customer','courier')) NOT VALID;
    """)
    op.execute("ALTER TABLE public.commercial_events VALIDATE CONSTRAINT ck_commercial_event_actor")
    for table in ("delivery_driver_access","delivery_driver_assignments"):
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_runtime ON public.{table} TO app_runtime
                USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
            CREATE POLICY {table}_migrator ON public.{table} TO app_migrator USING(true) WITH CHECK(true);
            REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support;
            GRANT SELECT,INSERT,UPDATE ON public.{table} TO app_runtime;
        """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.delivery_driver_access) THEN RAISE EXCEPTION 'Preserve o acesso e os expedientes'; END IF;
        END $$;
        DROP TABLE public.delivery_driver_assignments,public.delivery_driver_access;
        ALTER TABLE public.commercial_events DROP CONSTRAINT ck_commercial_event_actor;
        ALTER TABLE public.commercial_events ADD CONSTRAINT ck_commercial_event_actor CHECK
            (actor_role IN ('owner','editor','support','admin','customer'));""")
