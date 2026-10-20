"""Give the existing environment administrator an explicit, optional identity.

No account is provisioned and no credential, membership or session is rewritten.
"""
from alembic import op

revision = '0030_legacy_admin_identity'
down_revision = '0029_communication_contract'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
      ALTER TABLE public.panel_accounts ADD COLUMN auth_provider varchar(20) NOT NULL DEFAULT 'password';
      ALTER TABLE public.panel_accounts ADD COLUMN legacy_username varchar(254);
      ALTER TABLE public.panel_accounts ADD COLUMN legacy_credential_digest char(64);
      ALTER TABLE public.panel_accounts ALTER COLUMN email DROP NOT NULL;
      ALTER TABLE public.panel_accounts ALTER COLUMN password_hash DROP NOT NULL;
      ALTER TABLE public.panel_accounts ADD CONSTRAINT ck_panel_account_auth_identity CHECK (
        (auth_provider='password' AND email IS NOT NULL AND password_hash IS NOT NULL
          AND legacy_username IS NULL AND legacy_credential_digest IS NULL)
        OR (auth_provider='legacy_admin' AND role='admin' AND email IS NULL AND password_hash IS NULL
          AND legacy_username IS NOT NULL AND length(legacy_username) BETWEEN 1 AND 254
          AND legacy_username=lower(btrim(legacy_username)) AND legacy_username !~ '[[:cntrl:]]'
          AND legacy_credential_digest IS NOT NULL AND legacy_credential_digest ~ '^[a-f0-9]{64}$'));
      CREATE UNIQUE INDEX uq_panel_account_legacy_admin_identity ON public.panel_accounts(auth_provider)
        WHERE auth_provider='legacy_admin';
      CREATE OR REPLACE FUNCTION public.inbox_operator_version(t uuid,a uuid) RETURNS integer
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE v integer; r text; provider text; digest text; m record;
      BEGIN
        IF t IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN RETURN NULL; END IF;
        SELECT auth_version,role,auth_provider,legacy_credential_digest INTO v,r,provider,digest
          FROM public.panel_accounts WHERE id=a FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF provider='legacy_admin' THEN
          IF r<>'admin' OR digest IS DISTINCT FROM NULLIF(current_setting('app.legacy_admin_digest',true),'')
            THEN RETURN NULL; END IF;
          RETURN v;
        END IF;
        IF provider<>'password' THEN RETURN NULL; END IF;
        IF r='admin' THEN RETURN v; END IF;
        SELECT active,role INTO m FROM public.tenant_memberships WHERE tenant_id=t AND account_id=a FOR SHARE;
        IF NOT FOUND OR NOT m.active OR m.role NOT IN ('owner','editor') THEN RETURN NULL; END IF;
        RETURN v;
      END $fn$;
    ''')


def downgrade():
    op.execute('''
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.panel_accounts WHERE auth_provider='legacy_admin') THEN
          RAISE EXCEPTION 'Preserve the native administrator identity; downgrade refused';
        END IF;
      END $$;
      CREATE OR REPLACE FUNCTION public.inbox_operator_version(t uuid,a uuid) RETURNS integer
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE v integer; r text; m record;
      BEGIN
        IF t IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN RETURN NULL; END IF;
        SELECT auth_version,role INTO v,r FROM public.panel_accounts WHERE id=a FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF r='admin' THEN RETURN v; END IF;
        SELECT active,role INTO m FROM public.tenant_memberships WHERE tenant_id=t AND account_id=a FOR SHARE;
        IF NOT FOUND OR NOT m.active OR m.role NOT IN ('owner','editor') THEN RETURN NULL; END IF;
        RETURN v;
      END $fn$;
      DROP INDEX public.uq_panel_account_legacy_admin_identity;
      ALTER TABLE public.panel_accounts DROP CONSTRAINT ck_panel_account_auth_identity;
      ALTER TABLE public.panel_accounts ALTER COLUMN email SET NOT NULL;
      ALTER TABLE public.panel_accounts ALTER COLUMN password_hash SET NOT NULL;
      ALTER TABLE public.panel_accounts DROP COLUMN legacy_credential_digest;
      ALTER TABLE public.panel_accounts DROP COLUMN legacy_username;
      ALTER TABLE public.panel_accounts DROP COLUMN auth_provider;
    ''')
