"""Bind a single verified Orion account to a registered professional only.

No historical membership is promoted; generic invitations remain historical.
The new role is deliberately excluded from company-wide data routes.
"""
from alembic import op

revision = "0057_professional_portal"
down_revision = "0056_one_click_appointment_links"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='10s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("""
        -- A professional archived and later reactivated must not silently
        -- regain portal access from a previous verified invitation.
        ALTER TABLE professionals ADD COLUMN last_archived_at timestamptz;
        CREATE FUNCTION public.orion_professional_archive_fence()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $archive$
        BEGIN
            IF OLD.active AND NOT NEW.active THEN
                NEW.last_archived_at := clock_timestamp();
            END IF;
            RETURN NEW;
        END $archive$;
        REVOKE ALL ON FUNCTION public.orion_professional_archive_fence()
            FROM PUBLIC,app_runtime,app_support;
        CREATE TRIGGER orion_professional_archive_fence
            BEFORE UPDATE OF active ON professionals
            FOR EACH ROW EXECUTE FUNCTION public.orion_professional_archive_fence();
        ALTER TABLE tenant_memberships DROP CONSTRAINT ck_membership_role;
        ALTER TABLE tenant_memberships ADD CONSTRAINT ck_membership_role
            CHECK (role IN ('owner','editor','viewer','professional'));
        ALTER TABLE tenant_membership_events DROP CONSTRAINT ck_membership_event_role;
        ALTER TABLE tenant_membership_events ADD CONSTRAINT ck_membership_event_role
            CHECK (role IN ('owner','editor','viewer','professional'));
        ALTER TABLE management_team_invitations
            ADD COLUMN professional_id uuid NULL;
        ALTER TABLE management_team_invitations
            ADD CONSTRAINT fk_team_invitation_professional
            FOREIGN KEY (tenant_id,professional_id) REFERENCES professionals(tenant_id,id);
        ALTER TABLE management_team_invitations
            DROP CONSTRAINT management_team_invitations_role_check;
        ALTER TABLE management_team_invitations
            ADD CONSTRAINT management_team_invitations_role_check
            CHECK(role IN ('editor','viewer','professional'));
        ALTER TABLE management_team_invitations
            ADD CONSTRAINT ck_professional_invite_binding
            CHECK ((role='professional')=(professional_id IS NOT NULL));
        CREATE INDEX ix_team_professional_invites
            ON management_team_invitations (tenant_id,professional_id,created_at DESC)
            WHERE professional_id IS NOT NULL;
        CREATE TABLE professional_portal_links (
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            professional_id uuid NOT NULL,
            email varchar(254) NOT NULL,
            account_id uuid REFERENCES panel_accounts(id) ON DELETE RESTRICT,
            active boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id,professional_id),
            UNIQUE (tenant_id,account_id),
            FOREIGN KEY (tenant_id,professional_id)
                REFERENCES professionals(tenant_id,id) ON DELETE RESTRICT,
            CHECK (email=lower(email) AND position('@' IN email)>1),
            CHECK (NOT active OR account_id IS NOT NULL)
        );
        CREATE INDEX ix_professional_portal_account
            ON professional_portal_links (account_id) WHERE account_id IS NOT NULL;
        ALTER TABLE professional_portal_links ENABLE ROW LEVEL SECURITY;
        ALTER TABLE professional_portal_links FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON professional_portal_links FROM PUBLIC,app_runtime,app_support;
        GRANT SELECT,INSERT,UPDATE,DELETE ON professional_portal_links TO app_support;
        CREATE POLICY professional_portal_support ON professional_portal_links
            FOR ALL TO app_support USING (true) WITH CHECK (true);
        CREATE POLICY professional_portal_migrator ON professional_portal_links
            FOR ALL TO app_migrator USING (true) WITH CHECK (true);
    """)


def downgrade():
    raise RuntimeError("Professional identity links require an audited, forward-only recovery")
