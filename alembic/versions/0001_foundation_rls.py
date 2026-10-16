"""FATIA 0: tenants e tabela de verificação com RLS fail-closed.

Revision ID: 0001_foundation_rls
Revises:
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001_foundation_rls"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Campo_Grande"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active', 'paused')", name="ck_tenants_status"),
    )
    op.create_table(
        "tenant_probe",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("length(trim(label)) > 0", name="ck_tenant_probe_label"),
    )
    op.create_index("ix_tenant_probe_tenant_id", "tenant_probe", ["tenant_id"])

    # FORCE impede inclusive o dono da tabela de ignorar RLS sem política.
    for table in ("tenants", "tenant_probe"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(f"""
        CREATE POLICY tenants_runtime_select ON tenants FOR SELECT TO app_runtime
        USING (id = {TENANT_SETTING})
    """)
    op.execute(f"""
        CREATE POLICY tenant_probe_runtime ON tenant_probe FOR ALL TO app_runtime
        USING (tenant_id = {TENANT_SETTING})
        WITH CHECK (tenant_id = {TENANT_SETTING})
    """)
    # Migration role nunca é usado pelo servidor. Administração explicitamente
    # limitada à função de migração/seed de testes, com credenciais separadas.
    op.execute("""
        CREATE POLICY tenants_migrator ON tenants FOR ALL TO app_migrator
        USING (true) WITH CHECK (true)
    """)
    op.execute("""
        CREATE POLICY probe_migrator ON tenant_probe FOR ALL TO app_migrator
        USING (true) WITH CHECK (true)
    """)
    op.execute("REVOKE ALL ON tenants, tenant_probe FROM PUBLIC")
    op.execute("REVOKE ALL ON tenants, tenant_probe FROM app_runtime")
    op.execute("GRANT SELECT ON tenants TO app_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_probe TO app_runtime")


def downgrade() -> None:
    op.drop_index("ix_tenant_probe_tenant_id", table_name="tenant_probe")
    op.drop_table("tenant_probe")
    op.drop_table("tenants")
