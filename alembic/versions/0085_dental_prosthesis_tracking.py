"""Link append-only prosthesis snapshots without exposing clinical content."""
from alembic import op

revision = "0085_dental_prosthesis_tracking"
down_revision = "0084_dental_supply_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE public.dental_records
        ADD COLUMN previous_prosthesis_id uuid,
        ADD CONSTRAINT uq_dental_record_patient UNIQUE (tenant_id,patient_id,id),
        ADD CONSTRAINT fk_dental_prosthesis_predecessor
            FOREIGN KEY (tenant_id,patient_id,previous_prosthesis_id)
            REFERENCES public.dental_records (tenant_id,patient_id,id),
        ADD CONSTRAINT ck_dental_prosthesis_predecessor
            CHECK (previous_prosthesis_id IS NULL OR (kind='prosthesis' AND previous_prosthesis_id<>id));
        CREATE UNIQUE INDEX uq_dental_prosthesis_successor
            ON public.dental_records (tenant_id,previous_prosthesis_id)
            WHERE previous_prosthesis_id IS NOT NULL;
        CREATE INDEX ix_dental_prosthesis_worklist
            ON public.dental_records (tenant_id,created_at DESC,id DESC)
            WHERE kind='prosthesis';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_records WHERE previous_prosthesis_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Preserve linked clinical records before downgrading the schema';
            END IF;
        END $$;
        DROP INDEX public.ix_dental_prosthesis_worklist;
        DROP INDEX public.uq_dental_prosthesis_successor;
        ALTER TABLE public.dental_records
        DROP CONSTRAINT fk_dental_prosthesis_predecessor,
        DROP CONSTRAINT ck_dental_prosthesis_predecessor,
        DROP CONSTRAINT uq_dental_record_patient,
        DROP COLUMN previous_prosthesis_id;
    """)
