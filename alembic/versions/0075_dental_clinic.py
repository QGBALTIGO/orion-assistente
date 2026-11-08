"""Isolated dental clinical data: tenant-scoped, clinician-scoped and append-only.
Revision is additive; old scheduling, CRM and billing tables are not rewritten.
Clinical documents require separate professional/patient signature homologation.
"""
from alembic import op

revision = "0075_dental_clinic"
down_revision = "0074_fixed_client_series"
branch_labels = None
depends_on = None

TENANT = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"
ACTOR = "NULLIF(current_setting('app.dental_actor',true),'')::uuid"


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
    CREATE TABLE public.dental_clinicians (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
        professional_id uuid NOT NULL,
        level varchar(12) NOT NULL CHECK (level IN ('dentist','assistant')),
        cro_number varchar(14),
        cro_state char(2),
        enabled boolean NOT NULL DEFAULT true,
        granted_by uuid NOT NULL REFERENCES public.panel_accounts(id),
        granted_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id,account_id),
        FOREIGN KEY (tenant_id,professional_id) REFERENCES public.professionals(tenant_id,id),
        CHECK (level <> 'dentist' OR (cro_number IS NOT NULL AND cro_state IS NOT NULL))
    );
    CREATE TABLE public.dental_patients (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        customer_id uuid,
        name varchar(120) NOT NULL CHECK (length(btrim(name))>0),
        birth_date date,
        guardian_name varchar(120),
        guardian_phone varchar(30),
        contact_phone varchar(30),
        created_by uuid NOT NULL REFERENCES public.panel_accounts(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        version integer NOT NULL DEFAULT 1 CHECK (version>=1),
        PRIMARY KEY(id),
        UNIQUE(tenant_id,id),
        FOREIGN KEY (tenant_id,customer_id) REFERENCES public.customers(tenant_id,id),
        CHECK (birth_date IS NULL OR birth_date BETWEEN DATE '1900-01-01' AND CURRENT_DATE),
        CHECK (guardian_name IS NULL OR length(btrim(guardian_name))>0)
    );
    CREATE INDEX ix_dental_patients_find ON public.dental_patients(tenant_id,name,id);
    CREATE TABLE public.dental_records (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        patient_id uuid NOT NULL,
        kind varchar(30) NOT NULL CHECK(kind IN
            ('anamnesis','odontogram','periodontal','progress','treatment','consent',
             'budget','insurance','prosthesis','exam_note','certificate','referral')),
        encrypted_content bytea NOT NULL,
        nonce bytea NOT NULL CHECK (octet_length(nonce)=12),
        digest char(64) NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        authored_by uuid NOT NULL REFERENCES public.panel_accounts(id),
        professional_id uuid NOT NULL,
        entry_key varchar(120) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(id),
        UNIQUE(tenant_id,id),
        UNIQUE(tenant_id,entry_key),
        FOREIGN KEY (tenant_id,patient_id) REFERENCES public.dental_patients(tenant_id,id),
        FOREIGN KEY (tenant_id,professional_id) REFERENCES public.professionals(tenant_id,id),
        CHECK (octet_length(encrypted_content) BETWEEN 17 AND 131072)
    );
    CREATE INDEX ix_dental_records_patient ON public.dental_records(tenant_id,patient_id,created_at DESC,id);
    CREATE TABLE public.dental_exam_files (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        patient_id uuid NOT NULL,
        filename varchar(180) NOT NULL,
        media_type varchar(60) NOT NULL CHECK(media_type IN ('application/pdf','image/png','image/jpeg')),
        byte_count integer NOT NULL CHECK(byte_count BETWEEN 1 AND 8388608),
        digest char(64) NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
        nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
        encrypted_content bytea NOT NULL,
        authored_by uuid NOT NULL REFERENCES public.panel_accounts(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(id),
        UNIQUE(tenant_id,id),
        FOREIGN KEY (tenant_id,patient_id) REFERENCES public.dental_patients(tenant_id,id)
    );
    CREATE INDEX ix_dental_exams_patient ON public.dental_exam_files(tenant_id,patient_id,created_at DESC);
    CREATE TABLE public.dental_rooms (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        name varchar(80) NOT NULL CHECK(length(btrim(name))>0),
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(id),
        UNIQUE(tenant_id,id),
        UNIQUE(tenant_id,name)
    );
    CREATE TABLE public.dental_room_reservations (
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        appointment_id uuid NOT NULL,
        room_id uuid NOT NULL,
        period tstzrange NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,appointment_id),
        FOREIGN KEY(tenant_id,appointment_id) REFERENCES public.appointments(tenant_id,id),
        FOREIGN KEY(tenant_id,room_id) REFERENCES public.dental_rooms(tenant_id,id),
        EXCLUDE USING gist (tenant_id WITH =,room_id WITH =,period WITH &&)
          WHERE (active) DEFERRABLE INITIALLY IMMEDIATE
    );
    CREATE INDEX ix_dental_room_schedule ON public.dental_room_reservations(tenant_id,room_id);
    CREATE TABLE public.dental_supplies (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        name varchar(120) NOT NULL CHECK(length(btrim(name))>0),
        unit varchar(12) NOT NULL CHECK(unit IN ('unit','box','ml','g','kit')),
        quantity integer NOT NULL DEFAULT 0 CHECK(quantity BETWEEN 0 AND 100000000),
        version integer NOT NULL DEFAULT 1,
        PRIMARY KEY(id),
        UNIQUE(tenant_id,id),
        UNIQUE(tenant_id,name)
    );
    CREATE TABLE public.dental_supply_movements (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        supply_id uuid NOT NULL,
        movement varchar(3) NOT NULL CHECK(movement IN ('in','out')),
        quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 1000000),
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        authored_by uuid NOT NULL REFERENCES public.panel_accounts(id),
        entry_key varchar(120) NOT NULL,
        PRIMARY KEY(id),
        UNIQUE(tenant_id,entry_key),
        FOREIGN KEY(tenant_id,supply_id) REFERENCES public.dental_supplies(tenant_id,id)
    );
    CREATE TABLE public.dental_access_audit (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES public.tenants(id),
        patient_id uuid,
        actor_account_id uuid NOT NULL REFERENCES public.panel_accounts(id),
        action varchar(48) NOT NULL,
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (tenant_id,patient_id) REFERENCES public.dental_patients(tenant_id,id)
    );
    CREATE INDEX ix_dental_audit_tenant ON public.dental_access_audit(tenant_id,occurred_at DESC);
    """)
    op.execute("""
    CREATE FUNCTION public.dental_bind_room_reservation()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
    DECLARE a record;
    BEGIN
      SELECT occupied_from,occupied_until,status INTO a
      FROM public.appointments
      WHERE tenant_id=NEW.tenant_id AND id=NEW.appointment_id;
      IF NOT FOUND OR a.occupied_from IS NULL OR a.occupied_until IS NULL
         OR a.occupied_from >= a.occupied_until THEN
        RAISE EXCEPTION 'Reserva de consulta inexistente' USING ERRCODE='23503';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM public.dental_rooms
          WHERE tenant_id=NEW.tenant_id AND id=NEW.room_id AND active) THEN
        RAISE EXCEPTION 'Consultório indisponível' USING ERRCODE='23503';
      END IF;
      NEW.period := tstzrange(a.occupied_from,a.occupied_until,'[)');
      NEW.active := a.status IN ('hold','confirmed','completed');
      RETURN NEW;
    END $$;
    CREATE TRIGGER dental_bind_room BEFORE INSERT OR UPDATE ON public.dental_room_reservations
      FOR EACH ROW EXECUTE FUNCTION public.dental_bind_room_reservation();
    CREATE FUNCTION public.dental_refresh_room_from_appointment()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
    BEGIN
      UPDATE public.dental_room_reservations
      SET period=tstzrange(NEW.occupied_from,NEW.occupied_until,'[)'),
          active=NEW.status IN ('hold','confirmed','completed')
      WHERE tenant_id=NEW.tenant_id AND appointment_id=NEW.id;
      RETURN NEW;
    END $$;
    CREATE TRIGGER dental_follow_appointment AFTER UPDATE OF
      occupied_from,occupied_until,status,start_at,end_at ON public.appointments
      FOR EACH ROW EXECUTE FUNCTION public.dental_refresh_room_from_appointment();
    REVOKE ALL ON FUNCTION public.dental_bind_room_reservation() FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.dental_refresh_room_from_appointment() FROM PUBLIC;
    """)
    tables = ("dental_clinicians","dental_patients","dental_records","dental_exam_files",
              "dental_rooms","dental_room_reservations","dental_supplies",
              "dental_supply_movements","dental_access_audit")
    for table in tables:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,app_runtime,app_support")
        op.execute(f"CREATE POLICY {table}_migrator ON public.{table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        sensitive = table in ("dental_patients","dental_records","dental_exam_files")
        if sensitive:
            # Even an inadvertent SQL read in another tenant API cannot retrieve
            # health records: the private transaction must also set a clinician.
            scope = (f"tenant_id={TENANT} AND EXISTS (SELECT 1 FROM public.dental_clinicians g "
                     f"WHERE g.tenant_id=tenant_id AND g.account_id={ACTOR} AND g.enabled)")
        else:
            scope = f"tenant_id={TENANT}"
        op.execute(f"CREATE POLICY {table}_runtime ON public.{table} FOR ALL TO app_runtime "
                   f"USING({scope}) WITH CHECK({scope})")
    grants = {
      "dental_clinicians":"SELECT,INSERT,UPDATE",
      "dental_patients":"SELECT,INSERT,UPDATE",
      "dental_records":"SELECT,INSERT",
      "dental_exam_files":"SELECT,INSERT",
      "dental_rooms":"SELECT,INSERT,UPDATE",
      "dental_room_reservations":"SELECT,INSERT,UPDATE,DELETE",
      "dental_supplies":"SELECT,INSERT,UPDATE",
      "dental_supply_movements":"SELECT,INSERT",
      "dental_access_audit":"SELECT,INSERT",
    }
    for table,privileges in grants.items():
        op.execute(f"GRANT {privileges} ON public.{table} TO app_runtime")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE public.dental_access_audit_id_seq TO app_runtime")


def downgrade():
    raise RuntimeError("Registros odontológicos precisam ser preservados: somente migração de recuperação progressiva.")
