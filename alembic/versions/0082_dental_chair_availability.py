"""Keep room status and reservation writes consistent and preserve closed visits."""
from alembic import op

revision = "0082_dental_chair_availability"
down_revision = "0081_exceptional_overlap"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE OR REPLACE FUNCTION public.dental_bind_room_reservation()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE a record;
                preserve_history boolean := false;
        BEGIN
          SELECT occupied_from,occupied_until,status INTO a
          FROM public.appointments
          WHERE tenant_id=NEW.tenant_id AND id=NEW.appointment_id;
          IF NOT FOUND OR a.occupied_from IS NULL OR a.occupied_until IS NULL
             OR a.occupied_from >= a.occupied_until THEN
            RAISE EXCEPTION 'Reserva de consulta inexistente' USING ERRCODE='23503';
          END IF;
          IF TG_OP='UPDATE' THEN
            preserve_history := OLD.room_id=NEW.room_id AND OLD.tenant_id=NEW.tenant_id
              AND OLD.appointment_id=NEW.appointment_id AND a.status NOT IN ('hold','confirmed');
          END IF;
          IF NOT preserve_history THEN
            -- Serialize with the settings endpoint before it checks for open reservations.
            PERFORM 1 FROM public.dental_rooms
            WHERE tenant_id=NEW.tenant_id AND id=NEW.room_id AND active FOR SHARE;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'Cadeira indisponível' USING ERRCODE='23503';
            END IF;
          END IF;
          NEW.period := tstzrange(a.occupied_from,a.occupied_until,'[)');
          NEW.active := a.status IN ('hold','confirmed','completed');
          RETURN NEW;
        END $$;
    """)


def downgrade():
    op.execute("""
        CREATE OR REPLACE FUNCTION public.dental_bind_room_reservation()
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
    """)
