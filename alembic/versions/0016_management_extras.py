"""Recurring blocks, provider usage, operational preferences and reviewed invitations.

Revision ID: 0016_management_extras
Revises: 0015_loyalty_rewards
"""
from alembic import op

revision = "0016_management_extras"
down_revision = "0015_loyalty_rewards"
branch_labels = None
depends_on = None
SETTING = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"
RUNTIME_TABLES = ("management_block_series", "management_block_occurrences", "management_ai_runs", "management_preferences", "management_notification_reads")


def upgrade():
    op.execute("""
        CREATE TABLE management_block_series (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
            professional_id uuid NOT NULL, kind varchar(12) NOT NULL CHECK(kind IN ('hours','day_off')),
            frequency varchar(12) NOT NULL CHECK(frequency IN ('daily','weekly','weekdays')),
            start_date date NOT NULL, until_date date NOT NULL,
            weekdays jsonb NOT NULL DEFAULT '[]', start_time time, end_time time, reason varchar(200),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,professional_id) REFERENCES professionals(tenant_id,id),
            CHECK(until_date>=start_date AND until_date-start_date<366),
            CHECK(jsonb_typeof(weekdays)='array'),
            CHECK((kind='hours' AND start_time IS NOT NULL AND end_time IS NOT NULL AND start_time<end_time)
               OR (kind='day_off' AND start_time IS NULL AND end_time IS NULL))
        );
        CREATE TABLE management_block_occurrences (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
            series_id uuid NOT NULL, block_id uuid NOT NULL, occurrence_date date NOT NULL,
            start_at timestamptz NOT NULL, end_at timestamptz NOT NULL, active boolean NOT NULL DEFAULT true,
            removed_at timestamptz, UNIQUE(tenant_id,id), UNIQUE(tenant_id,series_id,occurrence_date),
            UNIQUE(tenant_id,block_id),
            FOREIGN KEY(tenant_id,series_id) REFERENCES management_block_series(tenant_id,id), CHECK(start_at<end_at)
        );
        CREATE INDEX ix_block_series_period ON management_block_series(tenant_id,professional_id,start_date,until_date);
        CREATE INDEX ix_block_occurrence_period ON management_block_occurrences(tenant_id,occurrence_date,active);
        CREATE TABLE management_ai_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
            idempotency_key varchar(120) NOT NULL, fingerprint char(64) NOT NULL,
            task varchar(32) NOT NULL CHECK(task IN ('campaign','consultant','loyalty')),
            status varchar(16) NOT NULL DEFAULT 'processing' CHECK(status IN ('processing','succeeded','failed')),
            model varchar(80) NOT NULL, output jsonb, sources jsonb NOT NULL DEFAULT '{}', usage jsonb NOT NULL DEFAULT '{}',
            created_by uuid REFERENCES panel_accounts(id), created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz, error_code integer,
            UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
            CHECK(length(idempotency_key) BETWEEN 8 AND 120),
            CHECK(output IS NULL OR jsonb_typeof(output)='object'),
            CHECK(jsonb_typeof(sources)='object' AND jsonb_typeof(usage)='object')
        );
        CREATE INDEX ix_management_ai_usage ON management_ai_runs(tenant_id,created_at);
        CREATE TABLE management_preferences (
            tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
            preferences jsonb NOT NULL DEFAULT '{}', version integer NOT NULL DEFAULT 1 CHECK(version>=1),
            updated_at timestamptz NOT NULL DEFAULT now(), CHECK(jsonb_typeof(preferences)='object')
        );
        CREATE TABLE management_notification_reads (
            tenant_id uuid NOT NULL REFERENCES tenants(id), account_id uuid NOT NULL REFERENCES panel_accounts(id),
            event_id uuid NOT NULL, read_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(tenant_id,account_id,event_id)
        );
        CREATE TABLE management_team_invitations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
            email varchar(254), role varchar(16) NOT NULL CHECK(role IN ('editor','viewer')),
            token_digest char(64) NOT NULL UNIQUE, idempotency_key varchar(120) NOT NULL, fingerprint char(64) NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','claimed','approved','revoked')),
            expires_at timestamptz NOT NULL, created_by uuid REFERENCES panel_accounts(id),
            claimed_by uuid REFERENCES panel_accounts(id), reviewed_by uuid REFERENCES panel_accounts(id),
            version integer NOT NULL DEFAULT 1 CHECK(version>=1),
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
            CHECK(length(idempotency_key) BETWEEN 8 AND 120),
            CHECK(email IS NULL OR (email=lower(email) AND position('@' in email)>1)),
            CHECK(status NOT IN ('claimed','approved') OR claimed_by IS NOT NULL)
        );
        CREATE INDEX ix_management_team_pending ON management_team_invitations(tenant_id,status,expires_at);
    """)
    for table in RUNTIME_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING(tenant_id={SETTING}) WITH CHECK(tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING(true) WITH CHECK(true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
        privileges = "SELECT,INSERT" if table == "management_block_series" else "SELECT,INSERT,UPDATE"
        op.execute(f"GRANT {privileges} ON {table} TO app_runtime")
    # Account membership is deliberately handled by the same narrow support
    # boundary as tenant_memberships. Runtime cannot enumerate accounts/invites.
    table = "management_team_invitations"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,app_runtime,app_support")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON {table} TO app_support")
    for role in ("app_support", "app_migrator"):
        op.execute(f"CREATE POLICY {table}_{role} ON {table} FOR ALL TO {role} USING(true) WITH CHECK(true)")


def downgrade():
    for table in ("management_team_invitations", "management_notification_reads", "management_preferences", "management_ai_runs", "management_block_occurrences", "management_block_series"):
        op.drop_table(table)
