"""Link a sales opportunity to one explicitly selected dental patient."""
from alembic import op

revision = "0089_dental_sales_patient_link"
down_revision = "0088_dental_sales_opportunities"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE public.dental_sales_opportunities ADD COLUMN patient_id uuid;
        ALTER TABLE public.dental_sales_opportunities ADD CONSTRAINT fk_dental_sales_patient
            FOREIGN KEY (tenant_id,patient_id) REFERENCES public.dental_patients(tenant_id,id);
        CREATE INDEX ix_dental_sales_patient ON public.dental_sales_opportunities
            (tenant_id,patient_id,created_at DESC,id DESC) WHERE patient_id IS NOT NULL;

        CREATE FUNCTION public.dental_sales_patient_identity(target uuid)
        RETURNS TABLE(id uuid,name varchar,birth_date date,guardian_name varchar)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
        AS $body$
            SELECT p.id,p.name,p.birth_date,p.guardian_name FROM public.dental_patients p
            WHERE public.dental_sales_owner()
              AND p.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
              AND p.id=target
        $body$;
        CREATE FUNCTION public.dental_sales_patient_options(term text,row_limit integer,row_offset integer)
        RETURNS TABLE(id uuid,name varchar,birth_date date,guardian_name varchar)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
        AS $body$
            SELECT p.id,p.name,p.birth_date,p.guardian_name FROM public.dental_patients p
            WHERE public.dental_sales_owner()
              AND p.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
              AND CASE
                WHEN term ~ '^[+0-9 ()-]+$' AND length(regexp_replace(term,'[^0-9]','','g'))>=4
                THEN regexp_replace(coalesce(p.contact_phone,''),'[^0-9]','','g') LIKE
                       '%'||regexp_replace(term,'[^0-9]','','g')||'%'
                  OR regexp_replace(coalesce(p.guardian_phone,''),'[^0-9]','','g') LIKE
                       '%'||regexp_replace(term,'[^0-9]','','g')||'%'
                ELSE p.name ILIKE '%'||replace(replace(replace(term,'!','!!'),'%','!%'),'_','!_')||'%' ESCAPE '!'
              END
            ORDER BY lower(p.name),p.id
            LIMIT least(greatest(row_limit,1),31) OFFSET greatest(row_offset,0)
        $body$;
        REVOKE ALL ON FUNCTION public.dental_sales_patient_identity(uuid) FROM PUBLIC,app_support;
        REVOKE ALL ON FUNCTION public.dental_sales_patient_options(text,integer,integer) FROM PUBLIC,app_support;
        GRANT EXECUTE ON FUNCTION public.dental_sales_patient_identity(uuid) TO app_runtime;
        GRANT EXECUTE ON FUNCTION public.dental_sales_patient_options(text,integer,integer) TO app_runtime;
    """)


def downgrade():
    op.execute("""
        LOCK TABLE public.dental_sales_opportunities IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.dental_sales_opportunities WHERE patient_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Preserve os vínculos de pacientes antes de reverter o esquema';
            END IF;
        END $$;
        DROP FUNCTION public.dental_sales_patient_options(text,integer,integer);
        DROP FUNCTION public.dental_sales_patient_identity(uuid);
        DROP INDEX public.ix_dental_sales_patient;
        ALTER TABLE public.dental_sales_opportunities DROP CONSTRAINT fk_dental_sales_patient;
        ALTER TABLE public.dental_sales_opportunities DROP COLUMN patient_id;
    """)
