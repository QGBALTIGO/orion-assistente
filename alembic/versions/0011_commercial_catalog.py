"""Separate product catalog, bounded stock observations, quotes and explicit orders.

Revision ID: 0011_commercial_catalog
Revises: 0010_business_profiles
Adds tables only; leaves appointments, existing services, billing and auth untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0011_commercial_catalog"
down_revision = "0010_business_profiles"
branch_labels = None
depends_on = None
SETTING = "NULLIF(current_setting('app.tenant_id',true),'')::uuid"


def uid():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def tenant():
    return sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)


def timestamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def scope(table):
    return sa.UniqueConstraint("tenant_id", "id", name="uq_" + table + "_scope")


def link(column, table, name):
    return sa.ForeignKeyConstraint(["tenant_id", column], [table + ".tenant_id", table + ".id"], name=name)


def upgrade():
    op.create_table("commercial_products", uid(), tenant(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("synonyms", JSONB(), nullable=False, server_default="[]"),
        sa.Column("faqs", JSONB(), nullable=False, server_default="[]"),
        sa.Column("official_benefits", JSONB(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        timestamp(), timestamp("updated_at"), scope("commercial_products"),
        sa.CheckConstraint("length(trim(name)) > 0 AND revision > 0", name="ck_commercial_product"),
        sa.CheckConstraint("jsonb_typeof(synonyms)='array' AND jsonb_typeof(faqs)='array' AND jsonb_typeof(official_benefits)='array'", name="ck_product_facts"))
    op.create_table("commercial_variants", uid(), tenant(),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sku", sa.String(80)),
        sa.Column("attributes", JSONB(), nullable=False, server_default="{}"),
        sa.Column("price_kind", sa.String(16), nullable=False, server_default="on_request"),
        sa.Column("unit_price_cents", sa.BigInteger()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        timestamp(), timestamp("updated_at"), scope("commercial_variants"),
        link("product_id", "commercial_products", "fk_commercial_variant_product"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_commercial_sku"),
        sa.UniqueConstraint("tenant_id", "product_id", "name", name="uq_commercial_variant_name"),
        sa.CheckConstraint("length(trim(name))>0 AND revision>0 AND jsonb_typeof(attributes)='object'", name="ck_commercial_variant"),
        sa.CheckConstraint("(price_kind IN ('fixed','from') AND unit_price_cents IS NOT NULL AND unit_price_cents BETWEEN 0 AND 100000000) OR (price_kind IN ('on_request','custom') AND unit_price_cents IS NULL)", name="ck_commercial_price"))
    op.create_table("commercial_inventory", tenant(),
        sa.Column("variant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("quantity_on_hand", sa.Integer()),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(160)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        timestamp("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "variant_id"),
        link("variant_id", "commercial_variants", "fk_inventory_variant"),
        sa.CheckConstraint("reserved_quantity>=0 AND revision>0", name="ck_inventory_counters"),
        sa.CheckConstraint("(mode='unknown' AND quantity_on_hand IS NULL AND observed_at IS NULL AND source IS NULL AND valid_until IS NULL) OR (mode='tracked' AND quantity_on_hand IS NOT NULL AND quantity_on_hand BETWEEN reserved_quantity AND 10000000 AND observed_at IS NOT NULL AND source IS NOT NULL AND length(trim(source))>0 AND valid_until IS NOT NULL AND valid_until>observed_at AND valid_until<=observed_at+interval '24 hours')", name="ck_inventory_observation"))
    op.create_table("commercial_complements", uid(), tenant(),
        sa.Column("source_product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        timestamp("updated_at"),
        link("source_product_id", "commercial_products", "fk_complement_source"),
        link("target_product_id", "commercial_products", "fk_complement_target"),
        sa.UniqueConstraint("tenant_id", "source_product_id", "target_product_id", name="uq_commercial_complement"),
        sa.CheckConstraint("source_product_id<>target_product_id AND length(trim(reason))>0", name="ck_complement"))
    op.create_table("commercial_quotes", uid(), tenant(),
        sa.Column("contact_e164", sa.String(16)),
        sa.Column("customer_name", sa.String(120)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("total_cents", sa.BigInteger()),
        sa.Column("minimum_total_cents", sa.BigInteger()),
        sa.Column("confirmation_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        timestamp(), scope("commercial_quotes"),
        sa.CheckConstraint("currency='BRL' AND (total_cents IS NULL OR total_cents>=0) AND (minimum_total_cents IS NULL OR minimum_total_cents>=0)", name="ck_quote_amount"),
        sa.CheckConstraint("contact_e164 IS NULL OR contact_e164 ~ '^\\+[1-9][0-9]{7,14}$'", name="ck_quote_contact"))
    op.create_table("commercial_quote_items", uid(), tenant(),
        sa.Column("quote_id", UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("variant_revision", sa.Integer(), nullable=False),
        sa.Column("inventory_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        link("quote_id", "commercial_quotes", "fk_commercial_quote_item"),
        link("variant_id", "commercial_variants", "fk_commercial_quote_variant"),
        link("product_id", "commercial_products", "fk_commercial_quote_product"),
        sa.UniqueConstraint("tenant_id", "quote_id", "variant_id", name="uq_commercial_quote_variant"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 1000 AND item_order>=0 AND product_revision>0 AND variant_revision>0 AND inventory_revision>0 AND jsonb_typeof(snapshot)='object'", name="ck_commercial_quote_item"))
    op.create_table("commercial_orders", uid(), tenant(),
        sa.Column("quote_id", UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("payment_status", sa.String(16), nullable=False, server_default="not_verified"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        timestamp(), timestamp("updated_at"), scope("commercial_orders"),
        link("quote_id", "commercial_quotes", "fk_commercial_order_quote"),
        sa.UniqueConstraint("tenant_id", "quote_id", name="uq_commercial_order_quote"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_commercial_order_key"),
        sa.CheckConstraint("status IN ('draft','confirmed','cancelled') AND payment_status='not_verified' AND revision>0", name="ck_commercial_order_state"))
    op.create_table("commercial_events", uid(), tenant(),
        sa.Column("entity", sa.String(24), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_account_id", UUID(as_uuid=True), sa.ForeignKey("panel_accounts.id", ondelete="RESTRICT")),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("details", JSONB(), nullable=False, server_default="{}"), timestamp(),
        sa.CheckConstraint("entity IN ('product','variant','stock','complement','quote','order') AND jsonb_typeof(details)='object'", name="ck_commercial_event"),
        sa.CheckConstraint("actor_role IN ('owner','editor','support','admin','customer')", name="ck_commercial_event_actor"))
    for table in ("commercial_products", "commercial_variants", "commercial_inventory", "commercial_complements", "commercial_quotes", "commercial_quote_items", "commercial_orders", "commercial_events"):
        op.create_index("ix_" + table + "_tenant", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_runtime ON {table} FOR ALL TO app_runtime USING (tenant_id={SETTING}) WITH CHECK (tenant_id={SETTING})")
        op.execute(f"CREATE POLICY {table}_migrator ON {table} FOR ALL TO app_migrator USING (true) WITH CHECK (true)")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, app_support, app_runtime")
        immutable = table in {"commercial_quotes", "commercial_quote_items", "commercial_events"}
        privileges = "SELECT,INSERT" if immutable else "SELECT,INSERT,UPDATE"
        op.execute(f"GRANT {privileges} ON {table} TO app_runtime")


def downgrade():
    # Production rollback keeps data and disables the new routes. Never use a
    # destructive schema downgrade as a normal application rollback.
    for table in ("commercial_events", "commercial_orders", "commercial_quote_items", "commercial_quotes", "commercial_complements", "commercial_inventory", "commercial_variants", "commercial_products"):
        op.drop_table(table)
