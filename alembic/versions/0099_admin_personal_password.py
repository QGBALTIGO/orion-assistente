"""Optional personal password for the existing native administrator."""
from alembic import op

revision = "0099_admin_personal_password"
down_revision = "0098_manual_pix_successor"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        SET LOCAL lock_timeout='5s';
        CREATE TABLE public.orion_legacy_admin_passwords (
            account_id uuid PRIMARY KEY REFERENCES public.panel_accounts(id) ON DELETE RESTRICT,
            password_hash text NOT NULL CHECK (password_hash LIKE '$argon2id$%' AND length(password_hash) BETWEEN 50 AND 255),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        REVOKE ALL ON public.orion_legacy_admin_passwords FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT,UPDATE ON public.orion_legacy_admin_passwords TO app_support;
        ALTER TABLE public.orion_legacy_admin_passwords ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.orion_legacy_admin_passwords FORCE ROW LEVEL SECURITY;
        CREATE POLICY admin_personal_password_support ON public.orion_legacy_admin_passwords
            TO app_support USING (true) WITH CHECK (true);
    """)

def downgrade():
    # Dropping a rotated credential would revive the previous environment
    # password. This release is deliberately forward-only.
    raise RuntimeError("Admin credential migration is forward-only")
