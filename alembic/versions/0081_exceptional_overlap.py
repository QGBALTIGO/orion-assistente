"""Explicit internal shared appointments, maximum two concurrent clients.

Normal appointments retain the original GiST exclusion. Shared exceptions
are checked under the exact same professional lock by both API and database.
Availability always considers both kinds of reservation.
"""
from alembic import op

revision = "0081_exceptional_overlap"
down_revision = "0080_service_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("""
        ALTER TABLE appointments ADD COLUMN allow_overlap boolean NOT NULL DEFAULT false;
        ALTER TABLE appointments ADD CONSTRAINT ck_appointment_shared_exception
          CHECK (NOT allow_overlap OR (booking_mode='exceptional' AND status<>'hold'
                 AND created_by_account_id IS NOT NULL));
        ALTER TABLE appointments DROP CONSTRAINT appointments_no_overlap;
        ALTER TABLE appointments ADD CONSTRAINT appointments_no_overlap
          EXCLUDE USING gist (tenant_id WITH =, professional_id WITH =,
            tstzrange(occupied_from,occupied_until,'[)') WITH &&)
          WHERE (status IN ('hold','confirmed') AND NOT allow_overlap);

        CREATE FUNCTION public.orion_guard_exceptional_overlap() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $guard$
        BEGIN
          IF NEW.status NOT IN ('hold','confirmed') THEN RETURN NEW; END IF;
          IF TG_OP='UPDATE' AND
             (NEW.tenant_id,NEW.professional_id,NEW.customer_id,NEW.occupied_from,
              NEW.occupied_until,NEW.allow_overlap,NEW.booking_mode) IS NOT DISTINCT FROM
             (OLD.tenant_id,OLD.professional_id,OLD.customer_id,OLD.occupied_from,
              OLD.occupied_until,OLD.allow_overlap,OLD.booking_mode) AND
             (OLD.status='confirmed' OR (OLD.status='hold' AND OLD.hold_expires_at>clock_timestamp()))
          THEN RETURN NEW; END IF;
          -- Match booking_service.acquire_professional_lock exactly. Every
          -- occupancy-changing API path takes it before touching this row.
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text||':'||NEW.professional_id::text,0));
          IF NEW.allow_overlap THEN
            IF EXISTS(SELECT 1 FROM public.appointments a
              WHERE a.tenant_id=NEW.tenant_id AND a.professional_id=NEW.professional_id
                AND a.id<>NEW.id AND a.status IN ('hold','confirmed')
                AND (a.status<>'hold' OR a.hold_expires_at>clock_timestamp())
                AND a.occupied_from<NEW.occupied_until AND a.occupied_until>NEW.occupied_from
                AND (a.status='hold' OR a.customer_id=NEW.customer_id)) OR EXISTS(
              SELECT 1 FROM public.appointments a JOIN public.appointments b
                ON b.tenant_id=a.tenant_id AND b.professional_id=a.professional_id AND a.id<b.id
              WHERE a.tenant_id=NEW.tenant_id AND a.professional_id=NEW.professional_id
                AND a.id<>NEW.id AND b.id<>NEW.id AND a.status='confirmed' AND b.status='confirmed'
                AND a.occupied_from<NEW.occupied_until AND a.occupied_until>NEW.occupied_from
                AND b.occupied_from<NEW.occupied_until AND b.occupied_until>NEW.occupied_from
                AND GREATEST(a.occupied_from,b.occupied_from,NEW.occupied_from)
                    < LEAST(a.occupied_until,b.occupied_until,NEW.occupied_until))
            THEN RAISE EXCEPTION 'exceptional overlap capacity exceeded' USING ERRCODE='23P01'; END IF;
          ELSIF EXISTS(SELECT 1 FROM public.appointments a
              WHERE a.tenant_id=NEW.tenant_id AND a.professional_id=NEW.professional_id AND a.id<>NEW.id
                AND a.status IN ('hold','confirmed')
                AND (a.status<>'hold' OR a.hold_expires_at>clock_timestamp())
                AND a.occupied_from<NEW.occupied_until AND a.occupied_until>NEW.occupied_from)
          THEN RAISE EXCEPTION 'appointment period is occupied' USING ERRCODE='23P01'; END IF;
          RETURN NEW;
        END $guard$;
        REVOKE ALL ON FUNCTION public.orion_guard_exceptional_overlap() FROM PUBLIC,app_support,app_runtime;
        CREATE TRIGGER appointments_exceptional_overlap_guard
          BEFORE INSERT OR UPDATE OF tenant_id,professional_id,customer_id,occupied_from,
            occupied_until,status,hold_expires_at,allow_overlap,booking_mode ON appointments
          FOR EACH ROW EXECUTE FUNCTION public.orion_guard_exceptional_overlap();
    """)


def downgrade():
    # Fail closed if shared active visits exist; never cancel customers or
    # erase their explicit authorization just to roll a release back.
    op.execute("""
      DO $$ BEGIN IF EXISTS(SELECT 1 FROM appointments WHERE allow_overlap AND status IN ('hold','confirmed'))
        THEN RAISE EXCEPTION 'active shared appointments require reconciliation'; END IF; END $$;
      DROP TRIGGER appointments_exceptional_overlap_guard ON appointments;
      DROP FUNCTION public.orion_guard_exceptional_overlap();
      ALTER TABLE appointments DROP CONSTRAINT appointments_no_overlap;
      ALTER TABLE appointments ADD CONSTRAINT appointments_no_overlap
        EXCLUDE USING gist (tenant_id WITH =,professional_id WITH =,
          tstzrange(occupied_from,occupied_until,'[)') WITH &&) WHERE(status IN ('hold','confirmed'));
      ALTER TABLE appointments DROP COLUMN allow_overlap;
    """)
